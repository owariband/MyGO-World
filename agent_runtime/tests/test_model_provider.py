"""Offline HTTP contract tests through the real ChatOpenAI and OpenAI SDK."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, cast

import httpx
import pytest

from agent_runtime.agent.personact.loop import PlanDraft
from agent_runtime.agent.personact.proposal import ProposalDraft
from agent_runtime.model_gateway import (
    ModelOutputInvalidError,
    ModelRequest,
    ModelRequestRejectedError,
    ModelTransportError,
)
from agent_runtime.model_provider import (
    DEEPSEEK_BASE_URL,
    DEFAULT_DEEPSEEK_MODEL,
    create_deepseek_gateway,
)
from agent_runtime.model_smoke import run_smoke
from agent_runtime.world.contracts import WorldRef

PLAN = '{"items":[{"planId":"talk","description":"talk to Soyo"}]}'
SECRET = "private-provider-body-or-key"


@dataclass
class MockAPI:
    outcomes: list[str | int | type[httpx.RequestError]]
    requests: list[httpx.Request] = field(default_factory=lambda: list[httpx.Request]())
    payloads: list[dict[str, Any]] = field(default_factory=lambda: list[dict[str, Any]]())

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        payload = cast(dict[str, Any], json.loads(request.content))
        self.payloads.append(payload)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, type):
            raise outcome(SECRET, request=request)
        if isinstance(outcome, int):
            return httpx.Response(outcome, json={"error": {"message": SECRET}})
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-offline",
                "object": "chat.completion",
                "created": 1,
                "model": payload["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-offline",
                                    "type": "function",
                                    "function": {
                                        "name": payload["tools"][0]["function"]["name"],
                                        "arguments": outcome,
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )


@pytest.fixture(autouse=True)
def offline_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "unused-openai-key")
    monkeypatch.setenv("OPENAI_API_BASE", "https://wrong-provider.invalid")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://wrong-provider.invalid")
    monkeypatch.setenv("OPENAI_ORG_ID", "unused-openai-organization")
    monkeypatch.setenv("OPENAI_ORGANIZATION", "unused-legacy-organization")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "unused-openai-project")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)


def test_openai_wire_configuration_and_strict_tuple_parsing() -> None:
    api = MockAPI([PLAN])
    with httpx.Client(transport=httpx.MockTransport(api.handle)) as client:
        gateway = create_deepseek_gateway(api_key=SECRET, http_client=client)
        result = gateway.generate(_request(), PlanDraft)

    assert result.structured.items[0].plan_id == "talk"
    assert isinstance(result.structured.items, tuple)
    assert result.trace.world_ref == _request().world_ref
    assert result.trace.transport_attempts == 1
    assert SECRET not in result.trace.model_dump_json()
    assert PLAN not in result.trace.model_dump_json()
    request = api.requests[0]
    assert request.method == "POST"
    assert str(request.url) == f"{DEEPSEEK_BASE_URL}/chat/completions"
    assert request.headers["authorization"] == f"Bearer {SECRET}"
    assert "openai-organization" not in request.headers
    assert "openai-project" not in request.headers
    assert request.extensions["timeout"] == dict.fromkeys(
        ("connect", "read", "write", "pool"), 60.0
    )
    payload = api.payloads[0]
    assert payload["model"] == DEFAULT_DEEPSEEK_MODEL
    assert payload["stream"] is False
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["max_tokens"] == 2048
    assert "max_completion_tokens" not in payload
    assert "parallel_tool_calls" not in payload
    assert "response_format" not in payload
    function = payload["tools"][0]["function"]
    assert "strict" not in function  # Local strict is not DeepSeek's /beta strict mode.
    assert payload["tool_choice"] == {"type": "function", "function": {"name": function["name"]}}
    assert function["parameters"]["additionalProperties"] is False
    assert payload["messages"][0]["role"] == "system"


def test_real_sdk_accepts_proposal_union_and_evidence_array() -> None:
    api = MockAPI(
        [
            '{"action":{"kind":"utter","target":{"kind":"character","id":"soyo"},'
            '"content":"Hi!"},"evidenceIds":["soyo-visible"]}'
        ]
    )
    with httpx.Client(transport=httpx.MockTransport(api.handle)) as client:
        result = create_deepseek_gateway(api_key=SECRET, http_client=client).generate(
            _request(), ProposalDraft
        )
    assert result.structured.evidence_ids == ("soyo-visible",)
    assert result.structured.action.kind == "utter"


@pytest.mark.parametrize(
    "value",
    [
        '{"items":[{"planId":7,"description":"talk"}]}',
        '{"items":[],"unknown":"private-provider-body-or-key"}',
        '{"items":"not an array"}',
        "not-json-private-provider-body-or-key",
    ],
)
def test_invalid_output_is_not_transport_retried(value: str) -> None:
    api = MockAPI([value])
    with httpx.Client(transport=httpx.MockTransport(api.handle)) as client:
        gateway = create_deepseek_gateway(api_key=SECRET, http_client=client)
        with pytest.raises(ModelOutputInvalidError) as caught:
            gateway.generate(_request(), PlanDraft)
    assert len(api.requests) == 1
    assert caught.value.trace.status == "invalid_output"
    assert SECRET not in caught.value.diagnostic
    assert SECRET not in caught.value.trace.model_dump_json()


@pytest.mark.parametrize("failure", [429, 500, 503, httpx.ReadTimeout, httpx.ConnectError])
def test_retryable_sdk_failures_recover_once(
    failure: int | type[httpx.RequestError],
) -> None:
    api = MockAPI([failure, PLAN])
    with httpx.Client(transport=httpx.MockTransport(api.handle)) as client:
        result = create_deepseek_gateway(api_key=SECRET, http_client=client).generate(
            _request(), PlanDraft
        )
    assert len(api.requests) == 2
    assert result.trace.transport_attempts == 2
    assert api.payloads[0] == api.payloads[1]


@pytest.mark.parametrize("failure", [429, 503, httpx.ReadTimeout])
def test_exhausted_retry_budget_has_no_nested_sdk_retries(
    failure: int | type[httpx.RequestError],
) -> None:
    api = MockAPI([failure, failure, failure])
    with httpx.Client(transport=httpx.MockTransport(api.handle)) as client:
        gateway = create_deepseek_gateway(api_key=SECRET, http_client=client)
        with pytest.raises(ModelTransportError) as caught:
            gateway.generate(_request(), PlanDraft)
    assert len(api.requests) == 3
    assert caught.value.trace.transport_attempts == 3
    assert caught.value.trace.status == "transport_failed"
    assert SECRET not in str(caught.value)
    assert SECRET not in caught.value.trace.model_dump_json()


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 422])
def test_configuration_and_authentication_errors_fail_without_retry(status: int) -> None:
    api = MockAPI([status])
    with httpx.Client(transport=httpx.MockTransport(api.handle)) as client:
        gateway = create_deepseek_gateway(api_key=SECRET, http_client=client)
        with pytest.raises(ModelRequestRejectedError) as caught:
            gateway.generate(_request(), PlanDraft)
    assert len(api.requests) == 1
    assert SECRET not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


def test_missing_key_never_falls_back_to_openai_key() -> None:
    with pytest.raises(ModelRequestRejectedError, match="DEEPSEEK_API_KEY is required"):
        create_deepseek_gateway()


def test_environment_key_and_explicit_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)
    api = MockAPI([PLAN])
    with httpx.Client(transport=httpx.MockTransport(api.handle)) as client:
        gateway = create_deepseek_gateway(model_id="deepseek-v4-pro", http_client=client)
        request = ModelRequest.model_validate(
            {**_request().model_dump(by_alias=False), "model_id": "deepseek-v4-pro"}
        )
        result = gateway.generate(request, PlanDraft)
    assert api.payloads[0]["model"] == "deepseek-v4-pro"
    assert result.trace.model_id == "deepseek-v4-pro"
    assert api.requests[0].headers["authorization"] == f"Bearer {SECRET}"


@pytest.mark.parametrize("key", ["", "   "])
def test_explicit_empty_key_does_not_fall_back_to_environment(
    key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)
    with pytest.raises(ModelRequestRejectedError, match="DEEPSEEK_API_KEY"):
        create_deepseek_gateway(api_key=key)


def test_model_override_is_rejected_before_http() -> None:
    api = MockAPI([])
    with httpx.Client(transport=httpx.MockTransport(api.handle)) as client:
        gateway = create_deepseek_gateway(
            model_id="deepseek-v4-pro", api_key=SECRET, http_client=client
        )
        with pytest.raises(ModelRequestRejectedError, match="cannot override"):
            gateway.generate(_request(), PlanDraft)
    assert not api.requests


@pytest.mark.parametrize(
    "bad_action",
    [
        None,
        '{"action":{"kind":"utter"},"evidenceIds":[]}',
        '{"action":{"kind":"utter","target":{"kind":"character","id":"tomori"},'
        '"content":"Hi!"},"evidenceIds":["invisible-evidence"]}',
    ],
)
def test_whole_agent_through_sdk_with_at_most_one_schema_or_semantic_repair(
    bad_action: str | None,
) -> None:
    responses: list[str | int | type[httpx.RequestError]] = ['{"score":3.0}', PLAN]
    if bad_action is not None:
        responses.append(bad_action)
    responses.append(
        '{"action":{"kind":"utter","target":{"kind":"character","id":"soyo"},'
        '"content":"Hi!"},"evidenceIds":["soyo-greeting"]}'
    )
    api = MockAPI(responses)
    with httpx.Client(transport=httpx.MockTransport(api.handle)) as client:
        gateway = create_deepseek_gateway(api_key=SECRET, http_client=client)
        result = run_smoke(gateway, model_id=DEFAULT_DEEPSEEK_MODEL)
    count = 3 if bad_action is None else 4
    assert len(api.requests) == len(result.traces) == count
    assert result.proposal.agent_id == "anon"
    assert result.proposal.evidence_ids == ("soyo-greeting",)
    assert all(trace.world_ref == result.proposal.world_ref for trace in result.traces)
    assert all(trace.transport_attempts == 1 for trace in result.traces)
    assert len({trace.call_id for trace in result.traces}) == count
    assert result.traces[-1].status == "succeeded"
    if bad_action is not None:
        assert result.traces[-2].status in ("invalid_output", "semantic_rejected")
        assert result.traces[-2].call_kind == "plan_action"
        assert result.traces[-1].call_kind == "plan_action_repair"
        assert api.payloads[-2]["messages"][1] == api.payloads[-1]["messages"][1]


def _request() -> ModelRequest:
    return ModelRequest(
        world_ref=WorldRef(project_id="provider-smoke", world_id="offline"),
        call_id="offline-call-1",
        agent_kind="character",
        agent_id="anon",
        call_kind="plan",
        model_id=DEFAULT_DEEPSEEK_MODEL,
        prompt_id="personact.v1",
        prompt_version="1",
        prompt_digest="test-prompt",
        skill_id="mygo.character.anon",
        skill_version="3.0.0",
        skill_content_hash="a" * 64,
        system_prompt="Return the requested structured plan.",
        input_json='{"observation":"Soyo says hello."}',
    )
