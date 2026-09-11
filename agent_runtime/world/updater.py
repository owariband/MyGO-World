"""Pure proposal validation and caller-owned public World writes."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Self

from pydantic import AwareDatetime, model_validator
from sqlalchemy.orm import Session

from agent_runtime.model import StrictModel
from agent_runtime.scenario import ObjectOperationSeed, ObjectSeed
from agent_runtime.world.affordances import WorldAffordanceResolver
from agent_runtime.world.contracts import (
    ActAction,
    ActionProposal,
    Affordance,
    AgentView,
    CharacterTarget,
    CommitPosition,
    ControlEpoch,
    DecisionSequence,
    DeliveryChannel,
    Identifier,
    InteractAction,
    NoOpAction,
    ObjectTarget,
    ProposalKind,
    RespondAction,
    UtterAction,
    WaitAction,
    WorldRef,
    WorldVersion,
)
from agent_runtime.world.entries import (
    ActionEntry,
    AudienceMode,
    DialogueEntry,
    EntryRelationKind,
    EventEntry,
    EventEntryLink,
    EventEntryRecipient,
    InteractionRequest,
    InteractionRequestStatus,
)
from agent_runtime.world.entry_storage import EventEntryStore
from agent_runtime.world.state import EventSessionNode, PublicWorldState, WorldStatus
from agent_runtime.world.storage import WorldStore


class WorldUpdateValidationError(ValueError):
    """A Proposal cannot be derived from the trusted committed snapshot."""


class WorldUpdateStatus(StrEnum):
    """The public-world meaning of one accepted Character decision."""

    APPLIED = "applied"
    NOT_APPLIED = "not_applied"
    WAIT = "wait"
    NO_OP = "no_op"


class ObjectStateChange(StrictModel):
    """One exact Scenario-registered Object transition."""

    world_ref: WorldRef
    object_id: Identifier
    expected_state: Identifier
    new_state: Identifier


class WorldUpdatePlan(StrictModel):
    """Fully validated public writes for one decision; contains no private state."""

    world_ref: WorldRef
    decision_id: Identifier
    agent_id: Identifier
    event_session_id: Identifier
    expected_world_version: WorldVersion
    expected_control_epoch: ControlEpoch
    expected_decision_seq: DecisionSequence
    status: WorldUpdateStatus
    entry: EventEntry | None = None
    links: tuple[EventEntryLink, ...] = ()
    recipients: tuple[EventEntryRecipient, ...] = ()
    create_request: InteractionRequest | None = None
    resolve_request_entry_id: Identifier | None = None
    object_change: ObjectStateChange | None = None

    @model_validator(mode="after")
    def _validate_public_writes(self) -> Self:
        if self.expected_world_version < 1:
            raise ValueError("expectedWorldVersion must be positive")
        if self.expected_control_epoch < 1:
            raise ValueError("expectedControlEpoch must be positive")
        if self.expected_decision_seq < 0:
            raise ValueError("expectedDecisionSeq cannot be negative")

        writes = (
            self.entry is not None,
            bool(self.links),
            bool(self.recipients),
            self.create_request is not None,
            self.resolve_request_entry_id is not None,
            self.object_change is not None,
        )
        if self.status is not WorldUpdateStatus.APPLIED:
            if any(writes):
                raise ValueError("a non-applied World plan cannot contain public writes")
            return self
        if self.entry is None or not self.recipients:
            raise ValueError("an applied World plan requires one Entry and recipients")

        entry = self.entry
        if entry.world_ref != self.world_ref or entry.source_id != self.decision_id:
            raise ValueError("Entry ownership or source does not match the World plan")
        if entry.actor_agent_id != self.agent_id:
            raise ValueError("Entry actor does not match the World plan")
        if entry.commit_position.world_version != self.expected_world_version + 1:
            raise ValueError("Entry must commit at the next World version")
        if entry.commit_position.entry_index != 0:
            raise ValueError("M3 permits only one Entry per decision")

        recipient_ids: list[str] = []
        for recipient in self.recipients:
            if recipient.world_ref != self.world_ref or recipient.entry_id != entry.entry_id:
                raise ValueError("Entry recipient does not match the World plan")
            recipient_ids.append(recipient.agent_id)
        if len(recipient_ids) != len(set(recipient_ids)):
            raise ValueError("Entry recipient Agent IDs must be unique")
        if self.agent_id not in recipient_ids:
            raise ValueError("Entry recipients must include the actor")

        for link in self.links:
            if link.world_ref != self.world_ref or link.entry_id != entry.entry_id:
                raise ValueError("Entry link does not match the World plan")
        link_keys = tuple(
            (link.relation_kind, link.related_entry_id, link.relation_order) for link in self.links
        )
        if len(link_keys) != len(set(link_keys)):
            raise ValueError("Entry links must be unique")

        if isinstance(entry, ActionEntry) != (self.object_change is not None):
            raise ValueError("only an ActionEntry may carry one Object state change")
        if self.object_change is not None:
            if self.object_change.world_ref != self.world_ref:
                raise ValueError("Object change belongs to a different WorldRef")
            if not isinstance(entry, ActionEntry):
                raise ValueError("Object change requires an ActionEntry")
            if self.object_change.object_id != entry.target_object_id:
                raise ValueError("Object change target does not match its Entry")

        if self.create_request is not None:
            request = self.create_request
            if not isinstance(entry, DialogueEntry):
                raise ValueError("only a DialogueEntry may create a response request")
            if request.world_ref != self.world_ref or request.request_entry_id != entry.entry_id:
                raise ValueError("created request does not match its source Entry")
            if request.status is not InteractionRequestStatus.PENDING:
                raise ValueError("a new request must be pending")
            if (
                request.requester_agent_id != entry.actor_agent_id
                or request.recipient_agent_id != entry.target_agent_id
            ):
                raise ValueError("created request participants do not match its Entry")
            if request.updated_world_version != entry.commit_position.world_version:
                raise ValueError("created request must use its Entry World version")
        if self.resolve_request_entry_id is not None:
            if not isinstance(entry, DialogueEntry):
                raise ValueError("only a DialogueEntry may resolve a response request")
            if not any(
                link.relation_kind is EntryRelationKind.REPLY
                and link.related_entry_id == self.resolve_request_entry_id
                for link in self.links
            ):
                raise ValueError("resolving a request requires its explicit reply link")
        if self.create_request is not None and self.resolve_request_entry_id is not None:
            raise ValueError("one Entry cannot create and resolve a response request")
        return self


class WorldUpdateResult(StrictModel):
    """Public outcome plus current snapshot and the Entry's own commit position."""

    world_ref: WorldRef
    decision_id: Identifier
    status: WorldUpdateStatus
    current_world_version: WorldVersion
    current_decision_seq: DecisionSequence
    entry_id: Identifier | None = None
    entry_position: CommitPosition | None = None
    replayed: bool = False

    @model_validator(mode="after")
    def _validate_entry_result(self) -> Self:
        if (self.entry_id is None) != (self.entry_position is None):
            raise ValueError("entryId and entryPosition must either both be set or both be absent")
        if self.status is WorldUpdateStatus.APPLIED and self.entry_id is None:
            raise ValueError("an applied World result requires its committed Entry identity")
        if self.status is not WorldUpdateStatus.APPLIED and self.entry_id is not None:
            raise ValueError("a non-applied World result cannot carry an Entry identity")
        if (
            self.entry_position is not None
            and self.entry_position.world_version > self.current_world_version
        ):
            raise ValueError("Entry position cannot be newer than the current World snapshot")
        return self


class WorldChangeValidator:
    """Derive one public update plan without reading or writing a database."""

    def __init__(self, world_ref: WorldRef, object_seeds: tuple[ObjectSeed, ...]) -> None:
        self._world_ref = WorldRef.model_validate(world_ref, strict=True)
        self._object_seeds = tuple(
            ObjectSeed.model_validate(seed, strict=True) for seed in object_seeds
        )
        ids = tuple(seed.id for seed in self._object_seeds)
        if len(ids) != len(set(ids)):
            raise ValueError("Scenario Object IDs must be unique")

    def plan(
        self,
        proposal: ActionProposal,
        view: AgentView,
        public_state: PublicWorldState,
        *,
        created_at: AwareDatetime,
        previous_entry: EventEntry | None = None,
        response_request: InteractionRequest | None = None,
        response_source: DialogueEntry | None = None,
    ) -> WorldUpdatePlan:
        """Validate authority and resolve trusted World effects for one Proposal."""

        proposal = ActionProposal.model_validate(proposal, strict=True)
        view = AgentView.model_validate(view, strict=True)
        public_state = PublicWorldState.model_validate(public_state, strict=True)
        _require_aware(created_at)
        self._require_envelopes(proposal, view, public_state)

        action = proposal.action
        if isinstance(action, NoOpAction):
            return self._empty_plan(proposal, WorldUpdateStatus.NO_OP)
        if isinstance(action, WaitAction):
            self._require_simple_affordance(view, proposal, ProposalKind.WAIT)
            return self._empty_plan(proposal, WorldUpdateStatus.WAIT)
        if isinstance(action, ActAction):
            self._require_simple_affordance(view, proposal, ProposalKind.ACT)
            return self._empty_plan(proposal, WorldUpdateStatus.NOT_APPLIED)

        affordance = self._find_action_affordance(view, action.affordance_id)
        if affordance.kind.value != action.kind or affordance.target != action.target:
            raise WorldUpdateValidationError("selected affordance does not match the action")
        if isinstance(action, InteractAction):
            if isinstance(action.target, CharacterTarget):
                return self._empty_plan(proposal, WorldUpdateStatus.NOT_APPLIED)
            return self._object_plan(
                proposal,
                view,
                public_state,
                action,
                affordance,
                created_at=created_at,
                previous_entry=previous_entry,
            )
        if isinstance(action, UtterAction):
            return self._utter_plan(
                proposal,
                public_state,
                action,
                affordance,
                created_at=created_at,
                previous_entry=previous_entry,
            )
        return self._response_plan(
            proposal,
            view,
            public_state,
            action,
            affordance,
            created_at=created_at,
            previous_entry=previous_entry,
            response_request=response_request,
            response_source=response_source,
        )

    def _require_envelopes(
        self,
        proposal: ActionProposal,
        view: AgentView,
        state: PublicWorldState,
    ) -> None:
        world = state.world
        if proposal.world_ref != self._world_ref or view.world_ref != self._world_ref:
            raise WorldUpdateValidationError("Proposal or view belongs to another WorldRef")
        if world.world_ref != self._world_ref:
            raise WorldUpdateValidationError("public state belongs to another WorldRef")
        if world.status is not WorldStatus.RUNNING:
            raise WorldUpdateValidationError("World must be running before a Character step")
        if proposal.agent_id != view.agent_id:
            raise WorldUpdateValidationError("Proposal and view Agent identities differ")
        if proposal.event_session_id != view.event_session_id:
            raise WorldUpdateValidationError("Proposal and view EventSession identities differ")
        if not set(proposal.evidence_ids).issubset(view.visible_evidence_ids):
            raise WorldUpdateValidationError("Proposal references evidence outside the Agent view")
        proposal_fence = (
            proposal.based_on_world_version,
            proposal.based_on_control_epoch,
            proposal.based_on_decision_seq,
        )
        view_fence = (
            view.based_on_world_version,
            view.based_on_control_epoch,
            view.based_on_decision_seq,
        )
        world_fence = (world.current_version, world.control_epoch, world.decision_seq)
        if proposal_fence != view_fence or proposal_fence != world_fence:
            raise WorldUpdateValidationError("Proposal snapshot fences are stale")
        node = _session_for_agent(state, proposal.agent_id)
        if node.session_id != proposal.event_session_id:
            raise WorldUpdateValidationError("Proposal uses the wrong stable EventSession")

    def _empty_plan(
        self,
        proposal: ActionProposal,
        status: WorldUpdateStatus,
    ) -> WorldUpdatePlan:
        return WorldUpdatePlan(
            world_ref=self._world_ref,
            decision_id=proposal.proposal_id,
            agent_id=proposal.agent_id,
            event_session_id=proposal.event_session_id,
            expected_world_version=proposal.based_on_world_version,
            expected_control_epoch=proposal.based_on_control_epoch,
            expected_decision_seq=proposal.based_on_decision_seq,
            status=status,
        )

    def _object_plan(
        self,
        proposal: ActionProposal,
        view: AgentView,
        state: PublicWorldState,
        action: InteractAction,
        affordance: Affordance,
        *,
        created_at: datetime,
        previous_entry: EventEntry | None,
    ) -> WorldUpdatePlan:
        if not isinstance(action.target, ObjectTarget):
            raise WorldUpdateValidationError("Object operation requires an Object target")
        object_state = next(
            (item for item in state.objects if item.object_id == action.target.id),
            None,
        )
        object_seed = next(
            (item for item in self._object_seeds if item.id == action.target.id),
            None,
        )
        actor = next(item for item in state.agents if item.agent_id == proposal.agent_id)
        if (
            object_state is None
            or object_seed is None
            or object_state.location_id != actor.location_id
        ):
            raise WorldUpdateValidationError("Object is not interactable from the Agent location")
        expected = WorldAffordanceResolver(
            world_ref=self._world_ref,
            world_version=state.world.current_version,
            agent_id=proposal.agent_id,
        ).object_affordances(object_state=object_state, object_seed=object_seed)
        if affordance not in expected or affordance.operation_id is None:
            raise WorldUpdateValidationError("Object affordance is forged or stale")
        operation = _operation(object_seed, affordance.operation_id, object_state.state)
        root = _session_for_agent(state, proposal.agent_id)
        entry = ActionEntry(
            world_ref=self._world_ref,
            entry_id=_entry_id(self._world_ref, proposal.proposal_id),
            source_id=proposal.proposal_id,
            commit_position=CommitPosition(
                world_version=state.world.current_version + 1,
            ),
            root_session_id_at_commit=root.root_session_id,
            topology_version=root.topology_version,
            actor_agent_id=proposal.agent_id,
            occurred_at=state.world.world_time,
            text=operation.result_text,
            created_at=created_at,
            target_object_id=object_state.object_id,
            operation_id=operation.operation_id,
        )
        return self._applied_plan(
            proposal,
            state,
            entry,
            previous_entry=previous_entry,
            object_change=ObjectStateChange(
                world_ref=self._world_ref,
                object_id=object_state.object_id,
                expected_state=operation.from_state,
                new_state=operation.to_state,
            ),
        )

    def _utter_plan(
        self,
        proposal: ActionProposal,
        state: PublicWorldState,
        action: UtterAction,
        affordance: Affordance,
        *,
        created_at: datetime,
        previous_entry: EventEntry | None,
    ) -> WorldUpdatePlan:
        self._require_character_target(state, proposal.agent_id, action.target)
        channel = affordance.delivery_channel
        if channel is None:
            raise WorldUpdateValidationError("Utter affordance lacks a delivery channel")
        expected = WorldAffordanceResolver(
            world_ref=self._world_ref,
            world_version=state.world.current_version,
            agent_id=proposal.agent_id,
        ).utter_affordance(target=action.target, delivery_channel=channel)
        if affordance != expected:
            raise WorldUpdateValidationError("Utter affordance is forged or stale")
        root = _session_for_agent(state, proposal.agent_id)
        entry = DialogueEntry(
            world_ref=self._world_ref,
            entry_id=_entry_id(self._world_ref, proposal.proposal_id),
            source_id=proposal.proposal_id,
            commit_position=CommitPosition(
                world_version=state.world.current_version + 1,
            ),
            root_session_id_at_commit=root.root_session_id,
            topology_version=root.topology_version,
            actor_agent_id=proposal.agent_id,
            occurred_at=state.world.world_time,
            text=action.content,
            created_at=created_at,
            target_agent_id=action.target.id,
            audience_mode=(
                AudienceMode.SESSION if channel is DeliveryChannel.DIRECT else AudienceMode.EXPLICIT
            ),
            delivery_channel=channel,
        )
        request = (
            InteractionRequest(
                world_ref=self._world_ref,
                request_entry_id=entry.entry_id,
                requester_agent_id=proposal.agent_id,
                recipient_agent_id=action.target.id,
                status=InteractionRequestStatus.PENDING,
                updated_world_version=entry.commit_position.world_version,
            )
            if action.expects_response
            else None
        )
        return self._applied_plan(
            proposal,
            state,
            entry,
            previous_entry=previous_entry,
            create_request=request,
        )

    def _response_plan(
        self,
        proposal: ActionProposal,
        view: AgentView,
        state: PublicWorldState,
        action: RespondAction,
        affordance: Affordance,
        *,
        created_at: datetime,
        previous_entry: EventEntry | None,
        response_request: InteractionRequest | None,
        response_source: DialogueEntry | None,
    ) -> WorldUpdatePlan:
        self._require_character_target(state, proposal.agent_id, action.target)
        if response_request is None or response_source is None:
            raise WorldUpdateValidationError(
                "Respond requires its pending request and source Entry"
            )
        if (
            response_request.world_ref != self._world_ref
            or response_source.world_ref != self._world_ref
        ):
            raise WorldUpdateValidationError("response source belongs to another WorldRef")
        if (
            response_request.status is not InteractionRequestStatus.PENDING
            or response_request.request_entry_id != action.in_reply_to_entry_id
            or response_request.recipient_agent_id != proposal.agent_id
            or response_request.requester_agent_id != action.target.id
            or response_source.entry_id != action.in_reply_to_entry_id
            or response_source.actor_agent_id != action.target.id
            or response_source.target_agent_id != proposal.agent_id
        ):
            raise WorldUpdateValidationError("response does not match the pending request")
        if not any(
            candidate.source_entry_id == response_source.entry_id for candidate in view.candidates
        ):
            raise WorldUpdateValidationError("response source is not visible in the Agent view")
        channel = affordance.delivery_channel
        if channel is None or channel != response_source.delivery_channel:
            raise WorldUpdateValidationError("response delivery does not match its source")
        expected = WorldAffordanceResolver(
            world_ref=self._world_ref,
            world_version=state.world.current_version,
            agent_id=proposal.agent_id,
        ).response_affordance(
            target=action.target,
            delivery_channel=channel,
            request_entry_id=response_source.entry_id,
        )
        if affordance != expected:
            raise WorldUpdateValidationError("Response affordance is forged or stale")
        root = _session_for_agent(state, proposal.agent_id)
        entry = DialogueEntry(
            world_ref=self._world_ref,
            entry_id=_entry_id(self._world_ref, proposal.proposal_id),
            source_id=proposal.proposal_id,
            commit_position=CommitPosition(
                world_version=state.world.current_version + 1,
            ),
            root_session_id_at_commit=root.root_session_id,
            topology_version=root.topology_version,
            actor_agent_id=proposal.agent_id,
            occurred_at=state.world.world_time,
            text=action.content,
            created_at=created_at,
            target_agent_id=action.target.id,
            audience_mode=(
                AudienceMode.SESSION if channel is DeliveryChannel.DIRECT else AudienceMode.EXPLICIT
            ),
            delivery_channel=channel,
        )
        return self._applied_plan(
            proposal,
            state,
            entry,
            previous_entry=previous_entry,
            reply_to=response_source.entry_id,
            resolve_request_entry_id=response_source.entry_id,
        )

    def _applied_plan(
        self,
        proposal: ActionProposal,
        state: PublicWorldState,
        entry: EventEntry,
        *,
        previous_entry: EventEntry | None,
        reply_to: str | None = None,
        create_request: InteractionRequest | None = None,
        resolve_request_entry_id: str | None = None,
        object_change: ObjectStateChange | None = None,
    ) -> WorldUpdatePlan:
        if previous_entry is not None:
            if previous_entry.world_ref != self._world_ref:
                raise WorldUpdateValidationError("previous Entry belongs to another WorldRef")
            if previous_entry.root_session_id_at_commit != entry.root_session_id_at_commit:
                raise WorldUpdateValidationError("previous Entry belongs to another story line")
            if previous_entry.commit_position.world_version > state.world.current_version:
                raise WorldUpdateValidationError("previous Entry is newer than the current World")
        links = tuple(
            link
            for link in (
                EventEntryLink(
                    world_ref=self._world_ref,
                    entry_id=entry.entry_id,
                    relation_kind=EntryRelationKind.PREVIOUS,
                    related_entry_id=previous_entry.entry_id,
                )
                if previous_entry is not None
                else None,
                EventEntryLink(
                    world_ref=self._world_ref,
                    entry_id=entry.entry_id,
                    relation_kind=EntryRelationKind.REPLY,
                    related_entry_id=reply_to,
                )
                if reply_to is not None
                else None,
            )
            if link is not None
        )
        recipient_ids = _recipient_ids(state, entry)
        return WorldUpdatePlan(
            world_ref=self._world_ref,
            decision_id=proposal.proposal_id,
            agent_id=proposal.agent_id,
            event_session_id=proposal.event_session_id,
            expected_world_version=proposal.based_on_world_version,
            expected_control_epoch=proposal.based_on_control_epoch,
            expected_decision_seq=proposal.based_on_decision_seq,
            status=WorldUpdateStatus.APPLIED,
            entry=entry,
            links=links,
            recipients=tuple(
                EventEntryRecipient(
                    world_ref=self._world_ref,
                    entry_id=entry.entry_id,
                    agent_id=agent_id,
                )
                for agent_id in recipient_ids
            ),
            create_request=create_request,
            resolve_request_entry_id=resolve_request_entry_id,
            object_change=object_change,
        )

    def _find_action_affordance(self, view: AgentView, affordance_id: str) -> Affordance:
        matches = tuple(item for item in view.affordances if item.affordance_id == affordance_id)
        if len(matches) != 1:
            raise WorldUpdateValidationError("action does not select one current affordance")
        return matches[0]

    def _require_simple_affordance(
        self,
        view: AgentView,
        proposal: ActionProposal,
        kind: ProposalKind,
    ) -> None:
        expected = WorldAffordanceResolver(
            world_ref=self._world_ref,
            world_version=proposal.based_on_world_version,
            agent_id=proposal.agent_id,
        ).simple_affordance(kind)
        if expected not in view.affordances:
            raise WorldUpdateValidationError(f"{kind.value} is not afforded by the current view")

    @staticmethod
    def _require_character_target(
        state: PublicWorldState,
        actor_agent_id: str,
        target: CharacterTarget,
    ) -> None:
        actor_root = _session_for_agent(state, actor_agent_id).root_session_id
        target_node = _session_for_agent(state, target.id)
        if target.id == actor_agent_id or target_node.root_session_id != actor_root:
            raise WorldUpdateValidationError("Character target is outside the current session")


class WorldUpdater:
    """Apply one validated public plan inside the caller's transaction."""

    def __init__(self, world_ref: WorldRef, object_seeds: tuple[ObjectSeed, ...]) -> None:
        self._world_ref = WorldRef.model_validate(world_ref, strict=True)
        seeds = tuple(ObjectSeed.model_validate(item, strict=True) for item in object_seeds)
        seed_ids = tuple(item.id for item in seeds)
        if len(seed_ids) != len(set(seed_ids)):
            raise ValueError("Scenario Object IDs must be unique")
        self._object_seeds = {item.id: item for item in seeds}
        self._world_store = WorldStore(self._world_ref)
        self._entry_store = EventEntryStore(self._world_ref)

    def apply(
        self,
        session: Session,
        plan: WorldUpdatePlan,
        *,
        checkpoint: Callable[[str], None] | None = None,
    ) -> WorldUpdateResult:
        """Stage public writes without beginning or committing a transaction."""

        plan = WorldUpdatePlan.model_validate(plan, strict=True)
        if plan.world_ref != self._world_ref:
            raise WorldUpdateValidationError("WorldUpdatePlan belongs to another WorldRef")
        self._require_trusted_object_change(plan)
        if plan.object_change is not None:
            self._world_store.require_object_interaction_context(
                session,
                agent_id=plan.agent_id,
                object_id=plan.object_change.object_id,
            )
        advances_world = plan.status is WorldUpdateStatus.APPLIED
        version, decision_seq = self._world_store.compare_and_advance(
            session,
            expected_version=plan.expected_world_version,
            expected_control_epoch=plan.expected_control_epoch,
            expected_decision_seq=plan.expected_decision_seq,
            advances_world=advances_world,
        )
        _checkpoint(checkpoint, "world")

        if plan.object_change is not None:
            self._world_store.compare_and_set_object_state(
                session,
                object_id=plan.object_change.object_id,
                expected_state=plan.object_change.expected_state,
                new_state=plan.object_change.new_state,
            )
        _checkpoint(checkpoint, "object")

        if plan.entry is not None:
            self._entry_store.append(
                session,
                plan.entry,
                links=plan.links,
                recipients=plan.recipients,
            )
            session.flush()
        _checkpoint(checkpoint, "entry")

        if plan.create_request is not None:
            self._entry_store.create_request(session, plan.create_request)
        if plan.resolve_request_entry_id is not None:
            if plan.entry is None:
                raise WorldUpdateValidationError("request resolution requires an Entry")
            self._entry_store.resolve_request(
                session,
                request_entry_id=plan.resolve_request_entry_id,
                resolution_entry_id=plan.entry.entry_id,
                updated_world_version=version,
            )
        session.flush()
        _checkpoint(checkpoint, "request")
        return WorldUpdateResult(
            world_ref=self._world_ref,
            decision_id=plan.decision_id,
            status=plan.status,
            current_world_version=version,
            current_decision_seq=decision_seq,
            entry_id=plan.entry.entry_id if plan.entry is not None else None,
            entry_position=(plan.entry.commit_position if plan.entry is not None else None),
        )

    def _require_trusted_object_change(self, plan: WorldUpdatePlan) -> None:
        change = plan.object_change
        if change is None:
            return
        entry = plan.entry
        if not isinstance(entry, ActionEntry):
            raise WorldUpdateValidationError("Object change requires an ActionEntry")
        seed = self._object_seeds.get(change.object_id)
        if seed is None:
            raise WorldUpdateValidationError("Object change is absent from the Scenario catalog")
        operation = _operation(seed, entry.operation_id, change.expected_state)
        if (
            operation.to_state != change.new_state
            or operation.result_text != entry.text
            or seed.id != entry.target_object_id
        ):
            raise WorldUpdateValidationError(
                "Object change does not match its trusted Scenario operation"
            )


def _operation(seed: ObjectSeed, operation_id: str, state: str) -> ObjectOperationSeed:
    operation = next(
        (
            item
            for item in seed.operations
            if item.operation_id == operation_id and item.from_state == state
        ),
        None,
    )
    if operation is None:
        raise WorldUpdateValidationError("Object operation is not enabled in the current state")
    return operation


def _session_for_agent(state: PublicWorldState, agent_id: str) -> EventSessionNode:
    node = next((item for item in state.sessions if item.agent_id == agent_id), None)
    if node is None:
        raise WorldUpdateValidationError(f'Agent "{agent_id}" does not exist in this World')
    return node


def _recipient_ids(state: PublicWorldState, entry: EventEntry) -> tuple[str, ...]:
    if isinstance(entry, DialogueEntry) and entry.audience_mode is AudienceMode.EXPLICIT:
        return tuple(sorted({entry.actor_agent_id, entry.target_agent_id}))
    return tuple(
        sorted(
            node.agent_id
            for node in state.sessions
            if node.root_session_id == entry.root_session_id_at_commit
        )
    )


def _entry_id(world_ref: WorldRef, decision_id: str) -> str:
    identity = (world_ref.project_id, world_ref.world_id, decision_id, 0)
    canonical = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
    return f"entry-{sha256(canonical.encode()).hexdigest()}"


def _require_aware(value: datetime) -> None:
    if value.utcoffset() is None:
        raise WorldUpdateValidationError("created_at must include a timezone")


def _checkpoint(callback: Callable[[str], None] | None, name: str) -> None:
    if callback is not None:
        callback(name)
