from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, ClassVar

from mygo_world.canonical import canonical_json
from mygo_world.contracts import (
    ActionProposal,
    CandidateEvent,
    PerceptionFrame,
    SegmentDraft,
    SuccessorSession,
    ValidatedCommitPlan,
    ValidationDiagnostic,
)


@dataclass(frozen=True)
class ValidationOutcome[T]:
    value: T | None
    diagnostics: tuple[ValidationDiagnostic, ...]

    @property
    def ok(self) -> bool:
        return self.value is not None and not self.diagnostics


def _diagnostic(code: str, path: str, message: str) -> ValidationDiagnostic:
    return ValidationDiagnostic(code=code, path=path, message=message)


class ProposalValidator:
    """Pure deterministic gate for one Character-owned Action Proposal."""

    def validate(
        self, frame: PerceptionFrame, proposal: ActionProposal
    ) -> ValidationOutcome[ActionProposal]:
        diagnostics: list[ValidationDiagnostic] = []
        if proposal.world_version != frame.world_version:
            diagnostics.append(
                _diagnostic(
                    "PROPOSAL_WORLD_VERSION_MISMATCH",
                    "world_version",
                    "Proposal must target the PerceptionFrame World Version",
                )
            )
        if proposal.session_id != frame.session_id:
            diagnostics.append(
                _diagnostic(
                    "PROPOSAL_SESSION_MISMATCH",
                    "session_id",
                    "Proposal must target the PerceptionFrame Event Session",
                )
            )
        if proposal.actor_id != frame.character_id:
            diagnostics.append(
                _diagnostic(
                    "PROPOSAL_ACTOR_MISMATCH",
                    "actor_id",
                    "A Character may only own its own proposal",
                )
            )

        visible_ids = {entity.entity_id for entity in frame.visible_entities}
        if proposal.action.kind == "utterance":
            for index, target_id in enumerate(proposal.action.addressee_ids):
                if target_id not in visible_ids:
                    diagnostics.append(
                        _diagnostic(
                            "PROPOSAL_TARGET_NOT_VISIBLE",
                            f"action.addressee_ids.{index}",
                            f"Target '{target_id}' is not visible",
                        )
                    )
        elif proposal.action.kind == "interact":
            if proposal.action.target_id not in visible_ids:
                diagnostics.append(
                    _diagnostic(
                        "PROPOSAL_TARGET_NOT_VISIBLE",
                        "action.target_id",
                        f"Target '{proposal.action.target_id}' is not visible",
                    )
                )
        elif proposal.action.kind == "move":
            reachable = {
                (item.location_id, item.scope_key)
                for item in frame.reachable_destinations
            }
            destination = (proposal.action.location_id, proposal.action.scope_key)
            if destination not in reachable:
                diagnostics.append(
                    _diagnostic(
                        "PROPOSAL_DESTINATION_UNREACHABLE",
                        "action",
                        f"Destination '{destination[0]}/{destination[1]}' is not reachable",
                    )
                )

        for index, change in enumerate(proposal.memory_changes):
            if change.agent_id != frame.character_id:
                diagnostics.append(
                    _diagnostic(
                        "MEMORY_OWNER_MISMATCH",
                        f"memory_changes.{index}.agent_id",
                        "A Character may only propose changes to its own Memory",
                    )
                )
            if change.supersedes_memory_id is not None:
                previous = next(
                    (
                        item
                        for item in frame.memories
                        if item.memory_id == change.supersedes_memory_id
                    ),
                    None,
                )
                if previous is None:
                    diagnostics.append(
                        _diagnostic(
                            "MEMORY_SUPERSEDES_NOT_FOUND",
                            f"memory_changes.{index}.supersedes_memory_id",
                            "A Memory change may only supersede visible Memory owned by the Character",
                        )
                    )
                elif (
                    previous.agent_id != frame.character_id
                    or previous.namespace != change.namespace
                    or previous.memory_type != change.memory_type
                ):
                    diagnostics.append(
                        _diagnostic(
                            "MEMORY_SUPERSEDES_MISMATCH",
                            f"memory_changes.{index}.supersedes_memory_id",
                            "Superseded Memory must have the same owner, namespace and type",
                        )
                    )
                elif change.memory_type == "commitment" and previous.status != "active":
                    diagnostics.append(
                        _diagnostic(
                            "COMMITMENT_NOT_ACTIVE",
                            f"memory_changes.{index}.supersedes_memory_id",
                            "Only an active Commitment can be superseded",
                        )
                    )
        supersedes = [
            change.supersedes_memory_id
            for change in proposal.memory_changes
            if change.supersedes_memory_id is not None
        ]
        if len(supersedes) != len(set(supersedes)):
            diagnostics.append(
                _diagnostic(
                    "MEMORY_SUPERSEDES_DUPLICATE",
                    "memory_changes",
                    "A Memory record may be superseded only once in a proposal",
                )
            )
        return ValidationOutcome(
            value=proposal if not diagnostics else None,
            diagnostics=tuple(diagnostics),
        )


class SegmentValidator:
    """Pure deterministic gate for Director-completed candidate facts."""

    _character_agency_types: ClassVar[set[str]] = {
        "utterance",
        "important_action",
        "motivation_change",
        "character_choice",
    }

    def __init__(self, *, max_wave_duration_ms: int = 300_000) -> None:
        self._max_wave_duration_ms = max_wave_duration_ms

    def validate(
        self,
        *,
        world_id: str,
        snapshot: dict[str, object],
        proposals: list[ActionProposal],
        draft: SegmentDraft,
        source_trace_id: str,
        completed_wave_count: int = 0,
        pending_response_ids: tuple[str, ...] = (),
        has_unresolved_key_commitments: bool = False,
    ) -> ValidationOutcome[ValidatedCommitPlan]:
        diagnostics: list[ValidationDiagnostic] = []
        base_version = int(snapshot["world_version"])  # type: ignore[arg-type]
        world_time = int(snapshot["world_time_ms"])  # type: ignore[arg-type]
        entities = {
            item["entity_id"]: item
            for item in snapshot["entities"]  # type: ignore[index,union-attr]
        }
        sessions = {
            item["session_id"]: item
            for item in snapshot["sessions"]  # type: ignore[index,union-attr]
        }
        session = sessions.get(draft.session_id)

        if draft.world_version != base_version:
            diagnostics.append(
                _diagnostic(
                    "SEGMENT_WORLD_VERSION_MISMATCH",
                    "world_version",
                    "Segment Draft must target the common Snapshot version",
                )
            )
        if session is None or session.get("status") != "runnable":
            diagnostics.append(
                _diagnostic(
                    "SEGMENT_SESSION_INVALID",
                    "session_id",
                    "Segment Draft must target a runnable Event Session",
                )
            )
        else:
            participant_ids = set(session.get("participant_ids", []))
            for index, proposal in enumerate(proposals):
                if (
                    proposal.world_version != base_version
                    or proposal.session_id != draft.session_id
                    or proposal.actor_id not in participant_ids
                ):
                    diagnostics.append(
                        _diagnostic(
                            "SEGMENT_PROPOSAL_CONTEXT_INVALID",
                            f"proposals.{index}",
                            "Proposal does not belong to the Snapshot and Event Session",
                        )
                    )

        if draft.wave_started_at_ms != world_time:
            diagnostics.append(
                _diagnostic(
                    "SEGMENT_TIME_OUT_OF_BOUNDS",
                    "wave_started_at_ms",
                    "Wave must begin at Snapshot World Time",
                )
            )
        if (
            draft.wave_ended_at_ms < draft.wave_started_at_ms
            or draft.wave_ended_at_ms
            > draft.wave_started_at_ms + self._max_wave_duration_ms
        ):
            diagnostics.append(
                _diagnostic(
                    "SEGMENT_TIME_OUT_OF_BOUNDS",
                    "wave_ended_at_ms",
                    "Wave end is outside the configured semantic-time bound",
                )
            )

        proposals_by_id = {item.proposal_id: item for item in proposals}
        if len(proposals_by_id) != len(proposals):
            diagnostics.append(
                _diagnostic(
                    "SEGMENT_DUPLICATE_PROPOSAL_ID",
                    "proposals",
                    "Action Proposal IDs must be unique within a Wave",
                )
            )
        events = [*draft.proposal_events, *draft.external_events]
        event_keys: dict[str, int] = {}
        for index, event in enumerate(events):
            if event.event_key in event_keys:
                diagnostics.append(
                    _diagnostic(
                        "SEGMENT_DUPLICATE_EVENT_KEY",
                        f"events.{index}.event_key",
                        f"Event key '{event.event_key}' is duplicated",
                    )
                )
            else:
                event_keys[event.event_key] = index
            self._validate_event_time(event, index, draft, diagnostics)
            location = entities.get(event.location_id)
            scopes = (
                location.get("payload", {}).get("scopes", [])
                if location is not None
                else []
            )
            if not any(item["scope_key"] == event.scope_key for item in scopes):
                diagnostics.append(
                    _diagnostic(
                        "SEGMENT_EVENT_SCOPE_INVALID",
                        f"events.{index}",
                        "Event Location/Interaction Scope does not exist",
                    )
                )

        for index, event in enumerate(draft.proposal_events):
            proposal = proposals_by_id.get(event.source_ref or "")
            if event.source_kind != "action_proposal" or proposal is None:
                diagnostics.append(
                    _diagnostic(
                        "SEGMENT_INTENT_MISMATCH",
                        f"proposal_events.{index}.source_ref",
                        "A proposal event must preserve its Action Proposal source",
                    )
                )
                continue
            self._validate_intent(event, proposal, index, diagnostics)

        represented_proposals = [
            event.source_ref
            for event in draft.proposal_events
            if event.source_kind == "action_proposal"
        ]
        for proposal in proposals:
            representation_count = represented_proposals.count(proposal.proposal_id)
            if proposal.action.kind != "no_op" and representation_count == 0:
                diagnostics.append(
                    _diagnostic(
                        "SEGMENT_INTENT_MISMATCH",
                        "proposal_events",
                        f"Proposal '{proposal.proposal_id}' has no objective result",
                    )
                )
            elif proposal.action.kind != "no_op" and representation_count > 1:
                diagnostics.append(
                    _diagnostic(
                        "SEGMENT_PROPOSAL_EVENT_COUNT_INVALID",
                        "proposal_events",
                        f"Proposal '{proposal.proposal_id}' has multiple objective results",
                    )
                )

        no_op_ids = {
            proposal.proposal_id
            for proposal in proposals
            if proposal.action.kind == "no_op"
        }
        for index, event in enumerate(draft.proposal_events):
            if event.source_ref in no_op_ids:
                diagnostics.append(
                    _diagnostic(
                        "SEGMENT_NO_OP_EVENT_FORBIDDEN",
                        f"proposal_events.{index}",
                        "A no_op proposal cannot produce a Character event",
                    )
                )
        if (
            proposals
            and len(no_op_ids) == len(proposals)
            and (events or draft.entity_changes)
        ):
            diagnostics.append(
                _diagnostic(
                    "SEGMENT_ALL_NO_OP_EVENT_FORBIDDEN",
                    "events",
                    "An all-no_op Wave cannot produce World Events or entity changes",
                )
            )

        character_ids = {
            entity_id
            for entity_id, item in entities.items()
            if item["entity_type"] == "character"
        }
        for index, event in enumerate(draft.external_events):
            represented_character = event.actor_id
            for key in ("actor_id", "character_id", "speaker_id"):
                value = event.payload.get(key)
                if isinstance(value, str) and value in character_ids:
                    represented_character = value
            if represented_character in character_ids and (
                event.event_type in self._character_agency_types
                or any(
                    key in event.payload
                    for key in (
                        "dialogue",
                        "utterance",
                        "important_action",
                        "motivation",
                    )
                )
            ):
                diagnostics.append(
                    _diagnostic(
                        "EXTERNAL_EVENT_CHARACTER_AGENCY",
                        f"external_events.{index}",
                        "External events cannot invent persistent Character agency",
                    )
                )

        for index, event in enumerate(events):
            for cause_index, cause_key in enumerate(event.cause_event_keys):
                earlier_index = event_keys.get(cause_key)
                time_is_invalid = (
                    earlier_index is not None
                    and earlier_index < index
                    and events[earlier_index].end_time_ms > event.start_time_ms
                )
                if earlier_index is None or earlier_index >= index or time_is_invalid:
                    diagnostics.append(
                        _diagnostic(
                            "SEGMENT_CAUSE_INVALID",
                            f"events.{index}.cause_event_keys.{cause_index}",
                            f"Cause '{cause_key}' must reference an earlier event",
                        )
                    )

        for index, change in enumerate(draft.entity_changes):
            entity = entities.get(change.entity_id)
            if entity is None:
                diagnostics.append(
                    _diagnostic(
                        "SEGMENT_STATE_TRANSITION_INVALID",
                        f"entity_changes.{index}.entity_id",
                        f"Entity '{change.entity_id}' does not exist",
                    )
                )
                continue
            if change.location_id is not None:
                location = entities.get(change.location_id)
                scopes = (
                    location.get("payload", {}).get("scopes", [])
                    if location is not None
                    else []
                )
                if not any(item["scope_key"] == change.scope_key for item in scopes):
                    diagnostics.append(
                        _diagnostic(
                            "SEGMENT_STATE_TRANSITION_INVALID",
                            f"entity_changes.{index}",
                            "Destination Location/Interaction Scope does not exist",
                        )
                    )
                if entity["entity_type"] == "character":
                    move = next(
                        (
                            proposal
                            for proposal in proposals
                            if proposal.actor_id == change.entity_id
                            and proposal.action.kind == "move"
                            and proposal.action.location_id == change.location_id
                            and proposal.action.scope_key == change.scope_key
                        ),
                        None,
                    )
                    if move is None:
                        diagnostics.append(
                            _diagnostic(
                                "SEGMENT_STATE_TRANSITION_INVALID",
                                f"entity_changes.{index}",
                                "Character movement must match its Action Proposal",
                            )
                        )

        next_pending_responses = set(pending_response_ids)
        for proposal in proposals:
            is_direct_response = (
                proposal.action.kind == "utterance"
                and bool(proposal.action.addressee_ids)
            ) or (
                proposal.action.kind == "interact"
                and proposal.action.target_id in character_ids
            )
            if is_direct_response:
                next_pending_responses.discard(proposal.actor_id)
        for proposal in proposals:
            if proposal.action.kind == "utterance":
                next_pending_responses.update(proposal.action.addressee_ids)

        closed_session_ids: list[str] = []
        successor_sessions: list[SuccessorSession] = []
        if session is not None and session.get("status") == "runnable":
            closed_session_ids, successor_sessions = self._lineage_transition(
                world_id=world_id,
                base_version=base_version,
                session=session,
                sessions=sessions,
                entities=entities,
                proposals=proposals,
                entity_changes=draft.entity_changes,
                pending_response_ids=next_pending_responses,
            )

        if draft.session_intent == "resolved":
            if successor_sessions:
                diagnostics.append(
                    _diagnostic(
                        "SEGMENT_SESSION_TRANSITION_CONFLICT",
                        "session_intent",
                        "A partition or merge transition cannot also resolve the Session",
                    )
                )
            if completed_wave_count < 1:
                diagnostics.append(
                    _diagnostic(
                        "SEGMENT_SESSION_RESOLUTION_TOO_EARLY",
                        "session_intent",
                        "An Event Session must complete one earlier Wave before resolving",
                    )
                )
            if next_pending_responses:
                diagnostics.append(
                    _diagnostic(
                        "SEGMENT_SESSION_RESPONSE_PENDING",
                        "session_intent",
                        "An Event Session with a pending direct response cannot resolve",
                    )
                )
            if session is not None and session.get("in_progress_action_ids"):
                diagnostics.append(
                    _diagnostic(
                        "SEGMENT_SESSION_ACTION_IN_PROGRESS",
                        "session_intent",
                        "An Event Session with an in-progress action cannot resolve",
                    )
                )
            if has_unresolved_key_commitments:
                diagnostics.append(
                    _diagnostic(
                        "SEGMENT_SESSION_COMMITMENT_PENDING",
                        "session_intent",
                        "An Event Session with a key Commitment cannot resolve",
                    )
                )

        if diagnostics:
            return ValidationOutcome(value=None, diagnostics=tuple(diagnostics))
        session_intent = "partitioned" if successor_sessions else draft.session_intent
        if session_intent == "resolved":
            closed_session_ids = [draft.session_id]
        plan = ValidatedCommitPlan(
            world_id=world_id,
            base_world_version=base_version,
            new_world_version=base_version + 1,
            session_id=draft.session_id,
            wave_started_at_ms=draft.wave_started_at_ms,
            wave_ended_at_ms=draft.wave_ended_at_ms,
            events=events,
            entity_changes=draft.entity_changes,
            accepted_memory_changes=[
                change for proposal in proposals for change in proposal.memory_changes
            ],
            session_intent=session_intent,
            closed_session_ids=closed_session_ids,
            successor_sessions=successor_sessions,
            pending_response_ids=sorted(next_pending_responses),
            proposal_ids=sorted(proposals_by_id),
            source_trace_id=source_trace_id,
        )
        return ValidationOutcome(value=plan, diagnostics=())

    def _lineage_transition(
        self,
        *,
        world_id: str,
        base_version: int,
        session: dict[str, Any],
        sessions: dict[str, dict[str, Any]],
        entities: dict[str, dict[str, Any]],
        proposals: list[ActionProposal],
        entity_changes: list[Any],
        pending_response_ids: set[str],
    ) -> tuple[list[str], list[SuccessorSession]]:
        current_session_id = str(session["session_id"])
        affected_session_ids = {current_session_id}
        participants = set(session.get("participant_ids", []))
        character_ids = {
            entity_id
            for entity_id, entity in entities.items()
            if entity.get("entity_type") == "character"
        }

        direct_targets: set[str] = set()
        for proposal in proposals:
            if proposal.action.kind == "utterance":
                direct_targets.update(proposal.action.addressee_ids)
            elif (
                proposal.action.kind == "interact"
                and proposal.action.target_id in character_ids
            ):
                direct_targets.add(proposal.action.target_id)

        runnable_sessions = [
            item for item in sessions.values() if item.get("status") == "runnable"
        ]
        for target_id in direct_targets - participants:
            participants.add(target_id)
            for other in runnable_sessions:
                if target_id in other.get("participant_ids", []):
                    affected_session_ids.add(str(other["session_id"]))
                    participants.update(other.get("participant_ids", []))

        # Closing a merged parent must carry all of its fixed members forward.
        for affected_id in affected_session_ids:
            participants.update(sessions[affected_id].get("participant_ids", []))
            if affected_id != current_session_id:
                pending_response_ids.update(
                    sessions[affected_id].get("pending_response_ids", [])
                )

        positions = {
            entity_id: (entity.get("location_id"), entity.get("scope_key"))
            for entity_id, entity in entities.items()
            if entity.get("entity_type") == "character"
        }
        for change in entity_changes:
            if change.entity_id in positions and change.location_id is not None:
                positions[change.entity_id] = (change.location_id, change.scope_key)

        groups: dict[tuple[str, str], list[str]] = {}
        for participant_id in sorted(participants):
            position = positions.get(participant_id)
            if position is None or position[0] is None or position[1] is None:
                continue
            key = (str(position[0]), str(position[1]))
            groups.setdefault(key, []).append(participant_id)

        current_participants = set(session.get("participant_ids", []))
        unchanged = (
            affected_session_ids == {current_session_id}
            and participants == current_participants
            and len(groups) == 1
            and next(iter(groups))
            == (str(session.get("location_id")), str(session.get("scope_key")))
        )
        if unchanged:
            return [], []

        ordered_groups = sorted(
            groups.items(),
            key=lambda item: tuple(item[1]),
        )
        successors: list[SuccessorSession] = []
        for index, ((location_id, scope_key), member_ids) in enumerate(
            ordered_groups, start=1
        ):
            parent_ids = sorted(
                parent_id
                for parent_id in affected_session_ids
                if set(sessions[parent_id].get("participant_ids", [])) & set(member_ids)
            )
            if not parent_ids:
                parent_ids = [current_session_id]
            identity = canonical_json(
                {
                    "world_id": world_id,
                    "world_version": base_version + 1,
                    "parents": parent_ids,
                    "participants": member_ids,
                    "location_id": location_id,
                    "scope_key": scope_key,
                }
            )
            digest = sha256(identity.encode("utf-8")).hexdigest()[:16]
            successors.append(
                SuccessorSession(
                    session_id=f"session-lineage-{base_version + 1}-{index}-{digest}",
                    location_id=location_id,
                    scope_key=scope_key,
                    participant_ids=member_ids,
                    parent_session_ids=parent_ids,
                    pending_response_ids=sorted(set(member_ids) & pending_response_ids),
                )
            )
        return sorted(affected_session_ids), successors

    def _validate_event_time(
        self,
        event: CandidateEvent,
        index: int,
        draft: SegmentDraft,
        diagnostics: list[ValidationDiagnostic],
    ) -> None:
        if (
            event.start_time_ms < draft.wave_started_at_ms
            or event.end_time_ms < event.start_time_ms
            or event.end_time_ms > draft.wave_ended_at_ms
        ):
            diagnostics.append(
                _diagnostic(
                    "SEGMENT_TIME_OUT_OF_BOUNDS",
                    f"events.{index}",
                    "Event time must be ordered and contained by the Wave",
                )
            )

    def _validate_intent(
        self,
        event: CandidateEvent,
        proposal: ActionProposal,
        index: int,
        diagnostics: list[ValidationDiagnostic],
    ) -> None:
        action = proposal.action
        mismatch = (
            event.actor_id != proposal.actor_id or event.event_type != action.kind
        )
        mismatch = (
            mismatch or event.payload.get("intent_summary") != proposal.intent_summary
        )
        if action.kind == "utterance":
            mismatch = mismatch or event.payload.get("text") != action.text
            mismatch = mismatch or sorted(
                event.payload.get("addressee_ids", [])
            ) != sorted(action.addressee_ids)
        elif action.kind == "interact":
            mismatch = mismatch or event.payload.get("target_id") != action.target_id
        elif action.kind == "move":
            mismatch = mismatch or (
                event.payload.get("location_id"),
                event.payload.get("scope_key"),
            ) != (action.location_id, action.scope_key)
        elif action.kind == "wait":
            mismatch = (
                mismatch or event.payload.get("duration_ms") != action.duration_ms
            )
            mismatch = mismatch or event.payload.get("reason") != action.reason
            mismatch = mismatch or (
                event.end_time_ms - event.start_time_ms != action.duration_ms
            )
        if mismatch:
            diagnostics.append(
                _diagnostic(
                    "SEGMENT_INTENT_MISMATCH",
                    f"proposal_events.{index}",
                    "Director result does not faithfully preserve Character intent",
                )
            )
