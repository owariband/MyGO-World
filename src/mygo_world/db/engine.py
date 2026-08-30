from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL

from mygo_world.errors import SchemaOutdatedError


def sqlite_url(path: Path) -> str:
    return URL.create("sqlite", database=str(path.resolve())).render_as_string(
        hide_password=False
    )


def create_world_engine(path: Path) -> Engine:
    engine = create_engine(sqlite_url(path), future=True)

    @event.listens_for(engine, "connect")
    def configure_sqlite(dbapi_connection: object, _connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    return engine


def alembic_config(path: Path) -> Config:
    migrations = Path(__file__).parent / "migrations"
    config = Config()
    config.set_main_option("script_location", str(migrations))
    config.set_main_option("sqlalchemy.url", sqlite_url(path).replace("%", "%%"))
    return config


def upgrade_to_head(path: Path) -> None:
    command.upgrade(alembic_config(path), "head")


def expected_schema_revision(path: Path) -> str:
    return ScriptDirectory.from_config(alembic_config(path)).get_current_head()


def current_schema_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def require_current_schema(path: Path, engine: Engine) -> None:
    expected = expected_schema_revision(path)
    current = current_schema_revision(engine)
    if current != expected:
        raise SchemaOutdatedError(current, expected)
