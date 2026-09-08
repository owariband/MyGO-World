"""M1 strict identity and minimal private plan-state contracts."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agent_runtime.agent.personact.state import CognitiveConfig, PersonaState, PlanItem
from agent_runtime.world.contracts import (
    AgentView,
    AttentionTier,
    PerceptCandidate,
    PerceptionChannel,
    WorldRef,
)

WORLD_REF = WorldRef(project_id="coffee-golden", world_id="save-001")


def test_world_ref_is_required_strict_frozen_and_round_trips() -> None:
    wire = '{"projectId":"coffee-golden","worldId":"save-001"}'
    assert WorldRef.model_validate_json(wire, strict=True) == WORLD_REF
    assert WORLD_REF.model_dump_json(by_alias=True) == wire
    with pytest.raises(ValidationError, match="frozen"):
        WORLD_REF.world_id = "another"
    with pytest.raises(ValidationError, match="Field required"):
        WorldRef.model_validate({"projectId": "coffee-golden"}, strict=True)
    with pytest.raises(ValidationError, match="valid string"):
        WorldRef.model_validate({"projectId": "p", "worldId": 1}, strict=True)
    with pytest.raises(ValidationError, match="Extra inputs"):
        WorldRef.model_validate({"projectId": "p", "worldId": "w", "runId": "r"}, strict=True)
    with pytest.raises(ValidationError, match="at least 1 character"):
        WorldRef(project_id=" ", world_id="w")


def test_agent_view_requires_world_and_has_no_legacy_frame_alias() -> None:
    view = AgentView(
        world_ref=WORLD_REF,
        agent_id="anon",
        event_session_id="cafe",
        based_on_world_version=7,
        current_location_id="cafe",
        affordances=(),
    )
    assert AgentView.model_validate_json(view.model_dump_json(by_alias=True), strict=True) == view
    raw = view.model_dump(by_alias=False)
    del raw["world_ref"]
    with pytest.raises(ValidationError, match="Field required"):
        AgentView.model_validate(raw, strict=True)
    assert view.world_time is None
    assert view.candidates == ()
    from agent_runtime.world import contracts

    assert not hasattr(contracts, "PerceptionFrame")


def test_perception_candidate_is_strict_and_owned_by_its_view() -> None:
    candidate = PerceptCandidate(
        candidate_id="coffee-ready-for-anon",
        source_event_id="coffee-ready",
        event_revision=2,
        channel=PerceptionChannel.COMMITMENT_UPDATE,
        attention_tier=AttentionTier.MANDATORY,
        subject="coffee-42",
        predicate="is",
        object="ready",
        content="Our coffee is ready.",
        visible_fields=("status", "owner-party"),
        salience=0.9,
        tags=("coffee",),
        source_fact_refs=("order-42-status-r2",),
    )
    view = AgentView(
        world_ref=WORLD_REF,
        agent_id="anon",
        event_session_id="cafe",
        based_on_world_version=7,
        current_location_id="cafe",
        world_time=datetime(2026, 8, 31, 9, 0, tzinfo=UTC),
        candidates=(candidate,),
        affordances=(),
    )
    assert view.candidates == (candidate,)
    with pytest.raises(ValidationError, match="less than or equal to 1"):
        PerceptCandidate.model_validate({**candidate.model_dump(), "salience": 1.1}, strict=True)


def test_plan_queue_round_trip_preserves_order_and_current_reference() -> None:
    plans = (
        PlanItem(plan_id="coffee", description="get coffee"),
        PlanItem(plan_id="talk", description="talk with Soyo"),
    )
    state = _state(plan_queue=plans, active_plan_id="coffee")
    restored = PersonaState.model_validate_json(state.model_dump_json(by_alias=True), strict=True)
    assert restored == state
    assert restored.plan_queue == plans
    assert restored.active_plan == plans[0]
    assert restored.world_ref == WORLD_REF
    assert restored.known_place_ids == ("cafe", "ring")
    assert restored.last_world_version == 7
    with pytest.raises(ValidationError, match="frozen"):
        state.active_plan_id = "talk"
    with pytest.raises(ValidationError, match="frozen"):
        plans[0].description = "override"
    assert "activePlan" not in state.model_dump(by_alias=True)


def test_plan_queue_rejects_duplicate_ids_and_dangling_current_plan() -> None:
    item = PlanItem(plan_id="coffee", description="get coffee")
    with pytest.raises(ValidationError, match="unique"):
        _state(plan_queue=(item, item))
    with pytest.raises(ValidationError, match="must reference"):
        _state(plan_queue=(item,), active_plan_id="unknown")
    with pytest.raises(ValidationError, match="must reference"):
        _state(active_plan_id="coffee")
    assert _state().active_plan is None


@pytest.mark.parametrize(
    "legacy_field",
    ["daily_plan", "current_daily_intentions", "conversation_cooldowns", "active_action"],
)
def test_removed_state_fields_are_rejected_not_silently_ignored(legacy_field: str) -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        PersonaState.model_validate({**_state().model_dump(), legacy_field: None}, strict=True)


def test_persona_state_rejects_naive_time_and_mutable_collections() -> None:
    raw = _state().model_dump(by_alias=False)
    with pytest.raises(ValidationError, match="timezone info"):
        PersonaState.model_validate(
            {**raw, "last_world_time": datetime.fromisoformat("2026-08-31T09:00:00")},
            strict=True,
        )
    with pytest.raises(ValidationError, match="valid tuple"):
        PersonaState.model_validate({**raw, "plan_queue": []}, strict=True)
    with pytest.raises(ValidationError, match="valid tuple"):
        PersonaState.model_validate({**raw, "known_place_ids": ["cafe"]}, strict=True)


def _state(
    *,
    plan_queue: tuple[PlanItem, ...] = (),
    active_plan_id: str | None = None,
) -> PersonaState:
    return PersonaState(
        world_ref=WORLD_REF,
        agent_id="anon",
        cognitive_config=CognitiveConfig(
            attention_budget=3,
            retention=5,
            recency_weight=1.0,
            relevance_weight=1.0,
            importance_weight=1.0,
            recency_decay=0.99,
            reflection_threshold=150.0,
            reflection_count=5,
        ),
        reflection_remaining=130.0,
        last_world_time=datetime(2026, 8, 31, 9, 0, tzinfo=UTC),
        last_world_version=7,
        plan_queue=plan_queue,
        active_plan_id=active_plan_id,
        reflection_new_memory_count=2,
        known_place_ids=("cafe", "ring"),
    )
