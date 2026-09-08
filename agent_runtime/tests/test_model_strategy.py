"""Contract tests for the MVP-derived Skill, Gateway, and model strategy seam."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, cast, override

import pytest
from langchain_core.callbacks.manager import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import ValidationError

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
from agent_runtime.agent.personact.errors import PlannerOutputError
from agent_runtime.agent.personact.loop import (
    ActionPlanningInput,
    DailyPlanDraft,
    DailyPlanningInput,
)
from agent_runtime.agent.personact.manifest import load_manifest
from agent_runtime.agent.personact.model_strategy import ModelCognitionStrategy
from agent_runtime.agent.personact.state import CognitiveConfig, NewDayStatus, PersonaState
from agent_runtime.agent.skill import RuntimeSkill, RuntimeSkillCatalog, load_runtime_skill
from agent_runtime.model import StrictModel
from agent_runtime.model_gateway import (
    FixtureModelGateway,
    LangChainModelGateway,
    ModelRequest,
    ModelTransportError,
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

FIXTURE_PATH = Path(__file__).parents[1] / "testdata" / "npc_diy" / "agents.json"
SKILLS_PATH = Path(__file__).parents[2] / "content" / "skills"


@dataclass(slots=True)
class SequentialIdGenerator:
    value: int = 0
    values: list[str] = field(default_factory=lambda: list[str]())

    def __call__(self) -> str:
        self.value += 1
        identifier = f"model-call-{self.value}"
        self.values.append(identifier)
        return identifier


class _Answer(StrictModel):
    value: str


class FixedEmbeddingProvider:
    def embed(self, text: str) -> tuple[float, ...]:
        del text
        return (1.0, 0.0)


class FakeStructuredChatModel(BaseChatModel):
    responses: tuple[object, ...]
    index: int = 0
    structured_calls: int = 0

    @property
    @override
    def _llm_type(self) -> str:
        return "fake-structured"

    @override
    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: object,
    ) -> ChatResult:
        del messages, stop, run_manager, kwargs
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="unused"))])

    @override
    def with_structured_output(
        self,
        schema: dict[str, Any] | type,
        *,
        include_raw: bool = False,
        **kwargs: object,
    ) -> Runnable[object, dict[str, Any] | StrictModel]:
        del include_raw, kwargs
        response_type = cast(type[StrictModel], schema)

        def invoke(_input: object) -> StrictModel:
            self.structured_calls += 1
            response = self.responses[self.index]
            self.index += 1
            if isinstance(response, BaseException):
                raise response
            return response_type.model_validate(response, strict=True)

        return RunnableLambda(invoke)


def test_runtime_skill_is_strict_versioned_and_hashed(tmp_path: Path) -> None:
    skill_path = tmp_path / "anon.md"
    skill_path.write_text(
        "---\nskill_id: mygo.character.anon\nversion: 3.0.0\n"
        "agent_kind: character\n---\n# 千早爱音\n\n主动推动交流。\n",
        encoding="utf-8",
    )

    skill = load_runtime_skill(skill_path)
    catalog = RuntimeSkillCatalog(skills=(skill,))

    assert catalog.resolve("mygo.character.anon", "3.0.0", agent_kind="character") == skill
    assert len(skill.content_hash) == 64
    assert skill.body == "# 千早爱音\n\n主动推动交流。"

    skill_path.write_text(
        "---\nskill_id: mygo.character.anon\nversion: 3.0.0\n"
        "agent_kind: character\nmodel: forbidden\n---\nBody.\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="metadata is invalid"):
        load_runtime_skill(skill_path)


def test_langchain_gateway_retries_only_configured_transport_errors() -> None:
    model = FakeStructuredChatModel(responses=(TimeoutError(), {"value": "ok"}))
    gateway = LangChainModelGateway(model, model_id="fake-model")

    generation = gateway.generate(_model_request(), _Answer)

    assert generation.structured == _Answer(value="ok")
    assert generation.trace.transport_attempts == 2
    assert model.structured_calls == 2


def test_langchain_gateway_rejects_model_override_before_call() -> None:
    model = FakeStructuredChatModel(responses=({"value": "ok"},))
    gateway = LangChainModelGateway(model, model_id="configured-model")

    with pytest.raises(RuntimeError, match="override"):
        gateway.generate(_model_request(model_id="other-model"), _Answer)

    assert model.structured_calls == 0


def test_langchain_gateway_traces_final_transport_failure() -> None:
    model = FakeStructuredChatModel(responses=(TimeoutError(), TimeoutError()))
    gateway = LangChainModelGateway(
        model,
        model_id="fake-model",
        max_transport_attempts=2,
    )

    with pytest.raises(ModelTransportError) as caught:
        gateway.generate(_model_request(), _Answer)

    assert caught.value.trace.status == "transport_failed"
    assert caught.value.trace.transport_attempts == 2
    assert caught.value.trace.output_hash is None
    assert model.structured_calls == 2


def test_model_strategy_retains_final_transport_failure_trace() -> None:
    model = FakeStructuredChatModel(responses=(TimeoutError(),))
    strategy = ModelCognitionStrategy(
        gateway=LangChainModelGateway(
            model,
            model_id="fake-model",
            max_transport_attempts=1,
        ),
        skill=_bound_skill(),
        model_id="fake-model",
        call_id_generator=SequentialIdGenerator(),
    )

    with pytest.raises(ModelTransportError):
        strategy.plan_day(_daily_planning_input())

    assert tuple(trace.status for trace in strategy.traces) == ("transport_failed",)


def test_model_strategy_repairs_semantically_invalid_action_once(tmp_path: Path) -> None:
    del tmp_path
    skill = _bound_skill()
    first = (
        '{"action":{"kind":"interact","target":'
        '{"kind":"character","id":"unknown"},'
        '"description":"talk"},"evidenceIds":[]}'
    )
    second = (
        '{"action":{"kind":"interact","target":'
        '{"kind":"character","id":"soyo"},'
        '"description":"talk"},"evidenceIds":["soyo-visible"]}'
    )
    gateway = FixtureModelGateway((first, second))
    ids = SequentialIdGenerator()
    strategy = ModelCognitionStrategy(
        gateway=gateway,
        skill=skill,
        model_id="fixture-model",
        call_id_generator=ids,
    )

    draft = strategy.plan_action(_planning_input())

    assert isinstance(draft.action, InteractAction)
    assert draft.action.target == CharacterTarget(id="soyo")
    assert tuple(request.call_kind for request in gateway.requests) == (
        "plan_action",
        "plan_action_repair",
    )
    assert gateway.requests[1].repair_diagnostic is not None
    assert tuple(trace.status for trace in strategy.traces) == (
        "semantic_rejected",
        "succeeded",
    )
    assert all(trace.skill_content_hash == skill.content_hash for trace in strategy.traces)
    assert all("talk" not in repr(trace) for trace in strategy.traces)


def test_personact_loop_uses_model_strategy_without_moving_world() -> None:
    gateway = FixtureModelGateway(
        (
            '{"score":3.0}',
            '{"intentions":["talk naturally"],"schedule":[]}',
            (
                '{"action":{"kind":"interact","target":'
                '{"kind":"character","id":"soyo"},'
                '"description":"offer the menu"},'
                '"evidenceIds":["soyo-visible"]}'
            ),
        )
    )
    strategy = ModelCognitionStrategy(
        gateway=gateway,
        skill=_bound_skill(),
        model_id="fixture-model",
        call_id_generator=SequentialIdGenerator(),
    )
    spec = _spec()
    agent = PersonActAgent(
        spec=spec,
        state=_state(),
        memory=MemoryStream(agent_id="anon", scope=spec.memory_scope),
        strategy=strategy,
        embedding_provider=FixedEmbeddingProvider(),
    )
    frame = PerceptionFrame(
        agent_id="anon",
        event_session_id="cafe",
        based_on_world_version=7,
        current_location_id="cafe",
        world_time=datetime(2026, 9, 1, 9, tzinfo=UTC),
        candidates=(_poignancy_candidate(),),
        visible_evidence_ids=("soyo-visible",),
        affordances=(
            Affordance(
                kind=ProposalKind.INTERACT,
                target=CharacterTarget(id="soyo"),
            ),
        ),
    )

    proposal = agent.decide(DecisionRequest(proposal_id="proposal-1", frame=frame))

    assert proposal.agent_id == "anon"
    assert proposal.event_session_id == "cafe"
    assert isinstance(proposal.action, InteractAction)
    assert tuple(request.call_kind for request in gateway.requests) == (
        "score_poignancy",
        "plan_day",
        "plan_action",
    )
    assert agent.last_trace is not None
    assert agent.last_trace.stages == ("prepare", "perceive", "retrieve", "plan", "propose")


def test_model_strategy_repairs_schema_failure_and_then_fails(tmp_path: Path) -> None:
    del tmp_path
    gateway = FixtureModelGateway(("{}", "{}"))
    strategy = ModelCognitionStrategy(
        gateway=gateway,
        skill=_bound_skill(),
        model_id="fixture-model",
        call_id_generator=SequentialIdGenerator(),
    )

    with pytest.raises(PlannerOutputError, match="after one repair"):
        strategy.plan_day(_daily_planning_input())

    assert tuple(request.call_kind for request in gateway.requests) == (
        "plan_day",
        "plan_day_repair",
    )
    assert tuple(trace.status for trace in strategy.traces) == (
        "invalid_output",
        "invalid_output",
    )
    assert gateway.requests[1].repair_diagnostic is not None
    assert "intentions:missing" in gateway.requests[1].repair_diagnostic
    assert "schedule:missing" in gateway.requests[1].repair_diagnostic


def test_model_strategy_trace_retention_is_bounded() -> None:
    gateway = FixtureModelGateway(
        (
            '{"score":1.0}',
            '{"score":2.0}',
            '{"score":3.0}',
        )
    )
    strategy = ModelCognitionStrategy(
        gateway=gateway,
        skill=_bound_skill(),
        model_id="fixture-model",
        call_id_generator=SequentialIdGenerator(),
        trace_retention=2,
    )
    spec = _spec()
    percept = _poignancy_candidate()
    strategy.score_poignancy(spec, percept)
    strategy.score_poignancy(spec, percept)
    strategy.score_poignancy(spec, percept)

    assert tuple(trace.call_id for trace in strategy.traces) == (
        "model-call-2",
        "model-call-3",
    )


def test_model_strategy_rejects_changed_pinned_skill_hash(tmp_path: Path) -> None:
    del tmp_path
    skill = _bound_skill()
    with pytest.raises(ValidationError, match="fields do not match"):
        RuntimeSkill(
            skill_id=skill.skill_id,
            version=skill.version,
            agent_kind=skill.agent_kind,
            body=skill.body + " changed",
            content_hash=skill.content_hash,
            source_path=skill.source_path,
            source_text=skill.source_text,
        )

    changed_body = skill.body + " changed"
    changed_source = skill.source_text.replace(skill.body, changed_body)
    changed_skill = RuntimeSkill(
        skill_id=skill.skill_id,
        version=skill.version,
        agent_kind=skill.agent_kind,
        body=changed_body,
        content_hash=sha256(changed_source.encode("utf-8")).hexdigest(),
        source_path=skill.source_path,
        source_text=changed_source,
    )
    strategy = ModelCognitionStrategy(
        gateway=FixtureModelGateway(
            (DailyPlanDraft(intentions=("talk",), schedule=()).model_dump_json(),)
        ),
        skill=changed_skill,
        model_id="fixture-model",
        call_id_generator=SequentialIdGenerator(),
    )

    with pytest.raises(ValueError, match="content hash"):
        strategy.plan_day(_daily_planning_input())


def _bound_skill() -> RuntimeSkill:
    return RuntimeSkillCatalog.load(SKILLS_PATH).resolve(
        "mygo.character.anon",
        "3.0.0",
        agent_kind="character",
    )


def _model_request(*, model_id: str = "fake-model") -> ModelRequest:
    return ModelRequest(
        call_id="call-1",
        agent_kind="character",
        agent_id="anon",
        call_kind="plan_action",
        model_id=model_id,
        prompt_id="personact.v1",
        prompt_version="1",
        prompt_digest="prompt-v1",
        skill_id="mygo.character.anon",
        skill_version="3.0.0",
        skill_content_hash="a" * 64,
        system_prompt="Follow the Character Skill.",
        input_json='{"frame":"visible"}',
    )


def _planning_input() -> ActionPlanningInput:
    spec = _spec()
    frame = PerceptionFrame(
        agent_id="anon",
        event_session_id="cafe",
        based_on_world_version=7,
        current_location_id="cafe",
        visible_evidence_ids=("soyo-visible",),
        affordances=(
            Affordance(
                kind=ProposalKind.INTERACT,
                target=CharacterTarget(id="soyo"),
            ),
        ),
    )
    return ActionPlanningInput(
        spec=spec,
        frame=frame,
        state=_state(),
        observations=(),
        retrieved=(),
        active_action_finished=True,
    )


def _poignancy_candidate() -> PerceptCandidate:
    return PerceptCandidate(
        candidate_id="candidate-1",
        channel=PerceptionChannel.SAME_SCENE,
        attention_tier=AttentionTier.RELEVANT,
        subject="soyo",
        predicate="speaks",
        content="Soyo says hello.",
        salience=0.5,
    )


def _daily_planning_input() -> DailyPlanningInput:
    spec = _spec()
    return DailyPlanningInput(
        spec=spec,
        state=_state(),
        memory=MemoryStream(agent_id="anon", scope=spec.memory_scope),
        world_time=datetime(2026, 9, 1, 9, tzinfo=UTC),
        new_day=NewDayStatus.FIRST_DAY,
        observations=(),
        retrieved=(),
    )


def _state() -> PersonaState:
    return PersonaState(
        agent_id="anon",
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
    )


def _spec() -> CompiledPersonActSpec:
    skills = RuntimeSkillCatalog.load(SKILLS_PATH).skills
    catalog = Catalog(
        tools=(ToolDefinition(id="visible_location.query", version="1", mode=ToolMode.QUERY),),
        prompts=(PromptDefinition(id="personact.v1", version="1", digest="prompt-v1"),),
        skills=skills,
    )
    return compile_manifest(load_manifest(FIXTURE_PATH), catalog)[0]
