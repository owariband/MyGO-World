"""Deterministic tests for the real PersonAct cognition path."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Thread
from typing import override

import pytest
from pydantic import ValidationError

from agent_runtime.agent.memory import MemoryKind, MemoryRecord, MemoryStream
from agent_runtime.agent.personact.agent import (
    ActionPlanningInput,
    DecisionRequest,
    PersonActAgent,
    PlanDraft,
    PlanningInput,
)
from agent_runtime.agent.personact.compiler import (
    Catalog,
    CompiledPersonActSpec,
    PromptDefinition,
    ToolDefinition,
    ToolMode,
    compile_manifest,
)
from agent_runtime.agent.personact.errors import (
    DecisionInputError,
    PlannerOutputError,
    ProposalValidationError,
)
from agent_runtime.agent.personact.loop import PersonActLoop, PersonActLoopInput
from agent_runtime.agent.personact.manifest import Manifest, MemoryWritePolicy, load_manifest
from agent_runtime.agent.personact.proposal import ProposalDraft
from agent_runtime.agent.personact.state import (
    CognitiveConfig,
    PersonaState,
    PlanItem,
)
from agent_runtime.agent.skill import RuntimeSkillCatalog
from agent_runtime.world.contracts import (
    Affordance,
    AgentView,
    AttentionTier,
    CharacterTarget,
    InteractAction,
    PerceptCandidate,
    PerceptionChannel,
    ProposalKind,
    WorldRef,
)

WORLD_REF = WorldRef(project_id="coffee-golden", world_id="save-001")
AFFORDANCE_ID = "interact-soyo"
NOW = datetime(2026, 8, 31, 9, 5, tzinfo=UTC)
FIXTURE_PATH = Path(__file__).parents[1] / "testdata" / "npc_diy" / "agents.json"
SKILLS_PATH = Path(__file__).parents[2] / "content" / "skills"


def _talk_action() -> InteractAction:
    return InteractAction(
        affordance_id=AFFORDANCE_ID,
        target=CharacterTarget(id="soyo"),
        description="talk",
    )


@dataclass(slots=True)
class FixedStrategy:
    draft: ProposalDraft
    calls: list[str] = field(default_factory=lambda: list[str]())
    stage_log: list[str] = field(default_factory=lambda: list[str]())
    planning_input: PlanningInput | None = None
    action_input: ActionPlanningInput | None = None

    def score_poignancy(
        self,
        spec: CompiledPersonActSpec,
        candidate: PerceptCandidate,
        *,
        world_ref: WorldRef,
    ) -> float:
        assert spec.agent_id == "anon"
        self.calls.append(f"score:{candidate.candidate_id}")
        self.stage_log.append(f"score:{candidate.candidate_id}")
        return 3.0

    def plan(self, planning_input: PlanningInput) -> PlanDraft:
        self.calls.append("plan")
        self.stage_log.append("plan")
        self.planning_input = planning_input
        return PlanDraft(items=(PlanItem(plan_id="coffee", description="have coffee"),))

    def plan_action(self, planning_input: ActionPlanningInput) -> ProposalDraft:
        self.calls.append("plan_action")
        self.stage_log.append("plan_action")
        self.action_input = planning_input
        return self.draft


@dataclass(slots=True)
class FixedEmbeddingProvider:
    calls: list[str] = field(default_factory=lambda: list[str]())
    stage_log: list[str] = field(default_factory=lambda: list[str]())

    def embed(self, text: str) -> tuple[float, ...]:
        self.calls.append(text)
        self.stage_log.append(f"embed:{text}")
        return (1.0, 0.0)


def test_decide_runs_real_cognition_and_returns_direct_action_proposal() -> None:
    state = _state()
    memory = _memory(
        _record(
            "old-soyo",
            created_at=NOW - timedelta(hours=2),
            subject="soyo",
            predicate="likes",
            object_="coffee",
            tags=("soyo", "coffee"),
            content="Soyo likes coffee.",
            novelty_key="old:soyo",
        ),
        _record(
            "seen-ready",
            created_at=NOW - timedelta(minutes=10),
            subject="coffee-42",
            predicate="is",
            object_="ready",
            tags=("coffee",),
            content="Coffee is ready.",
            novelty_key='["ready-event",["owner","status"]]',
        ),
    )
    stage_log: list[str] = []
    strategy = FixedStrategy(
        ProposalDraft(
            action=InteractAction(
                affordance_id=AFFORDANCE_ID,
                target=CharacterTarget(id="soyo"),
                description="ask Soyo about the coffee",
            ),
            evidence_ids=("direct-event",),
        ),
        stage_log=stage_log,
    )
    embeddings = FixedEmbeddingProvider(stage_log=stage_log)
    agent = PersonActAgent(_spec(), state, memory, strategy, embeddings, world_ref=WORLD_REF)

    proposal = agent.decide(DecisionRequest(proposal_id="proposal-1", view=_view()))

    assert isinstance(proposal.action, InteractAction)
    assert proposal.agent_id == "anon"
    assert proposal.action.target == CharacterTarget(id="soyo")
    assert proposal.model_dump(by_alias=True)["action"] == {
        "kind": "interact",
        "affordanceId": AFFORDANCE_ID,
        "target": {"kind": "character", "id": "soyo"},
        "description": "ask Soyo about the coffee",
    }
    assert strategy.calls == [
        "score:self-status",
        "score:direct-soyo",
        "score:relevant-a",
        "score:relevant-b",
        "plan",
        "plan_action",
    ]
    plan_index = stage_log.index("plan")
    assert all(entry.startswith(("score:", "embed:")) for entry in stage_log[:plan_index])
    assert stage_log[-2:] == ["plan", "plan_action"]

    trace = agent.last_trace
    assert trace is not None
    assert trace.stages == ("prepare", "perceive", "retrieve", "plan", "propose")
    assert tuple(item.candidate.candidate_id for item in trace.observations) == (
        "self-status",
        "direct-soyo",
        "ready-repeat",
        "relevant-a",
        "relevant-b",
    )
    assert trace.observations[0].memory_id is not None
    assert trace.observations[0].memory_id.startswith("event-")
    assert ":" not in trace.observations[0].memory_id
    assert trace.observations[2].memory_id is None
    assert len(trace.retrieved) == 4
    assert all(context.related and context.ranked for context in trace.retrieved)
    current_memory_ids = {
        observation.memory_id
        for observation in trace.observations
        if observation.memory_id is not None
    }
    assert all(
        current_memory_ids.isdisjoint(record.id for record in (*context.related, *context.ranked))
        for context in trace.retrieved
    )
    assert len(trace.touched_memory_ids) == len(set(trace.touched_memory_ids))
    assert trace.active_plan == PlanItem(plan_id="coffee", description="have coffee")
    assert trace.focused_observation is not None
    assert trace.focused_observation.candidate.candidate_id == "direct-soyo"
    assert strategy.planning_input is not None
    assert strategy.planning_input.observations == trace.observations
    assert strategy.planning_input.retrieved == trace.retrieved
    assert strategy.planning_input.memory == agent.memory

    assert state.last_world_time is None
    assert memory.records[-1].last_accessed_at == NOW - timedelta(minutes=10)
    assert agent.state.last_world_time == NOW
    assert agent.state.last_world_version == 7
    assert agent.state.plan_queue == (PlanItem(plan_id="coffee", description="have coffee"),)
    assert agent.state.active_plan_id == "coffee"
    assert agent.state.reflection_remaining == 0.0
    assert agent.state.reflection_new_memory_count == 4
    assert tuple(record.kind for record in agent.memory.records[-4:]) == (
        MemoryKind.EVENT,
        MemoryKind.EVENT,
        MemoryKind.EVENT,
        MemoryKind.EVENT,
    )


def test_novelty_uses_immutable_entry_id_and_sorted_visible_fields() -> None:
    strategy = FixedStrategy(
        ProposalDraft(
            action=InteractAction(
                affordance_id=AFFORDANCE_ID,
                target=CharacterTarget(id="soyo"),
                description="check the changed order",
            ),
            evidence_ids=("ready-event",),
        )
    )
    agent = PersonActAgent(
        _spec(), _state(), _memory(), strategy, FixedEmbeddingProvider(), world_ref=WORLD_REF
    )
    first = _single_candidate_view(
        candidate_id="ready-first",
        revision=1,
        visible_fields=("status", "owner"),
        world_version=7,
        world_time=NOW,
    )
    repeated = _single_candidate_view(
        candidate_id="ready-repeated",
        revision=1,
        visible_fields=("owner", "status"),
        world_version=8,
        world_time=NOW + timedelta(minutes=1),
    )
    revised = _single_candidate_view(
        candidate_id="ready-revised",
        revision=2,
        visible_fields=("owner", "status"),
        world_version=9,
        world_time=NOW + timedelta(minutes=2),
    )

    agent.decide(DecisionRequest(proposal_id="proposal-1", view=first))
    count_after_first = len(agent.memory.records)
    agent.decide(DecisionRequest(proposal_id="proposal-2", view=repeated))
    repeated_trace = agent.last_trace
    assert repeated_trace is not None
    assert repeated_trace.observations[0].memory_id is None
    assert len(agent.memory.records) == count_after_first

    agent.decide(DecisionRequest(proposal_id="proposal-3", view=revised))
    revised_trace = agent.last_trace
    assert revised_trace is not None
    assert revised_trace.observations[0].memory_id is not None
    assert revised_trace.observations[0].memory_id.startswith("event-")
    assert len(agent.memory.records) == count_after_first + 1


def test_novelty_replay_is_idempotent_after_retention_window() -> None:
    strategy = FixedStrategy(
        ProposalDraft(
            action=InteractAction(
                affordance_id=AFFORDANCE_ID,
                target=CharacterTarget(id="soyo"),
                description="check the order",
            ),
            evidence_ids=("ready-event",),
        )
    )
    base = _state()
    state = PersonaState(
        world_ref=WORLD_REF,
        agent_id=base.agent_id,
        cognitive_config=CognitiveConfig(
            attention_budget=base.cognitive_config.attention_budget,
            retention=1,
            recency_weight=base.cognitive_config.recency_weight,
            relevance_weight=base.cognitive_config.relevance_weight,
            importance_weight=base.cognitive_config.importance_weight,
            recency_decay=base.cognitive_config.recency_decay,
            reflection_threshold=base.cognitive_config.reflection_threshold,
            reflection_count=base.cognitive_config.reflection_count,
        ),
        reflection_remaining=base.reflection_remaining,
    )
    memory = _memory(
        _record(
            "seen-ready",
            created_at=NOW - timedelta(minutes=2),
            subject="coffee-42",
            predicate="is",
            object_="ready",
            tags=("coffee",),
            content="Coffee is ready.",
            novelty_key='["ready-event-1",["owner","status"]]',
        ),
        _record(
            "newer-unrelated",
            created_at=NOW - timedelta(minutes=1),
            subject="door",
            predicate="is",
            object_="open",
            tags=("door",),
            content="The door is open.",
            novelty_key="unrelated",
        ),
    )
    agent = PersonActAgent(
        _spec(), state, memory, strategy, FixedEmbeddingProvider(), world_ref=WORLD_REF
    )

    agent.decide(
        DecisionRequest(
            proposal_id="replay",
            view=_single_candidate_view(
                candidate_id="ready-replayed",
                revision=1,
                visible_fields=("status", "owner", "owner"),
                world_version=7,
                world_time=NOW,
            ),
        )
    )

    trace = agent.last_trace
    assert trace is not None
    assert trace.observations[0].memory_id is None
    assert len(agent.memory.records) == 2


def test_write_policy_off_keeps_novel_observation_without_writing_memory() -> None:
    strategy = FixedStrategy(
        ProposalDraft(
            action=InteractAction(
                affordance_id=AFFORDANCE_ID,
                target=CharacterTarget(id="soyo"),
                description="notice without remembering",
            ),
            evidence_ids=("ready-event",),
        )
    )
    base = _state()
    state = PersonaState(
        world_ref=WORLD_REF,
        agent_id=base.agent_id,
        cognitive_config=base.cognitive_config,
        reflection_remaining=base.reflection_remaining,
        last_world_time=NOW - timedelta(minutes=1),
    )
    memory = _memory(
        _record(
            "historical-coffee",
            created_at=NOW - timedelta(hours=1),
            subject="coffee-42",
            predicate="was",
            object_="ordered",
            tags=("coffee",),
            content="Anon ordered coffee.",
            novelty_key="historical-coffee",
        )
    )
    agent = PersonActAgent(
        _spec(write_policy=(MemoryWritePolicy.COMMITTED_OUTCOME,)),
        state,
        memory,
        strategy,
        FixedEmbeddingProvider(),
        world_ref=WORLD_REF,
    )

    agent.decide(
        DecisionRequest(
            proposal_id="observe-only",
            view=_single_candidate_view(
                candidate_id="ready",
                revision=1,
                visible_fields=("status",),
                world_version=7,
                world_time=NOW,
            ),
        )
    )

    trace = agent.last_trace
    assert trace is not None
    assert trace.observations[0].is_novel is True
    assert trace.observations[0].memory_id is None
    assert len(trace.retrieved) == 1
    assert trace.focused_observation == trace.observations[0]
    assert trace.memory_writes == ()
    assert agent.state.reflection_remaining == state.reflection_remaining
    assert agent.state.reflection_new_memory_count == 0
    assert strategy.calls == ["plan", "plan_action"]


def test_proposal_id_replay_is_cached_and_conflicting_view_is_rejected() -> None:
    strategy = FixedStrategy(
        ProposalDraft(
            action=InteractAction(
                affordance_id=AFFORDANCE_ID,
                target=CharacterTarget(id="soyo"),
                description="talk once",
            )
        )
    )
    agent = PersonActAgent(
        _spec(), _state(), _memory(), strategy, FixedEmbeddingProvider(), world_ref=WORLD_REF
    )
    request = DecisionRequest(proposal_id="stable-id", view=_view())

    first = agent.decide(request)
    calls_after_first = tuple(strategy.calls)
    memory_after_first = agent.memory
    trace_after_first = agent.last_trace
    second = agent.decide(request)

    assert second is first
    assert tuple(strategy.calls) == calls_after_first
    assert agent.memory is memory_after_first
    assert agent.last_trace is trace_after_first

    conflicting = DecisionRequest(
        proposal_id="stable-id",
        view=_view(world_version=8, world_time=NOW + timedelta(minutes=1)),
    )
    with pytest.raises(DecisionInputError, match="reused with a different view"):
        agent.decide(conflicting)

    agent.decide(
        DecisionRequest(
            proposal_id="next-id",
            view=_view(world_version=8, world_time=NOW + timedelta(minutes=1)),
        )
    )
    calls_before_old_id = len(strategy.calls)
    memory_before_old_id = agent.memory
    with pytest.raises(DecisionInputError, match="already consumed"):
        agent.decide(
            DecisionRequest(
                proposal_id="stable-id",
                view=_view(world_version=9, world_time=NOW + timedelta(minutes=2)),
            )
        )
    assert len(strategy.calls) == calls_before_old_id
    assert agent.memory is memory_before_old_id


def test_decide_rejects_world_version_regression_without_state_change() -> None:
    strategy = FixedStrategy(
        ProposalDraft(
            action=InteractAction(
                affordance_id=AFFORDANCE_ID,
                target=CharacterTarget(id="soyo"),
                description="talk",
            )
        )
    )
    agent = PersonActAgent(
        _spec(), _state(), _memory(), strategy, FixedEmbeddingProvider(), world_ref=WORLD_REF
    )
    agent.decide(
        DecisionRequest(
            proposal_id="version-10",
            view=_view(world_version=10, world_time=NOW),
        )
    )
    agent.decide(
        DecisionRequest(
            proposal_id="version-10-again",
            view=_view(world_version=10, world_time=NOW + timedelta(minutes=1)),
        )
    )
    state_before = agent.state
    memory_before = agent.memory
    trace_before = agent.last_trace

    with pytest.raises(DecisionInputError, match="world version cannot move"):
        agent.decide(
            DecisionRequest(
                proposal_id="version-9",
                view=_view(world_version=9, world_time=NOW + timedelta(minutes=2)),
            )
        )

    assert state_before.last_world_version == 10
    assert agent.state is state_before
    assert agent.memory is memory_before
    assert agent.last_trace is trace_before


def test_failed_proposal_does_not_advance_private_state_or_memory() -> None:
    state = _state()
    memory = _memory()
    strategy = FixedStrategy(
        ProposalDraft(
            action=InteractAction(
                affordance_id=AFFORDANCE_ID,
                target=CharacterTarget(id="unknown"),
                description="reach outside the affordance",
            )
        )
    )
    agent = PersonActAgent(
        _spec(), state, memory, strategy, FixedEmbeddingProvider(), world_ref=WORLD_REF
    )
    state_before = agent.state
    memory_before = agent.memory

    with pytest.raises(ProposalValidationError, match="not afforded"):
        agent.decide(DecisionRequest(proposal_id="rejected", view=_view()))

    assert agent.state is state_before
    assert agent.memory is memory_before
    assert agent.last_trace is None


def test_agent_rejects_foreign_state_memory_and_view() -> None:
    strategy = FixedStrategy(
        ProposalDraft(
            action=InteractAction(
                affordance_id=AFFORDANCE_ID,
                target=CharacterTarget(id="soyo"),
                description="talk",
            )
        )
    )
    with pytest.raises(DecisionInputError, match="state belongs"):
        PersonActAgent(
            _spec(),
            _state(agent_id="soyo"),
            _memory(),
            strategy,
            FixedEmbeddingProvider(),
            world_ref=WORLD_REF,
        )
    with pytest.raises(DecisionInputError, match="memory belongs"):
        PersonActAgent(
            _spec(),
            _state(),
            MemoryStream(
                world_ref=WORLD_REF, agent_id="soyo", scope="project/coffee-golden/persona/soyo"
            ),
            strategy,
            FixedEmbeddingProvider(),
            world_ref=WORLD_REF,
        )

    agent = PersonActAgent(
        _spec(), _state(), _memory(), strategy, FixedEmbeddingProvider(), world_ref=WORLD_REF
    )
    view = _view(agent_id="soyo")
    with pytest.raises(DecisionInputError, match="view belongs"):
        agent.decide(DecisionRequest(proposal_id="foreign-view", view=view))


def test_decide_serializes_calls_for_one_agent() -> None:
    barrier = Barrier(3)
    strategy = FixedStrategy(
        ProposalDraft(
            action=InteractAction(
                affordance_id=AFFORDANCE_ID,
                target=CharacterTarget(id="soyo"),
                description="talk",
            )
        )
    )
    base = _state()
    state = PersonaState(
        world_ref=WORLD_REF,
        agent_id=base.agent_id,
        cognitive_config=base.cognitive_config,
        reflection_remaining=base.reflection_remaining,
        last_world_time=NOW - timedelta(minutes=1),
    )
    agent = PersonActAgent(
        _spec(), state, _memory(), strategy, FixedEmbeddingProvider(), world_ref=WORLD_REF
    )
    proposals: list[str] = []

    def decide(proposal_id: str) -> None:
        barrier.wait()
        proposal = agent.decide(
            DecisionRequest(
                proposal_id=proposal_id,
                view=_single_candidate_view(
                    candidate_id="same-candidate",
                    revision=1,
                    visible_fields=("status",),
                    world_version=7,
                    world_time=NOW,
                ),
            )
        )
        proposals.append(proposal.proposal_id)

    first = Thread(target=decide, args=("concurrent-1",))
    second = Thread(target=decide, args=("concurrent-2",))
    first.start()
    second.start()
    barrier.wait()
    first.join()
    second.join()

    assert sorted(proposals) == ["concurrent-1", "concurrent-2"]
    assert tuple(record.kind for record in agent.memory.records) == (MemoryKind.EVENT,)
    assert agent.state.reflection_new_memory_count == 1


def test_percept_entry_identity_is_one_immutable_reference() -> None:
    candidate = PerceptCandidate(
        candidate_id="bell-for-anon",
        channel=PerceptionChannel.SAME_SCENE,
        attention_tier=AttentionTier.AMBIENT,
        subject="bell",
        predicate="rings",
        content="A bell rings.",
        salience=0.5,
        source_entry_id="bell-entry",
    )

    assert candidate.source_entry_id == "bell-entry"
    with pytest.raises(ValidationError, match="Extra inputs"):
        PerceptCandidate.model_validate(
            {**candidate.model_dump(by_alias=False), "event_revision": 2},
            strict=True,
        )


@pytest.mark.parametrize(
    "foreign_ref",
    [
        WorldRef(project_id="another-project", world_id="save-001"),
        WorldRef(project_id="coffee-golden", world_id="save-002"),
    ],
)
@pytest.mark.parametrize("cached", [False, True])
def test_world_mismatch_is_rejected_before_replay_and_cognition(
    foreign_ref: WorldRef, cached: bool
) -> None:
    strategy = FixedStrategy(ProposalDraft(action=_talk_action()))
    embeddings = FixedEmbeddingProvider()
    agent = PersonActAgent(_spec(), _state(), _memory(), strategy, embeddings, world_ref=WORLD_REF)
    request = DecisionRequest(proposal_id="same-id", view=_view())
    first = agent.decide(request) if cached else None
    before = (agent.state, agent.memory, agent.last_trace)
    calls = (tuple(strategy.calls), tuple(embeddings.calls))
    with pytest.raises(DecisionInputError, match="different WorldRef"):
        agent.decide(DecisionRequest(proposal_id="same-id", view=_view(world_ref=foreign_ref)))
    assert agent.state is before[0]
    assert agent.memory is before[1]
    assert agent.last_trace is before[2]
    assert calls == (tuple(strategy.calls), tuple(embeddings.calls))
    recovered = agent.decide(request)
    assert recovered.world_ref == WORLD_REF
    if cached:
        assert recovered is first
        assert calls == (tuple(strategy.calls), tuple(embeddings.calls))


@pytest.mark.parametrize("component", ["spec", "state", "memory", "scope"])
def test_agent_constructor_rejects_mismatched_binding_without_side_effects(component: str) -> None:
    foreign = WorldRef(project_id="coffee-golden", world_id="save-002")
    spec = _spec(project_id="another-project") if component == "spec" else _spec()
    state = _state(world_ref=foreign) if component == "state" else _state()
    memory = _memory(world_ref=foreign) if component == "memory" else _memory()
    if component == "scope":
        memory = MemoryStream(world_ref=WORLD_REF, agent_id="anon", scope="another-scope")
    strategy = FixedStrategy(ProposalDraft(action=_talk_action()))
    embeddings = FixedEmbeddingProvider()
    with pytest.raises(DecisionInputError):
        PersonActAgent(spec, state, memory, strategy, embeddings, world_ref=WORLD_REF)
    assert strategy.calls == []
    assert embeddings.calls == []


@pytest.mark.parametrize("component", ["view", "state", "memory"])
@pytest.mark.parametrize(
    "foreign_ref",
    [
        WorldRef(project_id="another-project", world_id="save-001"),
        WorldRef(project_id="coffee-golden", world_id="save-002"),
    ],
)
def test_direct_loop_rejects_foreign_world_before_cognitive_nodes(
    component: str, foreign_ref: WorldRef
) -> None:
    strategy = FixedStrategy(ProposalDraft(action=_talk_action()))
    embeddings = FixedEmbeddingProvider()
    loop = PersonActLoop(
        spec=_spec(), strategy=strategy, embedding_provider=embeddings, world_ref=WORLD_REF
    )
    state = _state(world_ref=foreign_ref) if component == "state" else _state()
    memory = _memory(world_ref=foreign_ref) if component == "memory" else _memory()
    view = _view(world_ref=foreign_ref) if component == "view" else _view()
    with pytest.raises(DecisionInputError, match="different WorldRef"):
        loop.invoke(
            PersonActLoopInput(
                request=DecisionRequest(proposal_id="same-id", view=view),
                state=state,
                memory=memory,
            )
        )
    assert strategy.calls == []
    assert embeddings.calls == []
    assert state.last_world_time is None
    assert memory.records == ()


@pytest.mark.parametrize(
    "second_ref",
    [
        WorldRef(project_id="another-project", world_id="save-001"),
        WorldRef(project_id="coffee-golden", world_id="save-002"),
    ],
)
def test_independent_worlds_can_reuse_all_internal_ids(second_ref: WorldRef) -> None:
    # Deliberately share a stateless strategy: provenance must not rely on current-world globals.
    strategy = FixedStrategy(ProposalDraft(action=_talk_action()))
    first = PersonActAgent(
        _spec(), _state(), _memory(), strategy, FixedEmbeddingProvider(), world_ref=WORLD_REF
    )
    second = PersonActAgent(
        _spec(project_id=second_ref.project_id),
        _state(world_ref=second_ref),
        _memory(world_ref=second_ref),
        strategy,
        FixedEmbeddingProvider(),
        world_ref=second_ref,
    )
    proposal1 = first.decide(DecisionRequest(proposal_id="same-id", view=_view()))
    proposal2 = second.decide(
        DecisionRequest(proposal_id="same-id", view=_view(world_ref=second_ref))
    )
    assert proposal1.proposal_id == proposal2.proposal_id
    assert proposal1.event_session_id == proposal2.event_session_id
    assert proposal1.world_ref != proposal2.world_ref
    assert tuple(record.id for record in first.memory.records) == tuple(
        record.id for record in second.memory.records
    )
    for agent, ref in ((first, WORLD_REF), (second, second_ref)):
        assert agent.state.world_ref == agent.memory.world_ref == ref
        assert all(record.world_ref == ref for record in agent.memory.records)
        assert agent.last_trace is not None
        assert agent.last_trace.world_ref == ref
        assert all(record.world_ref == ref for record in agent.last_trace.memory_writes)
    assert first.memory is not second.memory


def test_private_json_reload_continues_queue_across_dates_without_replanning() -> None:
    strategy = FixedStrategy(ProposalDraft(action=_talk_action()))
    agent = PersonActAgent(
        _spec(), _state(), _memory(), strategy, FixedEmbeddingProvider(), world_ref=WORLD_REF
    )
    agent.decide(DecisionRequest(proposal_id="first", view=_view()))
    state = PersonaState.model_validate_json(agent.state.model_dump_json(), strict=True)
    memory = MemoryStream.model_validate_json(agent.memory.model_dump_json(), strict=True)
    restored_strategy = FixedStrategy(strategy.draft)
    restored = PersonActAgent(
        _spec(), state, memory, restored_strategy, FixedEmbeddingProvider(), world_ref=WORLD_REF
    )
    restored.decide(
        DecisionRequest(
            proposal_id="next", view=_view(world_time=NOW + timedelta(days=3), world_version=8)
        )
    )
    assert restored_strategy.calls == ["plan_action"]
    assert restored.state.plan_queue == state.plan_queue
    assert restored.state.active_plan_id == state.active_plan_id
    assert restored_strategy.action_input is not None
    assert restored_strategy.action_input.active_plan == state.active_plan
    assert restored.state.last_world_time == NOW + timedelta(days=3)
    # A proposal never proves that the queued task completed.
    assert len(restored.state.plan_queue) == 1


def test_existing_plan_queue_selects_head_without_copying_or_dequeuing() -> None:
    plans = (
        PlanItem(plan_id="coffee", description="get coffee"),
        PlanItem(plan_id="talk", description="talk to Soyo"),
    )
    strategy = FixedStrategy(ProposalDraft(action=_talk_action()))
    agent = PersonActAgent(
        _spec(),
        _state(plan_queue=plans),
        _memory(),
        strategy,
        FixedEmbeddingProvider(),
        world_ref=WORLD_REF,
    )
    agent.decide(DecisionRequest(proposal_id="next", view=_view()))
    assert "plan" not in strategy.calls
    assert agent.state.plan_queue == plans
    assert agent.state.active_plan_id == "coffee"
    assert agent.last_trace is not None
    assert agent.last_trace.active_plan == plans[0]


def test_invalid_plan_cannot_publish_perception_writes_or_consume_proposal_id() -> None:
    class DuplicatePlanStrategy(FixedStrategy):
        broken: bool = True

        @override
        def plan(self, planning_input: PlanningInput) -> PlanDraft:
            if not self.broken:
                return super().plan(planning_input)
            item = PlanItem(plan_id="duplicate", description="talk")
            return PlanDraft.model_construct(items=(item, item))

    draft = ProposalDraft(action=_talk_action())
    strategy = DuplicatePlanStrategy(draft)
    agent = PersonActAgent(
        _spec(), _state(), _memory(), strategy, FixedEmbeddingProvider(), world_ref=WORLD_REF
    )
    state, memory = agent.state, agent.memory
    with pytest.raises(PlannerOutputError, match="invalid PlanDraft"):
        agent.decide(DecisionRequest(proposal_id="bad-plan", view=_view()))
    assert agent.state is state
    assert agent.memory is memory
    assert agent.last_trace is None
    assert "plan_action" not in strategy.calls
    strategy.broken = False
    recovered = agent.decide(DecisionRequest(proposal_id="bad-plan", view=_view()))
    assert recovered.proposal_id == "bad-plan"
    assert agent.state.active_plan_id == "coffee"
    assert agent.memory.records


@pytest.mark.parametrize("invalid_time", [None, NOW - timedelta(seconds=1)])
def test_missing_or_regressing_world_time_has_no_effect(invalid_time: datetime | None) -> None:
    strategy = FixedStrategy(ProposalDraft(action=_talk_action()))
    embeddings = FixedEmbeddingProvider()
    agent = PersonActAgent(_spec(), _state(), _memory(), strategy, embeddings, world_ref=WORLD_REF)
    agent.decide(DecisionRequest(proposal_id="valid", view=_view()))
    snapshot = (agent.state, agent.memory, agent.last_trace)
    calls = (tuple(strategy.calls), tuple(embeddings.calls))
    invalid_view = AgentView.model_validate(
        {**_view().model_dump(by_alias=False), "world_time": invalid_time}, strict=True
    )
    with pytest.raises(DecisionInputError):
        agent.decide(DecisionRequest(proposal_id="invalid", view=invalid_view))
    assert agent.state is snapshot[0]
    assert agent.memory is snapshot[1]
    assert agent.last_trace is snapshot[2]
    assert calls == (tuple(strategy.calls), tuple(embeddings.calls))


@pytest.mark.parametrize("component", ["view_agent", "state_agent", "memory_agent", "memory_scope"])
def test_direct_loop_rejects_foreign_agent_or_scope_before_cognition(component: str) -> None:
    strategy = FixedStrategy(ProposalDraft(action=_talk_action()))
    embeddings = FixedEmbeddingProvider()
    loop = PersonActLoop(
        spec=_spec(), strategy=strategy, embedding_provider=embeddings, world_ref=WORLD_REF
    )
    state = _state(agent_id="soyo") if component == "state_agent" else _state()
    memory = _memory()
    if component == "memory_agent":
        memory = MemoryStream(world_ref=WORLD_REF, agent_id="soyo", scope=memory.scope)
    elif component == "memory_scope":
        memory = MemoryStream(world_ref=WORLD_REF, agent_id="anon", scope="outside")
    view = _view(agent_id="soyo") if component == "view_agent" else _view()
    with pytest.raises(DecisionInputError):
        loop.invoke(
            PersonActLoopInput(
                request=DecisionRequest(proposal_id="same-id", view=view),
                state=state,
                memory=memory,
            )
        )
    assert strategy.calls == []
    assert embeddings.calls == []


def test_decision_request_rejects_legacy_frame_field() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        DecisionRequest.model_validate({"proposal_id": "legacy", "frame": _view()}, strict=True)


def _spec(
    *,
    write_policy: tuple[MemoryWritePolicy, ...] | None = None,
    project_id: str = "coffee-golden",
) -> CompiledPersonActSpec:
    catalog = Catalog(
        tools=(ToolDefinition(id="visible_location.query", version="1", mode=ToolMode.QUERY),),
        prompts=(PromptDefinition(id="personact.v1", version="1", digest="prompt-v1"),),
        skills=RuntimeSkillCatalog.load(SKILLS_PATH).skills,
    )
    manifest = load_manifest(FIXTURE_PATH)
    manifest = Manifest(
        format_version=manifest.format_version,
        project_id=project_id,
        agents=manifest.agents,
    )
    spec = compile_manifest(manifest, catalog)[0]
    if write_policy is None:
        return spec
    return CompiledPersonActSpec(
        format_version=spec.format_version,
        project_id=spec.project_id,
        agent_id=spec.agent_id,
        display_name=spec.display_name,
        digest=spec.digest,
        persona=spec.persona,
        memory_scope=spec.memory_scope,
        seeds=spec.seeds,
        retrieval=spec.retrieval,
        write_policy=write_policy,
        allowed_proposal_kinds=spec.allowed_proposal_kinds,
        tools=spec.tools,
        behavior=spec.behavior,
        character_skill=spec.character_skill,
        prompt=spec.prompt,
    )


def _state(
    *,
    agent_id: str = "anon",
    world_ref: WorldRef = WORLD_REF,
    plan_queue: tuple[PlanItem, ...] = (),
    active_plan_id: str | None = None,
) -> PersonaState:
    return PersonaState(
        world_ref=world_ref,
        agent_id=agent_id,
        cognitive_config=CognitiveConfig(
            attention_budget=5,
            retention=20,
            recency_weight=1.0,
            relevance_weight=1.0,
            importance_weight=1.0,
            recency_decay=0.99,
            reflection_threshold=10.0,
            reflection_count=5,
        ),
        reflection_remaining=10.0,
        plan_queue=plan_queue,
        active_plan_id=active_plan_id,
    )


def _memory(*records: MemoryRecord, world_ref: WorldRef = WORLD_REF) -> MemoryStream:
    return MemoryStream(
        world_ref=world_ref,
        agent_id="anon",
        scope=f"project/{world_ref.project_id}/persona/anon",
        records=records,
    )


def _record(
    memory_id: str,
    *,
    created_at: datetime,
    subject: str,
    predicate: str,
    object_: str | None,
    tags: tuple[str, ...],
    content: str,
    novelty_key: str,
) -> MemoryRecord:
    return MemoryRecord(
        world_ref=WORLD_REF,
        id=memory_id,
        agent_id="anon",
        scope="project/coffee-golden/persona/anon",
        kind=MemoryKind.EVENT,
        created_at=created_at,
        last_accessed_at=created_at,
        subject=subject,
        predicate=predicate,
        object=object_,
        content=content,
        poignancy=2.0,
        tags=tags,
        source=f"fixture:{memory_id}",
        embedding=(1.0, 0.0),
        novelty_key=novelty_key,
    )


def _view(
    *,
    agent_id: str = "anon",
    world_ref: WorldRef = WORLD_REF,
    world_version: int = 7,
    world_time: datetime = NOW,
) -> AgentView:
    return AgentView(
        world_ref=world_ref,
        agent_id=agent_id,
        event_session_id="cafe",
        based_on_world_version=world_version,
        based_on_control_epoch=1,
        based_on_decision_seq=0,
        current_location_id="cafe",
        world_time=world_time,
        candidates=(
            _candidate(
                "self-status",
                AttentionTier.MANDATORY,
                PerceptionChannel.SELF,
                0.9,
                source_entry_id="self-event",
            ),
            _candidate(
                "direct-soyo",
                AttentionTier.MANDATORY,
                PerceptionChannel.DIRECT_INTERACTION,
                0.8,
                source_entry_id="direct-event",
                subject="soyo",
            ),
            _candidate(
                "ready-repeat",
                AttentionTier.MANDATORY,
                PerceptionChannel.COMMITMENT_UPDATE,
                0.7,
                source_entry_id="ready-event",
                visible_fields=("status", "owner"),
            ),
            _candidate("relevant-a", AttentionTier.RELEVANT, PerceptionChannel.SAME_SCENE, 0.9),
            _candidate("relevant-b", AttentionTier.RELEVANT, PerceptionChannel.SAME_SCENE, 0.7),
            _candidate("ambient", AttentionTier.AMBIENT, PerceptionChannel.SAME_SCENE, 1.0),
        ),
        visible_evidence_ids=("direct-event", "ready-event"),
        affordances=(
            Affordance(
                affordance_id=AFFORDANCE_ID,
                kind=ProposalKind.INTERACT,
                target=CharacterTarget(id="soyo"),
            ),
        ),
    )


def _single_candidate_view(
    *,
    candidate_id: str,
    revision: int,
    visible_fields: tuple[str, ...],
    world_version: int,
    world_time: datetime,
) -> AgentView:
    return AgentView(
        world_ref=WORLD_REF,
        agent_id="anon",
        event_session_id="cafe",
        based_on_world_version=world_version,
        based_on_control_epoch=1,
        based_on_decision_seq=0,
        current_location_id="cafe",
        world_time=world_time,
        candidates=(
            _candidate(
                candidate_id,
                AttentionTier.MANDATORY,
                PerceptionChannel.COMMITMENT_UPDATE,
                1.0,
                source_entry_id=f"ready-event-{revision}",
                visible_fields=visible_fields,
            ),
        ),
        visible_evidence_ids=("ready-event",),
        affordances=(
            Affordance(
                affordance_id=AFFORDANCE_ID,
                kind=ProposalKind.INTERACT,
                target=CharacterTarget(id="soyo"),
            ),
        ),
    )


def _candidate(
    candidate_id: str,
    tier: AttentionTier,
    channel: PerceptionChannel,
    salience: float,
    *,
    source_entry_id: str | None = None,
    subject: str = "coffee-42",
    visible_fields: tuple[str, ...] = ("status",),
) -> PerceptCandidate:
    return PerceptCandidate(
        candidate_id=candidate_id,
        channel=channel,
        attention_tier=tier,
        subject=subject,
        predicate="is",
        object="ready",
        content=f"{candidate_id} happened",
        salience=salience,
        source_entry_id=source_entry_id,
        visible_fields=visible_fields,
        tags=("coffee", subject),
    )
