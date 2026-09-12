"""Contract tests for the MVP-derived Skill, Gateway, and model strategy seam."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, cast, override

import pytest
from langchain_core.callbacks.manager import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.base import LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field, ValidationError

from agent_runtime.agent.memory import MemoryKind, MemoryRecord, MemoryStream
from agent_runtime.agent.personact.agent import DecisionRequest, PersonActAgent
from agent_runtime.agent.personact.compiler import (
    Catalog,
    CompiledPersonActSpec,
    PromptDefinition,
    ToolDefinition,
    ToolMode,
    compile_manifest,
)
from agent_runtime.agent.personact.errors import DecisionInputError, PlannerOutputError
from agent_runtime.agent.personact.loop import (
    ActionPlanningInput,
    Observation,
    PlanDraft,
    PlanningInput,
    RetrievedContext,
)
from agent_runtime.agent.personact.manifest import load_manifest
from agent_runtime.agent.personact.model_strategy import ModelCognitionStrategy
from agent_runtime.agent.personact.proposal import ProposalDraft
from agent_runtime.agent.personact.state import CognitiveConfig, PersonaState, PlanItem
from agent_runtime.agent.skill import RuntimeSkill, RuntimeSkillCatalog, load_runtime_skill
from agent_runtime.model import StrictModel
from agent_runtime.model_gateway import (
    FixtureModelGateway,
    LangChainModelGateway,
    ModelOutputInvalidError,
    ModelRequest,
    ModelRequestRejectedError,
    ModelTransportError,
)
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


class _NestedTuple(StrictModel):
    values: tuple[int, ...]


class _TupleAnswer(StrictModel):
    groups: tuple[_NestedTuple, ...]


class _MappingAnswer(StrictModel):
    values: dict[str, int]


class FixedEmbeddingProvider:
    def embed(self, text: str) -> tuple[float, ...]:
        del text
        return (1.0, 0.0)


class FakeStructuredChatModel(BaseChatModel):
    """Fake only the provider transport; use LangChain's real structured parser."""

    responses: tuple[object, ...]
    index: int = 0
    structured_calls: int = 0
    schemas: list[dict[str, Any] | type] = Field(
        default_factory=lambda: list[dict[str, Any] | type]()
    )

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
        del messages, stop, run_manager
        self.structured_calls += 1
        response = self.responses[self.index]
        self.index += 1
        if isinstance(response, BaseException):
            raise response
        if isinstance(response, AIMessage):
            message = response
        else:
            arguments = response if isinstance(response, str) else json.dumps(response)
            message = AIMessage(
                content="",
                additional_kwargs={
                    "tool_calls": [
                        {
                            "id": "tool-result-1",
                            "type": "function",
                            "function": {"name": kwargs["tool_name"], "arguments": arguments},
                        }
                    ]
                },
            )
        return ChatResult(generations=[ChatGeneration(message=message)])

    @override
    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: object,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        del tool_choice, kwargs
        self.schemas.append(cast(dict[str, Any] | type, tools[0]))
        tool_name = convert_to_openai_tool(tools[0])["function"]["name"]
        return self.bind(tool_name=tool_name)


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


@pytest.mark.parametrize(
    "payload, expected",
    [
        ({"groups": list[dict[str, object]]()}, ()),
        ({"groups": [{"values": list[int]()}]}, ((),)),
        ({"groups": [{"values": [1, 2]}, {"values": [3]}]}, ((1, 2), (3,))),
    ],
)
def test_gateway_strict_json_accepts_nested_tuples_through_standard_parser(
    payload: dict[str, object], expected: tuple[tuple[int, ...], ...]
) -> None:
    model = FakeStructuredChatModel(responses=(payload,))
    generation = LangChainModelGateway(model, model_id="fake-model").generate(
        _model_request(), _TupleAnswer
    )

    assert tuple(group.values for group in generation.structured.groups) == expected
    assert isinstance(model.schemas[0], dict)
    assert FakeStructuredChatModel.with_structured_output is BaseChatModel.with_structured_output
    assert generation.trace.world_ref == _world_ref()
    with pytest.raises(ValidationError, match="frozen_instance"):
        generation.structured.groups = ()


def test_standard_pydantic_parser_reproduces_the_old_tuple_failure() -> None:
    model = FakeStructuredChatModel(responses=({"groups": [{"values": [1]}]},))
    with pytest.raises(ValidationError, match="tuple_type"):
        model.with_structured_output(_TupleAnswer).invoke("visible context")


def test_gateway_parses_real_proposal_union_and_evidence_array() -> None:
    model = FakeStructuredChatModel(
        responses=(
            {
                "action": {
                    "kind": "interact",
                    "affordanceId": "interact-soyo",
                    "target": {"kind": "character", "id": "soyo"},
                    "description": "offer the menu",
                },
                "evidenceIds": ["soyo-visible"],
            },
        )
    )
    result = LangChainModelGateway(model, model_id="fake-model").generate(
        _model_request(), ProposalDraft
    )
    assert isinstance(result.structured.action, InteractAction)
    assert result.structured.evidence_ids == ("soyo-visible",)
    assert result.trace.status == "succeeded"


@pytest.mark.parametrize(
    "payload",
    [
        {"groups": [], "extra": "secret-output"},
        {"groups": [{"values": ["1"]}]},
        {"groups": [{"values": [True]}]},
        {"groups": "secret-output"},
    ],
)
def test_gateway_keeps_strict_types_and_unknown_field_rejection(
    payload: dict[str, object],
) -> None:
    model = FakeStructuredChatModel(responses=(payload,))
    with pytest.raises(ModelOutputInvalidError) as caught:
        LangChainModelGateway(model, model_id="fake-model").generate(_model_request(), _TupleAnswer)
    assert model.structured_calls == 1
    assert caught.value.trace.status == "invalid_output"
    assert caught.value.trace.world_ref == _world_ref()
    assert "secret-output" not in repr(caught.value.trace)


@pytest.mark.parametrize(
    "response_type, payload, error_kind",
    [
        (_Answer, {"value": "ok", "private-story-secret": True}, "extra_forbidden"),
        (
            _MappingAnswer,
            {"values": {"private-story-secret": "private-story-value"}},
            "int_type",
        ),
    ],
)
def test_validation_diagnostics_never_expose_model_authored_error_locations(
    response_type: type[StrictModel], payload: dict[str, object], error_kind: str
) -> None:
    gateway = LangChainModelGateway(
        FakeStructuredChatModel(responses=(payload,)), model_id="fake-model"
    )
    with pytest.raises(ModelOutputInvalidError) as caught:
        gateway.generate(_model_request(), response_type)
    assert error_kind in caught.value.diagnostic
    assert "private-story" not in caught.value.diagnostic
    assert "private-story" not in caught.value.trace.model_dump_json()


def test_validation_diagnostics_have_a_bounded_number_of_error_details() -> None:
    gateway = LangChainModelGateway(
        FakeStructuredChatModel(
            responses=({"values": {f"private-story-{index}": "bad" for index in range(20)}},)
        ),
        model_id="fake-model",
    )
    with pytest.raises(ModelOutputInvalidError) as caught:
        gateway.generate(_model_request(), _MappingAnswer)
    assert caught.value.diagnostic.count("int_type") == 6
    assert "14 additional errors" in caught.value.diagnostic
    assert "private-story" not in caught.value.trace.model_dump_json()


@pytest.mark.parametrize(
    "response",
    [
        '{"value":"secret-output",',
        AIMessage(content="no structured result"),
        AIMessage(content="", tool_calls=[{"name": "wrong_tool", "args": {}, "id": "x"}]),
        {"value": float("nan")},
        {"value": float("inf")},
        AIMessage(
            content="",
            tool_calls=[{"name": "_Answer", "args": {"value": object()}, "id": "x"}],
        ),
    ],
)
def test_gateway_rejects_bad_missing_or_non_json_results_without_transport_retry(
    response: object,
) -> None:
    model = FakeStructuredChatModel(responses=(response,))
    with pytest.raises(ModelOutputInvalidError) as caught:
        LangChainModelGateway(model, model_id="fake-model").generate(_model_request(), _Answer)
    assert model.structured_calls == 1
    assert caught.value.trace.status == "invalid_output"
    assert caught.value.trace.world_ref == _world_ref()
    assert "secret-output" not in repr(caught.value.trace)


def test_gateway_does_not_treat_sdk_configuration_errors_as_model_output() -> None:
    model = FakeStructuredChatModel(responses=(ValueError("invalid SDK configuration"),))
    with pytest.raises(ValueError, match="SDK configuration"):
        LangChainModelGateway(model, model_id="fake-model").generate(_model_request(), _Answer)
    assert model.structured_calls == 1


def test_gateway_reports_unsupported_structured_output_as_request_rejection() -> None:
    model = FakeStructuredChatModel(responses=(NotImplementedError(),))
    with pytest.raises(ModelRequestRejectedError, match="does not support structured output"):
        LangChainModelGateway(model, model_id="fake-model").generate(_model_request(), _Answer)
    assert model.structured_calls == 1


@pytest.mark.parametrize("response", [{"groups": [1]}, {"groups": float("nan")}])
def test_output_failure_cannot_be_reclassified_by_broad_transport_configuration(
    response: dict[str, object],
) -> None:
    model = FakeStructuredChatModel(responses=(response,))
    with pytest.raises(ModelOutputInvalidError):
        LangChainModelGateway(
            model, model_id="fake-model", retryable_error_types=(Exception,)
        ).generate(_model_request(), _TupleAnswer)
    assert model.structured_calls == 1


@pytest.mark.parametrize("identity_key", ["worldRef", "agentId", "eventSessionId"])
def test_model_proposal_cannot_author_runtime_identity(identity_key: str) -> None:
    model = FakeStructuredChatModel(
        responses=(
            {
                "action": {"kind": "act", "description": "listen"},
                "evidenceIds": [],
                identity_key: "forged",
            },
        )
    )
    with pytest.raises(ModelOutputInvalidError):
        LangChainModelGateway(model, model_id="fake-model").generate(
            _model_request(), ProposalDraft
        )
    assert model.structured_calls == 1


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
        strategy.plan(_queue_planning_input())

    assert tuple(trace.status for trace in strategy.traces) == ("transport_failed",)
    assert strategy.traces[0].world_ref == _world_ref()


def test_model_strategy_repairs_semantically_invalid_action_once(tmp_path: Path) -> None:
    del tmp_path
    skill = _bound_skill()
    first = (
        '{"action":{"kind":"interact","affordanceId":"interact-soyo","target":'
        '{"kind":"character","id":"unknown"},'
        '"description":"talk"},"evidenceIds":[]}'
    )
    second = (
        '{"action":{"kind":"interact","affordanceId":"interact-soyo","target":'
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
    assert all(trace.world_ref == _world_ref() for trace in strategy.traces)
    assert all(request.world_ref == _world_ref() for request in gateway.requests)
    assert all("talk" not in repr(trace) for trace in strategy.traces)


@pytest.mark.parametrize("use_langchain", [False, True], ids=["fixture", "standard-parser"])
def test_personact_loop_uses_model_strategy_without_moving_world(use_langchain: bool) -> None:
    responses = (
        '{"score":3.0}',
        '{"items":[{"planId":"talk","description":"talk naturally"}]}',
        (
            '{"action":{"kind":"interact","affordanceId":"interact-soyo","target":'
            '{"kind":"character","id":"soyo"},'
            '"description":"offer the menu"},'
            '"evidenceIds":["soyo-visible"]}'
        ),
    )
    gateway = (
        LangChainModelGateway(
            FakeStructuredChatModel(responses=responses), model_id="fixture-model"
        )
        if use_langchain
        else FixtureModelGateway(responses)
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
        memory=MemoryStream(world_ref=_world_ref(), agent_id="anon", scope=spec.memory_scope),
        strategy=strategy,
        embedding_provider=FixedEmbeddingProvider(),
        world_ref=_world_ref(),
    )
    view = AgentView(
        world_ref=_world_ref(),
        agent_id="anon",
        event_session_id="cafe",
        based_on_world_version=7,
        based_on_control_epoch=1,
        based_on_decision_seq=0,
        current_location_id="cafe",
        world_time=datetime(2026, 9, 1, 9, tzinfo=UTC),
        candidates=(_poignancy_candidate(),),
        visible_evidence_ids=("soyo-visible",),
        affordances=(
            Affordance(
                affordance_id="interact-soyo",
                kind=ProposalKind.INTERACT,
                target=CharacterTarget(id="soyo"),
                operation_id="join_target_session",
            ),
        ),
    )

    proposal = agent.decide(DecisionRequest(proposal_id="proposal-1", view=view))

    assert proposal.agent_id == "anon"
    assert proposal.event_session_id == "cafe"
    assert proposal.world_ref == _world_ref()
    assert isinstance(proposal.action, InteractAction)
    assert tuple(trace.call_kind for trace in strategy.traces) == (
        "score_poignancy",
        "plan",
        "plan_action",
    )
    assert agent.last_trace is not None
    assert agent.last_trace.stages == ("prepare", "perceive", "retrieve", "plan", "propose")
    assert agent.last_trace.world_ref == _world_ref()
    assert agent.state.plan_queue == (PlanItem(plan_id="talk", description="talk naturally"),)
    assert agent.state.active_plan_id == "talk"
    assert agent.state.world_ref == agent.memory.world_ref == _world_ref()
    assert all(record.world_ref == _world_ref() for record in agent.memory.records)
    if isinstance(gateway, FixtureModelGateway):
        assert all(request.world_ref == _world_ref() for request in gateway.requests)
    assert all(trace.world_ref == _world_ref() for trace in strategy.traces)
    assert all(trace.status == "succeeded" for trace in strategy.traces)

    state_before_replay = agent.state
    assert agent.decide(DecisionRequest(proposal_id="proposal-1", view=view)) == proposal
    assert agent.state == state_before_replay
    assert len(strategy.traces) == 3


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
        strategy.plan(_queue_planning_input())

    assert tuple(request.call_kind for request in gateway.requests) == (
        "plan",
        "plan_repair",
    )
    assert tuple(trace.status for trace in strategy.traces) == (
        "invalid_output",
        "invalid_output",
    )
    assert gateway.requests[1].repair_diagnostic is not None
    assert "1:missing" in gateway.requests[1].repair_diagnostic
    assert all(request.world_ref == _world_ref() for request in gateway.requests)
    assert all(trace.world_ref == _world_ref() for trace in strategy.traces)


def test_model_strategy_shares_one_repair_budget_for_schema_and_semantics() -> None:
    model = FakeStructuredChatModel(
        responses=(
            {},
            {
                "action": {
                    "kind": "interact",
                    "affordanceId": "interact-soyo",
                    "target": {"kind": "character", "id": "unknown"},
                    "description": "talk",
                },
                "evidenceIds": [],
            },
        )
    )
    strategy = ModelCognitionStrategy(
        gateway=LangChainModelGateway(model, model_id="fake-model"),
        skill=_bound_skill(),
        model_id="fake-model",
        call_id_generator=SequentialIdGenerator(),
    )
    with pytest.raises(PlannerOutputError, match="after one repair"):
        strategy.plan_action(_planning_input())
    assert model.structured_calls == 2
    assert tuple(trace.status for trace in strategy.traces) == (
        "invalid_output",
        "semantic_rejected",
    )
    assert tuple(trace.call_kind for trace in strategy.traces) == (
        "plan_action",
        "plan_action_repair",
    )
    assert all(trace.world_ref == _world_ref() for trace in strategy.traces)


def test_model_strategy_repairs_duplicate_plan_ids() -> None:
    gateway = FixtureModelGateway(
        (
            '{"items":[{"planId":"a","description":"talk"},{"planId":"a","description":"listen"}]}',
            '{"items":[{"planId":"a","description":"talk"}]}',
        )
    )
    strategy = ModelCognitionStrategy(
        gateway=gateway,
        skill=_bound_skill(),
        model_id="fixture-model",
        call_id_generator=SequentialIdGenerator(),
    )
    assert strategy.plan(_queue_planning_input()).items == (
        PlanItem(plan_id="a", description="talk"),
    )
    assert tuple(trace.status for trace in strategy.traces) == ("invalid_output", "succeeded")


def test_repair_diagnostics_redact_private_keys_and_keep_one_repair_budget() -> None:
    invalid = '{"items":[],"private-story-secret":"private-story-value"}'
    gateway = FixtureModelGateway((invalid, invalid))
    strategy = ModelCognitionStrategy(
        gateway=gateway,
        skill=_bound_skill(),
        model_id="fixture-model",
        call_id_generator=SequentialIdGenerator(),
    )
    with pytest.raises(PlannerOutputError, match="after one repair") as caught:
        strategy.plan(_queue_planning_input())
    assert len(gateway.requests) == 2
    assert gateway.requests[1].repair_diagnostic is not None
    assert "extra_forbidden" in gateway.requests[1].repair_diagnostic
    assert "private-story" not in gateway.requests[1].repair_diagnostic
    assert "private-story" not in str(caught.value)
    assert all("private-story" not in trace.model_dump_json() for trace in strategy.traces)
    assert tuple(trace.status for trace in strategy.traces) == ("invalid_output", "invalid_output")


def test_shared_strategy_preserves_each_world_without_mutable_current_world() -> None:
    gateway = FixtureModelGateway(('{"score":1.0}', '{"score":2.0}'))
    strategy = ModelCognitionStrategy(
        gateway=gateway,
        skill=_bound_skill(),
        model_id="fixture-model",
        call_id_generator=SequentialIdGenerator(),
    )
    other_world = WorldRef(project_id=_world_ref().project_id, world_id="save-2")
    strategy.score_poignancy(_spec(), _poignancy_candidate(), world_ref=_world_ref())
    strategy.score_poignancy(_spec(), _poignancy_candidate(), world_ref=other_world)
    assert tuple(request.world_ref for request in gateway.requests) == (_world_ref(), other_world)
    assert tuple(trace.world_ref for trace in strategy.traces) == (_world_ref(), other_world)


@pytest.mark.parametrize("kind", ["score", "plan", "action"])
def test_strategy_rejects_wrong_ownership_before_call_or_trace(kind: str) -> None:
    gateway = FixtureModelGateway(("{}",))
    ids = SequentialIdGenerator()
    strategy = ModelCognitionStrategy(
        gateway=gateway,
        skill=_bound_skill(),
        model_id="fixture-model",
        call_id_generator=ids,
    )
    other_world = WorldRef(project_id=_world_ref().project_id, world_id="save-2")
    with pytest.raises(DecisionInputError):
        if kind == "score":
            strategy.score_poignancy(
                _spec(),
                _poignancy_candidate(),
                world_ref=WorldRef(project_id="other-project", world_id="save-1"),
            )
        elif kind == "plan":
            planning_input = _queue_planning_input()
            strategy.plan(
                PlanningInput.model_validate(
                    {
                        **planning_input.model_dump(),
                        "memory": MemoryStream(
                            world_ref=other_world, agent_id="anon", scope=_spec().memory_scope
                        ),
                    },
                    strict=True,
                )
            )
        else:
            action_input = _planning_input()
            strategy.plan_action(
                ActionPlanningInput.model_validate(
                    {
                        **action_input.model_dump(),
                        "view": AgentView.model_validate(
                            {
                                **action_input.view.model_dump(by_alias=False),
                                "world_ref": other_world,
                            },
                            strict=True,
                        ),
                    },
                    strict=True,
                )
            )
    assert gateway.requests == []
    assert ids.values == []
    assert strategy.traces == ()


@pytest.mark.parametrize("kind", ["plan", "action"])
@pytest.mark.parametrize("foreign_owner", ["world", "agent", "scope"])
def test_strategy_rejects_foreign_retrieved_memory_before_model_call(
    kind: str, foreign_owner: str
) -> None:
    gateway = FixtureModelGateway(("{}",))
    ids = SequentialIdGenerator()
    strategy = ModelCognitionStrategy(
        gateway=gateway,
        skill=_bound_skill(),
        model_id="fixture-model",
        call_id_generator=ids,
    )
    record = MemoryRecord(
        id="memory-1",
        world_ref=(
            WorldRef(project_id=_world_ref().project_id, world_id="save-2")
            if foreign_owner == "world"
            else _world_ref()
        ),
        agent_id="soyo" if foreign_owner == "agent" else "anon",
        scope="other-scope" if foreign_owner == "scope" else _spec().memory_scope,
        kind=MemoryKind.EVENT,
        created_at=datetime(2026, 9, 1, 9, tzinfo=UTC),
        last_accessed_at=datetime(2026, 9, 1, 9, tzinfo=UTC),
        subject="soyo",
        predicate="speaks",
        content="private memory",
        poignancy=1.0,
        source="world-entry",
        novelty_key="memory-1",
    )
    retrieved = RetrievedContext(
        observation=Observation(
            candidate=_poignancy_candidate(), novelty_key="candidate-1", is_novel=True
        ),
        related=(record,),
        ranked=(record,),
    )
    with pytest.raises(DecisionInputError, match="retrieved memory"):
        if kind == "plan":
            strategy.plan(
                PlanningInput.model_validate(
                    {**_queue_planning_input().model_dump(), "retrieved": (retrieved,)}, strict=True
                )
            )
        else:
            strategy.plan_action(
                ActionPlanningInput.model_validate(
                    {**_planning_input().model_dump(), "retrieved": (retrieved,)}, strict=True
                )
            )
    assert gateway.requests == []
    assert ids.values == []
    assert strategy.traces == ()


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
    strategy.score_poignancy(spec, percept, world_ref=_world_ref())
    strategy.score_poignancy(spec, percept, world_ref=_world_ref())
    strategy.score_poignancy(spec, percept, world_ref=_world_ref())

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
            (PlanDraft(items=(PlanItem(plan_id="talk", description="talk"),)).model_dump_json(),)
        ),
        skill=changed_skill,
        model_id="fixture-model",
        call_id_generator=SequentialIdGenerator(),
    )

    with pytest.raises(ValueError, match="content hash"):
        strategy.plan(_queue_planning_input())


def _bound_skill() -> RuntimeSkill:
    return RuntimeSkillCatalog.load(SKILLS_PATH).resolve(
        "mygo.character.anon",
        "3.0.0",
        agent_kind="character",
    )


def _model_request(*, model_id: str = "fake-model") -> ModelRequest:
    return ModelRequest(
        world_ref=_world_ref(),
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
        input_json='{"view":"visible"}',
    )


def _planning_input() -> ActionPlanningInput:
    spec = _spec()
    view = AgentView(
        world_ref=_world_ref(),
        agent_id="anon",
        event_session_id="cafe",
        based_on_world_version=7,
        based_on_control_epoch=1,
        based_on_decision_seq=0,
        current_location_id="cafe",
        visible_evidence_ids=("soyo-visible",),
        affordances=(
            Affordance(
                affordance_id="interact-soyo",
                kind=ProposalKind.INTERACT,
                target=CharacterTarget(id="soyo"),
                operation_id="join_target_session",
            ),
        ),
    )
    return ActionPlanningInput(
        spec=spec,
        view=view,
        state=_state(),
        observations=(),
        retrieved=(),
        active_plan=None,
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


def _queue_planning_input() -> PlanningInput:
    spec = _spec()
    return PlanningInput(
        spec=spec,
        state=_state(),
        memory=MemoryStream(world_ref=_world_ref(), agent_id="anon", scope=spec.memory_scope),
        world_time=datetime(2026, 9, 1, 9, tzinfo=UTC),
        observations=(),
        retrieved=(),
    )


def _state() -> PersonaState:
    return PersonaState(
        world_ref=_world_ref(),
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


def _world_ref() -> WorldRef:
    return WorldRef(project_id="coffee-golden", world_id="save-1")


def _spec() -> CompiledPersonActSpec:
    skills = RuntimeSkillCatalog.load(SKILLS_PATH).skills
    catalog = Catalog(
        tools=(ToolDefinition(id="visible_location.query", version="1", mode=ToolMode.QUERY),),
        prompts=(PromptDefinition(id="personact.v1", version="1", digest="prompt-v1"),),
        skills=skills,
    )
    return compile_manifest(load_manifest(FIXTURE_PATH), catalog)[0]
