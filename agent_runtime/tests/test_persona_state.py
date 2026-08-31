from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from agent_runtime.agent.personact.state import (
    ActiveAction,
    CognitiveConfig,
    ConversationCooldown,
    DailyPlan,
    NewDayStatus,
    PersonaState,
    ScheduleItem,
    calculate_new_day,
    is_active_action_finished,
)
from agent_runtime.world.contracts import (
    AttentionTier,
    PerceptCandidate,
    PerceptionChannel,
    PerceptionFrame,
)


def test_perception_frame_keeps_existing_constructors_compatible() -> None:
    frame = PerceptionFrame(
        agent_id="anon",
        event_session_id="cafe",
        based_on_world_version=7,
        current_location_id="cafe",
        affordances=(),
    )

    assert frame.world_time is None
    assert frame.candidates == ()


def test_perception_candidate_is_strict_and_world_scoped() -> None:
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
        source_info_refs=(),
    )
    frame = PerceptionFrame(
        agent_id="anon",
        event_session_id="cafe",
        based_on_world_version=7,
        current_location_id="cafe",
        world_time=datetime(2026, 8, 31, 9, 0, tzinfo=UTC),
        candidates=(candidate,),
        affordances=(),
    )

    assert frame.candidates == (candidate,)
    with pytest.raises(ValidationError, match="less than or equal to 1"):
        PerceptCandidate(
            candidate_id="invalid",
            channel=PerceptionChannel.SAME_SCENE,
            attention_tier=AttentionTier.AMBIENT,
            subject="coffee-machine",
            predicate="rings",
            content="The coffee machine rings.",
            salience=1.1,
        )


def test_persona_state_keeps_plans_and_cooldowns_private_and_immutable() -> None:
    now = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)
    config = CognitiveConfig(
        attention_budget=3,
        retention=5,
        recency_weight=1.0,
        relevance_weight=1.0,
        importance_weight=1.0,
        recency_decay=0.99,
        reflection_threshold=150.0,
        reflection_count=5,
    )
    action = ActiveAction(
        subject="anon",
        predicate="waits-for",
        object="coffee-42",
        description="wait for the coffee",
        started_at=now,
        planned_duration_minutes=10,
    )
    state = PersonaState(
        agent_id="anon",
        cognitive_config=config,
        reflection_remaining=130.0,
        last_world_time=now,
        last_world_version=7,
        daily_plan=DailyPlan(
            for_date=date(2026, 8, 31),
            schedule=(
                ScheduleItem(
                    description="visit the cafe",
                    planned_duration_minutes=60,
                ),
            ),
        ),
        current_daily_intentions=("talk with Soyo",),
        active_action=action,
        reflection_new_memory_count=2,
        conversation_cooldowns=(
            ConversationCooldown(peer_agent_id="soyo", until=now + timedelta(minutes=20)),
        ),
        known_place_ids=("cafe", "ring"),
    )

    assert state.active_action == action
    assert state.last_world_version == 7
    assert state.known_place_ids == ("cafe", "ring")
    with pytest.raises(ValidationError, match="frozen"):
        state.reflection_new_memory_count = 3


def test_persona_state_rejects_naive_time_and_mutable_collections() -> None:
    config = CognitiveConfig(
        attention_budget=3,
        retention=5,
        recency_weight=1.0,
        relevance_weight=1.0,
        importance_weight=1.0,
        recency_decay=0.99,
        reflection_threshold=150.0,
        reflection_count=5,
    )

    with pytest.raises(ValidationError, match="timezone info"):
        PersonaState.model_validate(
            {
                "agent_id": "anon",
                "cognitive_config": config,
                "reflection_remaining": 150.0,
                "last_world_time": datetime.fromisoformat("2026-08-31T09:00:00"),
            },
            strict=True,
        )
    with pytest.raises(ValidationError, match="valid tuple"):
        PersonaState.model_validate(
            {
                "agent_id": "anon",
                "cognitive_config": config,
                "reflection_remaining": 150.0,
                "known_place_ids": ["cafe"],
            },
            strict=True,
        )


def test_calculate_new_day_uses_the_full_calendar_date() -> None:
    current = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)

    assert calculate_new_day(None, current) is NewDayStatus.FIRST_DAY
    assert calculate_new_day(current, current + timedelta(hours=1)) is NewDayStatus.SAME_DAY
    assert calculate_new_day(current, current + timedelta(days=1)) is NewDayStatus.NEW_DAY
    assert calculate_new_day(current, current.replace(year=2027)) is NewDayStatus.NEW_DAY


def test_active_action_finishes_at_or_after_planned_end() -> None:
    started_at = datetime(2026, 8, 31, 9, 0, 30, tzinfo=UTC)
    action = ActiveAction(
        subject="anon",
        predicate="waits-for",
        object="coffee-42",
        description="wait for the coffee",
        started_at=started_at,
        planned_duration_minutes=10,
    )
    planned_end = started_at + timedelta(minutes=10)

    assert is_active_action_finished(None, started_at) is True
    assert is_active_action_finished(action, planned_end - timedelta(microseconds=1)) is False
    assert is_active_action_finished(action, planned_end) is True
    assert is_active_action_finished(action, planned_end + timedelta(minutes=1)) is True
