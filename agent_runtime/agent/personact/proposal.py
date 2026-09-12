"""Final ActionProposal construction and authority validation.

The cognitive sequence lives in ``loop.py``. This module deliberately contains no
placeholder perceive/retrieve nodes: it only turns a strategy draft into the
single world-facing proposal after checking compiled and current-world grants.
"""

from pydantic import ValidationError

from agent_runtime.agent.personact.compiler import CompiledPersonActSpec
from agent_runtime.agent.personact.errors import ProposalValidationError
from agent_runtime.agent.personact.state import PlanDisposition
from agent_runtime.model import StrictModel
from agent_runtime.world.contracts import (
    ActAction,
    ActionProposal,
    Affordance,
    AgentAction,
    AgentView,
    Identifier,
    InteractAction,
    InteractionTarget,
    NoOpAction,
    ProposalKind,
    RespondAction,
    UtterAction,
    WorldRef,
)


class ProposalDraft(StrictModel):
    """Typed strategy output before runtime authority is applied."""

    action: AgentAction
    evidence_ids: tuple[Identifier, ...] = ()
    plan_disposition: PlanDisposition = PlanDisposition.KEEP


def build_action_proposal(
    *,
    world_ref: WorldRef,
    spec: CompiledPersonActSpec,
    view: AgentView,
    proposal_id: Identifier,
    draft: ProposalDraft,
) -> ActionProposal:
    """Validate one draft and inject the actor and snapshot envelope."""

    try:
        validated_ref = WorldRef.model_validate(world_ref, strict=True)
        validated_spec = CompiledPersonActSpec.model_validate(spec, strict=True)
        validated_view = AgentView.model_validate(view, strict=True)
        validated_draft = ProposalDraft.model_validate(draft, strict=True)
    except ValidationError as error:
        raise ProposalValidationError("proposal inputs failed strict validation") from error

    if validated_ref.project_id != validated_spec.project_id:
        raise ProposalValidationError("WorldRef project does not match compiled spec")
    if validated_view.world_ref != validated_ref:
        raise ProposalValidationError("view belongs to a different WorldRef")
    if validated_view.agent_id != validated_spec.agent_id:
        raise ProposalValidationError(
            f'view belongs to agent "{validated_view.agent_id}", not "{validated_spec.agent_id}"'
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
        _matches_affordance(validated_draft.action, affordance)
        for affordance in validated_view.affordances
    ):
        raise ProposalValidationError(
            f'proposal kind "{action_kind.value}" and target are not afforded by current view'
        )

    if not set(validated_draft.evidence_ids).issubset(validated_view.visible_evidence_ids):
        raise ProposalValidationError("proposal references evidence outside the current view")

    return ActionProposal(
        world_ref=validated_ref,
        proposal_id=proposal_id,
        agent_id=validated_spec.agent_id,
        event_session_id=validated_view.event_session_id,
        based_on_world_version=validated_view.based_on_world_version,
        based_on_control_epoch=validated_view.based_on_control_epoch,
        based_on_decision_seq=validated_view.based_on_decision_seq,
        action=validated_draft.action,
        evidence_ids=validated_draft.evidence_ids,
    )


def _target(action: AgentAction) -> InteractionTarget | None:
    if isinstance(action, (InteractAction, UtterAction, RespondAction)):
        return action.target
    return None


def _matches_affordance(action: AgentAction, affordance: Affordance) -> bool:
    if affordance.kind is not ProposalKind(action.kind) or affordance.target != _target(action):
        return False
    if (
        isinstance(action, (ActAction, InteractAction, UtterAction, RespondAction))
        and action.affordance_id != affordance.affordance_id
    ):
        return False
    if isinstance(action, RespondAction):
        return action.in_reply_to_entry_id == affordance.request_entry_id
    return True
