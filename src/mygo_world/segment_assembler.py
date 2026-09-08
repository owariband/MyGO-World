from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mygo_world.contracts import (
    ActionProposal,
    DirectorResolution,
    EntityStateChange,
    ExternalEventCandidate,
    ProposalEventCandidate,
    SegmentDraft,
    ValidationDiagnostic,
)


@dataclass(frozen=True)
class SegmentAssemblyOutcome:
    value: SegmentDraft | None
    diagnostics: tuple[ValidationDiagnostic, ...]

    @property
    def ok(self) -> bool:
        return self.value is not None and not self.diagnostics

    @property
    def director_repairable(self) -> bool:
        """Whether every diagnostic belongs to Director-owned Resolution fields."""

        return bool(self.diagnostics) and all(
            item.code != "SEGMENT_ASSEMBLY_CONTEXT_INVALID" for item in self.diagnostics
        )


def _diagnostic(code: str, path: str, message: str) -> ValidationDiagnostic:
    return ValidationDiagnostic(code=code, path=path, message=message)


class SegmentAssembler:
    """Construct a Segment Draft from authoritative Wave inputs."""

    def __init__(self, *, max_wave_duration_ms: int = 300_000) -> None:
        self._max_wave_duration_ms = max_wave_duration_ms

    def assemble(
        self,
        *,
        snapshot: dict[str, Any],
        session_id: str,
        proposal: ActionProposal,
        resolution: DirectorResolution,
        director_trace_id: str,
    ) -> SegmentAssemblyOutcome:
        diagnostics: list[ValidationDiagnostic] = []
        world_time = int(snapshot["world_time_ms"])
        entities = {
            str(item["entity_id"]): item for item in snapshot.get("entities", [])
        }
        actor = entities.get(proposal.actor_id)

        if resolution.elapsed_ms > self._max_wave_duration_ms:
            diagnostics.append(
                _diagnostic(
                    "DIRECTOR_RESOLUTION_DURATION_INVALID",
                    "elapsed_ms",
                    "Resolution duration exceeds the configured Wave bound",
                )
            )
        if actor is None or actor.get("entity_type") != "character":
            diagnostics.append(
                _diagnostic(
                    "SEGMENT_ASSEMBLY_CONTEXT_INVALID",
                    "proposal.actor_id",
                    "Proposal actor is not a Character in the Snapshot",
                )
            )
        if proposal.world_version != int(snapshot["world_version"]):
            diagnostics.append(
                _diagnostic(
                    "SEGMENT_ASSEMBLY_CONTEXT_INVALID",
                    "proposal.world_version",
                    "Proposal must target the Snapshot World Version",
                )
            )
        if proposal.session_id != session_id:
            diagnostics.append(
                _diagnostic(
                    "SEGMENT_ASSEMBLY_CONTEXT_INVALID",
                    "proposal.session_id",
                    "Proposal must target the current Event Session",
                )
            )

        action = proposal.action
        if action.kind == "no_op":
            if (
                resolution.elapsed_ms != 0
                or resolution.outcome_summary is not None
                or resolution.external_events
                or resolution.entity_changes
            ):
                diagnostics.append(
                    _diagnostic(
                        "DIRECTOR_RESOLUTION_NO_OP_INVALID",
                        "$",
                        "A no_op Resolution cannot advance time or create results",
                    )
                )
        elif action.kind == "interact" and resolution.outcome_summary is None:
            diagnostics.append(
                _diagnostic(
                    "DIRECTOR_RESOLUTION_OUTCOME_REQUIRED",
                    "outcome_summary",
                    "An interact Resolution requires an observable outcome summary",
                )
            )
        elif action.kind == "wait" and resolution.elapsed_ms < action.duration_ms:
            diagnostics.append(
                _diagnostic(
                    "DIRECTOR_RESOLUTION_DURATION_INVALID",
                    "elapsed_ms",
                    "Resolution duration must contain the proposed wait",
                )
            )

        wave_end = world_time + resolution.elapsed_ms
        proposal_event = self._proposal_event(
            proposal=proposal,
            resolution=resolution,
            actor=actor,
            world_time=world_time,
            wave_end=wave_end,
        )
        external_events = self._external_events(
            snapshot=snapshot,
            proposal_event=proposal_event,
            proposal_id=proposal.proposal_id,
            resolution=resolution,
            director_trace_id=director_trace_id,
            world_time=world_time,
            diagnostics=diagnostics,
        )
        entity_changes = self._entity_changes(
            snapshot=snapshot,
            proposal=proposal,
            resolution=resolution,
            diagnostics=diagnostics,
        )
        if diagnostics:
            return SegmentAssemblyOutcome(value=None, diagnostics=tuple(diagnostics))

        return SegmentAssemblyOutcome(
            value=SegmentDraft(
                world_version=int(snapshot["world_version"]),
                session_id=session_id,
                wave_started_at_ms=world_time,
                wave_ended_at_ms=wave_end,
                proposal_events=[] if proposal_event is None else [proposal_event],
                external_events=external_events,
                entity_changes=entity_changes,
                session_intent=resolution.session_intent,
            ),
            diagnostics=(),
        )

    def _proposal_event(
        self,
        *,
        proposal: ActionProposal,
        resolution: DirectorResolution,
        actor: dict[str, Any] | None,
        world_time: int,
        wave_end: int,
    ) -> ProposalEventCandidate | None:
        action = proposal.action
        if action.kind == "no_op" or actor is None:
            return None
        payload: dict[str, Any] = {"intent_summary": proposal.intent_summary}
        if action.kind == "utterance":
            payload.update(
                text=action.text,
                addressee_ids=action.addressee_ids,
                expects_response=action.expects_response,
                response_to_event_id=action.response_to_event_id,
            )
        elif action.kind == "wait":
            payload.update(duration_ms=action.duration_ms, reason=action.reason)
        elif action.kind == "move":
            payload.update(location_id=action.location_id, scope_key=action.scope_key)
        elif action.kind == "interact":
            payload.update(target_id=action.target_id, description=action.description)
        if resolution.outcome_summary is not None:
            payload["outcome_summary"] = resolution.outcome_summary
        end_time = (
            world_time + action.duration_ms if action.kind == "wait" else wave_end
        )
        return ProposalEventCandidate(
            event_key=f"proposal:{proposal.proposal_id}",
            event_type=action.kind,
            actor_id=proposal.actor_id,
            start_time_ms=world_time,
            end_time_ms=end_time,
            cause_event_keys=[],
            source_kind="action_proposal",
            source_ref=proposal.proposal_id,
            evidence_refs=[],
            location_id=str(actor["location_id"]),
            scope_key=str(actor["scope_key"]),
            payload=payload,
        )

    def _external_events(
        self,
        *,
        snapshot: dict[str, Any],
        proposal_event: ProposalEventCandidate | None,
        proposal_id: str,
        resolution: DirectorResolution,
        director_trace_id: str,
        world_time: int,
        diagnostics: list[ValidationDiagnostic],
    ) -> list[ExternalEventCandidate]:
        result: list[ExternalEventCandidate] = []
        for index, event in enumerate(resolution.external_events):
            path = f"external_events.{index}"
            if not self._scope_exists(snapshot, event.location_id, event.scope_key):
                diagnostics.append(
                    _diagnostic(
                        "DIRECTOR_RESOLUTION_EVENT_SCOPE_INVALID",
                        path,
                        "External Event Location/Interaction Scope does not exist",
                    )
                )
            proposal_key = proposal_event.event_key if proposal_event else None
            result.append(
                ExternalEventCandidate(
                    event_key=f"external:{proposal_id}:{index}",
                    event_type=event.event_type,
                    actor_id=event.actor_id,
                    start_time_ms=world_time + resolution.elapsed_ms,
                    end_time_ms=world_time + resolution.elapsed_ms,
                    cause_event_keys=[] if proposal_key is None else [proposal_key],
                    source_kind="director",
                    source_ref=director_trace_id,
                    evidence_refs=[] if proposal_key is None else [proposal_key],
                    location_id=event.location_id,
                    scope_key=event.scope_key,
                    payload=event.payload,
                )
            )
        return result

    def _entity_changes(
        self,
        *,
        snapshot: dict[str, Any],
        proposal: ActionProposal,
        resolution: DirectorResolution,
        diagnostics: list[ValidationDiagnostic],
    ) -> list[EntityStateChange]:
        entities = {
            str(item["entity_id"]): item for item in snapshot.get("entities", [])
        }
        merged: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for index, change in enumerate(resolution.entity_changes):
            path = f"entity_changes.{index}"
            entity = entities.get(change.entity_id)
            if entity is None:
                diagnostics.append(
                    _diagnostic(
                        "DIRECTOR_RESOLUTION_ENTITY_CHANGE_INVALID",
                        f"{path}.entity_id",
                        "Entity State Change must target an existing Entity",
                    )
                )
                continue
            current = merged.get(change.entity_id)
            if current is None:
                current = {
                    "state_patch": {},
                    "location_id": None,
                    "scope_key": None,
                }
                merged[change.entity_id] = current
                order.append(change.entity_id)
            for key, value in change.state_patch.items():
                if (
                    key in current["state_patch"]
                    and current["state_patch"][key] != value
                ):
                    diagnostics.append(
                        _diagnostic(
                            "DIRECTOR_RESOLUTION_ENTITY_CHANGE_CONFLICT",
                            f"{path}.state_patch.{key}",
                            "Entity State Change values conflict within the Wave",
                        )
                    )
                else:
                    current["state_patch"][key] = value

        if proposal.action.kind == "move":
            current = merged.get(proposal.actor_id)
            if current is None:
                current = {
                    "state_patch": {},
                    "location_id": None,
                    "scope_key": None,
                }
                merged[proposal.actor_id] = current
                order.append(proposal.actor_id)
            current["location_id"] = proposal.action.location_id
            current["scope_key"] = proposal.action.scope_key

        return [
            EntityStateChange(entity_id=entity_id, **merged[entity_id])
            for entity_id in order
        ]

    def _scope_exists(
        self, snapshot: dict[str, Any], location_id: str, scope_key: str
    ) -> bool:
        location = next(
            (
                item
                for item in snapshot.get("entities", [])
                if item.get("entity_id") == location_id
                and item.get("entity_type") == "location"
            ),
            None,
        )
        if location is None:
            return False
        return any(
            item.get("scope_key") == scope_key
            for item in location.get("payload", {}).get("scopes", [])
        )
