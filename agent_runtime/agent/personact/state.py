"""Private, immutable cognitive state for one PersonAct agent."""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from agent_runtime.agent.memory import MemoryRecord, MemoryTouch
from agent_runtime.model import StrictModel
from agent_runtime.world.contracts import CommitPosition, Identifier, WorldRef, WorldVersion

NonEmptyText = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]
PositiveCount = Annotated[int, Field(ge=1)]
NonNegativeCount = Annotated[int, Field(ge=0)]
NonNegativeScore = Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
Decay = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]


class CognitiveConfig(StrictModel):
    """Stable attention, retrieval, and reflection controls for one Persona."""

    attention_budget: PositiveCount
    retention: PositiveCount
    recency_weight: NonNegativeScore
    relevance_weight: NonNegativeScore
    importance_weight: NonNegativeScore
    recency_decay: Decay
    reflection_threshold: Annotated[float, Field(gt=0.0, allow_inf_nan=False)]
    reflection_count: PositiveCount


class PlanItem(StrictModel):
    """One private intention, without a date or a claim of world completion."""

    plan_id: Identifier
    description: NonEmptyText


class PlanDisposition(StrEnum):
    """How one private plan changes after the World reports an outcome."""

    KEEP = "keep"
    COMPLETE_ON_APPLIED = "complete_on_applied"
    CANCEL = "cancel"


class DecisionOutcome(StrEnum):
    """World outcome retained with the latest durable Persona decision."""

    APPLIED = "applied"
    NOT_APPLIED = "not_applied"
    WAIT = "wait"
    NO_OP = "no_op"


class PersonaState(StrictModel):
    """Private Scratch-equivalent state owned by exactly one Persona."""

    world_ref: WorldRef
    agent_id: Identifier
    cognitive_config: CognitiveConfig
    reflection_remaining: NonNegativeScore
    last_world_time: AwareDatetime | None = None
    last_world_version: WorldVersion | None = None
    plan_queue: tuple[PlanItem, ...] = ()
    active_plan_id: Identifier | None = None
    reflection_new_memory_count: NonNegativeCount = 0
    known_place_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def _validate_plan_queue(self) -> Self:
        ids = tuple(item.plan_id for item in self.plan_queue)
        if len(ids) != len(set(ids)):
            raise ValueError("planQueue plan ids must be unique")
        if self.active_plan_id is not None and self.active_plan_id not in ids:
            raise ValueError("activePlanId must reference an item in planQueue")
        return self

    @property
    def active_plan(self) -> PlanItem | None:
        return next(
            (item for item in self.plan_queue if item.plan_id == self.active_plan_id),
            None,
        )


class PersonActStateUpdate(StrictModel):
    """Private values staged with one accepted World decision."""

    world_ref: WorldRef
    agent_id: Identifier
    decision_id: Identifier
    outcome: DecisionOutcome
    state: PersonaState
    memory_writes: tuple[MemoryRecord, ...] = ()
    memory_touches: tuple[MemoryTouch, ...] = ()
    observation_cursor: CommitPosition | None = None
    plan_disposition: PlanDisposition = PlanDisposition.KEEP

    @model_validator(mode="after")
    def _validate_owners(self) -> Self:
        if self.state.world_ref != self.world_ref or self.state.agent_id != self.agent_id:
            raise ValueError("PersonaState update ownership does not match its envelope")
        for record in self.memory_writes:
            if record.world_ref != self.world_ref or record.agent_id != self.agent_id:
                raise ValueError("Memory write ownership does not match its Persona update")
        for touch in self.memory_touches:
            if touch.world_ref != self.world_ref or touch.agent_id != self.agent_id:
                raise ValueError("Memory touch ownership does not match its Persona update")
        return self
