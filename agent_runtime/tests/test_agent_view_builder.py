"""M3.3 hard-visibility and deterministic AgentView projection tests."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from agent_runtime.scenario import ObjectOperationSeed, ObjectSeed
from agent_runtime.sqlite import ProjectDatabase, open_project_database
from agent_runtime.world.contracts import (
    AttentionTier,
    CharacterTarget,
    CommitPosition,
    DeliveryChannel,
    ObjectTarget,
    PerceptionChannel,
    ProposalKind,
    WorldRef,
)
from agent_runtime.world.entries import (
    ActionEntry,
    AudienceMode,
    DialogueEntry,
    EntryRelationKind,
    EventEntry,
    EventEntryLink,
    EventEntryRecipient,
    InteractionRequest,
    InteractionRequestStatus,
)
from agent_runtime.world.entry_storage import EventEntryStore
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
from agent_runtime.world.storage import WorldStore
from agent_runtime.world.view_builder import (
    AgentViewBuilder,
    AgentViewCatalogError,
    AgentViewStaleError,
)

PROJECT_ID = "view-builder-test"
WORLD_REF = WorldRef(project_id=PROJECT_ID, world_id="main")
OTHER_WORLD_REF = WorldRef(project_id=PROJECT_ID, world_id="other")
NOW = datetime(2026, 9, 11, 10, 30, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class PreparedRuntime:
    database: ProjectDatabase
    builder: AgentViewBuilder
    other_builder: AgentViewBuilder


@pytest.fixture
def runtime(tmp_path: Path) -> Iterator[PreparedRuntime]:
    database = open_project_database(tmp_path / "runtime", PROJECT_ID, create=True)
    seeds = _object_seeds()
    with database.session_factory.begin() as session:
        WorldStore(WORLD_REF).insert_initial(session, _initial_world(WORLD_REF))
        WorldStore(OTHER_WORLD_REF).insert_initial(session, _initial_world(OTHER_WORLD_REF))
        _append_main_history(session)
        _append_other_history(session)
    try:
        yield PreparedRuntime(
            database=database,
            builder=AgentViewBuilder(WORLD_REF, seeds),
            other_builder=AgentViewBuilder(OTHER_WORLD_REF, seeds),
        )
    finally:
        database.dispose()


@pytest.mark.parametrize(
    ("agent_id", "entry_id", "expected_channel", "expected_tier"),
    [
        ("anon", "direct-open", PerceptionChannel.SELF, AttentionTier.RELEVANT),
        (
            "soyo",
            "direct-open",
            PerceptionChannel.DIRECT_INTERACTION,
            AttentionTier.RELEVANT,
        ),
        (
            "tomori",
            "direct-open",
            PerceptionChannel.SAME_SCENE,
            AttentionTier.RELEVANT,
        ),
        ("taki", "direct-open", None, None),
        (
            "soyo",
            "direct-question",
            PerceptionChannel.DIRECT_INTERACTION,
            AttentionTier.MANDATORY,
        ),
        (
            "tomori",
            "direct-question",
            PerceptionChannel.SAME_SCENE,
            AttentionTier.RELEVANT,
        ),
        ("anon", "whisper", PerceptionChannel.SELF, AttentionTier.RELEVANT),
        (
            "soyo",
            "whisper",
            PerceptionChannel.TARGETED_MESSAGE,
            AttentionTier.RELEVANT,
        ),
        ("tomori", "whisper", None, None),
        ("tomori", "object-action", PerceptionChannel.SELF, AttentionTier.RELEVANT),
        ("anon", "object-action", PerceptionChannel.SAME_SCENE, AttentionTier.RELEVANT),
        ("taki", "object-action", None, None),
        ("anon", "other-root-dialogue", None, None),
    ],
)
def test_entry_visibility_matrix_uses_recipient_snapshots(
    runtime: PreparedRuntime,
    agent_id: str,
    entry_id: str,
    expected_channel: PerceptionChannel | None,
    expected_tier: AttentionTier | None,
) -> None:
    with runtime.database.session_factory() as session:
        view = runtime.builder.build(session, agent_id=agent_id)

    candidates = [item for item in view.candidates if item.source_entry_id == entry_id]
    if expected_channel is None:
        assert candidates == []
    else:
        assert len(candidates) == 1
        assert candidates[0].channel is expected_channel
        assert candidates[0].attention_tier is expected_tier


def test_pending_request_repeats_after_cursor_and_affords_bound_response(
    runtime: PreparedRuntime,
) -> None:
    cursor = CommitPosition(world_version=6)
    with runtime.database.session_factory() as session:
        view = runtime.builder.build(
            session,
            agent_id="soyo",
            observed_through=cursor,
        )

    entry_candidates = tuple(item for item in view.candidates if item.source_entry_id is not None)
    assert tuple(item.source_entry_id for item in entry_candidates) == ("direct-question",)
    assert entry_candidates[0].attention_tier is AttentionTier.MANDATORY
    assert entry_candidates[0].channel is PerceptionChannel.DIRECT_INTERACTION
    assert view.observed_through == cursor
    responses = tuple(item for item in view.affordances if item.kind is ProposalKind.RESPOND)
    assert len(responses) == 1
    assert responses[0].target == CharacterTarget(id="anon")
    assert responses[0].delivery_channel is DeliveryChannel.DIRECT
    assert responses[0].request_entry_id == "direct-question"
    assert "direct-question" in view.visible_evidence_ids


def test_same_location_is_ambient_but_does_not_grant_cross_root_dialogue(
    runtime: PreparedRuntime,
) -> None:
    with runtime.database.session_factory() as session:
        view = runtime.builder.build(session, agent_id="anon")

    present_agents = {item.subject for item in view.candidates if item.predicate == "located_at"}
    assert present_agents == {"anon", "soyo", "tomori", "taki", "mutsumi"}
    assert "rana" not in present_agents

    utter_routes = {
        (item.target.id, item.delivery_channel)
        for item in view.affordances
        if item.kind is ProposalKind.UTTER and isinstance(item.target, CharacterTarget)
    }
    assert utter_routes == {
        ("soyo", DeliveryChannel.DIRECT),
        ("soyo", DeliveryChannel.WHISPER),
        ("tomori", DeliveryChannel.DIRECT),
        ("tomori", DeliveryChannel.WHISPER),
    }
    assert not any(item.kind is ProposalKind.RESPOND for item in view.affordances)

    object_operations = {
        (item.target.id, item.operation_id)
        for item in view.affordances
        if item.kind is ProposalKind.INTERACT and isinstance(item.target, ObjectTarget)
    }
    assert object_operations == {("metronome", "reset")}
    assert {item.kind for item in view.affordances} >= {
        ProposalKind.ACT,
        ProposalKind.WAIT,
    }
    assert {
        "world-day",
        "cafe-open",
        "taki-public",
        "metronome-steady",
    }.issubset(view.visible_evidence_ids)
    assert {"hall-noisy", "rana-public", "keyboard-ready"}.isdisjoint(view.visible_evidence_ids)


def test_fiftieth_dialogue_requires_behavior_before_dialogue_returns(
    runtime: PreparedRuntime,
) -> None:
    with runtime.database.session_factory.begin() as session:
        session.execute(
            text(
                "UPDATE event_sessions SET consecutive_dialogue_turns = 50 "
                "WHERE world_id = 'main' AND agent_id = 'soyo'"
            )
        )

    with runtime.database.session_factory() as session:
        blocked = runtime.builder.build(session, agent_id="soyo")

    assert not any(
        item.kind in {ProposalKind.UTTER, ProposalKind.RESPOND} for item in blocked.affordances
    )
    act_operations = {
        item.operation_id for item in blocked.affordances if item.kind is ProposalKind.ACT
    }
    assert act_operations >= {
        "adjust_posture",
        "gather_thoughts",
        "observe_surroundings",
        "leave_current_session",
    }

    with runtime.database.session_factory.begin() as session:
        WorldStore(WORLD_REF).finish_session_step(
            session,
            agent_id="soyo",
            world_version=6,
            entry_kind="behavior",
            waiting=False,
            dispatch_count=None,
        )

    with runtime.database.session_factory() as session:
        resumed = runtime.builder.build(session, agent_id="soyo")

    assert any(item.kind is ProposalKind.UTTER for item in resumed.affordances)
    assert any(item.kind is ProposalKind.RESPOND for item in resumed.affordances)


def test_visible_entry_link_never_leaks_hidden_related_entry(
    runtime: PreparedRuntime,
) -> None:
    with runtime.database.session_factory() as session:
        view = runtime.builder.build(session, agent_id="anon")

    serialized = view.model_dump_json()
    assert "other-root-dialogue" not in view.visible_evidence_ids
    assert "OTHER ROOT SECRET" not in serialized
    assert "other-root-dialogue" not in serialized
    assert "object-action" in view.visible_evidence_ids


def test_later_root_membership_does_not_retroactively_change_history(
    runtime: PreparedRuntime,
) -> None:
    with runtime.database.session_factory.begin() as session:
        session.execute(
            text(
                "UPDATE event_sessions SET root_session_id = 'session-anon', "
                "topology_version = 6, updated_world_version = 6 "
                "WHERE world_id = 'main' AND agent_id IN ('taki', 'mutsumi')"
            )
        )
        session.execute(
            text(
                "UPDATE event_sessions SET topology_version = 6 "
                "WHERE world_id = 'main' AND root_session_id = 'session-anon'"
            )
        )

    with runtime.database.session_factory() as session:
        view = runtime.builder.build(session, agent_id="anon")

    assert not any(item.source_entry_id == "other-root-dialogue" for item in view.candidates)
    assert any(
        item.kind is ProposalKind.UTTER and item.target == CharacterTarget(id="taki")
        for item in view.affordances
    )


def test_build_order_is_deterministic_and_builder_reads_no_private_tables(
    runtime: PreparedRuntime,
) -> None:
    statements: list[str] = []

    def collect_sql(*args: object) -> None:
        statements.append(str(args[2]))

    event.listen(runtime.database.engine, "before_cursor_execute", collect_sql)
    try:
        with runtime.database.session_factory() as session:
            first = runtime.builder.build(session, agent_id="anon")
            runtime.builder.build(session, agent_id="soyo")
            repeated = runtime.builder.build(session, agent_id="anon")
            assert not session.new
            assert not session.dirty
            assert not session.deleted
    finally:
        event.remove(runtime.database.engine, "before_cursor_execute", collect_sql)

    assert first == repeated
    executed_sql = "\n".join(statements)
    assert "agent_memory_records" not in executed_sql
    assert "agent_runtime_states" not in executed_sql


def test_builder_isolates_worlds_even_when_internal_ids_are_reused(
    runtime: PreparedRuntime,
) -> None:
    with runtime.database.session_factory() as session:
        main = runtime.builder.build(session, agent_id="anon")
        other = runtime.other_builder.build(session, agent_id="anon")

    main_entries = {
        item.source_entry_id for item in main.candidates if item.source_entry_id is not None
    }
    other_entries = {
        item.source_entry_id for item in other.candidates if item.source_entry_id is not None
    }
    assert "foreign-entry" not in main_entries
    assert other_entries == {"foreign-entry"}
    assert main.world_ref == WORLD_REF
    assert other.world_ref == OTHER_WORLD_REF
    assert "FOREIGN WORLD SECRET" not in main.model_dump_json()


def test_cursor_and_object_catalog_are_rejected_before_projection(
    runtime: PreparedRuntime,
) -> None:
    with runtime.database.session_factory() as session:
        with pytest.raises(AgentViewStaleError, match="current World version"):
            runtime.builder.build(
                session,
                agent_id="anon",
                observed_through=CommitPosition(world_version=7),
            )
        with pytest.raises(AgentViewStaleError, match="visible committed Entry"):
            runtime.builder.build(
                session,
                agent_id="anon",
                observed_through=CommitPosition(world_version=5),
            )

        bad_builder = AgentViewBuilder(
            WORLD_REF,
            _object_seeds(metronome_name="Impostor"),
        )
        with pytest.raises(AgentViewCatalogError, match="does not match"):
            bad_builder.build(session, agent_id="anon")

    duplicate = _object_seeds()[0]
    with pytest.raises(AgentViewCatalogError, match="unique"):
        AgentViewBuilder(WORLD_REF, (duplicate, duplicate))


def _append_main_history(session: Session) -> None:
    store = EventEntryStore(WORLD_REF)
    entries: tuple[tuple[EventEntry, tuple[str, ...], tuple[EventEntryLink, ...]], ...] = (
        (
            _dialogue(
                WORLD_REF,
                entry_id="direct-open",
                version=2,
                actor="anon",
                target="soyo",
                root_session_id="session-anon",
                channel=DeliveryChannel.DIRECT,
                content="Good morning.",
            ),
            ("anon", "soyo", "tomori"),
            (),
        ),
        (
            _dialogue(
                WORLD_REF,
                entry_id="direct-question",
                version=3,
                actor="anon",
                target="soyo",
                root_session_id="session-anon",
                channel=DeliveryChannel.DIRECT,
                content="Will you count us in?",
            ),
            ("anon", "soyo", "tomori"),
            (),
        ),
        (
            _dialogue(
                WORLD_REF,
                entry_id="whisper",
                version=4,
                actor="anon",
                target="soyo",
                root_session_id="session-anon",
                channel=DeliveryChannel.WHISPER,
                content="Keep this between us.",
            ),
            ("anon", "soyo"),
            (),
        ),
        (
            _dialogue(
                WORLD_REF,
                entry_id="other-root-dialogue",
                version=5,
                actor="taki",
                target="mutsumi",
                root_session_id="session-taki",
                channel=DeliveryChannel.DIRECT,
                content="OTHER ROOT SECRET",
            ),
            ("taki", "mutsumi"),
            (),
        ),
        (
            ActionEntry(
                world_ref=WORLD_REF,
                entry_id="object-action",
                source_id="decision-6",
                commit_position=CommitPosition(world_version=6),
                root_session_id_at_commit="session-anon",
                topology_version=1,
                actor_agent_id="tomori",
                target_object_id="metronome",
                operation_id="start",
                occurred_at=NOW + timedelta(minutes=6),
                text="The metronome starts ticking.",
                created_at=NOW + timedelta(minutes=6),
            ),
            ("anon", "soyo", "tomori"),
            (
                EventEntryLink(
                    world_ref=WORLD_REF,
                    entry_id="object-action",
                    relation_kind=EntryRelationKind.CAUSE,
                    related_entry_id="other-root-dialogue",
                ),
            ),
        ),
    )
    for entry, recipient_ids, links in entries:
        store.append(
            session,
            entry,
            links=links,
            recipients=tuple(
                EventEntryRecipient(
                    world_ref=WORLD_REF,
                    entry_id=entry.entry_id,
                    agent_id=agent_id,
                )
                for agent_id in recipient_ids
            ),
        )
        session.flush()
    store.create_request(
        session,
        InteractionRequest(
            world_ref=WORLD_REF,
            request_entry_id="direct-question",
            requester_agent_id="anon",
            recipient_agent_id="soyo",
            status=InteractionRequestStatus.PENDING,
            updated_world_version=3,
        ),
    )
    session.execute(
        text(
            "UPDATE worlds SET current_version = 6, decision_seq = 5, status = 'running' "
            "WHERE world_id = 'main'"
        )
    )
    session.execute(
        text(
            "UPDATE objects SET state = 'running' "
            "WHERE world_id = 'main' AND object_id = 'metronome'"
        )
    )


def _append_other_history(session: Session) -> None:
    store = EventEntryStore(OTHER_WORLD_REF)
    entry = _dialogue(
        OTHER_WORLD_REF,
        entry_id="foreign-entry",
        version=2,
        actor="anon",
        target="soyo",
        root_session_id="session-anon",
        channel=DeliveryChannel.DIRECT,
        content="FOREIGN WORLD SECRET",
    )
    store.append(
        session,
        entry,
        recipients=tuple(
            EventEntryRecipient(
                world_ref=OTHER_WORLD_REF,
                entry_id=entry.entry_id,
                agent_id=agent_id,
            )
            for agent_id in ("anon", "soyo", "tomori")
        ),
    )
    session.execute(
        text(
            "UPDATE worlds SET current_version = 2, decision_seq = 1, status = 'running' "
            "WHERE world_id = 'other'"
        )
    )


def _dialogue(
    world_ref: WorldRef,
    *,
    entry_id: str,
    version: int,
    actor: str,
    target: str,
    root_session_id: str,
    channel: DeliveryChannel,
    content: str,
) -> DialogueEntry:
    return DialogueEntry(
        world_ref=world_ref,
        entry_id=entry_id,
        source_id=f"decision-{version}",
        commit_position=CommitPosition(world_version=version),
        root_session_id_at_commit=root_session_id,
        topology_version=1,
        actor_agent_id=actor,
        target_agent_id=target,
        audience_mode=(
            AudienceMode.SESSION if channel is DeliveryChannel.DIRECT else AudienceMode.EXPLICIT
        ),
        delivery_channel=channel,
        occurred_at=NOW + timedelta(minutes=version),
        text=content,
        created_at=NOW + timedelta(minutes=version),
    )


def _object_seeds(*, metronome_name: str = "Metronome") -> tuple[ObjectSeed, ...]:
    return (
        ObjectSeed(
            id="keyboard",
            name="Keyboard",
            kind="instrument",
            description="A keyboard in the rehearsal hall.",
            location_id="hall",
            state="ready",
        ),
        ObjectSeed(
            id="metronome",
            name=metronome_name,
            kind="instrument",
            description="A mechanical metronome.",
            location_id="cafe",
            state="stopped",
            operations=(
                ObjectOperationSeed(
                    operation_id="reset",
                    from_state="running",
                    to_state="stopped",
                    result_text="The metronome stops.",
                ),
                ObjectOperationSeed(
                    operation_id="start",
                    from_state="stopped",
                    to_state="running",
                    result_text="The metronome starts ticking.",
                ),
            ),
        ),
    )


def _initial_world(world_ref: WorldRef) -> PublicWorldState:
    return PublicWorldState(
        world=WorldState(
            world_ref=world_ref,
            seed_id="view-seed",
            seed_version=1,
            seed_hash="a" * 64,
            current_version=1,
            world_time=NOW,
            status=WorldStatus.PAUSED,
            created_at=NOW,
        ),
        locations=(
            LocationState(
                world_ref=world_ref,
                location_id="cafe",
                name="Cafe",
                description="A quiet cafe.",
            ),
            LocationState(
                world_ref=world_ref,
                location_id="hall",
                name="Hall",
                description="A rehearsal hall.",
            ),
        ),
        agents=tuple(
            AgentWorldState(
                world_ref=world_ref,
                agent_id=agent_id,
                location_id=location_id,
                public_status=status,
            )
            for agent_id, location_id, status in (
                ("anon", "cafe", "listening"),
                ("soyo", "cafe", "thinking"),
                ("tomori", "cafe", "watching"),
                ("taki", "cafe", "waiting"),
                ("mutsumi", "cafe", "quiet"),
                ("rana", "hall", "practicing"),
            )
        ),
        objects=tuple(
            ObjectState(
                world_ref=world_ref,
                object_id=seed.id,
                name=seed.name,
                kind=seed.kind,
                description=seed.description,
                location_id=seed.location_id,
                owner_agent_id=seed.owner_agent_id,
                state=seed.state,
            )
            for seed in _object_seeds()
        ),
        facts=(
            WorldFact(
                world_ref=world_ref,
                fact_id="world-day",
                predicate="day",
                object="friday",
                content="It is Friday.",
            ),
            WorldFact(
                world_ref=world_ref,
                fact_id="cafe-open",
                location_id="cafe",
                predicate="open",
                object="true",
                content="The cafe is open.",
            ),
            WorldFact(
                world_ref=world_ref,
                fact_id="hall-noisy",
                location_id="hall",
                predicate="noise",
                object="loud",
                content="The hall is loud.",
            ),
            WorldFact(
                world_ref=world_ref,
                fact_id="taki-public",
                agent_id="taki",
                predicate="mood",
                object="impatient",
                content="Taki looks impatient.",
            ),
            WorldFact(
                world_ref=world_ref,
                fact_id="rana-public",
                agent_id="rana",
                predicate="activity",
                object="practice",
                content="Rana is practicing.",
            ),
            WorldFact(
                world_ref=world_ref,
                fact_id="metronome-steady",
                object_id="metronome",
                predicate="tempo",
                object="steady",
                content="The metronome keeps steady time.",
            ),
            WorldFact(
                world_ref=world_ref,
                fact_id="keyboard-ready",
                object_id="keyboard",
                predicate="ready",
                object="true",
                content="The keyboard is ready.",
            ),
        ),
        sessions=(
            _session(world_ref, "session-anon", "anon", "session-anon"),
            _session(world_ref, "session-soyo", "soyo", "session-anon"),
            _session(world_ref, "session-tomori", "tomori", "session-anon"),
            _session(world_ref, "session-taki", "taki", "session-taki"),
            _session(world_ref, "session-mutsumi", "mutsumi", "session-taki"),
            _session(world_ref, "session-rana", "rana", "session-rana"),
        ),
    )


def _session(
    world_ref: WorldRef,
    session_id: str,
    agent_id: str,
    root_session_id: str,
) -> EventSessionNode:
    return EventSessionNode(
        world_ref=world_ref,
        session_id=session_id,
        agent_id=agent_id,
        root_session_id=root_session_id,
        topology_version=1,
        updated_world_version=1,
    )
