"""Project-bound SQLite setup and schema checks."""

from __future__ import annotations

import os
import re
import sqlite3
import tempfile
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import URL
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import ConnectionPoolEntry

_PROJECT_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_DATABASE_NAME = "world.sqlite"
_DIRECTORY_MODE = 0o700
_DATABASE_MODE = 0o600
_MIGRATION_LOCK = threading.Lock()
_SESSION_PROJECT_ID_KEY = "agent_runtime.project_id"


class ProjectDatabaseError(RuntimeError):
    """Base error for a Project SQLite boundary failure."""


class InvalidProjectIdError(ProjectDatabaseError):
    """The Project ID cannot safely identify a Project directory."""


class ProjectDatabaseNotFoundError(ProjectDatabaseError):
    """A load attempted to open a Project database that does not exist."""


class ProjectDatabasePathError(ProjectDatabaseError):
    """A Runtime path is not an owned, contained regular path."""


class ProjectDatabaseIdentityError(ProjectDatabaseError):
    """The database file does not belong to the requested Project."""


class SchemaRevisionError(ProjectDatabaseError):
    """The database schema is not at the Runtime's current migration head."""


class ProjectDatabaseIntegrityError(ProjectDatabaseError):
    """The database contains rows that violate its relational constraints."""


class Base(DeclarativeBase):
    """Shared declarative base for World-owned and Agent-owned rows."""


@dataclass(frozen=True, slots=True)
class ProjectDatabase:
    """One verified SQLite file and Session factory bound to one Project."""

    project_id: str
    path: Path
    engine: Engine
    session_factory: sessionmaker[Session]

    def dispose(self) -> None:
        self.engine.dispose()


def open_project_database(
    runtime_root: Path,
    project_id: str,
    *,
    create: bool,
) -> ProjectDatabase:
    """Open one Project database, optionally creating its file and schema."""

    project_directory, path = _resolve_database_path(runtime_root, project_id, create=create)
    if not path.exists() and not create:
        raise ProjectDatabaseNotFoundError(f'Project database for "{project_id}" does not exist')
    if not path.exists():
        _publish_new_database(project_directory, path, project_id)
    _require_regular_database(path)

    engine = create_project_engine(path)
    try:
        require_current_schema(engine)
        _require_project_identity(engine, project_id)
        _require_database_integrity(engine)
        path.chmod(_DATABASE_MODE)
        project_directory.chmod(_DIRECTORY_MODE)
    except BaseException:
        engine.dispose()
        raise

    return ProjectDatabase(
        project_id=project_id,
        path=path,
        engine=engine,
        session_factory=sessionmaker(
            engine,
            expire_on_commit=False,
            info={_SESSION_PROJECT_ID_KEY: project_id},
        ),
    )


def create_project_engine(path: Path) -> Engine:
    """Create a SQLite Engine whose every connection enforces the same pragmas."""

    # Python 3.12 still defaults sqlite3 to legacy transaction control, where
    # SELECT does not start a real database transaction.  Explicit PEP 249
    # control makes one Session transaction a consistent multi-table snapshot.
    engine = create_engine(
        _sqlite_url(path),
        connect_args={"autocommit": False},
    )
    event.listen(engine, "connect", configure_sqlite_connection)
    return engine


def upgrade_to_head(path: Path) -> None:
    """Apply all known migrations to a newly created Project database."""

    # Alembic's EnvironmentContext proxy is process-global and cannot safely run
    # two command.upgrade calls in parallel threads.
    with _MIGRATION_LOCK:
        engine = create_project_engine(path)
        try:
            config = _alembic_config(path)
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
        finally:
            engine.dispose()


def current_schema_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def expected_schema_revision() -> str:
    revision = ScriptDirectory.from_config(
        _alembic_config(Path("unused.sqlite"))
    ).get_current_head()
    if revision is None:
        raise SchemaRevisionError("Runtime migrations do not contain a head revision")
    return revision


def require_current_schema(engine: Engine) -> None:
    current = current_schema_revision(engine)
    expected = expected_schema_revision()
    if current != expected:
        raise SchemaRevisionError(f"Project database schema is {current!r}; expected {expected!r}")


def require_session_project(session: Session, project_id: str) -> None:
    """Reject a World-bound Store used with another Project's SQL session."""

    bound_project_id = session.info.get(_SESSION_PROJECT_ID_KEY)
    if bound_project_id is not None:
        if bound_project_id != project_id:
            raise ProjectDatabaseIdentityError(
                f"Session Project is {bound_project_id!r}; expected {project_id!r}"
            )
        return

    row_count: object = session.scalar(text("SELECT count(*) FROM project_database"))
    stored_project_id: object = session.scalar(
        text("SELECT project_id FROM project_database WHERE singleton_id = 1")
    )
    if row_count != 1 or stored_project_id != project_id:
        raise ProjectDatabaseIdentityError(
            f"Project database identity is {stored_project_id!r}; expected {project_id!r}"
        )


def _publish_new_database(
    project_directory: Path,
    path: Path,
    project_id: str,
) -> None:
    """Build a complete private database, then publish it without overwriting."""

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{_DATABASE_NAME}.",
        suffix=".tmp",
        dir=project_directory,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        temporary_path.chmod(_DATABASE_MODE)
        upgrade_to_head(temporary_path)
        temporary_engine = create_project_engine(temporary_path)
        try:
            require_current_schema(temporary_engine)
            _create_project_identity(temporary_engine, project_id)
            _require_project_identity(temporary_engine, project_id)
        finally:
            temporary_engine.dispose()
        _fsync_file(temporary_path)
        try:
            os.link(temporary_path, path)
        except FileExistsError:
            # Another creator atomically published the same Project database.
            # The caller validates that winner below before returning it.
            pass
        else:
            _fsync_directory(project_directory)
    finally:
        temporary_path.unlink(missing_ok=True)


def _fsync_file(path: Path) -> None:
    with path.open("rb") as file:
        os.fsync(file.fileno())


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _resolve_database_path(
    runtime_root: Path,
    project_id: str,
    *,
    create: bool,
) -> tuple[Path, Path]:
    if _PROJECT_ID.fullmatch(project_id) is None:
        raise InvalidProjectIdError(
            "project_id must start with a lowercase letter and contain only "
            "lowercase letters, digits, and hyphens"
        )

    if runtime_root.is_symlink():
        raise ProjectDatabasePathError("runtime root cannot be a symlink")
    if create:
        runtime_root.mkdir(parents=True, exist_ok=True)
        runtime_root.chmod(_DIRECTORY_MODE)
    if not runtime_root.is_dir():
        raise ProjectDatabaseNotFoundError(f'Runtime root "{runtime_root}" does not exist')

    resolved_root = runtime_root.resolve(strict=True)
    project_directory = resolved_root / project_id
    if project_directory.is_symlink():
        raise ProjectDatabasePathError("Project runtime directory cannot be a symlink")
    if create:
        project_directory.mkdir(exist_ok=True)
        project_directory.chmod(_DIRECTORY_MODE)
    if not project_directory.is_dir():
        raise ProjectDatabaseNotFoundError(
            f'Runtime directory for Project "{project_id}" does not exist'
        )
    if project_directory.resolve(strict=True).parent != resolved_root:
        raise ProjectDatabasePathError("Project runtime directory escapes the runtime root")

    path = project_directory / _DATABASE_NAME
    if path.is_symlink():
        raise ProjectDatabasePathError("Project database cannot be a symlink")
    return project_directory, path


def _require_regular_database(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise ProjectDatabasePathError(f'Project database "{path}" is not a regular file')
    if path.resolve(strict=True).parent != path.parent.resolve(strict=True):
        raise ProjectDatabasePathError("Project database escapes its Project directory")


def _create_project_identity(engine: Engine, project_id: str) -> None:
    with engine.begin() as connection:
        rows = connection.execute(
            text("SELECT singleton_id, project_id FROM project_database")
        ).all()
        if rows:
            raise ProjectDatabaseIdentityError(
                "new Project database already contains an identity row"
            )
        connection.execute(
            text(
                "INSERT INTO project_database(singleton_id, project_id, created_at) "
                "VALUES (1, :project_id, :created_at)"
            ),
            {"project_id": project_id, "created_at": datetime.now(UTC).isoformat()},
        )


def _require_project_identity(engine: Engine, project_id: str) -> None:
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT singleton_id, project_id FROM project_database")
        ).all()
    if len(rows) != 1 or rows[0] != (1, project_id):
        actual = None if len(rows) != 1 else rows[0].project_id
        raise ProjectDatabaseIdentityError(
            f'Project database identity is {actual!r}; expected "{project_id}"'
        )


def _require_database_integrity(engine: Engine) -> None:
    with engine.connect() as connection:
        violation = connection.exec_driver_sql("PRAGMA foreign_key_check").first()
    if violation is not None:
        raise ProjectDatabaseIntegrityError(
            f'Project database contains an invalid foreign key in table "{violation[0]}"'
        )


def _alembic_config(path: Path) -> Config:
    migrations = Path(__file__).with_name("migrations")
    config = Config()
    config.set_main_option("script_location", str(migrations))
    config.set_main_option("sqlalchemy.url", _sqlite_url(path).replace("%", "%%"))
    return config


def _sqlite_url(path: Path) -> str:
    return URL.create("sqlite", database=str(path.resolve())).render_as_string(hide_password=False)


def configure_sqlite_connection(
    dbapi_connection: DBAPIConnection,
    _connection_record: ConnectionPoolEntry,
) -> None:
    connection = cast(sqlite3.Connection, dbapi_connection)
    previous_autocommit = connection.autocommit
    connection.autocommit = True
    cursor = connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()
        connection.autocommit = previous_autocommit
