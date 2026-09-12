"""Hard-budget tests for the shared logical ModelGateway boundary."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_runtime.model import StrictModel
from agent_runtime.model_budget import BudgetedModelGateway, ModelCallBudgetExceededError
from agent_runtime.model_gateway import FixtureModelGateway, ModelOutputInvalidError, ModelRequest
from agent_runtime.world.contracts import WorldRef


class _Answer(StrictModel):
    value: str


def test_budget_precharges_calls_even_when_the_provider_output_fails() -> None:
    provider = FixtureModelGateway((' {"value":1}', '{"value":"ok"}'))
    gateway = BudgetedModelGateway(provider, call_limit=2)

    with pytest.raises(ModelOutputInvalidError):
        gateway.generate(_request("failed"), _Answer)
    assert gateway.generate_count == 1

    assert gateway.generate(_request("succeeded"), _Answer).structured.value == "ok"
    with pytest.raises(ModelCallBudgetExceededError, match="budget exhausted"):
        gateway.generate(_request("rejected"), _Answer)

    assert gateway.generate_count == gateway.call_limit == 2
    assert tuple(request.call_id for request in provider.requests) == ("failed", "succeeded")


def test_budget_is_shared_and_cannot_overissue_under_concurrency() -> None:
    limit = 7
    provider = FixtureModelGateway(tuple('{"value":"ok"}' for _ in range(limit)))
    exhaustion_notifications: list[None] = []
    gateway = BudgetedModelGateway(
        provider,
        call_limit=limit,
        on_exhausted=lambda: exhaustion_notifications.append(None),
    )

    def invoke(index: int) -> bool:
        try:
            gateway.generate(_request(f"call-{index}"), _Answer)
        except ModelCallBudgetExceededError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=20) as executor:
        accepted = tuple(executor.map(invoke, range(20)))

    assert sum(accepted) == limit
    assert gateway.generate_count == limit
    assert gateway.exhausted is True
    assert len(provider.requests) == limit
    assert exhaustion_notifications == [None]


@pytest.mark.parametrize("call_limit", [True, False, 0, -1])
def test_budget_rejects_non_positive_or_boolean_limits(call_limit: int) -> None:
    with pytest.raises(ValueError, match="call_limit must be positive"):
        BudgetedModelGateway(FixtureModelGateway(('{"value":"ok"}',)), call_limit=call_limit)


def _request(call_id: str) -> ModelRequest:
    return ModelRequest(
        world_ref=WorldRef(project_id="for-the-band", world_id="budget-test"),
        call_id=call_id,
        agent_kind="character",
        agent_id="anon",
        call_kind="plan_action",
        model_id="fixture-model",
        prompt_id="personact.v1",
        prompt_version="1",
        prompt_digest="prompt-v1",
        skill_id="mygo.character.anon",
        skill_version="3.0.0",
        skill_content_hash="a" * 64,
        system_prompt="Follow the Character Skill.",
        input_json='{"view":"visible"}',
    )
