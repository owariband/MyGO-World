"""M3 committed EventEntry contracts, ordering, isolation, and storage guards."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agent_runtime.sqlite import open_project_database
from agent_runtime.world.contracts import CommitPosition, DeliveryChannel, WorldRef
from agent_runtime.world.entries import (
    ActionEntry,
    AudienceMode,
    DialogueEntry,
    EntryRelationKind,
    EventEntryLink,
    EventEntryRecipient,
    InteractionRequest,
    InteractionRequestStatus,
)
from agent_runtime.world.entry_storage import (
    EventEntryLinkRow,
    EventEntryRecipientRow,
    EventEntryRow,
    EventEntryStore,
)
from agent_runtime.world.storage import WorldStore

PROJECT_ID = "coffee-golden"
WORLD_REF = WorldRef(project_id=PROJECT_ID, world_id="save-001")
OTHER_WORLD_REF = WorldRef(project_id=PROJECT_ID, world_id="save-002")
NOW = datetime(2026, 9, 11, 10, 15, tzinfo=UTC)


def test_entry_contracts_are_strict_frozen_and_preserve_typed_variants() -> None:
    dialogue = _dialogue("entry-1", version=2, actor="anon", target="soyo")
    action = _action("entry-2", version=3)

    assert DialogueEntry.model_validate_json(dialogue.model_dump_json(), strict=True) == dialogue
    assert ActionEntry.model_validate_json(action.model_dump_json(), strict=True) == action
    assert dialogue.entry_kind == "dialogue"
    assert action.entry_kind == "action"

    payload = dialogue.model_dump(mode="json", by_alias=True)
    payload["unknown"] = "not allowed"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        DialogueEntry.model_validate(payload, strict=True)
    with pytest.raises(ValidationError, match="frozen"):
        dialogue.__setattr__("text", "changed")


def test_entry_contracts_reject_invalid_position_delivery_and_request_shape() -> None:
    base = _dialogue("entry-1", version=2, actor="anon", target="soyo")

    with pytest.raises(ValidationError, match="entryIndex 0"):
        DialogueEntry.model_validate(
            {**base.model_dump(), "commitPosition": {"worldVersion": 2, "entryIndex": 1}},
            strict=True,
        )
    with pytest.raises(ValidationError, match="topologyVersion"):
        DialogueEntry.model_validate(
            {**base.model_dump(), "topologyVersion": 3},
            strict=True,
        )
    with pytest.raises(ValidationError, match="explicit audience"):
        DialogueEntry.model_validate(
            {
                **base.model_dump(),
                "audienceMode": AudienceMode.SESSION,
                "deliveryChannel": DeliveryChannel.WHISPER,
            },
            strict=True,
        )

    pending = _request(status=InteractionRequestStatus.PENDING)
    with pytest.raises(ValidationError, match=r"pending.*resolution"):
        InteractionRequest.model_validate(
            {**pending.model_dump(), "resolutionEntryId": "entry-2"},
            strict=True,
        )
    with pytest.raises(ValidationError, match=r"resolved.*requires"):
        InteractionRequest.model_validate(
            {**pending.model_dump(), "status": InteractionRequestStatus.RESOLVED},
            strict=True,
        )
    with pytest.raises(ValidationError, match="different recipient"):
        InteractionRequest.model_validate(
            {**pending.model_dump(), "recipientAgentId": "anon"},
            strict=True,
        )
    with pytest.raises(ValidationError, match="cannot link to itself"):
        EventEntryLink(
            world_ref=WORLD_REF,
            entry_id="entry-1",
            relation_kind=EntryRelationKind.REPLY,
            related_entry_id="entry-1",
        )


def test_store_round_trips_order_recipients_reply_and_request_lifecycle(
    tmp_path: Path,
) -> None:
    database = open_project_database(tmp_path / ".runtime", PROJECT_ID, create=True)
    store = EventEntryStore(WORLD_REF)
    try:
        with database.session_factory.begin() as session:
            _seed_world(session, WORLD_REF)
            world_store = WorldStore(WORLD_REF)
            assert world_store.compare_and_advance(
                session,
                expected_version=1,
                expected_control_epoch=1,
                expected_decision_seq=0,
                advances_world=True,
            ) == (2, 1)
            first = _dialogue("entry-1", version=2, actor="anon", target="soyo")
            store.append(
                session,
                first,
                recipients=_recipients(first.entry_id, "anon", "soyo"),
            )
            invalid_request = InteractionRequest(
                world_ref=WORLD_REF,
                request_entry_id=first.entry_id,
                requester_agent_id="anon",
                recipient_agent_id="soyo",
                status=InteractionRequestStatus.PENDING,
                updated_world_version=3,
            )
            with (
                pytest.raises(IntegrityError, match="source entry"),
                session.begin_nested(),
            ):
                store.create_request(session, invalid_request)
                session.flush()
            store.create_request(session, _request(status=InteractionRequestStatus.PENDING))

            assert world_store.compare_and_advance(
                session,
                expected_version=2,
                expected_control_epoch=1,
                expected_decision_seq=1,
                advances_world=True,
            ) == (3, 2)
            second = _dialogue("entry-2", version=3, actor="soyo", target="anon")
            reply = EventEntryLink(
                world_ref=WORLD_REF,
                entry_id=second.entry_id,
                relation_kind=EntryRelationKind.REPLY,
                related_entry_id=first.entry_id,
            )
            store.append(
                session,
                second,
                links=(reply,),
                recipients=_recipients(second.entry_id, "anon", "soyo"),
            )
            with (
                pytest.raises(IntegrityError, match="valid reply"),
                session.begin_nested(),
            ):
                store.resolve_request(
                    session,
                    request_entry_id=first.entry_id,
                    resolution_entry_id=second.entry_id,
                    updated_world_version=4,
                )
            resolved = store.resolve_request(
                session,
                request_entry_id=first.entry_id,
                resolution_entry_id=second.entry_id,
                updated_world_version=3,
            )
            assert resolved.status is InteractionRequestStatus.RESOLVED

        with database.session_factory() as session:
            assert store.get(session, "entry-1") == first
            assert store.get_by_source(session, source_id="proposal-entry-2") == second
            assert store.latest_for_root(session, "session-anon") == second
            assert store.list_visible_after(session, "soyo") == (first, second)
            assert store.list_visible_after(
                session,
                "soyo",
                after=first.commit_position,
            ) == (second,)
            assert store.links_for(session, (second.entry_id,)) == (reply,)
            assert store.pending_requests_for(session, "soyo") == ()
            assert store.get_pending_request(session, first.entry_id, "soyo") is None
            request = store.get_request(session, first.entry_id)
            assert request.resolution_entry_id == second.entry_id
            assert request.updated_world_version == 3
    finally:
        database.dispose()


def test_entry_history_is_append_only_and_links_must_point_backwards(tmp_path: Path) -> None:
    database = open_project_database(tmp_path / ".runtime", PROJECT_ID, create=True)
    store = EventEntryStore(WORLD_REF)
    try:
        with database.session_factory.begin() as session:
            _seed_world(session, WORLD_REF)
            WorldStore(WORLD_REF).compare_and_advance(
                session,
                expected_version=1,
                expected_control_epoch=1,
                expected_decision_seq=0,
                advances_world=True,
            )
            first = _dialogue("entry-1", version=2, actor="anon", target="soyo")
            store.append(
                session,
                first,
                recipients=_recipients(first.entry_id, "anon", "soyo"),
            )
            WorldStore(WORLD_REF).compare_and_advance(
                session,
                expected_version=2,
                expected_control_epoch=1,
                expected_decision_seq=1,
                advances_world=True,
            )
            second = _dialogue("entry-2", version=3, actor="soyo", target="anon")
            store.append(
                session,
                second,
                recipients=_recipients(second.entry_id, "anon", "soyo"),
            )

        with (
            pytest.raises(IntegrityError, match="append-only"),
            database.session_factory.begin() as session,
        ):
            session.execute(
                update(EventEntryRow)
                .where(
                    EventEntryRow.world_id == WORLD_REF.world_id,
                    EventEntryRow.entry_id == first.entry_id,
                )
                .values(text="rewritten")
            )
        with (
            pytest.raises(IntegrityError, match="append-only"),
            database.session_factory.begin() as session,
        ):
            session.execute(
                delete(EventEntryRecipientRow).where(
                    EventEntryRecipientRow.world_id == WORLD_REF.world_id,
                    EventEntryRecipientRow.entry_id == first.entry_id,
                )
            )
        with (
            pytest.raises(IntegrityError, match="earlier entry"),
            database.session_factory.begin() as session,
        ):
            session.add(
                EventEntryLinkRow(
                    world_id=WORLD_REF.world_id,
                    entry_id=first.entry_id,
                    relation_kind=EntryRelationKind.CAUSE.value,
                    related_entry_id=second.entry_id,
                    relation_order=0,
                )
            )
    finally:
        database.dispose()


def test_store_rejects_recipient_or_topology_snapshots_outside_current_partition(
    tmp_path: Path,
) -> None:
    database = open_project_database(tmp_path / ".runtime", PROJECT_ID, create=True)
    store = EventEntryStore(WORLD_REF)
    try:
        with database.session_factory.begin() as session:
            _seed_world(session, WORLD_REF)
            session.execute(
                text(
                    "INSERT INTO agent_world_states("
                    "world_id, agent_id, location_id, public_status) "
                    "VALUES (:world_id, 'taki', 'cafe', NULL)"
                ),
                {"world_id": WORLD_REF.world_id},
            )
            session.execute(
                text(
                    "INSERT INTO event_sessions("
                    "world_id, session_id, agent_id, root_session_id, "
                    "topology_version, updated_world_version"
                    ") VALUES (:world_id, 'session-taki', 'taki', 'session-taki', 1, 1)"
                ),
                {"world_id": WORLD_REF.world_id},
            )
            WorldStore(WORLD_REF).compare_and_advance(
                session,
                expected_version=1,
                expected_control_epoch=1,
                expected_decision_seq=0,
                advances_world=True,
            )

            direct = _dialogue("direct-leak", version=2, actor="anon", target="soyo")
            with pytest.raises(ValueError, match="committed audience"):
                store.append(
                    session,
                    direct,
                    recipients=_recipients(direct.entry_id, "anon", "soyo", "taki"),
                )

            whisper = _dialogue(
                "whisper-leak",
                version=2,
                actor="anon",
                target="soyo",
                delivery_channel=DeliveryChannel.WHISPER,
            )
            with pytest.raises(ValueError, match="committed audience"):
                store.append(
                    session,
                    whisper,
                    recipients=_recipients(whisper.entry_id, "anon", "soyo", "taki"),
                )

            cross_partition = _dialogue(
                "cross-partition-whisper",
                version=2,
                actor="anon",
                target="taki",
                delivery_channel=DeliveryChannel.WHISPER,
            )
            with pytest.raises(ValueError, match="cannot cross"):
                store.append(
                    session,
                    cross_partition,
                    recipients=_recipients(cross_partition.entry_id, "anon", "taki"),
                )

            stale_topology = _dialogue(
                "stale-topology",
                version=2,
                actor="anon",
                target="soyo",
                topology_version=2,
            )
            with pytest.raises(ValueError, match="topologyVersion"):
                store.append(
                    session,
                    stale_topology,
                    recipients=_recipients(stale_topology.entry_id, "anon", "soyo"),
                )

            assert session.scalar(select(EventEntryRow)) is None
    finally:
        database.dispose()


def test_composite_foreign_keys_prevent_cross_world_recipient_and_memory_sources(
    tmp_path: Path,
) -> None:
    database = open_project_database(tmp_path / ".runtime", PROJECT_ID, create=True)
    try:
        with database.session_factory.begin() as session:
            _seed_world(session, WORLD_REF)
            _seed_world(session, OTHER_WORLD_REF)
            WorldStore(WORLD_REF).compare_and_advance(
                session,
                expected_version=1,
                expected_control_epoch=1,
                expected_decision_seq=0,
                advances_world=True,
            )
            entry = _dialogue("world-one-only", version=2, actor="anon", target="soyo")
            EventEntryStore(WORLD_REF).append(
                session,
                entry,
                recipients=_recipients(entry.entry_id, "anon", "soyo"),
            )

        with pytest.raises(IntegrityError), database.session_factory.begin() as session:
            session.add(
                EventEntryRecipientRow(
                    world_id=OTHER_WORLD_REF.world_id,
                    entry_id="world-one-only",
                    agent_id="anon",
                )
            )
    finally:
        database.dispose()


def test_store_leaves_entry_commit_and_rollback_to_caller(tmp_path: Path) -> None:
    database = open_project_database(tmp_path / ".runtime", PROJECT_ID, create=True)
    try:
        with (
            pytest.raises(RuntimeError, match="injected rollback"),
            database.session_factory.begin() as session,
        ):
            _seed_world(session, WORLD_REF)
            WorldStore(WORLD_REF).compare_and_advance(
                session,
                expected_version=1,
                expected_control_epoch=1,
                expected_decision_seq=0,
                advances_world=True,
            )
            entry = _dialogue("rolled-back", version=2, actor="anon", target="soyo")
            EventEntryStore(WORLD_REF).append(
                session,
                entry,
                recipients=_recipients(entry.entry_id, "anon", "soyo"),
            )
            raise RuntimeError("injected rollback")

        with database.session_factory() as session:
            assert session.scalar(select(EventEntryRow)) is None
            assert session.scalar(text("SELECT count(*) FROM worlds")) == 0
    finally:
        database.dispose()


def _dialogue(
    entry_id: str,
    *,
    version: int,
    actor: str,
    target: str,
    delivery_channel: DeliveryChannel = DeliveryChannel.DIRECT,
    topology_version: int = 1,
) -> DialogueEntry:
    return DialogueEntry(
        world_ref=WORLD_REF,
        entry_id=entry_id,
        source_id=f"proposal-{entry_id}",
        commit_position=CommitPosition(world_version=version),
        root_session_id_at_commit="session-anon",
        topology_version=topology_version,
        actor_agent_id=actor,
        target_agent_id=target,
        audience_mode=(
            AudienceMode.EXPLICIT
            if delivery_channel is DeliveryChannel.WHISPER
            else AudienceMode.SESSION
        ),
        delivery_channel=delivery_channel,
        occurred_at=NOW,
        text=f"dialogue {entry_id}",
        created_at=NOW,
    )


def _action(entry_id: str, *, version: int) -> ActionEntry:
    return ActionEntry(
        world_ref=WORLD_REF,
        entry_id=entry_id,
        source_id=f"proposal-{entry_id}",
        commit_position=CommitPosition(world_version=version),
        root_session_id_at_commit="session-anon",
        topology_version=1,
        actor_agent_id="anon",
        target_object_id="metronome",
        operation_id="start",
        occurred_at=NOW,
        text="The metronome starts.",
        created_at=NOW,
    )


def _request(*, status: InteractionRequestStatus) -> InteractionRequest:
    return InteractionRequest(
        world_ref=WORLD_REF,
        request_entry_id="entry-1",
        requester_agent_id="anon",
        recipient_agent_id="soyo",
        status=status,
        resolution_entry_id="entry-2" if status is InteractionRequestStatus.RESOLVED else None,
        updated_world_version=3 if status is InteractionRequestStatus.RESOLVED else 2,
    )


def _recipients(entry_id: str, *agent_ids: str) -> tuple[EventEntryRecipient, ...]:
    return tuple(
        EventEntryRecipient(world_ref=WORLD_REF, entry_id=entry_id, agent_id=agent_id)
        for agent_id in agent_ids
    )


def _seed_world(session: Session, world_ref: WorldRef) -> None:
    session.execute(
        text(
            "INSERT INTO worlds("
            "world_id, project_id, seed_id, seed_version, seed_hash, current_version, "
            "world_time, status, created_at, control_epoch, decision_seq"
            ") VALUES ("
            ":world_id, :project_id, 'coffee-scene', 1, :seed_hash, 1, "
            ":world_time, 'running', :created_at, 1, 0)"
        ),
        {
            "world_id": world_ref.world_id,
            "project_id": world_ref.project_id,
            "seed_hash": "0" * 64,
            "world_time": NOW.isoformat(),
            "created_at": NOW.isoformat(),
        },
    )
    session.execute(
        text(
            "INSERT INTO locations(world_id, location_id, name, description) "
            "VALUES (:world_id, 'cafe', 'Cafe', 'A quiet cafe')"
        ),
        {"world_id": world_ref.world_id},
    )
    for agent_id in ("anon", "soyo"):
        session.execute(
            text(
                "INSERT INTO agent_world_states(world_id, agent_id, location_id, public_status) "
                "VALUES (:world_id, :agent_id, 'cafe', NULL)"
            ),
            {"world_id": world_ref.world_id, "agent_id": agent_id},
        )
    session.execute(
        text(
            "INSERT INTO objects("
            "world_id, object_id, name, kind, description, location_id, owner_agent_id, state"
            ") VALUES ("
            ":world_id, 'metronome', 'Metronome', 'instrument', "
            "'A mechanical metronome', 'cafe', NULL, 'stopped')"
        ),
        {"world_id": world_ref.world_id},
    )
    for session_id, agent_id, root_session_id in (
        ("session-anon", "anon", "session-anon"),
        ("session-soyo", "soyo", "session-anon"),
    ):
        session.execute(
            text(
                "INSERT INTO event_sessions("
                "world_id, session_id, agent_id, root_session_id, "
                "topology_version, updated_world_version"
                ") VALUES ("
                ":world_id, :session_id, :agent_id, :root_session_id, 1, 1)"
            ),
            {
                "world_id": world_ref.world_id,
                "session_id": session_id,
                "agent_id": agent_id,
                "root_session_id": root_session_id,
            },
        )
