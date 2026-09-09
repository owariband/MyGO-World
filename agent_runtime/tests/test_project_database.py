"""M2.1 Project database, public state, and isolation tests."""

from __future__ import annotations

import shutil
import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Never

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import ValidationError
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.schema import SchemaItem

import agent_runtime.sqlite as sqlite_runtime
from agent_runtime.agent.memory.storage import MemoryRow
from agent_runtime.agent.personact.storage import AgentRuntimeStateRow
from agent_runtime.sqlite import (
    Base,
    InvalidProjectIdError,
    ProjectDatabaseIdentityError,
    ProjectDatabaseIntegrityError,
    ProjectDatabaseNotFoundError,
    ProjectDatabasePathError,
    SchemaRevisionError,
    current_schema_revision,
    expected_schema_revision,
    open_project_database,
    upgrade_to_head,
)
from agent_runtime.world.contracts import WorldRef
from agent_runtime.world.initializer import initialize_public_world
from agent_runtime.world.state import (
    AgentWorldState,
    EventSessionNode,
    LocationState,
    ObjectState,
    PublicWorldState,
    WorldFact,
    WorldState,
    WorldStatus,
)
from agent_runtime.world.storage import (
    WorldAlreadyExistsError,
    WorldNotFoundError,
    WorldOwnershipError,
    WorldStore,
)

NOW = datetime(2026, 9, 8, 10, 15, tzinfo=UTC)
EXPECTED_TABLES = {
    "agent_memory_records",
    "agent_runtime_states",
    "agent_world_states",
    "alembic_version",
    "event_sessions",
    "locations",
    "objects",
    "project_database",
    "world_facts",
    "worlds",
}


def test_project_database_creates_private_current_schema_and_identity(tmp_path: Path) -> None:
    runtime_root = tmp_path / ".runtime"
    database = open_project_database(runtime_root, "coffee-golden", create=True)
    try:
        assert database.path == runtime_root / "coffee-golden" / "world.sqlite"
        assert stat.S_IMODE(runtime_root.stat().st_mode) == 0o700
        assert stat.S_IMODE(database.path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(database.path.stat().st_mode) == 0o600
        assert set(inspect(database.engine).get_table_names()) == EXPECTED_TABLES
        assert current_schema_revision(database.engine) == expected_schema_revision()

        with database.engine.connect() as connection:
            identity = connection.execute(
                text("SELECT singleton_id, project_id FROM project_database")
            ).one()
            foreign_keys = connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
            busy_timeout = connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one()
            driver_connection = connection.connection.driver_connection
            assert isinstance(driver_connection, sqlite3.Connection)
            assert driver_connection.autocommit is False
            assert driver_connection.in_transaction
        assert identity == (1, "coffee-golden")
        assert foreign_keys == 1
        assert busy_timeout == 5000
    finally:
        database.dispose()


def test_initial_migration_matches_all_mapped_storage_rows(tmp_path: Path) -> None:
    database = open_project_database(tmp_path / ".runtime", "coffee-golden", create=True)
    try:
        assert MemoryRow.metadata is Base.metadata
        assert AgentRuntimeStateRow.metadata is Base.metadata
        with database.engine.connect() as connection:
            differences = compare_metadata(
                MigrationContext.configure(connection, opts={"compare_type": True}),
                Base.metadata,
            )
        assert differences == []
    finally:
        database.dispose()


def test_failed_first_creation_never_publishes_a_partial_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / ".runtime"

    def fail_identity(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected identity failure")

    with monkeypatch.context() as patcher:
        patcher.setattr(sqlite_runtime, "_create_project_identity", fail_identity)
        with pytest.raises(RuntimeError, match="injected identity failure"):
            open_project_database(runtime_root, "coffee-golden", create=True)

    project_directory = runtime_root / "coffee-golden"
    assert not (project_directory / "world.sqlite").exists()
    assert tuple(project_directory.glob(".world.sqlite.*.tmp")) == ()

    database = open_project_database(runtime_root, "coffee-golden", create=True)
    database.dispose()


def test_migration_failure_rolls_back_ddl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "failed-migration.sqlite"
    original_create_table = Operations.create_table

    def create_first_table_then_fail(
        operations: Operations,
        table_name: str,
        *columns: SchemaItem,
        if_not_exists: bool | None = None,
        **kwargs: object,
    ) -> Never:
        assert not kwargs
        original_create_table(
            operations,
            table_name,
            *columns,
            if_not_exists=if_not_exists,
        )
        raise RuntimeError("injected migration failure")

    with monkeypatch.context() as patcher:
        patcher.setattr(Operations, "create_table", create_first_table_then_fail)
        with pytest.raises(RuntimeError, match="injected migration failure"):
            upgrade_to_head(path)

    with sqlite3.connect(path) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    assert tables == []


def test_concurrent_first_open_atomically_publishes_one_complete_database(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / ".runtime"

    def open_once(_index: int) -> Path:
        database = open_project_database(runtime_root, "coffee-golden", create=True)
        try:
            return database.path
        finally:
            database.dispose()

    with ThreadPoolExecutor(max_workers=6) as executor:
        paths = tuple(executor.map(open_once, range(12)))

    assert len(set(paths)) == 1
    assert paths[0] == runtime_root / "coffee-golden" / "world.sqlite"
    assert tuple(paths[0].parent.glob(".world.sqlite.*.tmp")) == ()
    reopened = open_project_database(runtime_root, "coffee-golden", create=False)
    reopened.dispose()


def test_load_missing_database_does_not_create_a_file(tmp_path: Path) -> None:
    runtime_root = tmp_path / ".runtime"

    with pytest.raises(ProjectDatabaseNotFoundError, match="does not exist"):
        open_project_database(runtime_root, "coffee-golden", create=False)

    assert not runtime_root.exists()


@pytest.mark.parametrize(
    "project_id",
    ("../coffee", "Coffee", "coffee_shop", "coffee/shop", ".", ""),
)
def test_project_id_is_validated_before_creating_directories(
    tmp_path: Path, project_id: str
) -> None:
    runtime_root = tmp_path / ".runtime"

    with pytest.raises(InvalidProjectIdError):
        open_project_database(runtime_root, project_id, create=True)

    assert not runtime_root.exists()


def test_runtime_root_project_directory_and_database_symlinks_are_rejected(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    linked_root = tmp_path / "linked-runtime"
    linked_root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ProjectDatabasePathError, match="root"):
        open_project_database(linked_root, "coffee-golden", create=True)

    runtime_root = tmp_path / ".runtime"
    runtime_root.mkdir()
    linked_project = runtime_root / "coffee-golden"
    linked_project.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ProjectDatabasePathError, match="directory"):
        open_project_database(runtime_root, "coffee-golden", create=True)

    linked_project.unlink()
    linked_project.mkdir()
    outside_database = outside / "other.sqlite"
    outside_database.touch()
    (linked_project / "world.sqlite").symlink_to(outside_database)
    with pytest.raises(ProjectDatabasePathError, match="database"):
        open_project_database(runtime_root, "coffee-golden", create=True)


def test_copied_database_is_rejected_by_project_identity(tmp_path: Path) -> None:
    runtime_root = tmp_path / ".runtime"
    original = open_project_database(runtime_root, "coffee-golden", create=True)
    original.dispose()

    copied_directory = runtime_root / "another-story"
    copied_directory.mkdir()
    shutil.copy2(runtime_root / "coffee-golden" / "world.sqlite", copied_directory)

    with pytest.raises(ProjectDatabaseIdentityError, match="coffee-golden"):
        open_project_database(runtime_root, "another-story", create=False)


def test_open_rejects_foreign_key_corruption_even_when_identity_matches(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / ".runtime"
    database = open_project_database(runtime_root, "coffee-golden", create=True)
    with database.session_factory.begin() as session:
        WorldStore(WorldRef(project_id="coffee-golden", world_id="save-001")).insert_initial(
            session,
            _initial_world(WorldRef(project_id="coffee-golden", world_id="save-001")),
        )
    path = database.path
    database.dispose()

    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE worlds SET project_id = 'another-story' WHERE world_id = 'save-001'"
        )

    with pytest.raises(ProjectDatabaseIntegrityError, match="worlds"):
        open_project_database(runtime_root, "coffee-golden", create=False)


def test_outdated_schema_is_rejected_without_automatic_upgrade(tmp_path: Path) -> None:
    runtime_root = tmp_path / ".runtime"
    database = open_project_database(runtime_root, "coffee-golden", create=True)
    with database.engine.begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num = 'outdated'"))
    database.dispose()

    with pytest.raises(SchemaRevisionError, match="outdated"):
        open_project_database(runtime_root, "coffee-golden", create=False)


def test_public_world_round_trips_after_engine_restart(tmp_path: Path) -> None:
    runtime_root = tmp_path / ".runtime"
    world_ref = WorldRef(project_id="coffee-golden", world_id="save-001")
    initial = _initial_world(world_ref)
    database = open_project_database(runtime_root, world_ref.project_id, create=True)
    with database.session_factory.begin() as session:
        initialize_public_world(session, initial)
    database.dispose()

    reopened = open_project_database(runtime_root, world_ref.project_id, create=False)
    try:
        with reopened.session_factory() as session:
            assert WorldStore(world_ref).load(session) == initial
    finally:
        reopened.dispose()


def test_store_does_not_commit_callers_transaction(tmp_path: Path) -> None:
    world_ref = WorldRef(project_id="coffee-golden", world_id="rolled-back")
    database = open_project_database(tmp_path / ".runtime", world_ref.project_id, create=True)
    try:
        with database.session_factory() as session:
            transaction = session.begin()
            WorldStore(world_ref).insert_initial(session, _initial_world(world_ref))
            transaction.rollback()

        with database.session_factory() as session, pytest.raises(WorldNotFoundError):
            WorldStore(world_ref).load(session)
    finally:
        database.dispose()


def test_duplicate_world_is_rejected_without_overwriting(tmp_path: Path) -> None:
    world_ref = WorldRef(project_id="coffee-golden", world_id="save-001")
    initial = _initial_world(world_ref)
    database = open_project_database(tmp_path / ".runtime", world_ref.project_id, create=True)
    try:
        with database.session_factory.begin() as session:
            WorldStore(world_ref).insert_initial(session, initial)

        with (
            pytest.raises(WorldAlreadyExistsError),
            database.session_factory.begin() as session,
        ):
            WorldStore(world_ref).insert_initial(session, initial)

        with database.session_factory() as session:
            assert WorldStore(world_ref).load(session) == initial
    finally:
        database.dispose()


def test_store_rejects_foreign_world_and_project_before_staging_rows(tmp_path: Path) -> None:
    database = open_project_database(tmp_path / ".runtime", "coffee-golden", create=True)
    own_ref = WorldRef(project_id="coffee-golden", world_id="save-001")
    foreign_world = WorldRef(project_id="coffee-golden", world_id="save-002")
    foreign_project = WorldRef(project_id="another-story", world_id="save-001")
    try:
        with database.session_factory() as session:
            with pytest.raises(WorldOwnershipError, match="not Store"):
                WorldStore(own_ref).insert_initial(session, _initial_world(foreign_world))
            assert not session.new

            with pytest.raises(WorldOwnershipError, match="database identity"):
                WorldStore(foreign_project).insert_initial(session, _initial_world(foreign_project))
            assert not session.new
    finally:
        database.dispose()


def test_two_worlds_can_reuse_internal_ids_without_cross_world_foreign_keys(
    tmp_path: Path,
) -> None:
    project_id = "coffee-golden"
    first_ref = WorldRef(project_id=project_id, world_id="save-001")
    second_ref = WorldRef(project_id=project_id, world_id="save-002")
    database = open_project_database(tmp_path / ".runtime", project_id, create=True)
    try:
        with database.session_factory.begin() as session:
            WorldStore(first_ref).insert_initial(session, _initial_world(first_ref))
            WorldStore(second_ref).insert_initial(session, _initial_world(second_ref))
            session.execute(
                text(
                    "INSERT INTO locations(world_id, location_id, name, description) "
                    "VALUES ('save-002', 'only-in-save-002', '二号出口', '仅存在于第二存档')"
                )
            )

        with database.session_factory() as session:
            assert WorldStore(first_ref).load(session).world.world_ref == first_ref
            assert WorldStore(second_ref).load(session).world.world_ref == second_ref

        with pytest.raises(IntegrityError), database.session_factory.begin() as session:
            session.execute(
                text(
                    "INSERT INTO agent_world_states"
                    "(world_id, agent_id, location_id, public_status) "
                    "VALUES ('save-001', 'foreign', 'only-in-save-002', NULL)"
                )
            )
    finally:
        database.dispose()


def test_session_root_foreign_key_and_loaded_partition_validation(tmp_path: Path) -> None:
    world_ref = WorldRef(project_id="coffee-golden", world_id="save-001")
    database = open_project_database(tmp_path / ".runtime", world_ref.project_id, create=True)
    try:
        with database.session_factory.begin() as session:
            WorldStore(world_ref).insert_initial(session, _initial_world(world_ref))

        with pytest.raises(IntegrityError), database.session_factory.begin() as session:
            session.execute(
                text(
                    "UPDATE event_sessions SET root_session_id = 'missing' "
                    "WHERE world_id = 'save-001' AND session_id = 'session-anon'"
                )
            )

        with database.session_factory.begin() as session:
            session.execute(
                text(
                    "UPDATE event_sessions SET root_session_id = 'session-soyo' "
                    "WHERE world_id = 'save-001' AND session_id = 'session-anon'"
                )
            )
        with (
            database.session_factory() as session,
            pytest.raises(ValidationError, match="must point to itself"),
        ):
            WorldStore(world_ref).load(session)
    finally:
        database.dispose()


def _initial_world(world_ref: WorldRef) -> PublicWorldState:
    locations = (
        LocationState(
            world_ref=world_ref,
            location_id="cafe",
            name="咖啡店",
            description="学校附近的安静咖啡店",
        ),
    )
    agents = (
        AgentWorldState(
            world_ref=world_ref,
            agent_id="anon",
            location_id="cafe",
            public_status="talking",
        ),
        AgentWorldState(
            world_ref=world_ref,
            agent_id="soyo",
            location_id="cafe",
            public_status="listening",
        ),
    )
    return PublicWorldState(
        world=WorldState(
            world_ref=world_ref,
            seed_id="coffee-opening",
            seed_version=1,
            seed_hash="a" * 64,
            current_version=1,
            world_time=NOW,
            status=WorldStatus.PAUSED,
            created_at=NOW,
        ),
        locations=locations,
        agents=agents,
        objects=(
            ObjectState(
                world_ref=world_ref,
                object_id="coffee-machine",
                name="咖啡机",
                kind="appliance",
                description="柜台后的咖啡机",
                location_id="cafe",
                state="idle",
            ),
        ),
        facts=(
            WorldFact(
                world_ref=world_ref,
                fact_id="cafe-open",
                location_id="cafe",
                predicate="is_open",
                object="true",
                content="咖啡店正在营业",
            ),
        ),
        sessions=(
            EventSessionNode(
                world_ref=world_ref,
                session_id="session-anon",
                agent_id="anon",
                root_session_id="session-anon",
                topology_version=1,
                updated_world_version=1,
            ),
            EventSessionNode(
                world_ref=world_ref,
                session_id="session-soyo",
                agent_id="soyo",
                root_session_id="session-anon",
                topology_version=1,
                updated_world_version=1,
            ),
        ),
    )
