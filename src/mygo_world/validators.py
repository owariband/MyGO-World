from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from mygo_world.contracts import (
    ActionProposal,
    CandidateEvent,
    PerceptionFrame,
    SegmentDraft,
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

        represented_proposals = {
            event.source_ref
            for event in draft.proposal_events
            if event.source_kind == "action_proposal"
        }
        for proposal in proposals:
            if (
                proposal.action.kind != "no_op"
                and proposal.proposal_id not in represented_proposals
            ):
                diagnostics.append(
                    _diagnostic(
                        "SEGMENT_INTENT_MISMATCH",
                        "proposal_events",
                        f"Proposal '{proposal.proposal_id}' has no objective result",
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

        if diagnostics:
            return ValidationOutcome(value=None, diagnostics=tuple(diagnostics))
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
            session_intent=draft.session_intent,
            proposal_ids=sorted(proposals_by_id),
            source_trace_id=source_trace_id,
        )
        return ValidationOutcome(value=plan, diagnostics=())

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
        if mismatch:
            diagnostics.append(
                _diagnostic(
                    "SEGMENT_INTENT_MISMATCH",
                    f"proposal_events.{index}",
                    "Director result does not faithfully preserve Character intent",
                )
            )
