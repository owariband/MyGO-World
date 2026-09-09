"""M2 Agent-owned SQLite persistence and isolation tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from agent_runtime.agent.memory.contracts import MemoryKind, MemoryRecord
from agent_runtime.agent.memory.errors import MemoryScopeError
from agent_runtime.agent.memory.storage import MemoryRow, MemoryStore
from agent_runtime.agent.memory.stream import MemoryStream
from agent_runtime.agent.personact.state import CognitiveConfig, PersonaState, PlanItem
from agent_runtime.agent.personact.storage import (
    AgentRuntimeStateRow,
    PersonaStateStore,
    StoredPersonaState,
)
from agent_runtime.sqlite import (
    ProjectDatabase,
    ProjectDatabaseIdentityError,
    open_project_database,
)
from agent_runtime.world.contracts import WorldRef

PROJECT_ID = "coffee-golden"
WORLD_REF = WorldRef(project_id=PROJECT_ID, world_id="save-001")
OTHER_WORLD_REF = WorldRef(project_id=PROJECT_ID, world_id="save-002")
BASE_TIME = datetime(2026, 9, 8, 10, tzinfo=UTC)


def test_agent_state_and_memory_round_trip_without_crossing_world_or_agent(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    anon_state = _state(WORLD_REF, "anon")
    soyo_state = _state(WORLD_REF, "soyo")
    other_state = _state(OTHER_WORLD_REF, "anon")
    anon_stream = _stream(
        WORLD_REF,
        "anon",
        _record(WORLD_REF, "anon", "shared-memory", minute=1),
        _record(
            WORLD_REF,
            "anon",
            "anon-plan",
            minute=2,
            kind=MemoryKind.PLAN,
        ),
    )
    soyo_stream = _stream(
        WORLD_REF,
        "soyo",
        _record(WORLD_REF, "soyo", "shared-memory", minute=3),
    )
    other_stream = _stream(
        OTHER_WORLD_REF,
        "anon",
        _record(OTHER_WORLD_REF, "anon", "shared-memory", minute=4),
    )

    try:
        with database.session_factory.begin() as session:
            _seed_world(session, WORLD_REF, "anon", "soyo")
            _seed_world(session, OTHER_WORLD_REF, "anon")
            state_store = PersonaStateStore(WORLD_REF)
            state_store.insert(session, anon_state, "a" * 64)
            state_store.insert(session, soyo_state, "b" * 64, revision=2)
            PersonaStateStore(OTHER_WORLD_REF).insert(session, other_state, "c" * 64)
            memory_store = MemoryStore(WORLD_REF)
            memory_store.insert_stream(session, anon_stream)
            memory_store.insert_stream(session, soyo_stream)
            MemoryStore(OTHER_WORLD_REF).insert_stream(session, other_stream)

        with database.session_factory() as session:
            assert PersonaStateStore(WORLD_REF).load_all_for_bootstrap(session) == (
                StoredPersonaState(1, "a" * 64, anon_state),
                StoredPersonaState(2, "b" * 64, soyo_state),
            )
            assert MemoryStore(WORLD_REF).load_all_for_bootstrap(session) == (
                anon_stream,
                soyo_stream,
            )
            assert PersonaStateStore(OTHER_WORLD_REF).load_all_for_bootstrap(session) == (
                StoredPersonaState(1, "c" * 64, other_state),
            )
            assert MemoryStore(OTHER_WORLD_REF).load_all_for_bootstrap(session) == (other_stream,)
    finally:
        database.dispose()


def test_agent_stores_leave_commit_and_rollback_to_the_caller(tmp_path: Path) -> None:
    database = _database(tmp_path)
    state = _state(WORLD_REF, "anon")
    stream = _stream(
        WORLD_REF,
        "anon",
        _record(WORLD_REF, "anon", "memory-1", minute=1),
    )

    try:
        with (
            pytest.raises(RuntimeError, match="rollback bootstrap"),
            database.session_factory.begin() as session,
        ):
            _seed_world(session, WORLD_REF, "anon")
            PersonaStateStore(WORLD_REF).insert(session, state, "a" * 64)
            MemoryStore(WORLD_REF).insert_stream(session, stream)
            raise RuntimeError("rollback bootstrap")

        with database.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(AgentRuntimeStateRow)) == 0
            assert session.scalar(select(func.count()).select_from(MemoryRow)) == 0
            assert session.scalar(text("SELECT count(*) FROM worlds")) == 0
    finally:
        database.dispose()


def test_agent_stores_reject_a_stream_or_state_from_another_world(tmp_path: Path) -> None:
    database = _database(tmp_path)

    try:
        with database.session_factory.begin() as session:
            _seed_world(session, WORLD_REF, "anon")
            with pytest.raises(ValueError, match="different WorldRef"):
                PersonaStateStore(WORLD_REF).insert(
                    session,
                    _state(OTHER_WORLD_REF, "anon"),
                    "a" * 64,
                )
            with pytest.raises(MemoryScopeError, match="different WorldRef"):
                MemoryStore(WORLD_REF).insert_stream(
                    session,
                    _stream(
                        OTHER_WORLD_REF,
                        "anon",
                        _record(OTHER_WORLD_REF, "anon", "memory-1", minute=1),
                    ),
                )

        with database.session_factory() as session:
            assert PersonaStateStore(WORLD_REF).load_all_for_bootstrap(session) == ()
            assert MemoryStore(WORLD_REF).load_all_for_bootstrap(session) == ()
    finally:
        database.dispose()


def test_agent_stores_reject_a_session_from_another_project(tmp_path: Path) -> None:
    database = _database(tmp_path)
    foreign_ref = WorldRef(project_id="another-project", world_id=WORLD_REF.world_id)

    try:
        with database.session_factory() as session:
            with pytest.raises(ProjectDatabaseIdentityError, match="another-project"):
                PersonaStateStore(foreign_ref).load_all_for_bootstrap(session)
            with pytest.raises(ProjectDatabaseIdentityError, match="another-project"):
                MemoryStore(foreign_ref).load_all_for_bootstrap(session)
    finally:
        database.dispose()


def test_persona_restore_rejects_valid_json_with_foreign_ownership(tmp_path: Path) -> None:
    database = _database(tmp_path)

    try:
        with database.session_factory.begin() as session:
            _seed_world(session, WORLD_REF, "anon")
            PersonaStateStore(WORLD_REF).insert(
                session,
                _state(WORLD_REF, "anon"),
                "a" * 64,
            )

        foreign_json = _state(OTHER_WORLD_REF, "anon").model_dump_json(
            by_alias=True,
            exclude_none=False,
        )
        with database.session_factory.begin() as session:
            session.execute(
                update(AgentRuntimeStateRow)
                .where(
                    AgentRuntimeStateRow.world_id == WORLD_REF.world_id,
                    AgentRuntimeStateRow.agent_id == "anon",
                )
                .values(persona_state_json=foreign_json)
            )

        with (
            database.session_factory() as session,
            pytest.raises(ValueError, match="ownership"),
        ):
            PersonaStateStore(WORLD_REF).load_all_for_bootstrap(session)
    finally:
        database.dispose()


def test_memory_restore_requires_contiguous_sequence_and_strict_json_arrays(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    stream = _stream(
        WORLD_REF,
        "anon",
        _record(WORLD_REF, "anon", "memory-1", minute=1),
        _record(WORLD_REF, "anon", "memory-2", minute=2),
    )

    try:
        with database.session_factory.begin() as session:
            _seed_world(session, WORLD_REF, "anon")
            MemoryStore(WORLD_REF).insert_stream(session, stream)

        with database.session_factory.begin() as session:
            session.execute(
                update(MemoryRow)
                .where(
                    MemoryRow.world_id == WORLD_REF.world_id,
                    MemoryRow.agent_id == "anon",
                    MemoryRow.memory_id == "memory-2",
                )
                .values(memory_seq=3)
            )
        with (
            database.session_factory() as session,
            pytest.raises(ValueError, match="not contiguous"),
        ):
            MemoryStore(WORLD_REF).load_all_for_bootstrap(session)

        with database.session_factory.begin() as session:
            session.execute(
                update(MemoryRow)
                .where(
                    MemoryRow.world_id == WORLD_REF.world_id,
                    MemoryRow.agent_id == "anon",
                    MemoryRow.memory_id == "memory-2",
                )
                .values(memory_seq=2, tags_json="{}")
            )
        with (
            database.session_factory() as session,
            pytest.raises(ValidationError, match="valid array"),
        ):
            MemoryStore(WORLD_REF).load_all_for_bootstrap(session)
    finally:
        database.dispose()


def _database(tmp_path: Path) -> ProjectDatabase:
    return open_project_database(tmp_path / "runtime", PROJECT_ID, create=True)


def _seed_world(session: Session, world_ref: WorldRef, *agent_ids: str) -> None:
    session.execute(
        text(
            "INSERT INTO worlds("
            "world_id, project_id, seed_id, seed_version, seed_hash, "
            "current_version, world_time, status, created_at"
            ") VALUES ("
            ":world_id, :project_id, 'coffee-scene', 1, :seed_hash, "
            "1, :world_time, 'paused', :created_at"
            ")"
        ),
        {
            "world_id": world_ref.world_id,
            "project_id": world_ref.project_id,
            "seed_hash": "0" * 64,
            "world_time": BASE_TIME.isoformat(),
            "created_at": BASE_TIME.isoformat(),
        },
    )
    session.execute(
        text(
            "INSERT INTO locations(world_id, location_id, name, description) "
            "VALUES (:world_id, 'cafe', 'Cafe', 'A quiet cafe')"
        ),
        {"world_id": world_ref.world_id},
    )
    for agent_id in agent_ids:
        session.execute(
            text(
                "INSERT INTO agent_world_states("
                "world_id, agent_id, location_id, public_status"
                ") VALUES (:world_id, :agent_id, 'cafe', NULL)"
            ),
            {"world_id": world_ref.world_id, "agent_id": agent_id},
        )


def _state(world_ref: WorldRef, agent_id: str) -> PersonaState:
    plan = PlanItem(plan_id="coffee", description="Buy a coffee")
    return PersonaState(
        world_ref=world_ref,
        agent_id=agent_id,
        cognitive_config=CognitiveConfig(
            attention_budget=3,
            retention=5,
            recency_weight=1.0,
            relevance_weight=1.0,
            importance_weight=1.0,
            recency_decay=0.99,
            reflection_threshold=150.0,
            reflection_count=5,
        ),
        reflection_remaining=130.0,
        last_world_time=BASE_TIME,
        last_world_version=1,
        plan_queue=(plan,),
        active_plan_id=plan.plan_id,
        reflection_new_memory_count=2,
        known_place_ids=("cafe",),
    )


def _stream(
    world_ref: WorldRef,
    agent_id: str,
    *records: MemoryRecord,
) -> MemoryStream:
    return MemoryStream(
        world_ref=world_ref,
        agent_id=agent_id,
        scope=_scope(world_ref, agent_id),
        records=records,
    )


def _record(
    world_ref: WorldRef,
    agent_id: str,
    memory_id: str,
    *,
    minute: int,
    kind: MemoryKind = MemoryKind.CHAT,
) -> MemoryRecord:
    created_at = BASE_TIME + timedelta(minutes=minute)
    return MemoryRecord(
        id=memory_id,
        world_ref=world_ref,
        agent_id=agent_id,
        scope=_scope(world_ref, agent_id),
        kind=kind,
        created_at=created_at,
        last_accessed_at=created_at + timedelta(seconds=10),
        expires_at=created_at + timedelta(days=1),
        subject=agent_id,
        predicate="remembers",
        object="咖啡",
        content=f"{agent_id} remembers coffee",
        poignancy=3.5,
        tags=("coffee", "会話"),
        source="event-entry",
        evidence_ids=("entry-1", "fact-1"),
        embedding=(0.1, -0.2, 0.3),
        novelty_key=f"{memory_id}/v1",
    )


def _scope(world_ref: WorldRef, agent_id: str) -> str:
    return f"project/{world_ref.project_id}/world/{world_ref.world_id}/persona/{agent_id}"
