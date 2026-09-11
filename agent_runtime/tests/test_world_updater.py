"""M3.2 public World validation, CAS, and atomic-write tests."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text

from agent_runtime.scenario import ObjectOperationSeed, ObjectSeed
from agent_runtime.sqlite import ProjectDatabase, open_project_database
from agent_runtime.world.affordances import WorldAffordanceResolver
from agent_runtime.world.contracts import (
    ActAction,
    ActionProposal,
    Affordance,
    AgentAction,
    AgentView,
    AttentionTier,
    CharacterTarget,
    DeliveryChannel,
    InteractAction,
    NoOpAction,
    ObjectTarget,
    PerceptCandidate,
    PerceptionChannel,
    ProposalKind,
    RespondAction,
    UtterAction,
    WaitAction,
    WorldRef,
)
from agent_runtime.world.entries import (
    ActionEntry,
    DialogueEntry,
    EntryRelationKind,
    InteractionRequestStatus,
)
from agent_runtime.world.entry_storage import EventEntryStore
from agent_runtime.world.state import (
    AgentWorldState,
    EventSessionNode,
    LocationState,
    ObjectState,
    PublicWorldState,
    WorldState,
    WorldStatus,
)
from agent_runtime.world.storage import WorldCommitConflictError, WorldStore
from agent_runtime.world.updater import (
    WorldChangeValidator,
    WorldUpdatePlan,
    WorldUpdater,
    WorldUpdateStatus,
    WorldUpdateValidationError,
)

NOW = datetime(2026, 9, 11, 10, tzinfo=UTC)
WORLD_REF = WorldRef(project_id="m3-fixture", world_id="save-001")
OBJECT_SEED = ObjectSeed(
    id="metronome",
    name="Metronome",
    kind="instrument",
    description="A metronome beside the music stand.",
    location_id="studio",
    state="stopped",
    operations=(
        ObjectOperationSeed(
            operation_id="start",
            from_state="stopped",
            to_state="running",
            result_text="The metronome starts ticking steadily.",
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class _Runtime:
    database: ProjectDatabase
    initial: PublicWorldState

    @property
    def validator(self) -> WorldChangeValidator:
        return WorldChangeValidator(WORLD_REF, (OBJECT_SEED,))


@pytest.fixture
def runtime(tmp_path: Path) -> Iterator[_Runtime]:
    database = open_project_database(tmp_path / ".runtime", WORLD_REF.project_id, create=True)
    initial = _initial_world()
    with database.session_factory.begin() as session:
        WorldStore(WORLD_REF).insert_initial(
            session,
            _initial_world(status=WorldStatus.PAUSED),
        )
        session.execute(
            text("UPDATE worlds SET status = 'running' WHERE world_id = :world_id"),
            {"world_id": WORLD_REF.world_id},
        )
    yield _Runtime(database=database, initial=initial)
    database.dispose()


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        pytest.param(ProposalKind.ACT, WorldUpdateStatus.NOT_APPLIED, id="not-applied"),
        pytest.param(ProposalKind.WAIT, WorldUpdateStatus.WAIT, id="wait"),
        pytest.param(ProposalKind.NO_OP, WorldUpdateStatus.NO_OP, id="no-op"),
    ],
)
def test_non_entry_outcomes_advance_only_decision_sequence(
    runtime: _Runtime,
    kind: ProposalKind,
    expected: WorldUpdateStatus,
) -> None:
    resolver = _resolver(runtime.initial, "anon")
    affordances: tuple[Affordance, ...] = ()
    if kind is ProposalKind.ACT:
        affordances = (resolver.simple_affordance(kind),)
        action: AgentAction = ActAction(description="Look around the studio.")
    elif kind is ProposalKind.WAIT:
        affordances = (resolver.simple_affordance(kind),)
        action = WaitAction(description="Wait a little.", next_wakeup="event_change")
    else:
        action = NoOpAction(next_wakeup="event_change")
    view = _view(runtime.initial, "anon", affordances=affordances)
    proposal = _proposal(view, action, decision_id=f"decision-{kind.value}")

    plan = runtime.validator.plan(proposal, view, runtime.initial, created_at=NOW)

    assert plan.status is expected
    assert plan.entry is None
    with runtime.database.session_factory.begin() as session:
        result = WorldUpdater(WORLD_REF, (OBJECT_SEED,)).apply(session, plan)
    assert result.status is expected
    assert result.current_world_version == 1
    assert result.current_decision_seq == 1
    with runtime.database.session_factory() as session:
        loaded = WorldStore(WORLD_REF).load(session)
        assert loaded.world.current_version == 1
        assert loaded.world.decision_seq == 1
        assert (
            EventEntryStore(WORLD_REF).get_by_source(session, source_id=proposal.proposal_id)
            is None
        )


def test_utter_plan_commits_entry_recipients_and_pending_request(runtime: _Runtime) -> None:
    proposal, view = _utter(runtime.initial, expects_response=True)

    plan = runtime.validator.plan(proposal, view, runtime.initial, created_at=NOW)

    assert plan.status is WorldUpdateStatus.APPLIED
    assert isinstance(plan.entry, DialogueEntry)
    assert plan.entry.text == "Do you want to start with this tempo?"
    assert plan.create_request is not None
    assert {item.agent_id for item in plan.recipients} == {"anon", "soyo"}
    with runtime.database.session_factory() as session:
        assert WorldStore(WORLD_REF).load(session) == runtime.initial
        assert (
            EventEntryStore(WORLD_REF).get_by_source(session, source_id=proposal.proposal_id)
            is None
        )

    with runtime.database.session_factory.begin() as session:
        result = WorldUpdater(WORLD_REF, (OBJECT_SEED,)).apply(session, plan)

    assert result.current_world_version == 2
    assert result.current_decision_seq == 1
    assert result.entry_id == plan.entry.entry_id
    with runtime.database.session_factory() as session:
        loaded = WorldStore(WORLD_REF).load(session)
        stored = EventEntryStore(WORLD_REF).get(session, plan.entry.entry_id)
        request = EventEntryStore(WORLD_REF).get_request(session, plan.entry.entry_id)
    assert loaded.world.current_version == 2
    assert loaded.world.decision_seq == 1
    assert stored == plan.entry
    assert request.status is InteractionRequestStatus.PENDING
    assert request.recipient_agent_id == "soyo"


def test_response_commits_reply_and_resolves_original_request(runtime: _Runtime) -> None:
    utter, utter_view = _utter(runtime.initial, expects_response=True)
    utter_plan = runtime.validator.plan(utter, utter_view, runtime.initial, created_at=NOW)
    assert isinstance(utter_plan.entry, DialogueEntry)
    with runtime.database.session_factory.begin() as session:
        WorldUpdater(WORLD_REF, (OBJECT_SEED,)).apply(session, utter_plan)
    with runtime.database.session_factory() as session:
        current = WorldStore(WORLD_REF).load(session)
        request = EventEntryStore(WORLD_REF).get_request(session, utter_plan.entry.entry_id)
        source = EventEntryStore(WORLD_REF).get(session, utter_plan.entry.entry_id)
    assert isinstance(source, DialogueEntry)

    resolver = _resolver(current, "soyo")
    affordance = resolver.response_affordance(
        target=CharacterTarget(id="anon"),
        delivery_channel=DeliveryChannel.DIRECT,
        request_entry_id=source.entry_id,
    )
    view = _view(
        current,
        "soyo",
        affordances=(affordance,),
        candidates=(
            PerceptCandidate(
                candidate_id="request-for-soyo",
                source_entry_id=source.entry_id,
                channel=PerceptionChannel.DIRECT_INTERACTION,
                attention_tier=AttentionTier.MANDATORY,
                subject="anon",
                predicate="asks",
                object="soyo",
                content=source.text,
                salience=1.0,
            ),
        ),
    )
    response = _proposal(
        view,
        RespondAction(
            affordance_id=affordance.affordance_id,
            target=CharacterTarget(id="anon"),
            content="Yes, start it.",
            in_reply_to_entry_id=source.entry_id,
        ),
        decision_id="soyo-response",
    )

    plan = runtime.validator.plan(
        response,
        view,
        current,
        created_at=NOW,
        previous_entry=source,
        response_request=request,
        response_source=source,
    )
    assert isinstance(plan.entry, DialogueEntry)
    assert plan.resolve_request_entry_id == source.entry_id
    assert {item.relation_kind for item in plan.links} == {
        EntryRelationKind.PREVIOUS,
        EntryRelationKind.REPLY,
    }

    with runtime.database.session_factory.begin() as session:
        result = WorldUpdater(WORLD_REF, (OBJECT_SEED,)).apply(session, plan)
    assert result.current_world_version == 3
    assert result.current_decision_seq == 2
    assert plan.entry is not None
    with runtime.database.session_factory() as session:
        resolved = EventEntryStore(WORLD_REF).get_request(session, source.entry_id)
        links = EventEntryStore(WORLD_REF).links_for(session, (plan.entry.entry_id,))
    assert resolved.status is InteractionRequestStatus.RESOLVED
    assert resolved.resolution_entry_id == plan.entry.entry_id
    assert {item.relation_kind for item in links} == {
        EntryRelationKind.PREVIOUS,
        EntryRelationKind.REPLY,
    }


def test_object_plan_uses_trusted_operation_result_and_changes_state(runtime: _Runtime) -> None:
    resolver = _resolver(runtime.initial, "anon")
    object_state = runtime.initial.objects[0]
    affordance = resolver.object_affordances(
        object_state=object_state,
        object_seed=OBJECT_SEED,
    )[0]
    view = _view(runtime.initial, "anon", affordances=(affordance,))
    proposal = _proposal(
        view,
        InteractAction(
            affordance_id=affordance.affordance_id,
            target=ObjectTarget(id="metronome"),
            description="Claim that the metronome explodes.",
        ),
        decision_id="start-metronome",
    )

    plan = runtime.validator.plan(proposal, view, runtime.initial, created_at=NOW)

    assert isinstance(plan.entry, ActionEntry)
    assert plan.entry.text == OBJECT_SEED.operations[0].result_text
    assert plan.object_change is not None
    assert plan.object_change.new_state == "running"
    with runtime.database.session_factory.begin() as session:
        WorldUpdater(WORLD_REF, (OBJECT_SEED,)).apply(session, plan)
    with runtime.database.session_factory() as session:
        loaded = WorldStore(WORLD_REF).load(session)
        stored = EventEntryStore(WORLD_REF).get(session, plan.entry.entry_id)
    assert loaded.objects[0].state == "running"
    assert stored == plan.entry


def test_updater_rejects_a_forged_object_plan_before_world_cas(runtime: _Runtime) -> None:
    resolver = _resolver(runtime.initial, "anon")
    object_state = runtime.initial.objects[0]
    affordance = resolver.object_affordances(
        object_state=object_state,
        object_seed=OBJECT_SEED,
    )[0]
    view = _view(runtime.initial, "anon", affordances=(affordance,))
    proposal = _proposal(
        view,
        InteractAction(
            affordance_id=affordance.affordance_id,
            target=ObjectTarget(id="metronome"),
            description="Start it.",
        ),
        decision_id="forged-object-plan",
    )
    plan = runtime.validator.plan(proposal, view, runtime.initial, created_at=NOW)
    assert plan.object_change is not None
    payload = plan.model_dump()
    payload["objectChange"] = {
        **plan.object_change.model_dump(),
        "newState": "exploded",
    }
    forged = WorldUpdatePlan.model_validate(payload, strict=True)

    with (
        pytest.raises(WorldUpdateValidationError, match="trusted Scenario operation"),
        runtime.database.session_factory.begin() as session,
    ):
        WorldUpdater(WORLD_REF, (OBJECT_SEED,)).apply(session, forged)

    with runtime.database.session_factory() as session:
        assert WorldStore(WORLD_REF).load(session) == runtime.initial
        assert (
            EventEntryStore(WORLD_REF).get_by_source(
                session,
                source_id=proposal.proposal_id,
            )
            is None
        )


def test_updater_rechecks_object_reachability_at_the_write_boundary(runtime: _Runtime) -> None:
    resolver = _resolver(runtime.initial, "anon")
    object_state = runtime.initial.objects[0]
    affordance = resolver.object_affordances(
        object_state=object_state,
        object_seed=OBJECT_SEED,
    )[0]
    view = _view(runtime.initial, "anon", affordances=(affordance,))
    proposal = _proposal(
        view,
        InteractAction(
            affordance_id=affordance.affordance_id,
            target=ObjectTarget(id="metronome"),
            description="Start it.",
        ),
        decision_id="moved-object",
    )
    plan = runtime.validator.plan(proposal, view, runtime.initial, created_at=NOW)
    with runtime.database.session_factory.begin() as session:
        session.execute(
            text(
                "INSERT INTO locations(world_id, location_id, name, description) "
                "VALUES (:world_id, 'hallway', 'Hallway', 'Outside the studio.')"
            ),
            {"world_id": WORLD_REF.world_id},
        )
        session.execute(
            text(
                "UPDATE objects SET location_id = 'hallway' "
                "WHERE world_id = :world_id AND object_id = 'metronome'"
            ),
            {"world_id": WORLD_REF.world_id},
        )

    with (
        pytest.raises(WorldCommitConflictError, match="can no longer interact"),
        runtime.database.session_factory.begin() as session,
    ):
        WorldUpdater(WORLD_REF, (OBJECT_SEED,)).apply(session, plan)

    with runtime.database.session_factory() as session:
        loaded = WorldStore(WORLD_REF).load(session)
        stored = EventEntryStore(WORLD_REF).get_by_source(
            session,
            source_id=proposal.proposal_id,
        )
    assert loaded.world.current_version == 1
    assert loaded.world.decision_seq == 0
    assert loaded.objects[0].state == "stopped"
    assert stored is None


def test_validator_rejects_forged_affordance_stale_snapshot_and_wrong_world(
    runtime: _Runtime,
) -> None:
    valid = _resolver(runtime.initial, "anon").utter_affordance(
        target=CharacterTarget(id="soyo"),
        delivery_channel=DeliveryChannel.DIRECT,
    )
    view = _view(runtime.initial, "anon", affordances=(valid,))
    forged = _proposal(
        view,
        UtterAction(
            affordance_id="forged-affordance",
            target=CharacterTarget(id="soyo"),
            content="forged",
            expects_response=False,
        ),
        decision_id="forged",
    )
    with pytest.raises(WorldUpdateValidationError, match="select one"):
        runtime.validator.plan(forged, view, runtime.initial, created_at=NOW)

    stale_affordance = WorldAffordanceResolver(
        world_ref=WORLD_REF,
        world_version=2,
        agent_id="anon",
    ).utter_affordance(
        target=CharacterTarget(id="soyo"),
        delivery_channel=DeliveryChannel.DIRECT,
    )
    stale_view = _view(runtime.initial, "anon", affordances=(stale_affordance,))
    stale = _proposal(
        stale_view,
        UtterAction(
            affordance_id=stale_affordance.affordance_id,
            target=CharacterTarget(id="soyo"),
            content="stale",
            expects_response=False,
        ),
        decision_id="stale",
    )
    with pytest.raises(WorldUpdateValidationError, match="forged or stale"):
        runtime.validator.plan(stale, stale_view, runtime.initial, created_at=NOW)

    foreign = WorldRef(project_id=WORLD_REF.project_id, world_id="save-foreign")
    foreign_view = AgentView.model_validate(
        {**view.model_dump(by_alias=False), "world_ref": foreign},
        strict=True,
    )
    foreign_proposal = ActionProposal.model_validate(
        {**forged.model_dump(by_alias=False), "world_ref": foreign},
        strict=True,
    )
    with pytest.raises(WorldUpdateValidationError, match="another WorldRef"):
        runtime.validator.plan(
            foreign_proposal,
            foreign_view,
            runtime.initial,
            created_at=NOW,
        )


def test_world_cas_prevents_duplicate_retry(runtime: _Runtime) -> None:
    proposal, view = _utter(runtime.initial, expects_response=False)
    plan = runtime.validator.plan(proposal, view, runtime.initial, created_at=NOW)
    updater = WorldUpdater(WORLD_REF, (OBJECT_SEED,))
    with runtime.database.session_factory.begin() as session:
        first = updater.apply(session, plan)

    with (
        pytest.raises(WorldCommitConflictError, match="stale"),
        runtime.database.session_factory.begin() as session,
    ):
        updater.apply(session, plan)

    with runtime.database.session_factory() as session:
        loaded = WorldStore(WORLD_REF).load(session)
        stored = EventEntryStore(WORLD_REF).get_by_source(session, source_id=proposal.proposal_id)
    assert first.current_world_version == loaded.world.current_version == 2
    assert loaded.world.decision_seq == 1
    assert stored is not None


@pytest.mark.parametrize("checkpoint", ["world", "object", "entry", "request"])
def test_public_checkpoint_failure_rolls_back_every_write(
    runtime: _Runtime,
    checkpoint: str,
) -> None:
    if checkpoint == "request":
        proposal, view = _utter(runtime.initial, expects_response=True)
    else:
        resolver = _resolver(runtime.initial, "anon")
        object_state = runtime.initial.objects[0]
        afforded = resolver.object_affordances(
            object_state=object_state,
            object_seed=OBJECT_SEED,
        )[0]
        view = _view(runtime.initial, "anon", affordances=(afforded,))
        proposal = _proposal(
            view,
            InteractAction(
                affordance_id=afforded.affordance_id,
                target=ObjectTarget(id="metronome"),
                description="Start it.",
            ),
            decision_id="rollback-object",
        )
    plan = runtime.validator.plan(proposal, view, runtime.initial, created_at=NOW)

    def fail_at(name: str) -> None:
        if name == checkpoint:
            raise RuntimeError(f"injected {checkpoint} failure")

    with (
        pytest.raises(RuntimeError, match=f"injected {checkpoint}"),
        runtime.database.session_factory.begin() as session,
    ):
        WorldUpdater(WORLD_REF, (OBJECT_SEED,)).apply(
            session,
            plan,
            checkpoint=fail_at,
        )

    with runtime.database.session_factory() as session:
        assert WorldStore(WORLD_REF).load(session) == runtime.initial
        assert (
            EventEntryStore(WORLD_REF).get_by_source(session, source_id=proposal.proposal_id)
            is None
        )
        counts = tuple(
            session.scalar(text(f"SELECT count(*) FROM {table}"))
            for table in (
                "event_entries",
                "event_entry_links",
                "event_entry_recipients",
                "interaction_requests",
            )
        )
    assert counts == (0, 0, 0, 0)


def test_updater_rejects_wrong_bound_world_before_first_write(runtime: _Runtime) -> None:
    proposal, view = _utter(runtime.initial, expects_response=False)
    plan = runtime.validator.plan(proposal, view, runtime.initial, created_at=NOW)
    foreign = WorldRef(project_id=WORLD_REF.project_id, world_id="save-foreign")

    with (
        pytest.raises(WorldUpdateValidationError, match="another WorldRef"),
        runtime.database.session_factory.begin() as session,
    ):
        WorldUpdater(foreign, (OBJECT_SEED,)).apply(session, plan)

    with runtime.database.session_factory() as session:
        assert WorldStore(WORLD_REF).load(session) == runtime.initial
        assert (
            EventEntryStore(WORLD_REF).get_by_source(session, source_id=proposal.proposal_id)
            is None
        )


def _utter(
    state: PublicWorldState,
    *,
    expects_response: bool,
) -> tuple[ActionProposal, AgentView]:
    affordance = _resolver(state, "anon").utter_affordance(
        target=CharacterTarget(id="soyo"),
        delivery_channel=DeliveryChannel.DIRECT,
    )
    view = _view(state, "anon", affordances=(affordance,))
    return (
        _proposal(
            view,
            UtterAction(
                affordance_id=affordance.affordance_id,
                target=CharacterTarget(id="soyo"),
                content="Do you want to start with this tempo?",
                expects_response=expects_response,
            ),
            decision_id="anon-utter",
        ),
        view,
    )


def _proposal(
    view: AgentView,
    action: AgentAction,
    *,
    decision_id: str,
) -> ActionProposal:
    return ActionProposal(
        world_ref=view.world_ref,
        proposal_id=decision_id,
        agent_id=view.agent_id,
        event_session_id=view.event_session_id,
        based_on_world_version=view.based_on_world_version,
        based_on_control_epoch=view.based_on_control_epoch,
        based_on_decision_seq=view.based_on_decision_seq,
        action=action,
    )


def _view(
    state: PublicWorldState,
    agent_id: str,
    *,
    affordances: tuple[Affordance, ...],
    candidates: tuple[PerceptCandidate, ...] = (),
) -> AgentView:
    agent = next(item for item in state.agents if item.agent_id == agent_id)
    session = next(item for item in state.sessions if item.agent_id == agent_id)
    world = state.world
    return AgentView(
        world_ref=WORLD_REF,
        agent_id=agent_id,
        event_session_id=session.session_id,
        based_on_world_version=world.current_version,
        based_on_control_epoch=world.control_epoch,
        based_on_decision_seq=world.decision_seq,
        current_location_id=agent.location_id,
        world_time=world.world_time,
        candidates=candidates,
        affordances=affordances,
    )


def _resolver(state: PublicWorldState, agent_id: str) -> WorldAffordanceResolver:
    return WorldAffordanceResolver(
        world_ref=WORLD_REF,
        world_version=state.world.current_version,
        agent_id=agent_id,
    )


def _initial_world(*, status: WorldStatus = WorldStatus.RUNNING) -> PublicWorldState:
    return PublicWorldState(
        world=WorldState(
            world_ref=WORLD_REF,
            seed_id="fixture",
            seed_version=1,
            seed_hash="a" * 64,
            current_version=1,
            world_time=NOW,
            status=status,
            created_at=NOW,
            control_epoch=1,
            decision_seq=0,
        ),
        locations=(
            LocationState(
                world_ref=WORLD_REF,
                location_id="studio",
                name="Studio",
                description="A quiet rehearsal studio.",
            ),
        ),
        agents=(
            AgentWorldState(
                world_ref=WORLD_REF,
                agent_id="anon",
                location_id="studio",
            ),
            AgentWorldState(
                world_ref=WORLD_REF,
                agent_id="soyo",
                location_id="studio",
            ),
        ),
        objects=(
            ObjectState(
                world_ref=WORLD_REF,
                object_id="metronome",
                name="Metronome",
                kind="instrument",
                description="A metronome beside the music stand.",
                location_id="studio",
                state="stopped",
            ),
        ),
        sessions=(
            EventSessionNode(
                world_ref=WORLD_REF,
                session_id="session-anon",
                agent_id="anon",
                root_session_id="session-anon",
                topology_version=1,
                updated_world_version=1,
            ),
            EventSessionNode(
                world_ref=WORLD_REF,
                session_id="session-soyo",
                agent_id="soyo",
                root_session_id="session-anon",
                topology_version=1,
                updated_world_version=1,
            ),
        ),
    )
