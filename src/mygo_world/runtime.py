from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from mygo_world.canonical import sha256_text
from mygo_world.committer import (
    Clock,
    GenerationTraceRecord,
    IdGenerator,
    WorldCommitter,
    system_clock,
    uuid4_id,
)
from mygo_world.contracts import ActionProposal, SegmentDraft
from mygo_world.db.engine import create_world_engine, require_current_schema
from mygo_world.db.models import (
    AgentMemoryRow,
    EventSessionMemberRow,
    RunnableSessionQueueRow,
    SnapshotRow,
    WorldRow,
)
from mygo_world.errors import WorldError, WorldNotFoundError
from mygo_world.gateways import (
    FixtureGateway,
    ModelGateway,
    ModelGeneration,
    ModelRequest,
    OpenAICompatibleGateway,
)
from mygo_world.perception import PerceptionProjector
from mygo_world.validators import ProposalValidator, SegmentValidator
from mygo_world.worlds import WorldPaths, mutation_lock, validate_world_id

CHARACTER_SKILL = "Propose one concise action consistent with visible facts."
DIRECTOR_SKILL = "Complete objective outcomes without inventing Character agency."


def _default_fixture_responses(
    *,
    snapshot: dict[str, Any],
    session_id: str,
    actor_id: str,
    participant_ids: list[str],
) -> dict[str, dict[str, Any]]:
    world_time = snapshot["world_time_ms"]
    target_ids = [item for item in participant_ids if item != actor_id]
    proposal = {
        "schema_version": 1,
        "proposal_id": f"fixture-proposal-v{snapshot['world_version']}",
        "world_version": snapshot["world_version"],
        "session_id": session_id,
        "actor_id": actor_id,
        "intent_summary": "Greets the other participant before rehearsal.",
        "action": {
            "kind": "utterance",
            "text": "早上好，今天也一起加油吧。",
            "addressee_ids": target_ids[:1],
        },
        "memory_changes": [
            {
                "agent_id": actor_id,
                "namespace": "default",
                "memory_type": "belief",
                "content": "The rehearsal can begin with a friendly greeting.",
                "importance": 2,
            }
        ],
    }
    proposal_event_key = "event-character-greeting"
    director = {
        "schema_version": 1,
        "world_version": snapshot["world_version"],
        "session_id": session_id,
        "wave_started_at_ms": world_time,
        "wave_ended_at_ms": world_time + 2_000,
        "proposal_events": [
            {
                "event_key": proposal_event_key,
                "event_type": "utterance",
                "actor_id": actor_id,
                "start_time_ms": world_time + 500,
                "end_time_ms": world_time + 1_000,
                "cause_event_keys": [],
                "source_kind": "action_proposal",
                "source_ref": proposal["proposal_id"],
                "evidence_refs": [],
                "location_id": _character(snapshot, actor_id)["location_id"],
                "scope_key": _character(snapshot, actor_id)["scope_key"],
                "payload": {
                    "intent_summary": proposal["intent_summary"],
                    "text": proposal["action"]["text"],
                    "addressee_ids": proposal["action"]["addressee_ids"],
                },
            }
        ],
        "external_events": [
            {
                "event_key": "event-house-lights-warm",
                "event_type": "environment_change",
                "actor_id": None,
                "start_time_ms": world_time + 1_000,
                "end_time_ms": world_time + 2_000,
                "cause_event_keys": [proposal_event_key],
                "source_kind": "director",
                "source_ref": "fixture-director-environment-v1",
                "evidence_refs": [proposal_event_key],
                "location_id": _character(snapshot, actor_id)["location_id"],
                "scope_key": _character(snapshot, actor_id)["scope_key"],
                "payload": {
                    "description": "The rehearsal lights settle into a warm glow."
                },
            }
        ],
        "entity_changes": [
            {
                "entity_id": _character(snapshot, actor_id)["location_id"],
                "state_patch": {"lighting": "warm"},
                "location_id": None,
                "scope_key": None,
            }
        ],
        "session_intent": "keep_open",
    }
    return {
        f"character:{actor_id}:action_proposal": proposal,
        "director:global-director:segment_draft": director,
    }


def _character(snapshot: dict[str, Any], character_id: str) -> dict[str, Any]:
    return next(
        item
        for item in snapshot["entities"]
        if item["entity_id"] == character_id and item["entity_type"] == "character"
    )


def _trace_record(
    *,
    trace_id: str,
    world_id: str,
    session_id: str,
    generation: ModelGeneration[Any],
    validation: dict[str, Any],
) -> GenerationTraceRecord:
    request = generation.request
    return GenerationTraceRecord(
        trace_id=trace_id,
        world_id=world_id,
        input_world_version=int(request.input_payload["world_version"]),
        session_id=session_id,
        agent_type=request.agent_type,
        agent_id=request.agent_id,
        call_kind=request.call_kind,
        skill_id=request.skill_id,
        skill_version=request.skill_version,
        skill_content_hash=request.skill_content_hash,
        model_id=request.model_id,
        model_config=request.model_config,
        request=request.trace_payload(),
        raw_response=generation.raw_response,
        structured_result=generation.structured.model_dump(mode="json"),
        validation=validation,
    )


def _diagnostics_payload(diagnostics: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "ok": not diagnostics,
        "diagnostics": [item.model_dump(mode="json") for item in diagnostics],
    }


def advance_world(
    world_id: str,
    worlds_dir: Path,
    *,
    gateway: ModelGateway | None = None,
    gateway_kind: str = "fixture",
    clock: Clock = system_clock,
    id_generator: IdGenerator = uuid4_id,
    failure_injector: Any | None = None,
) -> dict[str, Any]:
    validate_world_id(world_id)
    paths = WorldPaths(worlds_dir, world_id)
    if not paths.database.is_file():
        raise WorldNotFoundError(world_id)

    with mutation_lock(paths.mutation_lock):
        engine = create_world_engine(paths.database)
        try:
            require_current_schema(paths.database, engine)
            with Session(engine) as session:
                world = session.get(WorldRow, world_id)
                if world is None:
                    raise WorldNotFoundError(world_id)
                snapshot_row = session.get(SnapshotRow, world.current_version)
                if snapshot_row is None:
                    raise WorldError("SNAPSHOT_MISSING", "Current Snapshot is missing")
                if sha256_text(snapshot_row.snapshot_json) != snapshot_row.checksum:
                    raise WorldError(
                        "SNAPSHOT_CHECKSUM_MISMATCH",
                        "Current Snapshot failed checksum validation",
                    )
                snapshot = json.loads(snapshot_row.snapshot_json)
                queue = session.scalar(
                    select(RunnableSessionQueueRow)
                    .where(RunnableSessionQueueRow.dequeued_world_version.is_(None))
                    .order_by(RunnableSessionQueueRow.queue_order)
                )
                if queue is None:
                    return {
                        "command": "advance",
                        "status": "no_work",
                        "world_id": world_id,
                        "start_world_version": world.current_version,
                        "end_world_version": world.current_version,
                        "model_call_count": 0,
                    }
                participant_ids = list(
                    session.scalars(
                        select(EventSessionMemberRow.agent_id)
                        .where(EventSessionMemberRow.session_id == queue.session_id)
                        .order_by(EventSessionMemberRow.agent_id)
                    )
                )
                if not participant_ids:
                    raise WorldError(
                        "SESSION_HAS_NO_PARTICIPANTS",
                        f"Event Session '{queue.session_id}' has no participants",
                    )
                # Ticket 02 intentionally advances one Character proposal; ticket 03
                # expands this seam to the full parallel lockstep participant set.
                actor_id = participant_ids[0]
                memories = list(
                    session.scalars(
                        select(AgentMemoryRow).order_by(AgentMemoryRow.memory_id)
                    )
                )

            frame = PerceptionProjector().project_frame(
                snapshot,
                session_id=queue.session_id,
                character_id=actor_id,
                memories=memories,
            )
            if gateway is None:
                if gateway_kind == "fixture":
                    gateway = FixtureGateway(
                        _default_fixture_responses(
                            snapshot=snapshot,
                            session_id=queue.session_id,
                            actor_id=actor_id,
                            participant_ids=participant_ids,
                        )
                    )
                elif gateway_kind == "provider":
                    try:
                        gateway = OpenAICompatibleGateway.from_environment()
                    except ValueError as exc:
                        raise WorldError(
                            "GATEWAY_CONFIGURATION_INVALID", str(exc)
                        ) from exc
                else:
                    raise WorldError(
                        "GATEWAY_INVALID", f"Unknown gateway '{gateway_kind}'"
                    )

            character_request = ModelRequest(
                agent_type="character",
                agent_id=actor_id,
                call_kind="action_proposal",
                model_id=getattr(gateway, "model_id", "fixture-model-v1"),
                skill_id=f"character-skill:{actor_id}",
                skill_version="1",
                skill_content_hash=sha256_text(CHARACTER_SKILL),
                input_payload={
                    "world_version": snapshot["world_version"],
                    "perception_frame": frame.model_dump(mode="json"),
                },
                model_config={"temperature": 0},
            )
            character_generation = gateway.generate(character_request, ActionProposal)
            proposal_outcome = ProposalValidator().validate(
                frame, character_generation.structured
            )
            character_trace_id = id_generator()
            character_trace = _trace_record(
                trace_id=character_trace_id,
                world_id=world_id,
                session_id=queue.session_id,
                generation=character_generation,
                validation=_diagnostics_payload(proposal_outcome.diagnostics),
            )
            committer = WorldCommitter(engine, clock=clock, id_generator=id_generator)
            if not proposal_outcome.ok:
                committer.record_generation_traces([character_trace])
                first = proposal_outcome.diagnostics[0]
                raise WorldError(first.code, first.message)
            proposal = proposal_outcome.value
            assert proposal is not None

            director_request = ModelRequest(
                agent_type="director",
                agent_id="global-director",
                call_kind="segment_draft",
                model_id=getattr(gateway, "model_id", "fixture-model-v1"),
                skill_id="director-skill:global",
                skill_version="1",
                skill_content_hash=sha256_text(DIRECTOR_SKILL),
                input_payload={
                    "world_version": snapshot["world_version"],
                    "snapshot": snapshot,
                    "proposals": [proposal.model_dump(mode="json")],
                },
                model_config={"temperature": 0},
            )
            director_generation = gateway.generate(director_request, SegmentDraft)
            director_trace_id = id_generator()
            segment_outcome = SegmentValidator().validate(
                world_id=world_id,
                snapshot=snapshot,
                proposals=[proposal],
                draft=director_generation.structured,
                source_trace_id=director_trace_id,
            )
            director_trace = _trace_record(
                trace_id=director_trace_id,
                world_id=world_id,
                session_id=queue.session_id,
                generation=director_generation,
                validation=_diagnostics_payload(segment_outcome.diagnostics),
            )
            committer.record_generation_traces([character_trace, director_trace])
            if not segment_outcome.ok:
                first = segment_outcome.diagnostics[0]
                raise WorldError(first.code, first.message)
            plan = segment_outcome.value
            assert plan is not None
            result = committer.commit_wave(
                plan,
                failure_injector=failure_injector,
            )
            return {
                "command": "advance",
                "status": "advanced",
                "world_id": world_id,
                "session_id": queue.session_id,
                "start_world_version": plan.base_world_version,
                "end_world_version": result.world_version,
                "snapshot_checksum": result.snapshot_checksum,
                "world_event_count": result.world_event_count,
                "observation_count": result.observation_count,
                "entity_revision_count": result.entity_revision_count,
                "model_call_count": 2,
                "gateway": gateway_kind,
                "network_request_count": getattr(
                    gateway, "network_request_count", None
                ),
                "database_path": str(paths.database),
            }
        finally:
            engine.dispose()
