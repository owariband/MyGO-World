"""Offline checks for the opt-in, single-decision model smoke command."""

import json
from pathlib import Path

import pytest

from agent_runtime import model_smoke
from agent_runtime.model_gateway import FixtureModelGateway, ModelGateway
from agent_runtime.model_smoke import SmokeResult, run_smoke
from agent_runtime.world.contracts import UtterAction

RESPONSES = (
    '{"score":3.0}',
    '{"items":[{"planId":"coffee","description":"先问问爽世喜欢喝什么"}]}',
    '{"action":{"kind":"utter","affordanceId":"smoke-utter-soyo-direct",'
    '"target":{"kind":"character","id":"soyo"},'
    '"content":"爽世想喝什么？我还在考虑呢。","expectsResponse":true},'
    '"evidenceIds":["soyo-greeting"]}',
)


def test_smoke_uses_gold_skill_and_three_calls_for_exactly_one_proposal() -> None:
    gateway = FixtureModelGateway(RESPONSES)
    result = run_smoke(gateway, model_id="fixture-model")

    assert result.proposal.agent_id == "anon"
    assert result.proposal.proposal_id == "smoke-proposal-1"
    assert result.proposal.event_session_id == "smoke-cafe"
    assert result.proposal.based_on_world_version == 1
    assert result.proposal.world_ref.project_id == "coffee-golden"
    assert isinstance(result.proposal.action, UtterAction)
    assert result.proposal.action.target.id == "soyo"
    assert result.proposal.evidence_ids == ("soyo-greeting",)
    assert tuple(trace.call_kind for trace in result.traces) == (
        "score_poignancy",
        "plan",
        "plan_action",
    )
    assert len(gateway.requests) == 3
    assert all(request.world_ref == result.proposal.world_ref for request in gateway.requests)
    assert all(trace.world_ref == result.proposal.world_ref for trace in result.traces)
    assert all(trace.status == "succeeded" for trace in result.traces)
    assert all(trace.skill_id == "mygo.character.anon" for trace in result.traces)
    assert all(trace.skill_version == "3.0.0" for trace in result.traces)
    assert len({trace.call_id for trace in result.traces}) == 3
    planning_input = json.loads(gateway.requests[1].input_json)
    action_input = json.loads(gateway.requests[2].input_json)
    assert planning_input["state"]["planQueue"] == []
    assert len(planning_input["observations"]) == 1
    assert planning_input["memory"]["records"][0]["embedding"] == []
    assert action_input["state"]["activePlanId"] == "coffee"
    assert len(action_input["state"]["planQueue"]) == 1
    wire = result.model_dump_json(by_alias=True)
    assert SmokeResult.model_validate_json(wire, strict=True) == result
    assert set(json.loads(wire)) == {"proposal", "traces"}
    assert "systemPrompt" not in wire
    assert "inputJson" not in wire


@pytest.mark.parametrize("use_flag", [False, True])
def test_cli_model_selection_and_json_output_without_network(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    use_flag: bool,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("DEEPSEEK_MODEL", "environment-model")
    expected_model = "flag-model" if use_flag else "environment-model"
    gateway = FixtureModelGateway(RESPONSES, model_id=expected_model)
    calls: list[str] = []

    def fake_factory(*, model_id: str, api_key: str | None = None) -> ModelGateway:
        assert api_key is None
        calls.append(model_id)
        return gateway

    monkeypatch.setattr(model_smoke, "create_deepseek_gateway", fake_factory)
    args = ["--trace-root", str(tmp_path)]
    if use_flag:
        args.extend(["--model", expected_model])
    assert model_smoke.main(args) == 0
    captured = capsys.readouterr()
    result = SmokeResult.model_validate_json(captured.out, strict=True)
    assert result.proposal.agent_id == "anon"
    assert len(result.traces) == 3
    assert all(trace.model_id == expected_model for trace in result.traces)
    assert calls == [expected_model]
    assert "Local trace:" in captured.err
    logs = list(tmp_path.glob("coffee-golden/.runtime/traces/model-smoke/*.jsonl"))
    assert len(logs) == 1
    assert "decision.end" in logs[0].read_text()


def test_cli_missing_key_returns_safe_failure_without_network(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert model_smoke.main([]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "DEEPSEEK_API_KEY" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("debug", [False, True])
def test_cli_keeps_partial_trace_on_decision_failure_and_respects_debug(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    debug: bool,
) -> None:
    def fake_factory(*, model_id: str, api_key: str | None = None) -> ModelGateway:
        assert api_key is None
        return FixtureModelGateway(
            (RESPONSES[0], "bad-private-output", "bad-private-output"), model_id=model_id
        )

    monkeypatch.setattr(model_smoke, "create_deepseek_gateway", fake_factory)
    args = ["--trace-root", str(tmp_path)]
    if debug:
        args.append("--debug")
    assert model_smoke.main(args) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    logs = list(tmp_path.glob("coffee-golden/.runtime/traces/model-smoke/*.jsonl"))
    assert len(logs) == 1
    text = logs[0].read_text()
    assert "model.repair" in text
    assert "decision.error" in text
    assert "decision.end" not in text
    assert ("systemMessage" in text) is debug
    assert ("systemMessage" in captured.err) is debug
    assert "bad-private-output" not in text + captured.err


def test_cli_failure_does_not_print_sdk_message_or_prompt(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def failing_factory(*, model_id: str, api_key: str | None = None) -> ModelGateway:
        del model_id, api_key
        raise RuntimeError("secret-key and private Character Skill prompt")

    monkeypatch.setattr(model_smoke, "create_deepseek_gateway", failing_factory)
    assert model_smoke.main([]) == 1
    captured = capsys.readouterr()
    assert "RuntimeError" in captured.err
    assert "secret-key" not in captured.err
    assert "Character Skill" not in captured.err
    assert captured.out == ""


def test_cli_help_exits_before_creating_provider(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def unexpected_factory(*, model_id: str, api_key: str | None = None) -> ModelGateway:
        del model_id, api_key
        raise AssertionError("help must not create a provider")

    monkeypatch.setattr(model_smoke, "create_deepseek_gateway", unexpected_factory)
    with pytest.raises(SystemExit) as caught:
        model_smoke.main(["--help"])
    assert caught.value.code == 0
    assert "uncommitted proposal" in capsys.readouterr().out
