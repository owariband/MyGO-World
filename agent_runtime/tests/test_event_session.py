"""M4 EventSession partition planning and durable transition tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text

from agent_runtime.event.session import (
    SessionTransitionError,
    plan_join,
    plan_leave,
)
from agent_runtime.sqlite import open_project_database
from agent_runtime.world.contracts import CommitPosition, DeliveryChannel, WorldRef
from agent_runtime.world.entries import (
    AudienceMode,
    DialogueEntry,
    EventEntryRecipient,
    InteractionRequest,
    InteractionRequestStatus,
    SessionTransitionEntry,
    SessionTransitionReason,
)
from agent_runtime.world.entry_storage import EventEntryStore
from agent_runtime.world.state import (
    AgentWorldState,
    EventSessionNode,
    LocationState,
    PublicWorldState,
    WorldState,
    WorldStatus,
)
from agent_runtime.world.storage import WorldCommitConflictError, WorldStore

PROJECT_ID = "session-tests"
WORLD_REF = WorldRef(project_id=PROJECT_ID, world_id="save-001")
NOW = datetime(2026, 9, 11, 12, tzinfo=UTC)


def test_join_merges_a_singleton_into_the_target_root_without_creating_nodes() -> None:
    state = _state((("actor",), ("target", "ally")))

    transition = plan_join(state, actor_agent_id="actor", target_agent_id="target")

    assert transition.reason is SessionTransitionReason.MERGE
    assert transition.affected_session_ids == (
        "session-actor",
        "session-ally",
        "session-target",
    )
    assert tuple(
        (part.root_session_id, part.member_session_ids) for part in transition.after_parts
    ) == (("session-target", ("session-actor", "session-ally", "session-target")),)
    assert {member for part in transition.after_parts for member in part.member_session_ids} == {
        node.session_id for node in state.sessions
    }


def test_join_transfers_only_a_grouped_actor_and_preserves_both_partitions() -> None:
    state = _state((("leader", "actor", "peer"), ("target", "ally")))

    transition = plan_join(state, actor_agent_id="actor", target_agent_id="target")

    assert transition.reason is SessionTransitionReason.TRANSFER
    assert tuple(
        (part.root_session_id, part.member_session_ids) for part in transition.after_parts
    ) == (
        ("session-leader", ("session-leader", "session-peer")),
        ("session-target", ("session-actor", "session-ally", "session-target")),
    )


def test_root_transfer_and_leave_choose_a_deterministic_remaining_root() -> None:
    state = _state((("actor", "gamma", "beta"), ("target",)))

    transferred = plan_join(state, actor_agent_id="actor", target_agent_id="target")
    left = plan_leave(state, actor_agent_id="actor")

    assert transferred.reason is SessionTransitionReason.TRANSFER
    assert tuple(
        (part.root_session_id, part.member_session_ids) for part in transferred.after_parts
    ) == (
        ("session-beta", ("session-beta", "session-gamma")),
        ("session-target", ("session-actor", "session-target")),
    )
    assert left.reason is SessionTransitionReason.SPLIT
    assert tuple((part.root_session_id, part.member_session_ids) for part in left.after_parts) == (
        ("session-actor", ("session-actor",)),
        ("session-beta", ("session-beta", "session-gamma")),
    )
    assert {part.topology_version for part in (*transferred.after_parts, *left.after_parts)} == {
        state.world.current_version + 1
    }


def test_invalid_partition_requests_do_not_mutate_the_input_snapshot() -> None:
    grouped = _state((("actor", "peer"),))
    singleton = _state((("actor",), ("target",)))

    with pytest.raises(SessionTransitionError, match="already share"):
        plan_join(grouped, actor_agent_id="actor", target_agent_id="peer")
    with pytest.raises(SessionTransitionError, match="singleton"):
        plan_leave(singleton, actor_agent_id="actor")
    with pytest.raises(SessionTransitionError, match="does not exist"):
        plan_join(singleton, actor_agent_id="missing", target_agent_id="target")

    assert grouped == _state((("actor", "peer"),))
    assert singleton == _state((("actor",), ("target",)))


def test_persisted_transition_is_all_or_nothing_and_rejects_a_stale_replay(
    tmp_path: Path,
) -> None:
    initial = _state((("actor", "peer"), ("target",)))
    transition = plan_join(initial, actor_agent_id="actor", target_agent_id="target")
    database = open_project_database(tmp_path / ".runtime", PROJECT_ID, create=True)
    store = WorldStore(WORLD_REF)
    try:
        with database.session_factory.begin() as session:
            store.insert_initial(session, initial)
            session.execute(
                text("UPDATE worlds SET status = 'running' WHERE world_id = :world_id"),
                {"world_id": WORLD_REF.world_id},
            )
            store.compare_and_advance(
                session,
                expected_version=1,
                expected_control_epoch=1,
                expected_decision_seq=0,
                advances_world=True,
            )
            store.apply_session_transition(session, transition)

        with database.session_factory() as session:
            committed = store.load(session)
        assert len(committed.sessions) == len(initial.sessions)
        assert {
            node.agent_id: (node.root_session_id, node.topology_version)
            for node in committed.sessions
        } == {
            "actor": ("session-target", 2),
            "peer": ("session-peer", 2),
            "target": ("session-target", 2),
        }

        with (
            pytest.raises(WorldCommitConflictError, match="topology changed"),
            database.session_factory.begin() as session,
        ):
            store.apply_session_transition(session, transition)

        with database.session_factory() as session:
            assert store.load(session) == committed
    finally:
        database.dispose()


def test_split_cancels_only_when_a_pending_request_becomes_unreachable(
    tmp_path: Path,
) -> None:
    initial = _state((("actor", "peer"),))
    database = open_project_database(tmp_path / ".runtime", PROJECT_ID, create=True)
    world_store = WorldStore(WORLD_REF)
    entry_store = EventEntryStore(WORLD_REF)
    request_entry = _dialogue_entry("request-entry", version=2)
    try:
        with database.session_factory.begin() as session:
            world_store.insert_initial(session, initial)
            session.execute(
                text("UPDATE worlds SET status = 'running' WHERE world_id = :world_id"),
                {"world_id": WORLD_REF.world_id},
            )
            world_store.compare_and_advance(
                session,
                expected_version=1,
                expected_control_epoch=1,
                expected_decision_seq=0,
                advances_world=True,
            )
            entry_store.append(
                session,
                request_entry,
                recipients=_recipients(request_entry.entry_id, "actor", "peer"),
            )
            entry_store.create_request(
                session,
                InteractionRequest(
                    world_ref=WORLD_REF,
                    request_entry_id=request_entry.entry_id,
                    requester_agent_id="actor",
                    recipient_agent_id="peer",
                    status=InteractionRequestStatus.PENDING,
                    updated_world_version=2,
                ),
            )

        with database.session_factory.begin() as session:
            current = world_store.load(session)
            transition = plan_leave(current, actor_agent_id="actor")
            world_store.compare_and_advance(
                session,
                expected_version=2,
                expected_control_epoch=1,
                expected_decision_seq=1,
                advances_world=True,
            )
            world_store.apply_session_transition(session, transition)
            transition_entry = SessionTransitionEntry(
                world_ref=WORLD_REF,
                entry_id="split-entry",
                source_id="split-decision",
                commit_position=CommitPosition(world_version=3),
                root_session_id_at_commit="session-actor",
                topology_version=3,
                actor_agent_id="actor",
                occurred_at=NOW,
                text="actor leaves the conversation",
                created_at=NOW,
                transition_reason=SessionTransitionReason.SPLIT,
            )
            entry_store.append(
                session,
                transition_entry,
                recipients=_recipients(transition_entry.entry_id, "actor", "peer"),
                transition=transition,
            )
            assert entry_store.cancel_unreachable_requests(
                session,
                transition=transition,
                cancellation_entry_id=transition_entry.entry_id,
                updated_world_version=3,
            ) == (request_entry.entry_id,)

        with database.session_factory() as session:
            cancelled = entry_store.get_request(session, request_entry.entry_id)
            restored_entry = entry_store.get(session, "split-entry")
            assert isinstance(restored_entry, SessionTransitionEntry)
            restored_transition = entry_store.transition_for(
                session,
                restored_entry,
            )
        assert cancelled.status is InteractionRequestStatus.CANCELLED
        assert cancelled.cancellation_entry_id == "split-entry"
        assert restored_transition == transition
    finally:
        database.dispose()


def _state(partitions: tuple[tuple[str, ...], ...]) -> PublicWorldState:
    agent_ids = tuple(agent_id for partition in partitions for agent_id in partition)
    return PublicWorldState(
        world=WorldState(
            world_ref=WORLD_REF,
            seed_id="session-seed",
            seed_version=1,
            seed_hash="a" * 64,
            current_version=1,
            world_time=NOW,
            status=WorldStatus.PAUSED,
            created_at=NOW,
        ),
        locations=(
            LocationState(
                world_ref=WORLD_REF,
                location_id="room",
                name="Room",
                description="One shared test room",
            ),
        ),
        agents=tuple(
            AgentWorldState(world_ref=WORLD_REF, agent_id=agent_id, location_id="room")
            for agent_id in agent_ids
        ),
        sessions=tuple(
            EventSessionNode(
                world_ref=WORLD_REF,
                session_id=f"session-{agent_id}",
                agent_id=agent_id,
                root_session_id=f"session-{partition[0]}",
                topology_version=1,
                updated_world_version=1,
            )
            for partition in partitions
            for agent_id in partition
        ),
    )


def _dialogue_entry(entry_id: str, *, version: int) -> DialogueEntry:
    return DialogueEntry(
        world_ref=WORLD_REF,
        entry_id=entry_id,
        source_id=f"proposal-{entry_id}",
        commit_position=CommitPosition(world_version=version),
        root_session_id_at_commit="session-actor",
        topology_version=1,
        actor_agent_id="actor",
        target_agent_id="peer",
        audience_mode=AudienceMode.SESSION,
        delivery_channel=DeliveryChannel.DIRECT,
        occurred_at=NOW,
        text="Will you answer me?",
        created_at=NOW,
    )


def _recipients(entry_id: str, *agent_ids: str) -> tuple[EventEntryRecipient, ...]:
    return tuple(
        EventEntryRecipient(world_ref=WORLD_REF, entry_id=entry_id, agent_id=agent_id)
        for agent_id in agent_ids
    )
