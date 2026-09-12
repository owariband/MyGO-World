"""M3.4 end-to-end tests for one disposable Character decision step."""

from __future__ import annotations

import shutil
from collections import defaultdict, deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import text

from agent_runtime.agent.memory import MemoryStream
from agent_runtime.agent.memory.storage import MemoryStore
from agent_runtime.agent.personact.agent import ActionPlanningInput, PlanDraft, PlanningInput
from agent_runtime.agent.personact.compiler import (
    Catalog,
    CompiledPersonActSpec,
    PromptDefinition,
    ToolDefinition,
    ToolMode,
)
from agent_runtime.agent.personact.proposal import ProposalDraft
from agent_runtime.agent.personact.state import PlanItem
from agent_runtime.agent.personact.storage import PersonaStateStore, StoredPersonaState
from agent_runtime.agent.skill import RuntimeSkillCatalog
from agent_runtime.bootstrap import create_world, load_world
from agent_runtime.event.character_step import CharacterStep, CharacterStepError
from agent_runtime.event.runner import WorldRunner
from agent_runtime.scenario import ObjectSeed
from agent_runtime.sqlite import ProjectDatabase, open_project_database
from agent_runtime.world.contracts import (
    ActionProposal,
    Affordance,
    CharacterTarget,
    DeliveryChannel,
    InteractAction,
    NoOpAction,
    ObjectTarget,
    PerceptCandidate,
    ProposalKind,
    RespondAction,
    UtterAction,
    WaitAction,
    WorldRef,
)
from agent_runtime.world.entries import (
    ActionEntry,
    DialogueEntry,
    InteractionRequestStatus,
)
from agent_runtime.world.entry_storage import EventEntryStore
from agent_runtime.world.state import PublicWorldState, WorldStatus
from agent_runtime.world.storage import WorldCommitConflictError, WorldStore
from agent_runtime.world.updater import WorldUpdateStatus

REPOSITORY_ROOT = Path(__file__).parents[2]
PROJECT_ID = "for-the-band"
WORLD_ID = "m3-character-step"
STEP_TIME = datetime(2026, 9, 11, 20, tzinfo=UTC)

type DraftBuilder = Callable[[ActionPlanningInput], ProposalDraft]


@dataclass(slots=True)
class _StrategyHarness:
    scripts: dict[str, deque[DraftBuilder]] = field(default_factory=lambda: defaultdict(deque))
    calls: list[tuple[str, str]] = field(default_factory=lambda: list[tuple[str, str]]())
    action_inputs: dict[str, list[ActionPlanningInput]] = field(
        default_factory=lambda: defaultdict(list)
    )

    def queue(self, agent_id: str, *builders: DraftBuilder) -> None:
        self.scripts[agent_id].extend(builders)

    def factory(self, spec: CompiledPersonActSpec) -> _ScriptedStrategy:
        return _ScriptedStrategy(agent_id=spec.agent_id, harness=self)

    def action_call_count(self, agent_id: str) -> int:
        return self.calls.count((agent_id, "plan_action"))


@dataclass(slots=True)
class _ScriptedStrategy:
    agent_id: str
    harness: _StrategyHarness

    def score_poignancy(
        self,
        spec: CompiledPersonActSpec,
        candidate: PerceptCandidate,
        *,
        world_ref: WorldRef,
    ) -> float:
        assert spec.agent_id == self.agent_id
        assert world_ref.project_id == PROJECT_ID
        self.harness.calls.append((self.agent_id, f"score:{candidate.candidate_id}"))
        return 1.0

    def plan(self, planning_input: PlanningInput) -> PlanDraft:
        assert planning_input.spec.agent_id == self.agent_id
        self.harness.calls.append((self.agent_id, "plan"))
        return PlanDraft(
            items=(
                PlanItem(
                    plan_id=f"plan-{self.agent_id}",
                    description=f"Continue {self.agent_id}'s current intention.",
                ),
            )
        )

    def plan_action(self, planning_input: ActionPlanningInput) -> ProposalDraft:
        assert planning_input.spec.agent_id == self.agent_id
        self.harness.calls.append((self.agent_id, "plan_action"))
        self.harness.action_inputs[self.agent_id].append(planning_input)
        try:
            builder = self.harness.scripts[self.agent_id].popleft()
        except IndexError as error:
            raise AssertionError(f'no scripted action for Agent "{self.agent_id}"') from error
        return builder(planning_input)


@dataclass(frozen=True, slots=True)
class _FixedEmbeddingProvider:
    def embed(self, text: str) -> tuple[float, ...]:
        assert text
        return (1.0, 0.0)


@dataclass(frozen=True, slots=True)
class _Runtime:
    repository: Path
    database: ProjectDatabase
    world_ref: WorldRef
    specs: tuple[CompiledPersonActSpec, ...]
    object_seeds: tuple[ObjectSeed, ...]
    harness: _StrategyHarness
    step: CharacterStep


@pytest.fixture
def runtime(tmp_path: Path) -> Iterator[_Runtime]:
    repository = tmp_path / "repository"
    project_directory = repository / "projects" / PROJECT_ID
    project_directory.mkdir(parents=True)
    for filename in ("project.json", "agents.json", "scenario.yaml"):
        shutil.copy2(REPOSITORY_ROOT / "projects" / PROJECT_ID / filename, project_directory)

    catalog = _catalog()
    created = create_world(
        repository,
        PROJECT_ID,
        WORLD_ID,
        catalog=catalog,
        clock=lambda: STEP_TIME,
    )
    database = open_project_database(repository / ".runtime", PROJECT_ID, create=False)
    with database.session_factory.begin() as session:
        session.execute(
            text("UPDATE worlds SET status = 'running' WHERE world_id = :world_id"),
            {"world_id": WORLD_ID},
        )

    harness = _StrategyHarness()
    step = CharacterStep(
        database=database,
        world_ref=created.world_ref,
        specs=created.specs,
        object_seeds=created.object_seeds,
        strategy_factory=harness.factory,
        embedding_provider=_FixedEmbeddingProvider(),
        clock=lambda: STEP_TIME,
    )
    try:
        yield _Runtime(
            repository=repository,
            database=database,
            world_ref=created.world_ref,
            specs=created.specs,
            object_seeds=created.object_seeds,
            harness=harness,
            step=step,
        )
    finally:
        database.dispose()


def test_utter_request_becomes_mandatory_and_response_resolves_it(
    runtime: _Runtime,
) -> None:
    runtime.harness.queue("anon", _utter("soyo", expects_response=True))
    utter_decision_id = runtime.step.next_decision_id("anon")

    utter_result = runtime.step.run("anon", utter_decision_id)

    assert utter_result.world_update.status is WorldUpdateStatus.APPLIED
    assert utter_result.world_update.current_world_version == 2
    assert isinstance(utter_result.proposal, ActionProposal)
    assert isinstance(utter_result.proposal.action, UtterAction)
    with runtime.database.session_factory() as session:
        utter_entry = EventEntryStore(runtime.world_ref).get_by_source(
            session,
            source_id=utter_decision_id,
        )
        assert isinstance(utter_entry, DialogueEntry)
        request = EventEntryStore(runtime.world_ref).get_request(session, utter_entry.entry_id)
    assert request.status is InteractionRequestStatus.PENDING

    runtime.harness.queue("soyo", _respond("anon", "Yes. Let's try it."))
    response_decision_id = runtime.step.next_decision_id("soyo")
    response_result = runtime.step.run("soyo", response_decision_id)

    planning_input = runtime.harness.action_inputs["soyo"][-1]
    request_candidate = next(
        item
        for item in planning_input.view.candidates
        if item.source_entry_id == utter_entry.entry_id
    )
    assert request_candidate.attention_tier.value == "mandatory"
    response_affordance = next(
        item for item in planning_input.view.affordances if item.kind is ProposalKind.RESPOND
    )
    assert response_affordance.request_entry_id == utter_entry.entry_id
    assert isinstance(response_result.proposal, ActionProposal)
    assert isinstance(response_result.proposal.action, RespondAction)
    assert response_result.proposal.action.in_reply_to_entry_id == utter_entry.entry_id
    assert response_result.world_update.status is WorldUpdateStatus.APPLIED
    assert response_result.world_update.current_world_version == 3

    with runtime.database.session_factory() as session:
        resolved = EventEntryStore(runtime.world_ref).get_request(session, utter_entry.entry_id)
        response_entry = EventEntryStore(runtime.world_ref).get_by_source(
            session,
            source_id=response_decision_id,
        )
    assert resolved.status is InteractionRequestStatus.RESOLVED
    assert response_entry is not None
    assert resolved.resolution_entry_id == response_entry.entry_id


def test_object_operation_commits_trusted_entry_and_metronome_state(
    runtime: _Runtime,
) -> None:
    runtime.harness.queue("anon", _start_metronome)
    decision_id = runtime.step.next_decision_id("anon")

    result = runtime.step.run("anon", decision_id)

    assert result.world_update.status is WorldUpdateStatus.APPLIED
    with runtime.database.session_factory() as session:
        public = WorldStore(runtime.world_ref).load(session)
        entry = EventEntryStore(runtime.world_ref).get_by_source(
            session,
            source_id=decision_id,
        )
    metronome = next(item for item in public.objects if item.object_id == "metronome")
    assert metronome.state == "running"
    assert isinstance(entry, ActionEntry)
    assert entry.operation_id == "start"
    assert entry.text == "节拍器被启动，稳定的节拍声在排练室里响起。"


@pytest.mark.parametrize(
    ("kind", "status"),
    [
        pytest.param(ProposalKind.WAIT, WorldUpdateStatus.WAIT, id="wait"),
        pytest.param(
            ProposalKind.NO_OP,
            WorldUpdateStatus.NO_OP,
            id="no-op",
        ),
    ],
)
def test_entryless_outcomes_are_durable_and_immediate_retry_skips_strategy(
    runtime: _Runtime,
    kind: ProposalKind,
    status: WorldUpdateStatus,
) -> None:
    builders = {
        ProposalKind.WAIT: _wait,
        ProposalKind.NO_OP: _no_op,
    }
    builder = builders[kind]
    runtime.harness.queue("anon", builder)
    decision_id = runtime.step.next_decision_id("anon")

    first = runtime.step.run("anon", decision_id)
    calls_after_first = runtime.harness.action_call_count("anon")
    replay = runtime.step.run("anon", decision_id)

    assert first.world_update.status is status
    assert first.world_update.entry_id is None
    assert replay.proposal is None
    assert replay.world_update.status is status
    assert replay.world_update.replayed is True
    assert runtime.harness.action_call_count("anon") == calls_after_first == 1
    with runtime.database.session_factory() as session:
        public = WorldStore(runtime.world_ref).load(session)
        entry = EventEntryStore(runtime.world_ref).get_by_source(
            session,
            source_id=decision_id,
        )
    assert public.world.current_version == 1
    assert public.world.decision_seq == 1
    assert entry is None


def test_older_entryless_decision_is_rejected_before_strategy(runtime: _Runtime) -> None:
    runtime.harness.queue("anon", _no_op, _wait)
    first_decision_id = runtime.step.next_decision_id("anon")
    runtime.step.run("anon", first_decision_id)
    second_decision_id = runtime.step.next_decision_id("anon")
    runtime.step.run("anon", second_decision_id)
    calls_after_two_steps = runtime.harness.action_call_count("anon")

    with pytest.raises(CharacterStepError, match="current World decision fence"):
        runtime.step.run("anon", first_decision_id)

    assert runtime.harness.action_call_count("anon") == calls_after_two_steps == 2
    with runtime.database.session_factory() as session:
        public = WorldStore(runtime.world_ref).load(session)
    assert public.world.current_version == 1
    assert public.world.decision_seq == 2


def test_entryless_decision_id_is_bound_to_its_agent(runtime: _Runtime) -> None:
    runtime.harness.queue("anon", _no_op)
    anon_decision_id = runtime.step.next_decision_id("anon")
    runtime.step.run("anon", anon_decision_id)

    with pytest.raises(CharacterStepError, match="current World decision fence"):
        runtime.step.run("soyo", anon_decision_id)

    assert runtime.harness.action_call_count("soyo") == 0


def test_applied_immediate_retry_is_idempotent_and_identity_bound(runtime: _Runtime) -> None:
    runtime.harness.queue("anon", _utter("soyo", expects_response=False))
    decision_id = runtime.step.next_decision_id("anon")

    first = runtime.step.run("anon", decision_id)
    calls_after_first = runtime.harness.action_call_count("anon")
    replay = runtime.step.run("anon", decision_id)

    assert replay.proposal is None
    assert replay.world_update.entry_id == first.world_update.entry_id
    assert replay.world_update.replayed is True
    assert runtime.harness.action_call_count("anon") == calls_after_first == 1
    with runtime.database.session_factory() as session:
        entry_count = session.scalar(
            text("SELECT count(*) FROM event_entries WHERE world_id = :world_id"),
            {"world_id": WORLD_ID},
        )
    assert entry_count == 1

    with pytest.raises(CharacterStepError, match="another Agent"):
        runtime.step.run("soyo", decision_id)
    with pytest.raises(CharacterStepError, match="no compiled PersonAct spec"):
        runtime.step.run("unknown", "unknown-decision")


def test_applied_replay_preserves_original_entry_position_after_later_commits(
    runtime: _Runtime,
) -> None:
    runtime.harness.queue("anon", _utter("soyo", expects_response=False))
    first_decision_id = runtime.step.next_decision_id("anon")
    first = runtime.step.run("anon", first_decision_id)
    runtime.harness.queue("anon", _utter("soyo", expects_response=False))
    later_decision_id = runtime.step.next_decision_id("anon")
    later = runtime.step.run("anon", later_decision_id)

    replay = runtime.step.run("anon", first_decision_id)

    assert first.world_update.current_world_version == 2
    assert first.world_update.current_decision_seq == 1
    assert first.world_update.entry_position is not None
    assert later.world_update.current_world_version == 3
    assert later.world_update.current_decision_seq == 2
    assert replay.world_update.replayed is True
    assert replay.world_update.entry_id == first.world_update.entry_id
    assert replay.world_update.entry_position == first.world_update.entry_position
    assert replay.world_update.current_world_version == 3
    assert replay.world_update.current_decision_seq == 2


def test_step_normalizes_identifier_whitespace_before_deciding(runtime: _Runtime) -> None:
    runtime.harness.queue("anon", _no_op)
    decision_id = runtime.step.next_decision_id("anon")

    result = runtime.step.run("  anon  ", f"  {decision_id}  ")
    replay = runtime.step.run("anon", decision_id)

    assert result.agent_id == "anon"
    assert result.decision_id == decision_id
    assert result.world_update.decision_id == decision_id
    assert replay.world_update.replayed is True
    assert runtime.harness.action_call_count("anon") == 1


def test_step_rejects_non_string_and_empty_identifiers_as_boundary_errors(
    runtime: _Runtime,
) -> None:
    with pytest.raises(CharacterStepError, match="agent_id must be a string"):
        runtime.step.next_decision_id(cast(str, None))
    with pytest.raises(CharacterStepError, match="decision_id must be a string"):
        runtime.step.run("anon", cast(str, 1))
    with pytest.raises(CharacterStepError, match="decision_id cannot be empty"):
        runtime.step.run("anon", "  ")


def test_stale_world_fence_rolls_back_private_and_public_state(runtime: _Runtime) -> None:
    before_persona, before_memory = _private_snapshot(runtime, "anon")
    decision_id = runtime.step.next_decision_id("anon")

    def make_stale(planning_input: ActionPlanningInput) -> ProposalDraft:
        with runtime.database.session_factory.begin() as session:
            session.execute(
                text(
                    "UPDATE worlds SET decision_seq = decision_seq + 1 WHERE world_id = :world_id"
                ),
                {"world_id": WORLD_ID},
            )
        return _wait(planning_input)

    runtime.harness.queue("anon", make_stale)

    with pytest.raises(WorldCommitConflictError, match="stale"):
        runtime.step.run("anon", decision_id)

    after_persona, after_memory = _private_snapshot(runtime, "anon")
    assert after_persona == before_persona
    assert after_memory == before_memory
    with runtime.database.session_factory() as session:
        public = WorldStore(runtime.world_ref).load(session)
    assert public.world.current_version == 1
    assert public.world.decision_seq == 1


def test_paused_world_rejects_step_before_strategy(runtime: _Runtime) -> None:
    decision_id = runtime.step.next_decision_id("anon")
    with runtime.database.session_factory.begin() as session:
        session.execute(
            text("UPDATE worlds SET status = 'paused' WHERE world_id = :world_id"),
            {"world_id": WORLD_ID},
        )
    calls_before = tuple(runtime.harness.calls)

    with pytest.raises(CharacterStepError, match="must be running"):
        runtime.step.run("anon", decision_id)

    assert tuple(runtime.harness.calls) == calls_before


def test_unscheduled_step_cannot_bypass_a_runner_owned_or_active_dispatch(
    runtime: _Runtime,
) -> None:
    bypass_id = runtime.step.next_decision_id("soyo")
    calls_before = tuple(runtime.harness.calls)
    runner = WorldRunner(
        database=runtime.database,
        world_ref=runtime.world_ref,
        character_step=runtime.step,
        owner_id="runner-owner",
    )
    try:
        runner.resume(additional_decisions=1)
        with pytest.raises(CharacterStepError, match="requires a CharacterDispatch"):
            runtime.step.next_decision_id("soyo")

        dispatch = runner.next_dispatch()
        assert dispatch is not None
        with pytest.raises(CharacterStepError, match="requires a CharacterDispatch"):
            runtime.step.next_decision_id("soyo")
        with pytest.raises(CharacterStepError, match="requires a CharacterDispatch"):
            runtime.step.run("soyo", bypass_id)

        with runtime.database.session_factory() as session:
            world = WorldStore(runtime.world_ref).load(session).world
        assert world.active_dispatch_count == dispatch.dispatch_count
        assert world.active_dispatch_agent_id == dispatch.agent_id
        assert world.current_version == 1
        assert world.decision_seq == 0
        assert tuple(runtime.harness.calls) == calls_before
    finally:
        runner.close()


@pytest.mark.parametrize(
    "checkpoint",
    ["world", "object", "entry", "request", "persona", "memory"],
)
def test_every_checkpoint_failure_rolls_back_whole_character_step(
    runtime: _Runtime,
    checkpoint: str,
) -> None:
    before_public = _public_state(runtime)
    before_persona, before_memory = _private_snapshot(runtime, "anon")
    runtime.harness.queue(
        "anon",
        _start_metronome if checkpoint == "object" else _utter("soyo", expects_response=True),
    )
    decision_id = runtime.step.next_decision_id("anon")

    def fail_at(name: str) -> None:
        if name == checkpoint:
            raise RuntimeError(f"injected {checkpoint} failure")

    with pytest.raises(RuntimeError, match=f"injected {checkpoint}"):
        runtime.step.run("anon", decision_id, checkpoint=fail_at)

    assert _public_state(runtime) == before_public
    after_persona, after_memory = _private_snapshot(runtime, "anon")
    assert after_persona == before_persona
    assert after_memory == before_memory
    with runtime.database.session_factory() as session:
        entry_count, request_count = (
            session.scalar(text(f"SELECT count(*) FROM {table}"))
            for table in ("event_entries", "interaction_requests")
        )
    assert (entry_count, request_count) == (0, 0)


def test_failed_transaction_can_retry_the_same_fenced_decision_id(runtime: _Runtime) -> None:
    runtime.harness.queue(
        "anon",
        _utter("soyo", expects_response=False),
        _utter("soyo", expects_response=False),
    )
    decision_id = runtime.step.next_decision_id("anon")

    def fail_after_persona(name: str) -> None:
        if name == "persona":
            raise RuntimeError("injected retryable failure")

    with pytest.raises(RuntimeError, match="retryable failure"):
        runtime.step.run("anon", decision_id, checkpoint=fail_after_persona)

    recovered = runtime.step.run("anon", decision_id)

    assert recovered.world_update.status is WorldUpdateStatus.APPLIED
    assert recovered.world_update.replayed is False
    assert runtime.harness.action_call_count("anon") == 2


def test_engine_close_then_paused_bootstrap_reload_preserves_committed_step(
    runtime: _Runtime,
) -> None:
    runtime.harness.queue("anon", _utter("soyo", expects_response=True))
    decision_id = runtime.step.next_decision_id("anon")
    committed = runtime.step.run("anon", decision_id)
    assert committed.world_update.entry_id is not None
    before_persona, before_memory = _private_snapshot(runtime, "anon")
    with runtime.database.session_factory.begin() as session:
        session.execute(
            text(
                "UPDATE worlds SET status = 'paused', control_epoch = control_epoch + 1 "
                "WHERE world_id = :world_id"
            ),
            {"world_id": WORLD_ID},
        )
    runtime.database.dispose()

    restarted = load_world(
        runtime.repository,
        PROJECT_ID,
        WORLD_ID,
        catalog=_catalog(),
    )

    assert restarted.public_state.world.status is WorldStatus.PAUSED
    assert restarted.public_state.world.current_version == 2
    restarted_persona = next(
        item for item in restarted.persona_states if item.state.agent_id == "anon"
    )
    restarted_memory = next(item for item in restarted.memory_streams if item.agent_id == "anon")
    assert restarted_persona == before_persona
    assert restarted_memory == before_memory

    reopened = open_project_database(runtime.repository / ".runtime", PROJECT_ID, create=False)
    try:
        with reopened.session_factory() as session:
            entry = EventEntryStore(runtime.world_ref).get_by_source(
                session,
                source_id=decision_id,
            )
            request = EventEntryStore(runtime.world_ref).get_request(
                session,
                committed.world_update.entry_id,
            )
        assert entry is not None
        assert entry.entry_id == committed.world_update.entry_id
        assert request.status is InteractionRequestStatus.PENDING
    finally:
        reopened.dispose()


def _catalog() -> Catalog:
    skills = RuntimeSkillCatalog.load(REPOSITORY_ROOT / "content" / "skills")
    return Catalog(
        tools=(
            ToolDefinition(
                id="visible_location.query",
                version="1",
                mode=ToolMode.QUERY,
            ),
        ),
        prompts=(PromptDefinition(id="personact.v1", version="1", digest="prompt-v1"),),
        skills=skills.skills,
    )


def _utter(target_id: str, *, expects_response: bool) -> DraftBuilder:
    def build(planning_input: ActionPlanningInput) -> ProposalDraft:
        affordance = _affordance(
            planning_input,
            ProposalKind.UTTER,
            target_id=target_id,
            channel=DeliveryChannel.DIRECT,
        )
        return ProposalDraft(
            action=UtterAction(
                affordance_id=affordance.affordance_id,
                target=CharacterTarget(id=target_id),
                content="Would you like to keep rehearsing?",
                expects_response=expects_response,
            )
        )

    return build


def _respond(target_id: str, content: str) -> DraftBuilder:
    def build(planning_input: ActionPlanningInput) -> ProposalDraft:
        affordance = _affordance(
            planning_input,
            ProposalKind.RESPOND,
            target_id=target_id,
        )
        assert affordance.request_entry_id is not None
        return ProposalDraft(
            action=RespondAction(
                affordance_id=affordance.affordance_id,
                target=CharacterTarget(id=target_id),
                content=content,
                in_reply_to_entry_id=affordance.request_entry_id,
            ),
            evidence_ids=(affordance.request_entry_id,),
        )

    return build


def _start_metronome(planning_input: ActionPlanningInput) -> ProposalDraft:
    affordance = _affordance(
        planning_input,
        ProposalKind.INTERACT,
        target_id="metronome",
        operation_id="start",
    )
    return ProposalDraft(
        action=InteractAction(
            affordance_id=affordance.affordance_id,
            target=ObjectTarget(id="metronome"),
            description="Start the metronome.",
        )
    )


def _wait(planning_input: ActionPlanningInput) -> ProposalDraft:
    _affordance(planning_input, ProposalKind.WAIT)
    return ProposalDraft(
        action=WaitAction(
            description="Wait for the scene to change.",
            next_wakeup="event_change",
        )
    )


def _no_op(planning_input: ActionPlanningInput) -> ProposalDraft:
    del planning_input
    return ProposalDraft(action=NoOpAction(next_wakeup="event_change"))


def _affordance(
    planning_input: ActionPlanningInput,
    kind: ProposalKind,
    *,
    target_id: str | None = None,
    operation_id: str | None = None,
    channel: DeliveryChannel | None = None,
) -> Affordance:
    matches = tuple(
        item
        for item in planning_input.view.affordances
        if item.kind is kind
        and (target_id is None or (item.target is not None and item.target.id == target_id))
        and (operation_id is None or item.operation_id == operation_id)
        and (channel is None or item.delivery_channel is channel)
    )
    assert len(matches) == 1
    return matches[0]


def _private_snapshot(
    runtime: _Runtime,
    agent_id: str,
) -> tuple[StoredPersonaState, MemoryStream]:
    spec = next(item for item in runtime.specs if item.agent_id == agent_id)
    with runtime.database.session_factory() as session:
        persona = PersonaStateStore(runtime.world_ref).load(session, agent_id)
        memory = MemoryStore(runtime.world_ref).load(session, agent_id, spec.memory_scope)
    return persona, memory


def _public_state(runtime: _Runtime) -> PublicWorldState:
    with runtime.database.session_factory() as session:
        return WorldStore(runtime.world_ref).load(session)
