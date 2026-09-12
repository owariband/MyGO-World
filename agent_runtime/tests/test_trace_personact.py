"""Local trace wiring preserves the existing one-decision Persona lifecycle."""

import json
from pathlib import Path
from typing import Any, override

import pytest

from agent_runtime.agent.memory import MemoryStream
from agent_runtime.agent.personact.agent import DecisionRequest, PersonActAgent
from agent_runtime.agent.personact.compiler import (
    Catalog,
    CompiledPersonActSpec,
    PromptDefinition,
    ToolDefinition,
    ToolMode,
    compile_manifest,
)
from agent_runtime.agent.personact.errors import DecisionInputError, PlanningError
from agent_runtime.agent.personact.loop import (
    ActionPlanningInput,
    DecisionTrace,
    Observation,
    PersonActLoop,
    PersonActLoopInput,
)
from agent_runtime.agent.personact.manifest import load_manifest
from agent_runtime.agent.personact.proposal import ProposalDraft
from agent_runtime.agent.personact.state import CognitiveConfig, PersonaState, PlanItem
from agent_runtime.agent.skill import RuntimeSkillCatalog
from agent_runtime.tests.test_personact_agent import (
    AFFORDANCE_ID,
    FIXTURE_PATH,
    NOW,
    SKILLS_PATH,
    WORLD_REF,
    FixedEmbeddingProvider,
    FixedStrategy,
)
from agent_runtime.trace import LocalTrace, trace_scope
from agent_runtime.world.contracts import (
    ActionProposal,
    Affordance,
    AgentView,
    AttentionTier,
    CharacterTarget,
    InteractAction,
    NoOpAction,
    PerceptCandidate,
    PerceptionChannel,
    ProposalKind,
    WorldRef,
)


@pytest.mark.parametrize("debug", [False, True])
def test_success_and_replay_have_distinct_traces_without_repeating_cognition(
    tmp_path: Path, debug: bool
) -> None:
    strategy = FixedStrategy(
        ProposalDraft(
            action=InteractAction(
                affordance_id=AFFORDANCE_ID,
                target=CharacterTarget(id="soyo"),
                description="talk",
            )
        )
    )
    with LocalTrace(tmp_path, WORLD_REF, debug=debug) as log:
        agent = PersonActAgent(
            _spec(),
            _state(),
            _memory(),
            strategy,
            FixedEmbeddingProvider(),
            world_ref=WORLD_REF,
            trace_log=log,
        )
        request = DecisionRequest(proposal_id="trace-proposal", view=_view())
        proposal = agent.decide(request)
        calls = tuple(strategy.calls)
        assert agent.decide(request) is proposal
        assert tuple(strategy.calls) == calls
    rows = _rows(log.path)
    starts = [row for row in rows if row["event"] == "decision.start"]
    assert len(starts) == 2
    assert starts[0]["traceId"] != starts[1]["traceId"]
    first = [row for row in rows if row["traceId"] == starts[0]["traceId"]]
    replay = [row["event"] for row in rows if row["traceId"] == starts[1]["traceId"]]
    assert [row["event"] for row in first] == [
        "decision.start",
        "decision.input",
        "prepare.start",
        "prepare.end",
        "perceive.start",
        "perceive.end",
        "retrieve.start",
        "retrieve.end",
        "plan.start",
        "plan.end",
        "propose.start",
        "propose.end",
        "decision.result",
        "decision.end",
    ]
    assert replay == [
        "decision.start",
        "decision.input",
        "decision.replay",
        "decision.result",
        "decision.end",
    ]
    assert all(row["agentId"] == "anon" for row in rows)
    result = next(row for row in first if row["event"] == "decision.result")
    assert result["data"]["kind"] == "interact"
    assert ("content" in result["data"]) is debug
    if debug:
        assert result["data"]["content"]["proposal"]["proposalId"] == proposal.proposal_id
        assert "decisionTrace" in result["data"]["content"]
    else:
        assert "have coffee" not in log.path.read_text()
        assert all("content" not in row["data"] for row in rows)


def test_failure_keeps_last_stage_and_does_not_publish_private_snapshot(tmp_path: Path) -> None:
    class FailingStrategy(FixedStrategy):
        @override
        def plan_action(self, planning_input: ActionPlanningInput) -> ProposalDraft:
            del planning_input
            raise RuntimeError("private-prompt-secret")

    strategy = FailingStrategy(ProposalDraft(action=NoOpAction(next_wakeup="event_change")))
    with LocalTrace(tmp_path, WORLD_REF, debug=True) as log:
        agent = PersonActAgent(
            _spec(),
            _state(),
            _memory(),
            strategy,
            FixedEmbeddingProvider(),
            world_ref=WORLD_REF,
            trace_log=log,
        )
        state, memory = agent.state, agent.memory
        with pytest.raises(PlanningError):
            agent.decide(DecisionRequest(proposal_id="failure", view=_view()))
        assert agent.state is state
        assert agent.memory is memory
        assert agent.last_trace is None
    events = [row["event"] for row in _rows(log.path)]
    assert "plan.start" in events
    assert "plan.end" not in events
    assert "decision.error" in events
    assert "decision.result" not in events
    assert "private-prompt-secret" not in log.path.read_text()


def test_foreign_world_never_writes_unvalidated_view_into_bound_log(tmp_path: Path) -> None:
    foreign = WorldRef(project_id=WORLD_REF.project_id, world_id="foreign-world")
    strategy = FixedStrategy(ProposalDraft(action=NoOpAction(next_wakeup="event_change")))
    with LocalTrace(tmp_path, WORLD_REF, debug=True) as log:
        with pytest.raises(DecisionInputError, match="trace log"):
            PersonActAgent(
                _spec(),
                _state(world_ref=foreign),
                _memory(world_ref=foreign),
                strategy,
                FixedEmbeddingProvider(),
                world_ref=foreign,
                trace_log=log,
            )
        agent = PersonActAgent(
            _spec(),
            _state(),
            _memory(),
            strategy,
            FixedEmbeddingProvider(),
            world_ref=WORLD_REF,
            trace_log=log,
        )
        with pytest.raises(DecisionInputError, match="different WorldRef"):
            agent.decide(DecisionRequest(proposal_id="foreign", view=_view(world_ref=foreign)))
    events = [row["event"] for row in _rows(log.path)]
    assert "decision.start" in events
    assert "decision.error" in events
    assert "decision.input" not in events
    assert "prepare.start" not in events
    assert "foreign-world" not in log.path.read_text()
    assert strategy.calls == []


def test_two_actors_share_file_but_not_decision_trace_identity(tmp_path: Path) -> None:
    specs = compile_manifest(load_manifest(FIXTURE_PATH), _catalog())
    with LocalTrace(tmp_path, WORLD_REF) as log:
        for spec in specs:
            strategy = FixedStrategy(ProposalDraft(action=NoOpAction(next_wakeup="event_change")))
            agent = PersonActAgent(
                spec,
                _state(agent_id=spec.agent_id),
                MemoryStream(world_ref=WORLD_REF, agent_id=spec.agent_id, scope=spec.memory_scope),
                strategy,
                FixedEmbeddingProvider(),
                world_ref=WORLD_REF,
                trace_log=log,
            )
            view = AgentView.model_validate(
                {**_view(agent_id=spec.agent_id).model_dump(by_alias=False), "candidates": ()},
                strict=True,
            )
            agent.decide(DecisionRequest(proposal_id="same-proposal", view=view))
    rows = _rows(log.path)
    trace_ids = {row["traceId"] for row in rows}
    assert len(trace_ids) == 2
    assert {row["agentId"] for row in rows} == {"anon", "soyo"}
    for trace_id in trace_ids:
        assert len({row["agentId"] for row in rows if row["traceId"] == trace_id}) == 1


@pytest.mark.parametrize("wrong_world", [False, True])
def test_direct_loop_skips_all_records_in_a_foreign_trace_context(
    tmp_path: Path, wrong_world: bool
) -> None:
    trace_ref = (
        WorldRef(project_id=WORLD_REF.project_id, world_id="foreign") if wrong_world else WORLD_REF
    )
    strategy = FixedStrategy(ProposalDraft(action=NoOpAction(next_wakeup="event_change")))
    loop = PersonActLoop(
        spec=_spec(),
        strategy=strategy,
        embedding_provider=FixedEmbeddingProvider(),
        world_ref=WORLD_REF,
    )
    with (
        LocalTrace(tmp_path, trace_ref, debug=True) as log,
        trace_scope(
            log,
            world_ref=trace_ref,
            agent_kind="character",
            agent_id="anon" if wrong_world else "soyo",
        ),
    ):
        loop.invoke(
            PersonActLoopInput(
                request=DecisionRequest(proposal_id="unrelated", view=_view()),
                state=_state(),
                memory=_memory(),
            )
        )
    assert [row["event"] for row in _rows(log.path)] == ["decision.start", "decision.end"]


@pytest.mark.parametrize("enable_log", [False, True])
def test_private_debug_payloads_are_not_serialized_without_debug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enable_log: bool
) -> None:
    def unexpected_dump(*_: object, **__: object) -> dict[str, object]:
        raise AssertionError("disabled debug must not serialize private trace payloads")

    for model in (ActionProposal, DecisionTrace, Observation, PlanItem):
        monkeypatch.setattr(model, "model_dump", unexpected_dump)
    strategy = FixedStrategy(ProposalDraft(action=NoOpAction(next_wakeup="event_change")))
    with LocalTrace(tmp_path, WORLD_REF) as log:
        agent = PersonActAgent(
            _spec(),
            _state(),
            _memory(),
            strategy,
            FixedEmbeddingProvider(),
            world_ref=WORLD_REF,
            trace_log=log if enable_log else None,
        )
        request = DecisionRequest(proposal_id="metadata-only", view=_view())
        proposal = agent.decide(request)
        assert agent.decide(request) is proposal


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _catalog() -> Catalog:
    return Catalog(
        tools=(ToolDefinition(id="visible_location.query", version="1", mode=ToolMode.QUERY),),
        prompts=(PromptDefinition(id="personact.v1", version="1", digest="prompt-v1"),),
        skills=RuntimeSkillCatalog.load(SKILLS_PATH).skills,
    )


def _spec() -> CompiledPersonActSpec:
    return compile_manifest(load_manifest(FIXTURE_PATH), _catalog())[0]


def _state(*, agent_id: str = "anon", world_ref: WorldRef = WORLD_REF) -> PersonaState:
    return PersonaState(
        world_ref=world_ref,
        agent_id=agent_id,
        cognitive_config=CognitiveConfig(
            attention_budget=1,
            retention=5,
            recency_weight=1.0,
            relevance_weight=1.0,
            importance_weight=1.0,
            recency_decay=0.99,
            reflection_threshold=10.0,
            reflection_count=5,
        ),
        reflection_remaining=10.0,
    )


def _memory(*, world_ref: WorldRef = WORLD_REF) -> MemoryStream:
    return MemoryStream(
        world_ref=world_ref, agent_id="anon", scope="project/coffee-golden/persona/anon"
    )


def _view(*, agent_id: str = "anon", world_ref: WorldRef = WORLD_REF) -> AgentView:
    return AgentView(
        world_ref=world_ref,
        agent_id=agent_id,
        event_session_id="cafe",
        based_on_world_version=1,
        based_on_control_epoch=1,
        based_on_decision_seq=0,
        current_location_id="cafe",
        world_time=NOW,
        affordances=(
            Affordance(
                affordance_id=AFFORDANCE_ID,
                kind=ProposalKind.INTERACT,
                target=CharacterTarget(id="soyo"),
                operation_id="join_target_session",
            ),
        ),
        candidates=(
            PerceptCandidate(
                candidate_id="soyo-visible",
                channel=PerceptionChannel.DIRECT_INTERACTION,
                attention_tier=AttentionTier.MANDATORY,
                subject="soyo",
                predicate="speaks",
                content="private-soyo-greeting",
                salience=1.0,
            ),
        ),
    )
