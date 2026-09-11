"""Local model traces through fixtures and the real SDK's offline HTTP transport."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from agent_runtime.agent.personact.errors import PlanningError
from agent_runtime.agent.personact.loop import PlanDraft
from agent_runtime.model_gateway import FixtureModelGateway, ModelOutputInvalidError, ModelRequest
from agent_runtime.model_provider import DEFAULT_DEEPSEEK_MODEL, create_deepseek_gateway
from agent_runtime.model_smoke import run_smoke
from agent_runtime.tests.test_model_provider import PLAN, SECRET, MockAPI
from agent_runtime.tests.test_model_smoke import RESPONSES
from agent_runtime.trace import LocalTrace, TraceRecord, trace_scope
from agent_runtime.world.contracts import WorldRef


@pytest.fixture(autouse=True)
def disable_external_tracing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")


@pytest.mark.parametrize("failure", [429, 503, httpx.ReadTimeout])
@pytest.mark.parametrize("recover", [False, True])
def test_sdk_attempts_and_final_result_remain_visible_after_retries(
    tmp_path: Path, failure: int | type[httpx.RequestError], recover: bool
) -> None:
    api = MockAPI([failure, failure, *RESPONSES] if recover else [failure, failure, failure])
    ref = WorldRef(project_id="coffee-golden", world_id="model-smoke")
    with (
        httpx.Client(transport=httpx.MockTransport(api.handle)) as client,
        LocalTrace(tmp_path, ref) as log,
    ):
        gateway = create_deepseek_gateway(api_key=SECRET, http_client=client)
        if recover:
            run_smoke(gateway, model_id=DEFAULT_DEEPSEEK_MODEL, trace_log=log)
        else:
            with pytest.raises(PlanningError, match="poignancy"):
                run_smoke(gateway, model_id=DEFAULT_DEEPSEEK_MODEL, trace_log=log)

    records = _records(log)
    attempts = [
        record
        for record in records
        if record.event.startswith("model.attempt.") and record.data["callId"] == "smoke-call-1"
    ]
    assert [record.event for record in attempts] == [
        "model.attempt.start",
        "model.attempt.error",
    ] * 2 + ["model.attempt.start", "model.attempt.end" if recover else "model.attempt.error"]
    assert [record.data["attempt"] for record in attempts] == [1, 1, 2, 2, 3, 3]
    expected_error = (
        {429: "OpenAIRateLimitError", 503: "OpenAIAPIError"}[failure]
        if isinstance(failure, int)
        else "OpenAITimeoutError"
    )
    assert all(
        record.data["errorType"] == expected_error
        for record in attempts
        if record.event == "model.attempt.error"
    )
    results = [record for record in records if record.event == "model.result"]
    assert len(api.requests) == (5 if recover else 3)
    assert len(results) == (3 if recover else 1)
    assert results[0].data["status"] == ("succeeded" if recover else "transport_failed")
    assert results[0].data["transportAttempts"] == 3
    assert not any(record.event == "model.repair" for record in records)
    assert SECRET not in log.path.read_text()
    assert all("content" not in record.data for record in records)
    assert records[-1].event == ("decision.end" if recover else "decision.error")


@pytest.mark.parametrize("provider", ["fixture", "sdk"])
@pytest.mark.parametrize("reason", ["schema", "semantic"])
def test_full_decision_records_one_explicit_repair_without_private_bodies(
    tmp_path: Path, provider: str, reason: str
) -> None:
    bad = (
        '{"action":{"kind":"utter"},"evidenceIds":[]}'
        if reason == "schema"
        else '{"action":{"kind":"utter","affordanceId":"smoke-utter-soyo-direct",'
        '"target":{"kind":"character","id":"tomori"},'
        '"content":"private-rejected-output","expectsResponse":false},"evidenceIds":[]}'
    )
    responses = (*RESPONSES[:2], bad, RESPONSES[2])
    api = MockAPI(list(responses))
    ref = WorldRef(project_id="coffee-golden", world_id="model-smoke")
    with (
        httpx.Client(transport=httpx.MockTransport(api.handle)) as client,
        LocalTrace(tmp_path, ref) as log,
    ):
        gateway = (
            FixtureModelGateway(responses)
            if provider == "fixture"
            else create_deepseek_gateway(api_key=SECRET, http_client=client)
        )
        result = run_smoke(
            gateway,
            model_id="fixture-model" if provider == "fixture" else DEFAULT_DEEPSEEK_MODEL,
            trace_log=log,
        )

    records = _records(log)
    model_results = [record for record in records if record.event == "model.result"]
    repairs = [record for record in records if record.event == "model.repair"]
    assert len(repairs) == 1
    assert repairs[0].data == {
        "callId": result.traces[-1].call_id,
        "repairOfCallId": result.traces[-2].call_id,
        "reason": reason,
    }
    assert [record.data["status"] for record in model_results] == [
        "succeeded",
        "succeeded",
        "invalid_output" if reason == "schema" else "semantic_rejected",
        "succeeded",
    ]
    assert model_results[-2].seq < repairs[0].seq < model_results[-1].seq
    assert all(record.agent_id == "anon" and record.world_ref == ref for record in records)
    assert len({record.trace_id for record in records}) == 1
    assert all("content" not in record.data for record in records)
    text = log.path.read_text()
    assert "private-rejected-output" not in text
    assert "先问问爽世喜欢喝什么" not in text
    assert SECRET not in text


@pytest.mark.parametrize("debug", [False, True])
def test_fixture_content_requires_opt_in_and_preserves_actual_gateway_messages(
    tmp_path: Path, debug: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_debug_payload(*_: object, **__: object) -> None:
        raise AssertionError("debug-only payload must not be constructed")

    if not debug:
        monkeypatch.setattr("agent_runtime.model_gateway._system_message", unexpected_debug_payload)
        monkeypatch.setattr(PlanDraft, "model_dump", unexpected_debug_payload)
    request = _request()
    with (
        LocalTrace(tmp_path, request.world_ref, debug=debug) as log,
        trace_scope(log, world_ref=request.world_ref, agent_kind="character", agent_id="anon"),
    ):
        FixtureModelGateway((PLAN,)).generate(request, PlanDraft)
    records = _records(log)
    start, end = [record for record in records if record.event.startswith("model.attempt.")]
    assert start.data["inputHash"] == request.input_hash
    assert start.data["skillContentHash"] == request.skill_content_hash
    if debug:
        assert start.data["content"] == {
            "systemMessage": (
                "Runtime Skill test-skill@1:\n\nprivate-system-prompt\n\n"
                "Return exactly one value matching the requested structured contract."
            ),
            "inputJson": request.input_json,
        }
        assert end.data["content"] == {"validatedStructuredOutput": json.loads(PLAN)}
    else:
        assert all("content" not in record.data for record in records)
        assert "private-system-prompt" not in log.path.read_text()
        assert "private-model-input" not in log.path.read_text()


def test_invalid_fixture_does_not_log_unvalidated_body_even_in_debug(tmp_path: Path) -> None:
    request = _request()
    with (
        LocalTrace(tmp_path, request.world_ref, debug=True) as log,
        pytest.raises(ModelOutputInvalidError),
        trace_scope(log, world_ref=request.world_ref, agent_kind="character", agent_id="anon"),
    ):
        FixtureModelGateway(('{"items":[],"private-unknown-field":true}',)).generate(
            request, PlanDraft
        )
    records = _records(log)
    error = next(record for record in records if record.event == "model.attempt.error")
    assert error.data["errorType"] == "ValidationError"
    assert "content" not in error.data
    assert "private-unknown-field" not in log.path.read_text()


def _records(log: LocalTrace) -> list[TraceRecord]:
    return [TraceRecord.model_validate_json(line) for line in log.path.read_text().splitlines()]


def _request() -> ModelRequest:
    return ModelRequest(
        world_ref=WorldRef(project_id="trace-tests", world_id="fixture"),
        call_id="call-1",
        agent_kind="character",
        agent_id="anon",
        call_kind="plan",
        model_id="fixture-model",
        prompt_id="prompt-1",
        prompt_version="1",
        prompt_digest="hash",
        skill_id="test-skill",
        skill_version="1",
        skill_content_hash="a" * 64,
        system_prompt="private-system-prompt",
        input_json='{"text":"private-model-input"}',
    )
