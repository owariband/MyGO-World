"""Deterministic tests for the real PersonAct cognition path."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Thread

import pytest
from pydantic import ValidationError

from agent_runtime.agent.memory import MemoryKind, MemoryRecord, MemoryStream
from agent_runtime.agent.personact.agent import (
    ActionPlanningInput,
    DailyPlanDraft,
    DailyPlanningInput,
    DecisionRequest,
    PersonActAgent,
)
from agent_runtime.agent.personact.compiler import (
    Catalog,
    CompiledPersonActSpec,
    PromptDefinition,
    ToolDefinition,
    ToolMode,
    compile_manifest,
)
from agent_runtime.agent.personact.errors import DecisionInputError, ProposalValidationError
from agent_runtime.agent.personact.manifest import MemoryWritePolicy, load_manifest
from agent_runtime.agent.personact.proposal import ProposalDraft
from agent_runtime.agent.personact.state import (
    ActiveAction,
    CognitiveConfig,
    DailyPlan,
    PersonaState,
    ScheduleItem,
)
from agent_runtime.world.contracts import (
    Affordance,
    AttentionTier,
    CharacterTarget,
    InteractAction,
    PerceptCandidate,
    PerceptionChannel,
    PerceptionFrame,
    ProposalKind,
)

NOW = datetime(2026, 8, 31, 9, 5, tzinfo=UTC)
FIXTURE_PATH = Path(__file__).parents[1] / "testdata" / "npc_diy" / "agents.json"


@dataclass(slots=True)
class FixedStrategy:
    draft: ProposalDraft
    calls: list[str] = field(default_factory=lambda: list[str]())
    stage_log: list[str] = field(default_factory=lambda: list[str]())
    daily_input: DailyPlanningInput | None = None
    action_input: ActionPlanningInput | None = None

    def score_poignancy(
        self,
        spec: CompiledPersonActSpec,
        candidate: PerceptCandidate,
    ) -> float:
        assert spec.agent_id == "anon"
        self.calls.append(f"score:{candidate.candidate_id}")
        self.stage_log.append(f"score:{candidate.candidate_id}")
        return 3.0

    def plan_day(self, planning_input: DailyPlanningInput) -> DailyPlanDraft:
        assert planning_input.new_day.value == "first_day"
        self.calls.append("plan_day")
        self.stage_log.append("plan_day")
        self.daily_input = planning_input
        return DailyPlanDraft(
            intentions=("talk naturally with Soyo",),
            schedule=(
                ScheduleItem(description="morning routine", planned_duration_minutes=540),
                ScheduleItem(description="have coffee", planned_duration_minutes=60),
                ScheduleItem(description="rest", planned_duration_minutes=840),
            ),
        )

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
    state = _state(
        active_action=ActiveAction(
            subject="anon",
            predicate="waits-for",
            object="coffee-42",
            description="wait for coffee",
            started_at=NOW,
            planned_duration_minutes=30,
        )
    )
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
            novelty_key='["ready-event",1,["owner","status"]]',
        ),
    )
    stage_log: list[str] = []
    strategy = FixedStrategy(
        ProposalDraft(
            action=InteractAction(
                target=CharacterTarget(id="soyo"),
                description="ask Soyo about the coffee",
            ),
            evidence_ids=("direct-event",),
        ),
        stage_log=stage_log,
    )
    embeddings = FixedEmbeddingProvider(stage_log=stage_log)
    agent = PersonActAgent(_spec(), state, memory, strategy, embeddings)

    proposal = agent.decide(DecisionRequest(proposal_id="proposal-1", frame=_frame()))

    assert isinstance(proposal.action, InteractAction)
    assert proposal.agent_id == "anon"
    assert proposal.action.target == CharacterTarget(id="soyo")
    assert proposal.model_dump(by_alias=True)["action"] == {
        "kind": "interact",
        "target": {"kind": "character", "id": "soyo"},
        "description": "ask Soyo about the coffee",
    }
    assert strategy.calls == [
        "score:self-status",
        "score:direct-soyo",
        "score:relevant-a",
        "score:relevant-b",
        "plan_day",
        "plan_action",
    ]
    plan_day_index = stage_log.index("plan_day")
    assert all(entry.startswith(("score:", "embed:")) for entry in stage_log[:plan_day_index])
    assert stage_log[-2:] == ["plan_day", "plan_action"]

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
    assert trace.schedule_item == ScheduleItem(
        description="have coffee",
        planned_duration_minutes=60,
    )
    assert trace.schedule_remaining_minutes == 55
    assert trace.active_action_finished is False
    assert trace.focused_observation is not None
    assert trace.focused_observation.candidate.candidate_id == "direct-soyo"
    assert strategy.daily_input is not None
    assert strategy.daily_input.observations == trace.observations
    assert strategy.daily_input.retrieved == trace.retrieved
    assert strategy.daily_input.memory == agent.memory

    assert state.last_world_time is None
    assert memory.records[-1].last_accessed_at == NOW - timedelta(minutes=10)
    assert agent.state.last_world_time == NOW
    assert agent.state.last_world_version == 7
    assert agent.state.current_daily_intentions == ("talk naturally with Soyo",)
    assert agent.state.active_action == state.active_action
    assert agent.state.reflection_remaining == 0.0
    assert agent.state.reflection_new_memory_count == 4
    assert tuple(record.kind for record in agent.memory.records[-4:]) == (
        MemoryKind.EVENT,
        MemoryKind.EVENT,
        MemoryKind.EVENT,
        MemoryKind.EVENT,
    )


def test_novelty_uses_event_revision_and_sorted_visible_fields() -> None:
    strategy = FixedStrategy(
        ProposalDraft(
            action=InteractAction(
                target=CharacterTarget(id="soyo"),
                description="check the changed order",
            ),
            evidence_ids=("ready-event",),
        )
    )
    agent = PersonActAgent(_spec(), _state(), _memory(), strategy, FixedEmbeddingProvider())
    first = _single_candidate_frame(
        candidate_id="ready-first",
        revision=1,
        visible_fields=("status", "owner"),
        world_version=7,
        world_time=NOW,
    )
    repeated = _single_candidate_frame(
        candidate_id="ready-repeated",
        revision=1,
        visible_fields=("owner", "status"),
        world_version=8,
        world_time=NOW + timedelta(minutes=1),
    )
    revised = _single_candidate_frame(
        candidate_id="ready-revised",
        revision=2,
        visible_fields=("owner", "status"),
        world_version=9,
        world_time=NOW + timedelta(minutes=2),
    )

    agent.decide(DecisionRequest(proposal_id="proposal-1", frame=first))
    count_after_first = len(agent.memory.records)
    agent.decide(DecisionRequest(proposal_id="proposal-2", frame=repeated))
    repeated_trace = agent.last_trace
    assert repeated_trace is not None
    assert repeated_trace.observations[0].memory_id is None
    assert len(agent.memory.records) == count_after_first

    agent.decide(DecisionRequest(proposal_id="proposal-3", frame=revised))
    revised_trace = agent.last_trace
    assert revised_trace is not None
    assert revised_trace.observations[0].memory_id is not None
    assert revised_trace.observations[0].memory_id.startswith("event-")
    assert len(agent.memory.records) == count_after_first + 1


def test_novelty_replay_is_idempotent_after_retention_window() -> None:
    strategy = FixedStrategy(
        ProposalDraft(
            action=InteractAction(
                target=CharacterTarget(id="soyo"),
                description="check the order",
            ),
            evidence_ids=("ready-event",),
        )
    )
    base = _state()
    state = PersonaState(
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
            novelty_key='["ready-event",1,["owner","status"]]',
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
    agent = PersonActAgent(_spec(), state, memory, strategy, FixedEmbeddingProvider())

    agent.decide(
        DecisionRequest(
            proposal_id="replay",
            frame=_single_candidate_frame(
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
                target=CharacterTarget(id="soyo"),
                description="notice without remembering",
            ),
            evidence_ids=("ready-event",),
        )
    )
    base = _state()
    state = PersonaState(
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
    )

    agent.decide(
        DecisionRequest(
            proposal_id="observe-only",
            frame=_single_candidate_frame(
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
    assert strategy.calls == ["plan_action"]


def test_proposal_id_replay_is_cached_and_conflicting_frame_is_rejected() -> None:
    strategy = FixedStrategy(
        ProposalDraft(
            action=InteractAction(
                target=CharacterTarget(id="soyo"),
                description="talk once",
            )
        )
    )
    agent = PersonActAgent(_spec(), _state(), _memory(), strategy, FixedEmbeddingProvider())
    request = DecisionRequest(proposal_id="stable-id", frame=_frame())

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
        frame=_frame(world_version=8, world_time=NOW + timedelta(minutes=1)),
    )
    with pytest.raises(DecisionInputError, match="reused with a different frame"):
        agent.decide(conflicting)

    agent.decide(
        DecisionRequest(
            proposal_id="next-id",
            frame=_frame(world_version=8, world_time=NOW + timedelta(minutes=1)),
        )
    )
    calls_before_old_id = len(strategy.calls)
    memory_before_old_id = agent.memory
    with pytest.raises(DecisionInputError, match="already consumed"):
        agent.decide(
            DecisionRequest(
                proposal_id="stable-id",
                frame=_frame(world_version=9, world_time=NOW + timedelta(minutes=2)),
            )
        )
    assert len(strategy.calls) == calls_before_old_id
    assert agent.memory is memory_before_old_id


def test_decide_rejects_world_version_regression_without_state_change() -> None:
    strategy = FixedStrategy(
        ProposalDraft(
            action=InteractAction(
                target=CharacterTarget(id="soyo"),
                description="talk",
            )
        )
    )
    agent = PersonActAgent(_spec(), _state(), _memory(), strategy, FixedEmbeddingProvider())
    agent.decide(
        DecisionRequest(
            proposal_id="version-10",
            frame=_frame(world_version=10, world_time=NOW),
        )
    )
    agent.decide(
        DecisionRequest(
            proposal_id="version-10-again",
            frame=_frame(world_version=10, world_time=NOW + timedelta(minutes=1)),
        )
    )
    state_before = agent.state
    memory_before = agent.memory
    trace_before = agent.last_trace

    with pytest.raises(DecisionInputError, match="world version cannot move"):
        agent.decide(
            DecisionRequest(
                proposal_id="version-9",
                frame=_frame(world_version=9, world_time=NOW + timedelta(minutes=2)),
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
                target=CharacterTarget(id="unknown"),
                description="reach outside the affordance",
            )
        )
    )
    agent = PersonActAgent(_spec(), state, memory, strategy, FixedEmbeddingProvider())
    state_before = agent.state
    memory_before = agent.memory

    with pytest.raises(ProposalValidationError, match="not afforded"):
        agent.decide(DecisionRequest(proposal_id="rejected", frame=_frame()))

    assert agent.state is state_before
    assert agent.memory is memory_before
    assert agent.last_trace is None


def test_agent_rejects_foreign_state_memory_and_frame() -> None:
    strategy = FixedStrategy(
        ProposalDraft(
            action=InteractAction(
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
        )
    with pytest.raises(DecisionInputError, match="memory belongs"):
        PersonActAgent(
            _spec(),
            _state(),
            MemoryStream(agent_id="soyo", scope="project/coffee-golden/persona/soyo"),
            strategy,
            FixedEmbeddingProvider(),
        )

    agent = PersonActAgent(_spec(), _state(), _memory(), strategy, FixedEmbeddingProvider())
    frame = _frame(agent_id="soyo")
    with pytest.raises(DecisionInputError, match="frame belongs"):
        agent.decide(DecisionRequest(proposal_id="foreign-frame", frame=frame))


def test_daily_plan_may_be_partial_and_does_not_extend_its_last_item() -> None:
    strategy = FixedStrategy(
        ProposalDraft(
            action=InteractAction(
                target=CharacterTarget(id="soyo"),
                description="talk",
            )
        )
    )
    state = _state(
        active_action=ActiveAction(
            subject="anon",
            predicate="rests",
            description="finished",
            started_at=NOW - timedelta(minutes=2),
            planned_duration_minutes=1,
        )
    )
    state = PersonaState(
        agent_id=state.agent_id,
        cognitive_config=state.cognitive_config,
        reflection_remaining=state.reflection_remaining,
        last_world_time=NOW - timedelta(minutes=1),
        daily_plan=DailyPlan(
            for_date=NOW.date(),
            schedule=(ScheduleItem(description="morning only", planned_duration_minutes=60),),
        ),
        current_daily_intentions=(),
        active_action=state.active_action,
        reflection_new_memory_count=0,
        conversation_cooldowns=(),
        known_place_ids=(),
    )
    agent = PersonActAgent(_spec(), state, _memory(), strategy, FixedEmbeddingProvider())

    agent.decide(DecisionRequest(proposal_id="partial", frame=_frame()))

    assert strategy.action_input is not None
    assert strategy.action_input.schedule_item is None
    assert agent.state.active_action == state.active_action


def test_planning_context_uses_only_the_current_slot_remaining_minutes() -> None:
    strategy = FixedStrategy(
        ProposalDraft(
            action=InteractAction(
                target=CharacterTarget(id="soyo"),
                description="talk",
            )
        )
    )
    base = _state()
    state = PersonaState(
        agent_id=base.agent_id,
        cognitive_config=base.cognitive_config,
        reflection_remaining=base.reflection_remaining,
        last_world_time=NOW - timedelta(minutes=1),
        daily_plan=DailyPlan(
            for_date=NOW.date(),
            schedule=(
                ScheduleItem(description="morning", planned_duration_minutes=540),
                ScheduleItem(description="coffee", planned_duration_minutes=60),
            ),
        ),
    )
    agent = PersonActAgent(_spec(), state, _memory(), strategy, FixedEmbeddingProvider())

    agent.decide(DecisionRequest(proposal_id="remaining", frame=_frame()))

    assert strategy.action_input is not None
    assert strategy.action_input.schedule_item == ScheduleItem(
        description="coffee",
        planned_duration_minutes=60,
    )
    assert strategy.action_input.schedule_remaining_minutes == 55
    assert agent.state.active_action is None


def test_decide_serializes_calls_for_one_agent() -> None:
    barrier = Barrier(3)
    strategy = FixedStrategy(
        ProposalDraft(
            action=InteractAction(
                target=CharacterTarget(id="soyo"),
                description="talk",
            )
        )
    )
    base = _state()
    state = PersonaState(
        agent_id=base.agent_id,
        cognitive_config=base.cognitive_config,
        reflection_remaining=base.reflection_remaining,
        last_world_time=NOW - timedelta(minutes=1),
    )
    agent = PersonActAgent(_spec(), state, _memory(), strategy, FixedEmbeddingProvider())
    proposals: list[str] = []

    def decide(proposal_id: str) -> None:
        barrier.wait()
        proposal = agent.decide(
            DecisionRequest(
                proposal_id=proposal_id,
                frame=_single_candidate_frame(
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


def test_percept_event_identity_requires_id_and_revision_together() -> None:
    with pytest.raises(ValidationError, match="provided together"):
        PerceptCandidate(
            candidate_id="broken-event",
            channel=PerceptionChannel.SAME_SCENE,
            attention_tier=AttentionTier.AMBIENT,
            subject="bell",
            predicate="rings",
            content="A bell rings.",
            salience=0.5,
            source_event_id="bell-event",
        )


def _spec(
    *,
    write_policy: tuple[MemoryWritePolicy, ...] | None = None,
) -> CompiledPersonActSpec:
    catalog = Catalog(
        tools=(ToolDefinition(id="visible_location.query", version="1", mode=ToolMode.QUERY),),
        prompts=(PromptDefinition(id="personact.v1", version="1", digest="prompt-v1"),),
    )
    spec = compile_manifest(load_manifest(FIXTURE_PATH), catalog)[0]
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
        prompt=spec.prompt,
    )


def _state(
    *,
    agent_id: str = "anon",
    active_action: ActiveAction | None = None,
) -> PersonaState:
    return PersonaState(
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
        active_action=active_action,
    )


def _memory(*records: MemoryRecord) -> MemoryStream:
    return MemoryStream(
        agent_id="anon",
        scope="project/coffee-golden/persona/anon",
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


def _frame(
    *,
    agent_id: str = "anon",
    world_version: int = 7,
    world_time: datetime = NOW,
) -> PerceptionFrame:
    return PerceptionFrame(
        agent_id=agent_id,
        event_session_id="cafe",
        based_on_world_version=world_version,
        current_location_id="cafe",
        world_time=world_time,
        candidates=(
            _candidate(
                "self-status",
                AttentionTier.MANDATORY,
                PerceptionChannel.SELF,
                0.9,
                source_event_id="self-event",
            ),
            _candidate(
                "direct-soyo",
                AttentionTier.MANDATORY,
                PerceptionChannel.DIRECT_INTERACTION,
                0.8,
                source_event_id="direct-event",
                subject="soyo",
            ),
            _candidate(
                "ready-repeat",
                AttentionTier.MANDATORY,
                PerceptionChannel.COMMITMENT_UPDATE,
                0.7,
                source_event_id="ready-event",
                visible_fields=("status", "owner"),
            ),
            _candidate("relevant-a", AttentionTier.RELEVANT, PerceptionChannel.SAME_SCENE, 0.9),
            _candidate("relevant-b", AttentionTier.RELEVANT, PerceptionChannel.SAME_SCENE, 0.7),
            _candidate("ambient", AttentionTier.AMBIENT, PerceptionChannel.SAME_SCENE, 1.0),
        ),
        visible_evidence_ids=("direct-event", "ready-event"),
        affordances=(Affordance(kind=ProposalKind.INTERACT, target=CharacterTarget(id="soyo")),),
    )


def _single_candidate_frame(
    *,
    candidate_id: str,
    revision: int,
    visible_fields: tuple[str, ...],
    world_version: int,
    world_time: datetime,
) -> PerceptionFrame:
    return PerceptionFrame(
        agent_id="anon",
        event_session_id="cafe",
        based_on_world_version=world_version,
        current_location_id="cafe",
        world_time=world_time,
        candidates=(
            _candidate(
                candidate_id,
                AttentionTier.MANDATORY,
                PerceptionChannel.COMMITMENT_UPDATE,
                1.0,
                source_event_id="ready-event",
                revision=revision,
                visible_fields=visible_fields,
            ),
        ),
        visible_evidence_ids=("ready-event",),
        affordances=(Affordance(kind=ProposalKind.INTERACT, target=CharacterTarget(id="soyo")),),
    )


def _candidate(
    candidate_id: str,
    tier: AttentionTier,
    channel: PerceptionChannel,
    salience: float,
    *,
    source_event_id: str | None = None,
    subject: str = "coffee-42",
    revision: int = 1,
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
        source_event_id=source_event_id,
        event_revision=revision if source_event_id is not None else None,
        visible_fields=visible_fields,
        tags=("coffee", subject),
    )
