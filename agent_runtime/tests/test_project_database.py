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
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import ValidationError
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.schema import SchemaItem

import agent_runtime.sqlite as sqlite_runtime
from agent_runtime.agent.memory.storage import MemoryRow, MemoryStore
from agent_runtime.agent.personact.state import CognitiveConfig, PersonaState
from agent_runtime.agent.personact.storage import AgentRuntimeStateRow, PersonaStateStore
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
from agent_runtime.world.entry_storage import (
    EventEntryLinkRow,
    EventEntryRecipientRow,
    EventEntryRow,
    EventEntryStore,
    InteractionRequestRow,
)
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
    WorldCommitConflictError,
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
    "event_entries",
    "event_entry_links",
    "event_entry_recipients",
    "event_session_transition_parts",
    "event_sessions",
    "interaction_requests",
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
        assert EventEntryRow.metadata is Base.metadata
        assert EventEntryLinkRow.metadata is Base.metadata
        assert EventEntryRecipientRow.metadata is Base.metadata
        assert InteractionRequestRow.metadata is Base.metadata
        with database.engine.connect() as connection:
            differences = compare_metadata(
                MigrationContext.configure(connection, opts={"compare_type": True}),
                Base.metadata,
            )
        assert differences == []
    finally:
        database.dispose()


def test_m3_migration_downgrades_and_reupgrades_without_losing_m2_world(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / ".runtime"
    world_ref = WorldRef(project_id="coffee-golden", world_id="save-001")
    database = open_project_database(runtime_root, world_ref.project_id, create=True)
    with database.session_factory.begin() as session:
        WorldStore(world_ref).insert_initial(session, _initial_world(world_ref))

    config = Config()
    config.set_main_option(
        "script_location",
        str(Path(sqlite_runtime.__file__).parent / "migrations"),
    )
    try:
        with database.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "0001_m2_world")
        assert current_schema_revision(database.engine) == "0001_m2_world"
        inspector = inspect(database.engine)
        assert "event_entries" not in inspector.get_table_names()
        assert "control_epoch" not in {column["name"] for column in inspector.get_columns("worlds")}
        with database.engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM worlds")) == 1

        with database.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        assert current_schema_revision(database.engine) == expected_schema_revision()
        with database.session_factory() as session:
            restored = WorldStore(world_ref).load(session)
        assert restored.world.control_epoch == 1
        assert restored.world.decision_seq == 0
        assert restored.agents == _initial_world(world_ref).agents
    finally:
        database.dispose()


def test_m4_downgrade_refuses_runtime_state_without_data_loss(tmp_path: Path) -> None:
    runtime_root = tmp_path / ".runtime"
    world_ref = WorldRef(project_id="coffee-golden", world_id="m4-save")
    database = open_project_database(runtime_root, world_ref.project_id, create=True)
    with database.session_factory.begin() as session:
        WorldStore(world_ref).insert_initial(session, _initial_world(world_ref))
        session.execute(
            text(
                "UPDATE worlds SET dispatch_count = 1, dispatch_limit_at = 1 "
                "WHERE world_id = :world_id"
            ),
            {"world_id": world_ref.world_id},
        )

    config = Config()
    config.set_main_option(
        "script_location",
        str(Path(sqlite_runtime.__file__).parent / "migrations"),
    )
    try:
        with (
            pytest.raises(RuntimeError, match="M4 runtime state"),
            database.engine.begin() as connection,
        ):
            config.attributes["connection"] = connection
            command.downgrade(config, "0002_m3_event_entries")

        assert current_schema_revision(database.engine) == expected_schema_revision()
        assert "dispatch_count" in {
            column["name"] for column in inspect(database.engine).get_columns("worlds")
        }
        with database.engine.connect() as connection:
            saved = connection.execute(
                text(
                    "SELECT dispatch_count, dispatch_limit_at FROM worlds "
                    "WHERE world_id = :world_id"
                ),
                {"world_id": world_ref.world_id},
            ).one()
        assert saved == (1, 1)
    finally:
        database.dispose()


def test_m4_migration_preserves_real_entry_request_persona_and_memory_rows(
    tmp_path: Path,
) -> None:
    project_id = "migration-fixture"
    world_ref = WorldRef(project_id=project_id, world_id="save-001")
    runtime_root = tmp_path / ".runtime"
    project_directory = runtime_root / project_id
    project_directory.mkdir(parents=True)
    path = project_directory / "world.sqlite"
    engine = sqlite_runtime.create_project_engine(path)
    config = Config()
    config.set_main_option(
        "script_location",
        str(Path(sqlite_runtime.__file__).parent / "migrations"),
    )
    persona = PersonaState(
        world_ref=world_ref,
        agent_id="soyo",
        cognitive_config=CognitiveConfig(
            attention_budget=1,
            retention=20,
            recency_weight=1.0,
            relevance_weight=1.0,
            importance_weight=1.0,
            recency_decay=0.99,
            reflection_threshold=10.0,
            reflection_count=5,
        ),
        reflection_remaining=7.0,
        last_world_time=NOW,
        last_world_version=2,
        known_place_ids=("cafe",),
    )
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0002_m3_event_entries")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO project_database(singleton_id, project_id, created_at) "
                    "VALUES (1, :project_id, :created_at)"
                ),
                {"project_id": project_id, "created_at": NOW.isoformat()},
            )
            connection.execute(
                text(
                    "INSERT INTO worlds("
                    "world_id, project_id, seed_id, seed_version, seed_hash, current_version, "
                    "world_time, status, created_at, control_epoch, decision_seq"
                    ") VALUES ("
                    ":world_id, :project_id, 'migration-seed', 1, :seed_hash, 2, "
                    ":world_time, 'paused', :created_at, 4, 9)"
                ),
                {
                    "world_id": world_ref.world_id,
                    "project_id": project_id,
                    "seed_hash": "d" * 64,
                    "world_time": NOW.isoformat(),
                    "created_at": NOW.isoformat(),
                },
            )
            connection.execute(
                text(
                    "INSERT INTO locations(world_id, location_id, name, description) "
                    "VALUES (:world_id, 'cafe', 'Cafe', 'Migration fixture cafe')"
                ),
                {"world_id": world_ref.world_id},
            )
            for agent_id in ("anon", "soyo"):
                connection.execute(
                    text(
                        "INSERT INTO agent_world_states("
                        "world_id, agent_id, location_id, public_status"
                        ") VALUES (:world_id, :agent_id, 'cafe', 'talking')"
                    ),
                    {"world_id": world_ref.world_id, "agent_id": agent_id},
                )
            for session_id, agent_id in (("session-anon", "anon"), ("session-soyo", "soyo")):
                connection.execute(
                    text(
                        "INSERT INTO event_sessions("
                        "world_id, session_id, agent_id, root_session_id, "
                        "topology_version, updated_world_version"
                        ") VALUES ("
                        ":world_id, :session_id, :agent_id, 'session-anon', 1, 1)"
                    ),
                    {
                        "world_id": world_ref.world_id,
                        "session_id": session_id,
                        "agent_id": agent_id,
                    },
                )
            connection.execute(
                text(
                    "INSERT INTO event_entries("
                    "world_id, entry_id, status, entry_kind, source_kind, source_id, "
                    "source_index, world_version, entry_index, root_session_id_at_commit, "
                    "topology_version, actor_agent_id, target_agent_id, target_object_id, "
                    "operation_id, audience_mode, delivery_channel, occurred_at, text, created_at"
                    ") VALUES ("
                    ":world_id, 'entry-1', 'committed', 'dialogue', 'character_proposal', "
                    "'proposal-1', 0, 2, 0, 'session-anon', 1, 'anon', 'soyo', NULL, NULL, "
                    "'session', 'direct', :occurred_at, 'Migration keeps this dialogue', "
                    ":created_at)"
                ),
                {
                    "world_id": world_ref.world_id,
                    "occurred_at": NOW.isoformat(),
                    "created_at": NOW.isoformat(),
                },
            )
            for agent_id in ("anon", "soyo"):
                connection.execute(
                    text(
                        "INSERT INTO event_entry_recipients(world_id, entry_id, agent_id) "
                        "VALUES (:world_id, 'entry-1', :agent_id)"
                    ),
                    {"world_id": world_ref.world_id, "agent_id": agent_id},
                )
            connection.execute(
                text(
                    "INSERT INTO interaction_requests("
                    "world_id, request_entry_id, request_kind, requester_agent_id, "
                    "recipient_agent_id, status, resolution_entry_id, updated_world_version"
                    ") VALUES ("
                    ":world_id, 'entry-1', 'response', 'anon', 'soyo', "
                    "'pending', NULL, 2)"
                ),
                {"world_id": world_ref.world_id},
            )
            connection.execute(
                text(
                    "INSERT INTO agent_runtime_states("
                    "world_id, agent_id, state_revision, spec_digest, persona_state_json, "
                    "observation_world_version, observation_entry_index, "
                    "last_decision_id, last_decision_outcome"
                    ") VALUES ("
                    ":world_id, 'soyo', 3, :spec_digest, :persona, 2, 0, "
                    "'proposal-1', 'applied')"
                ),
                {
                    "world_id": world_ref.world_id,
                    "spec_digest": "e" * 64,
                    "persona": persona.model_dump_json(by_alias=True, exclude_none=False),
                },
            )
            connection.execute(
                text(
                    "INSERT INTO agent_memory_records("
                    "world_id, agent_id, memory_id, memory_seq, scope, kind, created_at, "
                    "last_accessed_at, expires_at, subject, predicate, object_value, content, "
                    "poignancy, tags_json, source, evidence_ids_json, embedding_json, "
                    "novelty_key, source_entry_id"
                    ") VALUES ("
                    ":world_id, 'soyo', 'memory-1', 1, :scope, 'chat', :created_at, "
                    ":last_accessed_at, NULL, 'anon', 'said', NULL, "
                    "'Migration keeps this memory', 2.5, '[\"migration\"]', "
                    "'event-entry', '[\"entry-1\"]', '[0.25,0.75]', 'migration-memory', "
                    "'entry-1')"
                ),
                {
                    "world_id": world_ref.world_id,
                    "scope": "project/migration/persona/soyo",
                    "created_at": NOW.isoformat(),
                    "last_accessed_at": NOW.isoformat(),
                },
            )

        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    finally:
        engine.dispose()

    database = open_project_database(runtime_root, project_id, create=False)
    try:
        assert current_schema_revision(database.engine) == expected_schema_revision()
        with database.session_factory() as session:
            world = WorldStore(world_ref).load(session)
            entry = EventEntryStore(world_ref).get(session, "entry-1")
            request = EventEntryStore(world_ref).get_request(session, "entry-1")
            stored_persona = PersonaStateStore(world_ref).load(session, "soyo")
            memory = MemoryStore(world_ref).load(
                session,
                "soyo",
                "project/migration/persona/soyo",
            )
            foreign_key_violations = session.execute(text("PRAGMA foreign_key_check")).all()

        assert world.world.control_epoch == 4
        assert world.world.decision_seq == 9
        assert world.world.dispatch_count == 0
        assert world.world.dispatch_limit_at == 0
        assert all(node.last_dispatch_count == 0 for node in world.sessions)
        assert entry.text == "Migration keeps this dialogue"
        assert request.status.value == "pending"
        assert request.cancellation_entry_id is None
        assert request.priority_consumed_dispatch_count is None
        assert stored_persona.state_revision == 3
        assert stored_persona.state == persona
        assert stored_persona.observation_cursor is not None
        assert stored_persona.observation_cursor.world_version == 2
        assert stored_persona.last_decision_id == "proposal-1"
        assert len(memory.records) == 1
        assert memory.records[0].source_entry_id == "entry-1"
        assert memory.records[0].content == "Migration keeps this memory"
        assert foreign_key_violations == []
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


def test_m3_migration_failure_rolls_back_to_complete_m2_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / ".runtime"
    database = open_project_database(runtime_root, "coffee-golden", create=True)
    config = Config()
    config.set_main_option(
        "script_location",
        str(Path(sqlite_runtime.__file__).parent / "migrations"),
    )
    original_create_table = Operations.create_table
    try:
        with database.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "0001_m2_world")

        def create_m3_entry_table_then_fail(
            operations: Operations,
            table_name: str,
            *columns: SchemaItem,
            if_not_exists: bool | None = None,
            **kwargs: object,
        ) -> object:
            created = original_create_table(
                operations,
                table_name,
                *columns,
                if_not_exists=if_not_exists,
                **kwargs,
            )
            if table_name == "event_entries":
                raise RuntimeError("injected M3 migration failure")
            return created

        with monkeypatch.context() as patcher:
            patcher.setattr(Operations, "create_table", create_m3_entry_table_then_fail)
            with (
                pytest.raises(RuntimeError, match="injected M3 migration failure"),
                database.engine.begin() as connection,
            ):
                config.attributes["connection"] = connection
                command.upgrade(config, "head")

        assert current_schema_revision(database.engine) == "0001_m2_world"
        inspector = inspect(database.engine)
        assert "event_entries" not in inspector.get_table_names()
        world_columns = {column["name"] for column in inspector.get_columns("worlds")}
        assert "control_epoch" not in world_columns
        assert "decision_seq" not in world_columns
    finally:
        database.dispose()


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


def test_world_compare_and_advance_fences_stale_work_and_object_state(
    tmp_path: Path,
) -> None:
    world_ref = WorldRef(project_id="coffee-golden", world_id="save-001")
    database = open_project_database(tmp_path / ".runtime", world_ref.project_id, create=True)
    store = WorldStore(world_ref)
    try:
        with database.session_factory.begin() as session:
            store.insert_initial(session, _initial_world(world_ref))

        with (
            pytest.raises(WorldCommitConflictError, match="not running"),
            database.session_factory.begin() as session,
        ):
            store.compare_and_advance(
                session,
                expected_version=1,
                expected_control_epoch=1,
                expected_decision_seq=0,
                advances_world=False,
            )

        with database.session_factory.begin() as session:
            session.execute(
                text("UPDATE worlds SET status = 'running' WHERE world_id = 'save-001'")
            )
            assert store.compare_and_advance(
                session,
                expected_version=1,
                expected_control_epoch=1,
                expected_decision_seq=0,
                advances_world=False,
            ) == (1, 1)

        with (
            pytest.raises(WorldCommitConflictError, match="decision sequence"),
            database.session_factory.begin() as session,
        ):
            store.compare_and_advance(
                session,
                expected_version=1,
                expected_control_epoch=1,
                expected_decision_seq=0,
                advances_world=True,
            )

        with database.session_factory.begin() as session:
            assert store.compare_and_advance(
                session,
                expected_version=1,
                expected_control_epoch=1,
                expected_decision_seq=1,
                advances_world=True,
            ) == (2, 2)
            store.compare_and_set_object_state(
                session,
                object_id="coffee-machine",
                expected_state="idle",
                new_state="brewing",
            )

        with (
            pytest.raises(WorldCommitConflictError, match="no longer"),
            database.session_factory.begin() as session,
        ):
            store.compare_and_set_object_state(
                session,
                object_id="coffee-machine",
                expected_state="idle",
                new_state="ready",
            )

        with database.session_factory() as session:
            restored = store.load(session)
        assert restored.world.current_version == 2
        assert restored.world.decision_seq == 2
        assert restored.objects[0].state == "brewing"
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
