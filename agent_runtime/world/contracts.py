"""World-owned boundary contracts shared with Agent code.

This module does not implement the World Runtime. It owns the values that the
future World layer gives to an Agent and accepts back from it, so PersonAct
cannot redefine visibility, affordances, or what counts as a world proposal.
"""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from agent_runtime.model import StrictModel

Identifier = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]
WorldVersion = Annotated[int, Field(ge=1)]
ControlEpoch = Annotated[int, Field(ge=1)]
DecisionSequence = Annotated[int, Field(ge=0)]
EntryIndex = Annotated[int, Field(ge=0)]
Salience = Annotated[float, Field(ge=0.0, le=1.0)]


class WorldRef(StrictModel):
    """Ownership of one story world; stable across saves, not an authorization token."""

    project_id: Identifier
    world_id: Identifier


class CommitPosition(StrictModel):
    """Stable ordering of one committed Entry inside a World."""

    world_version: WorldVersion
    entry_index: EntryIndex = 0


class ProposalKind(StrEnum):
    ACT = "act"
    INTERACT = "interact"
    UTTER = "utter"
    RESPOND = "respond"
    WAIT = "wait"
    NO_OP = "no_op"


class PerceptionChannel(StrEnum):
    """How a committed fact became perceptible to one actor."""

    SELF = "self"
    DIRECT_INTERACTION = "direct_interaction"
    SAME_SCENE = "same_scene"
    TARGETED_MESSAGE = "targeted_message"
    COMMITMENT_UPDATE = "commitment_update"


class AttentionTier(StrEnum):
    """Stable priority assigned before Persona attention is applied."""

    MANDATORY = "mandatory"
    RELEVANT = "relevant"
    AMBIENT = "ambient"


class DeliveryChannel(StrEnum):
    """How one Entry is delivered to its materialized recipients."""

    DIRECT = "direct"
    WHISPER = "whisper"
    PUBLIC = "public"


class CharacterTarget(StrictModel):
    """A character that the deciding Persona may address or interact with."""

    kind: Literal["character"] = "character"
    id: Identifier


class ObjectTarget(StrictModel):
    """A world object that the deciding Persona may interact with."""

    kind: Literal["object"] = "object"
    id: Identifier


InteractionTarget = Annotated[
    CharacterTarget | ObjectTarget,
    Field(discriminator="kind"),
]


class Affordance(StrictModel):
    """One semantic action currently allowed by the committed world view."""

    affordance_id: Identifier
    kind: ProposalKind
    target: InteractionTarget | None = None
    operation_id: Identifier | None = None
    delivery_channel: DeliveryChannel | None = None
    request_entry_id: Identifier | None = None

    @model_validator(mode="after")
    def _validate_target(self) -> Self:
        targeted = {
            ProposalKind.INTERACT,
            ProposalKind.UTTER,
            ProposalKind.RESPOND,
        }
        if self.kind in targeted and self.target is None:
            raise ValueError(f"{self.kind.value} affordance requires a target")
        if self.kind in {ProposalKind.UTTER, ProposalKind.RESPOND} and not isinstance(
            self.target, CharacterTarget
        ):
            raise ValueError(f"{self.kind.value} affordance requires a character target")
        if self.kind not in targeted and self.target is not None:
            raise ValueError(f"{self.kind.value} affordance cannot carry a target")

        if self.kind is ProposalKind.INTERACT:
            if isinstance(self.target, ObjectTarget) and self.operation_id is None:
                raise ValueError("object interact affordance requires an operationId")
            if isinstance(self.target, CharacterTarget) and self.operation_id is not None:
                raise ValueError("character interact affordance cannot carry an operationId")
            if self.delivery_channel is not None or self.request_entry_id is not None:
                raise ValueError("interact affordance cannot carry dialogue routing")
        elif self.kind is ProposalKind.UTTER:
            if self.delivery_channel not in {
                DeliveryChannel.DIRECT,
                DeliveryChannel.WHISPER,
            }:
                raise ValueError("utter affordance requires a direct or whisper channel")
            if self.operation_id is not None or self.request_entry_id is not None:
                raise ValueError("utter affordance cannot carry an operation or request source")
        elif self.kind is ProposalKind.RESPOND:
            if self.delivery_channel not in {
                DeliveryChannel.DIRECT,
                DeliveryChannel.WHISPER,
            }:
                raise ValueError("respond affordance requires a direct or whisper channel")
            if self.request_entry_id is None:
                raise ValueError("respond affordance requires a requestEntryId")
            if self.operation_id is not None:
                raise ValueError("respond affordance cannot carry an operation")
        elif any(
            value is not None
            for value in (self.operation_id, self.delivery_channel, self.request_entry_id)
        ):
            raise ValueError(f"{self.kind.value} affordance cannot carry routing details")
        return self


class PerceptCandidate(StrictModel):
    """World-filtered material that this actor may notice."""

    candidate_id: Identifier
    channel: PerceptionChannel
    attention_tier: AttentionTier
    subject: Identifier
    predicate: Identifier
    object: Identifier | None = None
    content: Identifier
    salience: Salience
    source_entry_id: Identifier | None = None
    visible_fields: tuple[Identifier, ...] = ()
    tags: tuple[Identifier, ...] = ()
    source_fact_refs: tuple[Identifier, ...] = ()
    source_info_refs: tuple[Identifier, ...] = ()


class AgentView(StrictModel):
    """The complete world-facing input visible to one actor for one decision."""

    world_ref: WorldRef
    agent_id: Identifier
    event_session_id: Identifier
    based_on_world_version: WorldVersion
    based_on_control_epoch: ControlEpoch
    based_on_decision_seq: DecisionSequence
    current_location_id: Identifier
    world_time: AwareDatetime | None = None
    observed_through: CommitPosition | None = None
    candidates: tuple[PerceptCandidate, ...] = ()
    visible_evidence_ids: tuple[Identifier, ...] = ()
    affordances: tuple[Affordance, ...]

    @model_validator(mode="after")
    def _validate_snapshot(self) -> Self:
        if (
            self.observed_through is not None
            and self.observed_through.world_version > self.based_on_world_version
        ):
            raise ValueError("observedThrough cannot be newer than the Agent view")
        affordance_ids = tuple(item.affordance_id for item in self.affordances)
        if len(affordance_ids) != len(set(affordance_ids)):
            raise ValueError("Agent view affordance ids must be unique")
        return self


ActionText = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]


class ActAction(StrictModel):
    kind: Literal["act"] = "act"
    description: ActionText


class InteractAction(StrictModel):
    kind: Literal["interact"] = "interact"
    affordance_id: Identifier
    target: InteractionTarget
    description: ActionText


class UtterAction(StrictModel):
    kind: Literal["utter"] = "utter"
    affordance_id: Identifier
    target: CharacterTarget
    content: ActionText
    expects_response: bool


class RespondAction(StrictModel):
    kind: Literal["respond"] = "respond"
    affordance_id: Identifier
    target: CharacterTarget
    content: ActionText
    in_reply_to_entry_id: Identifier


class WaitAction(StrictModel):
    kind: Literal["wait"] = "wait"
    description: ActionText
    next_wakeup: ActionText


class NoOpAction(StrictModel):
    kind: Literal["no_op"] = "no_op"
    next_wakeup: ActionText


AgentAction = Annotated[
    ActAction | InteractAction | UtterAction | RespondAction | WaitAction | NoOpAction,
    Field(discriminator="kind"),
]


class ActionProposal(StrictModel):
    """A candidate action; only WorldUpdater can turn it into fact."""

    world_ref: WorldRef
    proposal_id: Identifier
    agent_id: Identifier
    event_session_id: Identifier
    based_on_world_version: WorldVersion
    based_on_control_epoch: ControlEpoch
    based_on_decision_seq: DecisionSequence
    action: AgentAction
    evidence_ids: tuple[Identifier, ...] = ()
