from __future__ import annotations

import json
import signal
import threading
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mygo_world.canonical import canonical_json, sha256_text
from mygo_world.committer import (
    Clock,
    GenerationTraceRecord,
    IdGenerator,
    WorldCommitter,
    system_clock,
    uuid4_id,
)
from mygo_world.contracts import (
    ActionProposal,
    PerceptionFrame,
    SegmentDraft,
    ValidatedCommitPlan,
)
from mygo_world.db.engine import create_world_engine, require_current_schema
from mygo_world.db.models import (
    AgentMemoryRow,
    EventSessionMemberRow,
    EventSessionPendingResponseRow,
    GenerationBatchRow,
    GenerationWaveRow,
    RunnableSessionQueueRow,
    SnapshotRow,
    WorldRow,
)
from mygo_world.errors import WorldError, WorldNotFoundError
from mygo_world.gateways import (
    FixtureGateway,
    ModelGateway,
    ModelGeneration,
    ModelOutputInvalidError,
    ModelRequest,
    ModelRequestRejectedError,
    ModelTransportError,
    OpenAICompatibleGateway,
)
from mygo_world.memory import AgentMemoryRepository
from mygo_world.perception import PerceptionProjector
from mygo_world.skill_bindings import load_effective_skills, require_effective_skill
from mygo_world.skills import DEFAULT_SKILLS_DIR, RuntimeSkillCatalog
from mygo_world.telemetry import (
    finish_wave_span,
    model_call_span,
    record_generation,
    start_wave_span,
    traced_operation,
)
from mygo_world.validators import ProposalValidator, SegmentValidator
from mygo_world.worlds import WorldPaths, mutation_lock, validate_world_id

DEFAULT_REQUEST_BUDGET = 40
DEFAULT_TRANSPORT_RETRIES = 2
DEFAULT_CHARACTER_CONCURRENCY = 4
DEFAULT_MAX_WAVES = 6

_GLOBAL_CHARACTER_SEMAPHORE = threading.BoundedSemaphore(DEFAULT_CHARACTER_CONCURRENCY)


def _gateway_model_config(gateway: ModelGateway) -> dict[str, Any]:
    configured = getattr(gateway, "model_parameters", None)
    return dict(configured) if isinstance(configured, dict) else {"temperature": 0}


def _default_fixture_responses(
    *,
    snapshot: dict[str, Any],
    session_id: str,
    actor_id: str,
    participant_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """Build a deterministic two-Agent lockstep Wave with one explicit no-op."""

    world_time = snapshot["world_time_ms"]
    target_ids = [item for item in participant_ids if item != actor_id]
    responses: dict[str, dict[str, Any]] = {}
    proposals: list[dict[str, Any]] = []
    for participant_id in participant_ids:
        if participant_id == actor_id:
            proposal = {
                "schema_version": 1,
                "proposal_id": f"fixture-proposal-{participant_id}-v{snapshot['world_version']}",
                "world_version": snapshot["world_version"],
                "session_id": session_id,
                "actor_id": participant_id,
                "intent_summary": "Greets the other participant before rehearsal.",
                "action": {
                    "kind": "utterance",
                    "text": "早上好，今天也一起加油吧。",
                    "addressee_ids": target_ids[:1],
                },
                "memory_changes": [
                    {
                        "agent_id": participant_id,
                        "namespace": "default",
                        "memory_type": "belief",
                        "content": "The rehearsal can begin with a friendly greeting.",
                        "importance": 2,
                    }
                ],
            }
        else:
            proposal = {
                "schema_version": 1,
                "proposal_id": f"fixture-proposal-{participant_id}-v{snapshot['world_version']}",
                "world_version": snapshot["world_version"],
                "session_id": session_id,
                "actor_id": participant_id,
                "intent_summary": "Listens without taking a separate action.",
                "action": {"kind": "no_op", "reason": "Listening"},
                "memory_changes": [],
            }
        proposals.append(proposal)
        responses[f"character:{participant_id}:action_proposal"] = proposal

    primary = next(item for item in proposals if item["actor_id"] == actor_id)
    proposal_event_key = "event-character-greeting"
    responses["director:global-director:segment_draft"] = {
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
                "source_ref": primary["proposal_id"],
                "evidence_refs": [],
                "location_id": _character(snapshot, actor_id)["location_id"],
                "scope_key": _character(snapshot, actor_id)["scope_key"],
                "payload": {
                    "intent_summary": primary["intent_summary"],
                    "text": primary["action"]["text"],
                    "addressee_ids": primary["action"]["addressee_ids"],
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
    return responses


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
    trace_validation = dict(validation)
    if generation.usage is not None or generation.latency_ms is not None:
        trace_validation["provider"] = {
            "usage": generation.usage,
            "latency_ms": generation.latency_ms,
            "transport_attempts": generation.transport_attempts,
        }
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
        validation=trace_validation,
    )


def _invalid_trace_record(
    *,
    trace_id: str,
    world_id: str,
    session_id: str,
    error: ModelOutputInvalidError,
    attempt: int,
) -> GenerationTraceRecord:
    request = error.request
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
        raw_response=error.raw_response,
        structured_result={},
        validation={
            "ok": False,
            "attempt": attempt,
            "diagnostics": [
                {
                    "code": "MODEL_SCHEMA_INVALID",
                    "path": "$",
                    "message": error.diagnostic,
                }
            ],
        },
    )


def _diagnostics_payload(
    diagnostics: tuple[Any, ...], *, attempt: int = 1
) -> dict[str, Any]:
    return {
        "ok": not diagnostics,
        "attempt": attempt,
        "diagnostics": [item.model_dump(mode="json") for item in diagnostics],
    }


def _timestamp(clock: Clock) -> str:
    return clock().isoformat(timespec="microseconds")


@dataclass
class _RequestBudget:
    limit: int
    used: int = 0

    def __post_init__(self) -> None:
        self._lock = threading.Lock()

    def consume(self) -> None:
        with self._lock:
            if self.used >= self.limit:
                raise WorldError(
                    "REQUEST_BUDGET_EXHAUSTED",
                    f"Provider request budget of {self.limit} was exhausted",
                )
            self.used += 1


@dataclass(frozen=True)
class _ValidatedGeneration[ResponseT]:
    value: ResponseT
    attempts: tuple[tuple[ModelGeneration[ResponseT], dict[str, Any]], ...]
    invalid_attempts: tuple[tuple[ModelOutputInvalidError, int], ...] = ()


def _call_gateway[ResponseT: BaseModel](
    gateway: ModelGateway,
    request: ModelRequest,
    response_type: type[ResponseT],
    *,
    budget: _RequestBudget,
    cancellation_event: threading.Event,
    retries: int,
    semaphore: threading.Semaphore | None = None,
) -> ModelGeneration[ResponseT]:
    last_error: BaseException | None = None
    for attempt in range(retries + 1):
        if cancellation_event.is_set():
            raise WorldError("BATCH_CANCELLED", "Generation Batch was cancelled")
        try:
            if semaphore is None:
                budget.consume()
                with model_call_span(request, attempt + 1) as span:
                    generation = gateway.generate(request, response_type)
                    record_generation(span, generation)
                    return replace(generation, transport_attempts=attempt + 1)
            with semaphore:
                if cancellation_event.is_set():
                    raise WorldError(
                        "BATCH_CANCELLED", "Generation Batch was cancelled"
                    )
                budget.consume()
                with model_call_span(request, attempt + 1) as span:
                    generation = gateway.generate(request, response_type)
                    record_generation(span, generation)
                    return replace(generation, transport_attempts=attempt + 1)
        except ModelRequestRejectedError as exc:
            raise WorldError("MODEL_REQUEST_REJECTED", str(exc)) from exc
        except (ModelTransportError, TimeoutError, ConnectionError, OSError) as exc:
            last_error = exc
            if attempt == retries:
                break
    assert last_error is not None
    raise WorldError(
        "MODEL_TRANSPORT_FAILED",
        f"Provider request failed after {retries + 1} attempts: "
        f"{type(last_error).__name__}",
    ) from last_error


def _repair_request(
    request: ModelRequest,
    *,
    call_kind: str,
    diagnostic: dict[str, Any],
) -> ModelRequest:
    payload = dict(request.input_payload)
    payload["repair"] = diagnostic
    return ModelRequest(
        agent_type=request.agent_type,
        agent_id=request.agent_id,
        call_kind=call_kind,
        model_id=request.model_id,
        skill_id=request.skill_id,
        skill_version=request.skill_version,
        skill_content_hash=request.skill_content_hash,
        input_payload=payload,
        model_config=request.model_config,
        skill_body=request.skill_body,
    )


def _generate_character(
    *,
    gateway: ModelGateway,
    request: ModelRequest,
    frame: PerceptionFrame,
    budget: _RequestBudget,
    cancellation_event: threading.Event,
    retries: int,
    semaphore: threading.Semaphore,
) -> _ValidatedGeneration[ActionProposal]:
    attempts: list[tuple[ModelGeneration[ActionProposal], dict[str, Any]]] = []
    invalid_attempts: list[tuple[ModelOutputInvalidError, int]] = []
    current = request
    for semantic_attempt in (1, 2):
        try:
            generation = _call_gateway(
                gateway,
                current,
                ActionProposal,
                budget=budget,
                cancellation_event=cancellation_event,
                retries=retries,
                semaphore=semaphore,
            )
        except ModelOutputInvalidError as exc:
            invalid_attempts.append((exc, semantic_attempt))
            if semantic_attempt == 2:
                error = WorldError(
                    "MODEL_SCHEMA_INVALID",
                    f"Character '{frame.character_id}' returned invalid structured output",
                )
                error.schema_attempts = tuple(invalid_attempts)
                raise error from exc
            current = _repair_request(
                request,
                call_kind="action_proposal_repair",
                diagnostic={
                    "code": "MODEL_SCHEMA_INVALID",
                    "message": exc.diagnostic,
                },
            )
            continue
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            if semantic_attempt == 2:
                raise WorldError(
                    "MODEL_SCHEMA_INVALID",
                    f"Character '{frame.character_id}' returned invalid structured output",
                ) from exc
            current = _repair_request(
                request,
                call_kind="action_proposal_repair",
                diagnostic={"code": "MODEL_SCHEMA_INVALID", "message": str(exc)},
            )
            continue
        outcome = ProposalValidator().validate(frame, generation.structured)
        validation = _diagnostics_payload(outcome.diagnostics, attempt=semantic_attempt)
        attempts.append((generation, validation))
        if outcome.ok:
            assert outcome.value is not None
            return _ValidatedGeneration(
                outcome.value, tuple(attempts), tuple(invalid_attempts)
            )
        if semantic_attempt == 2:
            first = outcome.diagnostics[0]
            error = WorldError(first.code, first.message)
            error.trace_attempts = tuple(attempts)
            error.schema_attempts = tuple(invalid_attempts)
            raise error
        current = _repair_request(
            request,
            call_kind="action_proposal_repair",
            diagnostic=validation,
        )
    raise AssertionError("unreachable")


def _generate_director(
    *,
    gateway: ModelGateway,
    request: ModelRequest,
    world_id: str,
    snapshot: dict[str, Any],
    proposals: list[ActionProposal],
    trace_id: str,
    budget: _RequestBudget,
    cancellation_event: threading.Event,
    retries: int,
    completed_wave_count: int,
    pending_response_ids: tuple[str, ...],
    has_unresolved_key_commitments: bool,
) -> tuple[
    ValidatedCommitPlan,
    tuple[tuple[ModelGeneration[SegmentDraft], dict[str, Any]], ...],
    tuple[tuple[ModelOutputInvalidError, int], ...],
]:
    attempts: list[tuple[ModelGeneration[SegmentDraft], dict[str, Any]]] = []
    invalid_attempts: list[tuple[ModelOutputInvalidError, int]] = []
    current = request
    for semantic_attempt in (1, 2):
        try:
            generation = _call_gateway(
                gateway,
                current,
                SegmentDraft,
                budget=budget,
                cancellation_event=cancellation_event,
                retries=retries,
            )
        except ModelOutputInvalidError as exc:
            invalid_attempts.append((exc, semantic_attempt))
            if semantic_attempt == 2:
                error = WorldError(
                    "MODEL_SCHEMA_INVALID",
                    "Director returned invalid structured output",
                )
                error.schema_attempts = tuple(invalid_attempts)
                raise error from exc
            current = _repair_request(
                request,
                call_kind="segment_draft_repair",
                diagnostic={
                    "code": "MODEL_SCHEMA_INVALID",
                    "message": exc.diagnostic,
                },
            )
            continue
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            if semantic_attempt == 2:
                raise WorldError(
                    "MODEL_SCHEMA_INVALID",
                    "Director returned invalid structured output",
                ) from exc
            current = _repair_request(
                request,
                call_kind="segment_draft_repair",
                diagnostic={"code": "MODEL_SCHEMA_INVALID", "message": str(exc)},
            )
            continue
        outcome = SegmentValidator().validate(
            world_id=world_id,
            snapshot=snapshot,
            proposals=proposals,
            draft=generation.structured,
            source_trace_id=trace_id,
            completed_wave_count=completed_wave_count,
            pending_response_ids=pending_response_ids,
            has_unresolved_key_commitments=has_unresolved_key_commitments,
        )
        validation = _diagnostics_payload(outcome.diagnostics, attempt=semantic_attempt)
        attempts.append((generation, validation))
        if outcome.ok:
            assert outcome.value is not None
            return outcome.value, tuple(attempts), tuple(invalid_attempts)
        if semantic_attempt == 2:
            first = outcome.diagnostics[0]
            error = WorldError(first.code, first.message)
            error.trace_attempts = tuple(attempts)
            error.schema_attempts = tuple(invalid_attempts)
            raise error
        current = _repair_request(
            request,
            call_kind="segment_draft_repair",
            diagnostic=validation,
        )
    raise AssertionError("unreachable")


def _interrupt_stale_batches(engine: Any, world_id: str, clock: Clock) -> None:
    now = _timestamp(clock)
    with Session(engine) as session, session.begin():
        session.execute(
            update(GenerationBatchRow)
            .where(
                GenerationBatchRow.world_id == world_id,
                GenerationBatchRow.status == "running",
            )
            .values(
                status="interrupted", error_code="BATCH_INTERRUPTED", updated_at=now
            )
        )
        session.execute(
            update(GenerationWaveRow)
            .where(
                GenerationWaveRow.status == "running",
                GenerationWaveRow.run_id.in_(
                    select(GenerationBatchRow.run_id).where(
                        GenerationBatchRow.world_id == world_id,
                        GenerationBatchRow.status == "interrupted",
                    )
                ),
            )
            .values(
                status="interrupted", error_code="BATCH_INTERRUPTED", updated_at=now
            )
        )


def _create_batch(
    engine: Any,
    *,
    run_id: str,
    world_id: str,
    session_id: str | None,
    start_version: int,
    clock: Clock,
) -> None:
    now = _timestamp(clock)
    try:
        with Session(engine) as session, session.begin():
            session.add(
                GenerationBatchRow(
                    run_id=run_id,
                    world_id=world_id,
                    session_id=session_id,
                    status="running",
                    start_world_version=start_version,
                    end_world_version=start_version,
                    wave_count=0,
                    request_count=0,
                    warnings_json="[]",
                    error_code=None,
                    created_at=now,
                    updated_at=now,
                )
            )
    except IntegrityError as exc:
        raise WorldError(
            "BATCH_RUN_ID_EXISTS", f"Generation Batch '{run_id}' already exists"
        ) from exc


def _start_wave(
    engine: Any,
    *,
    wave_id: str,
    run_id: str,
    wave_number: int,
    session_id: str,
    version: int,
    world_time_ms: int,
    clock: Clock,
) -> None:
    now = _timestamp(clock)
    with Session(engine) as session, session.begin():
        session.add(
            GenerationWaveRow(
                wave_id=wave_id,
                run_id=run_id,
                wave_number=wave_number,
                session_id=session_id,
                status="running",
                start_world_version=version,
                end_world_version=version,
                world_time_ms=world_time_ms,
                error_code=None,
                created_at=now,
                updated_at=now,
            )
        )
    start_wave_span(
        wave_id,
        run_id=run_id,
        session_id=session_id,
        wave_number=wave_number,
        world_version=version,
    )


def _finish_wave(
    engine: Any,
    *,
    wave_id: str,
    status: str,
    end_version: int,
    error_code: str | None,
    clock: Clock,
) -> None:
    with Session(engine) as session, session.begin():
        wave = session.get(GenerationWaveRow, wave_id)
        if wave is not None:
            wave.status = status
            wave.end_world_version = end_version
            wave.error_code = error_code
            wave.updated_at = _timestamp(clock)
    finish_wave_span(wave_id, result=status)


def _finish_batch(
    engine: Any,
    *,
    run_id: str,
    status: str,
    end_version: int,
    wave_count: int,
    request_count: int,
    warnings: list[str],
    error_code: str | None,
    clock: Clock,
) -> None:
    with Session(engine) as session, session.begin():
        batch = session.get(GenerationBatchRow, run_id)
        if batch is not None:
            batch.status = status
            batch.end_world_version = end_version
            batch.wave_count = wave_count
            batch.request_count = request_count
            batch.warnings_json = canonical_json(warnings)
            batch.error_code = error_code
            batch.updated_at = _timestamp(clock)


def _current_version(engine: Any, world_id: str) -> int:
    with Session(engine) as session:
        world = session.get(WorldRow, world_id)
        if world is None:
            raise WorldNotFoundError(world_id)
        return world.current_version


def _load_wave_context(
    engine: Any, world_id: str, session_id: str
) -> tuple[
    dict[str, Any],
    list[str],
    dict[str, list[AgentMemoryRow]],
    int,
    tuple[str, ...],
    bool,
]:
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
        participants = list(
            session.scalars(
                select(EventSessionMemberRow.agent_id)
                .where(EventSessionMemberRow.session_id == session_id)
                .order_by(EventSessionMemberRow.agent_id)
            )
        )
        memory_repository = AgentMemoryRepository(session)
        memories_by_agent = {
            participant_id: [
                memory
                for namespace in (
                    memory_repository.namespaces(agent_id=participant_id) or ["default"]
                )
                for memory in memory_repository.retrieve(
                    agent_id=participant_id,
                    namespace=namespace,
                    entity_tags=participants,
                    location_tags=[_character(snapshot, participant_id)["location_id"]],
                )
            ]
            for participant_id in participants
        }
        memories = [
            memory
            for participant_id in participants
            for memory in memories_by_agent[participant_id]
        ]
        completed_wave_count = int(
            session.scalar(
                select(func.count())
                .select_from(GenerationWaveRow)
                .where(
                    GenerationWaveRow.session_id == session_id,
                    GenerationWaveRow.status.in_(("committed", "no_op")),
                )
            )
            or 0
        )
        pending_response_ids = tuple(
            session.scalars(
                select(EventSessionPendingResponseRow.responder_id)
                .where(EventSessionPendingResponseRow.session_id == session_id)
                .order_by(EventSessionPendingResponseRow.responder_id)
            )
        )
    if not participants:
        raise WorldError(
            "SESSION_HAS_NO_PARTICIPANTS",
            f"Event Session '{session_id}' has no participants",
        )
    superseded_memory_ids = {
        item.supersedes_memory_id
        for item in memories
        if item.supersedes_memory_id is not None
    }
    has_unresolved_key_commitments = any(
        item.agent_id in participants
        and item.memory_type == "commitment"
        and item.importance >= 4
        and item.memory_id not in superseded_memory_ids
        and item.status == "active"
        for item in memories
    )
    return (
        snapshot,
        participants,
        memories_by_agent,
        completed_wave_count,
        pending_response_ids,
        has_unresolved_key_commitments,
    )


def _find_queue_head(engine: Any) -> tuple[str | None, int]:
    with Session(engine) as session:
        world = session.scalar(select(WorldRow))
        if world is None:
            raise WorldError("WORLD_NOT_FOUND", "World record is missing")
        queue = session.scalar(
            select(RunnableSessionQueueRow)
            .where(RunnableSessionQueueRow.dequeued_world_version.is_(None))
            .order_by(RunnableSessionQueueRow.queue_order)
        )
        return (queue.session_id if queue is not None else None, world.current_version)


def _receipt(
    *,
    world_id: str,
    run_id: str,
    session_id: str | None,
    status: str,
    start_version: int,
    end_version: int,
    wave_count: int,
    request_count: int,
    warnings: list[str],
    error_code: str | None,
    gateway_kind: str,
    gateway: ModelGateway | None,
    database_path: Path,
    last_result: Any | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "command": "advance",
        "run_id": run_id,
        "status": status,
        "world_id": world_id,
        "session_id": session_id,
        "start_world_version": start_version,
        "end_world_version": end_version,
        "wave_count": wave_count,
        "warnings": warnings,
        "error_code": error_code,
        "model_call_count": request_count,
        "gateway": gateway_kind,
        "network_request_count": getattr(gateway, "network_request_count", 0),
        "database_path": str(database_path),
    }
    if last_result is not None:
        result.update(
            snapshot_checksum=last_result.snapshot_checksum,
            world_event_count=last_result.world_event_count,
            observation_count=last_result.observation_count,
            entity_revision_count=last_result.entity_revision_count,
        )
    else:
        result.update(
            world_event_count=0,
            observation_count=0,
            entity_revision_count=0,
        )
    return result


@contextmanager
def _cancel_on_signals(cancel: threading.Event) -> Iterator[None]:
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous: dict[signal.Signals, Any] = {}

    def request_cancel(_signum: int, _frame: Any) -> None:
        cancel.set()

    for item in (signal.SIGINT, signal.SIGTERM):
        previous[item] = signal.getsignal(item)
        signal.signal(item, request_cancel)
    try:
        yield
    finally:
        for item, handler in previous.items():
            signal.signal(item, handler)


@traced_operation("mygo.advance")
def advance_world(
    world_id: str,
    worlds_dir: Path,
    *,
    gateway: ModelGateway | None = None,
    gateway_kind: str = "fixture",
    run_id: str | None = None,
    max_waves: int = DEFAULT_MAX_WAVES,
    request_budget: int = DEFAULT_REQUEST_BUDGET,
    max_character_concurrency: int = DEFAULT_CHARACTER_CONCURRENCY,
    transport_retries: int = DEFAULT_TRANSPORT_RETRIES,
    cancellation_event: threading.Event | None = None,
    clock: Clock = system_clock,
    id_generator: IdGenerator = uuid4_id,
    failure_injector: Any | None = None,
    skills_dir: Path = DEFAULT_SKILLS_DIR,
    env_file: Path | None = None,
) -> dict[str, Any]:
    validate_world_id(world_id)
    if max_waves < 1:
        raise WorldError("MAX_WAVES_INVALID", "max_waves must be at least one")
    if request_budget < 1:
        raise WorldError("REQUEST_BUDGET_INVALID", "request_budget must be positive")
    if max_character_concurrency < 1:
        raise WorldError(
            "CHARACTER_CONCURRENCY_INVALID",
            "max_character_concurrency must be positive",
        )
    if transport_retries < 0 or transport_retries > 2:
        raise WorldError(
            "TRANSPORT_RETRIES_INVALID",
            "transport_retries must be between zero and two",
        )
    if gateway_kind not in {"fixture", "provider"}:
        raise WorldError("GATEWAY_INVALID", f"Unknown gateway '{gateway_kind}'")
    selected_gateway = gateway
    if selected_gateway is None and gateway_kind == "provider":
        try:
            selected_gateway = OpenAICompatibleGateway.from_environment(
                env_file=env_file
            )
        except ValueError as exc:
            raise WorldError("GATEWAY_CONFIGURATION_INVALID", str(exc)) from exc

    paths = WorldPaths(worlds_dir, world_id)
    if not paths.database.is_file():
        raise WorldNotFoundError(world_id)
    cancel = cancellation_event or threading.Event()
    batch_run_id = run_id or id_generator()
    budget = _RequestBudget(request_budget)
    warnings: list[str] = []
    wave_count = 0
    active_wave_id: str | None = None
    last_result: Any | None = None
    using_builtin_fixture = gateway is None and gateway_kind == "fixture"

    with mutation_lock(paths.mutation_lock), _cancel_on_signals(cancel):
        engine = create_world_engine(paths.database)
        try:
            require_current_schema(paths.database, engine)
            _interrupt_stale_batches(engine, world_id, clock)
            session_id, start_version = _find_queue_head(engine)
            if session_id is None:
                return _receipt(
                    world_id=world_id,
                    run_id=batch_run_id,
                    session_id=None,
                    status="no_work",
                    start_version=start_version,
                    end_version=start_version,
                    wave_count=0,
                    request_count=0,
                    warnings=warnings,
                    error_code=None,
                    gateway_kind=gateway_kind,
                    gateway=selected_gateway,
                    database_path=paths.database,
                )
            pinned_skills = load_effective_skills(
                engine,
                world_id=world_id,
                catalog=RuntimeSkillCatalog.load(skills_dir),
            )
            initial_session_id = session_id
            _create_batch(
                engine,
                run_id=batch_run_id,
                world_id=world_id,
                session_id=session_id,
                start_version=start_version,
                clock=clock,
            )

            committer = WorldCommitter(engine, clock=clock, id_generator=id_generator)
            semaphore = (
                _GLOBAL_CHARACTER_SEMAPHORE
                if max_character_concurrency == DEFAULT_CHARACTER_CONCURRENCY
                else threading.BoundedSemaphore(max_character_concurrency)
            )

            try:
                for wave_number in range(1, max_waves + 1):
                    if cancel.is_set():
                        raise WorldError(
                            "BATCH_CANCELLED", "Generation Batch was cancelled"
                        )
                    wave_count = wave_number
                    (
                        snapshot,
                        participant_ids,
                        memories_by_agent,
                        completed_wave_count,
                        pending_response_ids,
                        has_unresolved_key_commitments,
                    ) = _load_wave_context(engine, world_id, session_id)
                    active_wave_id = id_generator()
                    _start_wave(
                        engine,
                        wave_id=active_wave_id,
                        run_id=batch_run_id,
                        wave_number=wave_number,
                        session_id=session_id,
                        version=snapshot["world_version"],
                        world_time_ms=snapshot["world_time_ms"],
                        clock=clock,
                    )
                    if selected_gateway is None:
                        if gateway_kind == "fixture":
                            selected_gateway = FixtureGateway(
                                _default_fixture_responses(
                                    snapshot=snapshot,
                                    session_id=session_id,
                                    actor_id=participant_ids[0],
                                    participant_ids=participant_ids,
                                )
                            )
                        else:
                            raise WorldError(
                                "GATEWAY_INVALID", f"Unknown gateway '{gateway_kind}'"
                            )

                    frames = {
                        participant_id: PerceptionProjector().project_frame(
                            snapshot,
                            session_id=session_id,
                            character_id=participant_id,
                            memories=memories_by_agent[participant_id],
                        )
                        for participant_id in participant_ids
                    }
                    futures: dict[
                        Future[_ValidatedGeneration[ActionProposal]], str
                    ] = {}
                    with ThreadPoolExecutor(
                        max_workers=len(participant_ids),
                        thread_name_prefix="mygo-character",
                    ) as executor:
                        for participant_id in participant_ids:
                            frame = frames[participant_id]
                            skill = require_effective_skill(
                                pinned_skills, "character", participant_id
                            )
                            request = ModelRequest(
                                agent_type="character",
                                agent_id=participant_id,
                                call_kind="action_proposal",
                                model_id=getattr(
                                    selected_gateway, "model_id", "fixture-model-v1"
                                ),
                                skill_id=skill.skill_id,
                                skill_version=skill.version,
                                skill_content_hash=skill.content_hash,
                                input_payload={
                                    "world_version": snapshot["world_version"],
                                    "world_time_ms": snapshot["world_time_ms"],
                                    "run_id": batch_run_id,
                                    "wave_number": wave_number,
                                    "perception_frame": frame.model_dump(mode="json"),
                                },
                                model_config=_gateway_model_config(selected_gateway),
                                skill_body=skill.body,
                            )
                            futures[
                                executor.submit(
                                    _generate_character,
                                    gateway=selected_gateway,
                                    request=request,
                                    frame=frame,
                                    budget=budget,
                                    cancellation_event=cancel,
                                    retries=transport_retries,
                                    semaphore=semaphore,
                                )
                            ] = participant_id

                        character_results: dict[
                            str, _ValidatedGeneration[ActionProposal]
                        ] = {}
                        character_error: BaseException | None = None
                        failed_attempts: tuple[Any, ...] = ()
                        failed_schema_attempts: tuple[Any, ...] = ()
                        for future in as_completed(futures):
                            participant_id = futures[future]
                            try:
                                character_results[participant_id] = future.result()
                            except Exception as exc:  # noqa: BLE001 - collect all worker failures
                                character_error = character_error or exc
                                failed_attempts = failed_attempts or (
                                    exc.trace_attempts
                                    if isinstance(exc, WorldError)
                                    else ()
                                )
                                failed_schema_attempts = failed_schema_attempts or (
                                    exc.schema_attempts
                                    if isinstance(exc, WorldError)
                                    else ()
                                )
                        trace_records: list[GenerationTraceRecord] = []
                        for participant_id in participant_ids:
                            result = character_results.get(participant_id)
                            if result is None:
                                continue
                            for error, attempt in result.invalid_attempts:
                                trace_records.append(
                                    _invalid_trace_record(
                                        trace_id=id_generator(),
                                        world_id=world_id,
                                        session_id=session_id,
                                        error=error,
                                        attempt=attempt,
                                    )
                                )
                            for generation, validation in result.attempts:
                                trace_records.append(
                                    _trace_record(
                                        trace_id=id_generator(),
                                        world_id=world_id,
                                        session_id=session_id,
                                        generation=generation,
                                        validation=validation,
                                    )
                                )
                        for generation, validation in failed_attempts:
                            trace_records.append(
                                _trace_record(
                                    trace_id=id_generator(),
                                    world_id=world_id,
                                    session_id=session_id,
                                    generation=generation,
                                    validation=validation,
                                )
                            )
                        for error, attempt in failed_schema_attempts:
                            trace_records.append(
                                _invalid_trace_record(
                                    trace_id=id_generator(),
                                    world_id=world_id,
                                    session_id=session_id,
                                    error=error,
                                    attempt=attempt,
                                )
                            )
                        if trace_records:
                            committer.record_generation_traces(trace_records)
                        if character_error is not None:
                            raise character_error

                    proposals = [
                        character_results[item].value for item in participant_ids
                    ]
                    director_skill = require_effective_skill(
                        pinned_skills, "director", "global-director"
                    )
                    director_trace_id = id_generator()
                    director_request = ModelRequest(
                        agent_type="director",
                        agent_id="global-director",
                        call_kind="segment_draft",
                        model_id=getattr(
                            selected_gateway, "model_id", "fixture-model-v1"
                        ),
                        skill_id=director_skill.skill_id,
                        skill_version=director_skill.version,
                        skill_content_hash=director_skill.content_hash,
                        input_payload={
                            "world_version": snapshot["world_version"],
                            "world_time_ms": snapshot["world_time_ms"],
                            "run_id": batch_run_id,
                            "wave_number": wave_number,
                            "snapshot": snapshot,
                            "proposals": [
                                item.model_dump(mode="json") for item in proposals
                            ],
                        },
                        model_config=_gateway_model_config(selected_gateway),
                        skill_body=director_skill.body,
                    )
                    try:
                        plan, director_attempts, director_schema_attempts = (
                            _generate_director(
                                gateway=selected_gateway,
                                request=director_request,
                                world_id=world_id,
                                snapshot=snapshot,
                                proposals=proposals,
                                trace_id=director_trace_id,
                                budget=budget,
                                cancellation_event=cancel,
                                retries=transport_retries,
                                completed_wave_count=completed_wave_count,
                                pending_response_ids=pending_response_ids,
                                has_unresolved_key_commitments=(
                                    has_unresolved_key_commitments
                                ),
                            )
                        )
                    except WorldError as exc:
                        director_attempts = exc.trace_attempts
                        director_schema_attempts = exc.schema_attempts
                        if director_attempts or director_schema_attempts:
                            failed_director_records = [
                                _invalid_trace_record(
                                    trace_id=id_generator(),
                                    world_id=world_id,
                                    session_id=session_id,
                                    error=error,
                                    attempt=attempt,
                                )
                                for error, attempt in director_schema_attempts
                            ]
                            failed_director_records.extend(
                                [
                                    _trace_record(
                                        trace_id=(
                                            director_trace_id
                                            if index == len(director_attempts)
                                            else id_generator()
                                        ),
                                        world_id=world_id,
                                        session_id=session_id,
                                        generation=generation,
                                        validation=validation,
                                    )
                                    for index, (generation, validation) in enumerate(
                                        director_attempts, start=1
                                    )
                                ]
                            )
                            committer.record_generation_traces(failed_director_records)
                        raise
                    director_records = [
                        _invalid_trace_record(
                            trace_id=id_generator(),
                            world_id=world_id,
                            session_id=session_id,
                            error=error,
                            attempt=attempt,
                        )
                        for error, attempt in director_schema_attempts
                    ]
                    director_records.extend(
                        [
                            _trace_record(
                                trace_id=(
                                    director_trace_id
                                    if index == len(director_attempts)
                                    else id_generator()
                                ),
                                world_id=world_id,
                                session_id=session_id,
                                generation=generation,
                                validation=validation,
                            )
                            for index, (generation, validation) in enumerate(
                                director_attempts, start=1
                            )
                        ]
                    )
                    committer.record_generation_traces(director_records)

                    if wave_number == max_waves and plan.session_intent == "keep_open":
                        plan = plan.model_copy(
                            update={
                                "session_intent": "limit_reached",
                                "closed_session_ids": [session_id],
                                "pending_response_ids": [],
                                "wave_ended_at_ms": (
                                    snapshot["world_time_ms"]
                                    if all(
                                        item.action.kind == "no_op"
                                        for item in proposals
                                    )
                                    else plan.wave_ended_at_ms
                                ),
                            }
                        )
                        warnings.append("MAX_WAVES_REACHED")
                    all_no_op = all(item.action.kind == "no_op" for item in proposals)
                    if all_no_op and plan.session_intent == "keep_open":
                        _finish_wave(
                            engine,
                            wave_id=active_wave_id,
                            status="no_op",
                            end_version=snapshot["world_version"],
                            error_code=None,
                            clock=clock,
                        )
                        active_wave_id = None
                        continue

                    if cancel.is_set():
                        raise WorldError(
                            "BATCH_CANCELLED", "Generation Batch was cancelled"
                        )
                    last_result = committer.commit_wave(
                        plan, failure_injector=failure_injector
                    )
                    _finish_wave(
                        engine,
                        wave_id=active_wave_id,
                        status="committed",
                        end_version=last_result.world_version,
                        error_code=None,
                        clock=clock,
                    )
                    active_wave_id = None
                    if plan.session_intent in {"resolved", "limit_reached"}:
                        break
                    if last_result.continuation_session_id is not None:
                        session_id = last_result.continuation_session_id
                    if using_builtin_fixture:
                        # The checked-in fixture has one Wave of responses. It still
                        # exercises the full lockstep scheduler without closing the
                        # Session or fabricating further responses in this Batch.
                        break

                end_version = _current_version(engine, world_id)
                _finish_batch(
                    engine,
                    run_id=batch_run_id,
                    status="completed",
                    end_version=end_version,
                    wave_count=wave_count,
                    request_count=budget.used,
                    warnings=warnings,
                    error_code=None,
                    clock=clock,
                )
                return _receipt(
                    world_id=world_id,
                    run_id=batch_run_id,
                    session_id=initial_session_id,
                    status="completed",
                    start_version=start_version,
                    end_version=end_version,
                    wave_count=wave_count,
                    request_count=budget.used,
                    warnings=warnings,
                    error_code=None,
                    gateway_kind=gateway_kind,
                    gateway=selected_gateway,
                    database_path=paths.database,
                    last_result=last_result,
                )
            except BaseException as exc:
                error_code = (
                    exc.code
                    if isinstance(exc, WorldError)
                    else "BATCH_CANCELLED"
                    if isinstance(exc, KeyboardInterrupt)
                    else "BATCH_FAILED"
                )
                status = "cancelled" if error_code == "BATCH_CANCELLED" else "failed"
                end_version = _current_version(engine, world_id)
                if active_wave_id is not None:
                    _finish_wave(
                        engine,
                        wave_id=active_wave_id,
                        status=status,
                        end_version=end_version,
                        error_code=error_code,
                        clock=clock,
                    )
                _finish_batch(
                    engine,
                    run_id=batch_run_id,
                    status=status,
                    end_version=end_version,
                    wave_count=wave_count,
                    request_count=budget.used,
                    warnings=warnings,
                    error_code=error_code,
                    clock=clock,
                )
                receipt = _receipt(
                    world_id=world_id,
                    run_id=batch_run_id,
                    session_id=initial_session_id,
                    status=status,
                    start_version=start_version,
                    end_version=end_version,
                    wave_count=wave_count,
                    request_count=budget.used,
                    warnings=warnings,
                    error_code=error_code,
                    gateway_kind=gateway_kind,
                    gateway=selected_gateway,
                    database_path=paths.database,
                    last_result=last_result,
                )
                if isinstance(exc, WorldError):
                    exc.receipt = receipt
                    raise
                if isinstance(exc, KeyboardInterrupt):
                    raise WorldError(
                        "BATCH_CANCELLED",
                        "Generation Batch was cancelled",
                        receipt=receipt,
                    ) from exc
                raise
        finally:
            engine.dispose()
