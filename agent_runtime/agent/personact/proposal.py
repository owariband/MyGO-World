"""Final ActionProposal construction and authority validation.

The cognitive sequence lives in ``loop.py``. This module deliberately contains no
placeholder perceive/retrieve nodes: it only turns a strategy draft into the
single world-facing proposal after checking compiled and current-world grants.
"""

from pydantic import ValidationError

from agent_runtime.agent.personact.compiler import CompiledPersonActSpec
from agent_runtime.agent.personact.errors import ProposalValidationError
from agent_runtime.model import StrictModel
from agent_runtime.world.contracts import (
    ActionProposal,
    AgentAction,
    Identifier,
    InteractAction,
    InteractionTarget,
    NoOpAction,
    PerceptionFrame,
    ProposalKind,
    RespondAction,
    UtterAction,
)


class ProposalDraft(StrictModel):
    """Typed strategy output before runtime authority is applied."""

    action: AgentAction
    evidence_ids: tuple[Identifier, ...] = ()


def build_action_proposal(
    *,
    spec: CompiledPersonActSpec,
    frame: PerceptionFrame,
    proposal_id: Identifier,
    draft: ProposalDraft,
) -> ActionProposal:
    """Validate one draft and inject the actor and snapshot envelope."""

    try:
        validated_spec = CompiledPersonActSpec.model_validate(spec, strict=True)
        validated_frame = PerceptionFrame.model_validate(frame, strict=True)
        validated_draft = ProposalDraft.model_validate(draft, strict=True)
    except ValidationError as error:
        raise ProposalValidationError("proposal inputs failed strict validation") from error

    if validated_frame.agent_id != validated_spec.agent_id:
        raise ProposalValidationError(
            f'frame belongs to agent "{validated_frame.agent_id}", not "{validated_spec.agent_id}"'
        )

    action_kind = ProposalKind(validated_draft.action.kind)
    if action_kind not in validated_spec.allowed_proposal_kinds:
        raise ProposalValidationError(
            f'proposal kind "{action_kind.value}" is not granted by compiled spec'
        )
    if isinstance(validated_draft.action, NoOpAction):
        if validated_draft.evidence_ids:
            raise ProposalValidationError("no_op proposal cannot carry evidence")
    elif not any(
        affordance.kind is action_kind and affordance.target == _target(validated_draft.action)
        for affordance in validated_frame.affordances
    ):
        raise ProposalValidationError(
            f'proposal kind "{action_kind.value}" and target are not afforded by current frame'
        )

    if not set(validated_draft.evidence_ids).issubset(validated_frame.visible_evidence_ids):
        raise ProposalValidationError("proposal references evidence outside the current frame")

    return ActionProposal(
        proposal_id=proposal_id,
        agent_id=validated_spec.agent_id,
        event_session_id=validated_frame.event_session_id,
        based_on_world_version=validated_frame.based_on_world_version,
        action=validated_draft.action,
        evidence_ids=validated_draft.evidence_ids,
    )


def _target(action: AgentAction) -> InteractionTarget | None:
    if isinstance(action, (InteractAction, UtterAction, RespondAction)):
        return action.target
    return None
