"""Private, immutable cognitive state for one PersonAct agent."""

from datetime import date, timedelta
from enum import StrEnum
from typing import Annotated

from pydantic import AwareDatetime, Field, StringConstraints

from agent_runtime.model import StrictModel
from agent_runtime.world.contracts import Identifier, WorldVersion

NonEmptyText = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]
PositiveCount = Annotated[int, Field(ge=1)]
NonNegativeCount = Annotated[int, Field(ge=0)]
PositiveMinutes = Annotated[int, Field(ge=1)]
NonNegativeScore = Annotated[float, Field(ge=0.0)]
Decay = Annotated[float, Field(ge=0.0, le=1.0)]


class CognitiveConfig(StrictModel):
    """Stable attention, retrieval, and reflection controls for one Persona."""

    attention_budget: PositiveCount
    retention: PositiveCount
    recency_weight: NonNegativeScore
    relevance_weight: NonNegativeScore
    importance_weight: NonNegativeScore
    recency_decay: Decay
    reflection_threshold: Annotated[float, Field(gt=0.0)]
    reflection_count: PositiveCount


class ScheduleItem(StrictModel):
    """A private planning hint, not a committed World duration."""

    description: NonEmptyText
    planned_duration_minutes: PositiveMinutes


class DailyPlan(StrictModel):
    """The Persona's progressively refinable schedule for one local date."""

    for_date: date
    schedule: tuple[ScheduleItem, ...] = ()


class ActiveAction(StrictModel):
    """The Persona's current planned action, not proof that it occurred."""

    subject: NonEmptyText
    predicate: NonEmptyText
    object: NonEmptyText | None = None
    description: NonEmptyText
    started_at: AwareDatetime
    planned_duration_minutes: PositiveMinutes


class ConversationCooldown(StrictModel):
    """An absolute private cooldown replacing Stanford's tick counter."""

    peer_agent_id: Identifier
    until: AwareDatetime


class PersonaState(StrictModel):
    """Private Scratch-equivalent state owned by exactly one Persona."""

    agent_id: Identifier
    cognitive_config: CognitiveConfig
    reflection_remaining: NonNegativeScore
    last_world_time: AwareDatetime | None = None
    last_world_version: WorldVersion | None = None
    daily_plan: DailyPlan | None = None
    current_daily_intentions: tuple[NonEmptyText, ...] = ()
    active_action: ActiveAction | None = None
    reflection_new_memory_count: NonNegativeCount = 0
    conversation_cooldowns: tuple[ConversationCooldown, ...] = ()
    known_place_ids: tuple[Identifier, ...] = ()


class NewDayStatus(StrEnum):
    FIRST_DAY = "first_day"
    NEW_DAY = "new_day"
    SAME_DAY = "same_day"


def calculate_new_day(
    previous_world_time: AwareDatetime | None,
    world_time: AwareDatetime,
) -> NewDayStatus:
    """Classify a decision time without mutating Persona state."""

    if previous_world_time is None:
        return NewDayStatus.FIRST_DAY
    if previous_world_time.date() != world_time.date():
        return NewDayStatus.NEW_DAY
    return NewDayStatus.SAME_DAY


def is_active_action_finished(
    active_action: ActiveAction | None,
    world_time: AwareDatetime,
) -> bool:
    """Return whether a private action hint has reached its planned end."""

    if active_action is None:
        return True
    planned_end = active_action.started_at + timedelta(
        minutes=active_action.planned_duration_minutes
    )
    return world_time >= planned_end
