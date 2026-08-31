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
EventRevision = Annotated[int, Field(ge=1)]
Salience = Annotated[float, Field(ge=0.0, le=1.0)]


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

    kind: ProposalKind
    target: InteractionTarget | None = None

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
    source_event_id: Identifier | None = None
    event_revision: EventRevision | None = None
    visible_fields: tuple[Identifier, ...] = ()
    tags: tuple[Identifier, ...] = ()
    source_fact_refs: tuple[Identifier, ...] = ()
    source_info_refs: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def _validate_event_identity(self) -> Self:
        if (self.source_event_id is None) != (self.event_revision is None):
            raise ValueError("sourceEventId and eventRevision must be provided together")
        return self


class PerceptionFrame(StrictModel):
    """The complete world-facing input visible to one actor for one decision."""

    agent_id: Identifier
    event_session_id: Identifier
    based_on_world_version: WorldVersion
    current_location_id: Identifier
    world_time: AwareDatetime | None = None
    candidates: tuple[PerceptCandidate, ...] = ()
    visible_evidence_ids: tuple[Identifier, ...] = ()
    affordances: tuple[Affordance, ...]


ActionText = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]


class ActAction(StrictModel):
    kind: Literal["act"] = "act"
    description: ActionText


class InteractAction(StrictModel):
    kind: Literal["interact"] = "interact"
    target: InteractionTarget
    description: ActionText


class UtterAction(StrictModel):
    kind: Literal["utter"] = "utter"
    target: CharacterTarget
    content: ActionText


class RespondAction(StrictModel):
    kind: Literal["respond"] = "respond"
    target: CharacterTarget
    content: ActionText


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
    """A candidate action; only World Committer can turn it into fact."""

    proposal_id: Identifier
    agent_id: Identifier
    event_session_id: Identifier
    based_on_world_version: WorldVersion
    action: AgentAction
    evidence_ids: tuple[Identifier, ...] = ()
