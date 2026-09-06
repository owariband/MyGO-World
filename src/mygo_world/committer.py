from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from mygo_world.canonical import canonical_json, sha256_text
from mygo_world.contracts import (
    CharacterSeed,
    LoadedSeed,
    LocationSeed,
    ObjectSeed,
    ValidatedCommitPlan,
)
from mygo_world.db.models import (
    AgentMemoryRow,
    EntityRevisionRow,
    EventSessionMemberRow,
    EventSessionPendingResponseRow,
    EventSessionRow,
    GenerationTraceRow,
    SkillBindingRow,
    SnapshotRow,
    WorldEventRow,
    WorldRow,
    WorldSegmentRow,
    WorldVersionRow,
)
from mygo_world.perception import PerceptionProjector
from mygo_world.recognizer import EventRecognizer
from mygo_world.repositories import LedgerRepository
from mygo_world.skills import EffectiveSkill

Clock = Callable[[], datetime]
IdGenerator = Callable[[], str]


def system_clock() -> datetime:
    return datetime.now(UTC)


def uuid4_id() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True)
class GenesisCommitPlan:
    world_id: str
    loaded_seed: LoadedSeed
    skill_bindings: tuple[EffectiveSkill, ...]


@dataclass(frozen=True)
class GenesisCommitResult:
    world_version: int
    snapshot_checksum: str


@dataclass(frozen=True)
class GenerationTraceRecord:
    trace_id: str
    world_id: str
    input_world_version: int
    session_id: str
    agent_type: str
    agent_id: str
    call_kind: str
    skill_id: str
    skill_version: str
    skill_content_hash: str
    model_id: str
    model_config: dict[str, Any]
    request: dict[str, Any]
    raw_response: str
    structured_result: dict[str, Any]
    validation: dict[str, Any]


@dataclass(frozen=True)
class WaveCommitResult:
    world_version: int
    snapshot_checksum: str
    world_event_count: int
    observation_count: int
    entity_revision_count: int
    continuation_session_id: str | None


def _entity_payload(
    entity: LocationSeed | CharacterSeed | ObjectSeed,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"state": entity.state}
    if isinstance(entity, LocationSeed):
        payload["scopes"] = [
            scope.model_dump(mode="json")
            for scope in sorted(entity.scopes, key=lambda value: value.scope_key)
        ]
    return payload


def _snapshot(plan: GenesisCommitPlan) -> dict[str, Any]:
    seed = plan.loaded_seed.seed
    entities = []
    for entity in sorted(seed.entities, key=lambda value: value.entity_id):
        entities.append(
            {
                "entity_id": entity.entity_id,
                "entity_type": entity.entity_type,
                "name": entity.name,
                "revision_order": 1,
                "location_id": getattr(entity, "location_id", None),
                "scope_key": getattr(entity, "scope_key", None),
                "payload": _entity_payload(entity),
            }
        )
    sessions = [
        {
            "session_id": item.session_id,
            "status": "runnable",
            "location_id": item.location_id,
            "scope_key": item.scope_key,
            "participant_ids": sorted(item.participant_ids),
            "parent_session_ids": [],
            "pending_response_ids": [],
        }
        for item in sorted(seed.sessions, key=lambda value: value.session_id)
    ]
    queue = [
        {"queue_order": index, "session_id": item.session_id}
        for index, item in enumerate(seed.sessions, start=1)
    ]
    return {
        "schema_version": 1,
        "world_id": plan.world_id,
        "world_version": 1,
        "world_time_ms": seed.world_time_ms,
        "entities": entities,
        "sessions": sessions,
        "runnable_session_queue": queue,
    }


def _genesis_payload(plan: GenesisCommitPlan) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "genesis",
        "session_initializations": [
            session.model_dump(mode="json")
            for session in plan.loaded_seed.seed.sessions
        ],
        "queue_initializations": [
            {"queue_order": index, "session_id": session.session_id}
            for index, session in enumerate(plan.loaded_seed.seed.sessions, start=1)
        ],
    }


class WorldCommitter:
    """The sole transactional writer of authoritative World state."""

    def __init__(
        self,
        engine: Engine,
        *,
        clock: Clock,
        id_generator: IdGenerator,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._id_generator = id_generator

    def commit_genesis(self, plan: GenesisCommitPlan) -> GenesisCommitResult:
        created_at = self._timestamp()
        segment_id = self._id_generator()
        snapshot = _snapshot(plan)
        snapshot_json = canonical_json(snapshot)
        snapshot_checksum = sha256_text(snapshot_json)
        seed = plan.loaded_seed.seed

        with Session(self._engine) as session, session.begin():
            world = WorldRow(
                world_id=plan.world_id,
                name=seed.name,
                seed_id=seed.seed_id,
                seed_version=seed.version,
                seed_content_hash=plan.loaded_seed.content_hash,
                current_version=0,
                calendar_anchor=seed.calendar_anchor,
                created_at=created_at,
            )
            session.add(world)
            ledger = LedgerRepository(session)
            ledger.append_segment(
                WorldSegmentRow(
                    segment_id=segment_id,
                    segment_order=1,
                    world_version=1,
                    segment_type="genesis",
                    source_trace_id=None,
                    schema_version=1,
                    payload_json=canonical_json(_genesis_payload(plan)),
                    committed_at=created_at,
                )
            )
            session.flush()
            session.add(
                WorldVersionRow(
                    version=1,
                    segment_id=segment_id,
                    world_time_ms=seed.world_time_ms,
                    created_at=created_at,
                )
            )
            session.flush()

            for entity in seed.entities:
                ledger.append_entity_revision(
                    EntityRevisionRow(
                        entity_revision_id=self._id_generator(),
                        entity_id=entity.entity_id,
                        entity_type=entity.entity_type,
                        revision_order=1,
                        world_version=1,
                        segment_id=segment_id,
                        name=entity.name,
                        location_id=getattr(entity, "location_id", None),
                        scope_key=getattr(entity, "scope_key", None),
                        schema_version=1,
                        payload_json=canonical_json(_entity_payload(entity)),
                    )
                )

            for queue_order, event_session in enumerate(seed.sessions, start=1):
                session.add(
                    EventSessionRow(
                        session_id=event_session.session_id,
                        queue_order=queue_order,
                        status="runnable",
                        location_id=event_session.location_id,
                        scope_key=event_session.scope_key,
                        created_world_version=1,
                        closed_world_version=None,
                        closure_reason=None,
                    )
                )
                session.flush()
                for participant_id in event_session.participant_ids:
                    session.add(
                        EventSessionMemberRow(
                            session_id=event_session.session_id,
                            agent_id=participant_id,
                        )
                    )

            remaining_memories = {item.memory_id: item for item in seed.memories}
            inserted_memory_ids: set[str] = set()
            while remaining_memories:
                ready = sorted(
                    (
                        item
                        for item in remaining_memories.values()
                        if item.supersedes_memory_id is None
                        or item.supersedes_memory_id in inserted_memory_ids
                    ),
                    key=lambda item: item.memory_id,
                )
                if not ready:
                    raise ValueError("Scenario Seed Memory supersedes graph is cyclic")
                memory = ready[0]
                session.add(
                    AgentMemoryRow(
                        memory_id=memory.memory_id,
                        agent_id=memory.agent_id,
                        namespace=memory.namespace,
                        memory_type=memory.memory_type,
                        world_version=1,
                        relative_time_ms=memory.relative_time_ms,
                        importance=memory.importance,
                        source=memory.source,
                        status=memory.status,
                        supersedes_memory_id=memory.supersedes_memory_id,
                        entity_tags_json=canonical_json(sorted(memory.entity_tags)),
                        location_tags_json=canonical_json(sorted(memory.location_tags)),
                        schema_version=1,
                        payload_json=canonical_json(
                            {
                                "content": memory.content,
                                "entity_tags": sorted(memory.entity_tags),
                                "location_tags": sorted(memory.location_tags),
                                "source": memory.source,
                            }
                        ),
                    )
                )
                session.flush()
                inserted_memory_ids.add(memory.memory_id)
                del remaining_memories[memory.memory_id]

            for binding_order, binding in enumerate(plan.skill_bindings, start=1):
                session.add(
                    SkillBindingRow(
                        binding_id=(f"seed:{binding.agent_kind}:{binding.agent_id}"),
                        binding_order=binding_order,
                        world_id=plan.world_id,
                        agent_kind=binding.agent_kind,
                        agent_id=binding.agent_id,
                        previous_skill_id=None,
                        previous_skill_version=None,
                        previous_skill_content_hash=None,
                        skill_id=binding.skill_id,
                        skill_version=binding.version,
                        skill_content_hash=binding.content_hash,
                        operator="scenario_seed",
                        reason="initial binding",
                        bound_at=created_at,
                    )
                )

            session.add(
                SnapshotRow(
                    world_version=1,
                    schema_version=1,
                    snapshot_json=snapshot_json,
                    checksum=snapshot_checksum,
                    created_at=created_at,
                )
            )
            world.current_version = 1
            event_count = session.scalar(
                select(func.count()).select_from(WorldEventRow)
            )
            if event_count != 0:
                raise AssertionError("Genesis must not create World Events")

        return GenesisCommitResult(
            world_version=1,
            snapshot_checksum=snapshot_checksum,
        )

    def record_generation_traces(self, records: list[GenerationTraceRecord]) -> None:
        created_at = self._timestamp()
        with Session(self._engine) as session, session.begin():
            for record in records:
                self._reject_credentials(record.model_config)
                self._reject_credentials(record.request)
                session.add(
                    GenerationTraceRow(
                        trace_id=record.trace_id,
                        world_id=record.world_id,
                        input_world_version=record.input_world_version,
                        session_id=record.session_id,
                        agent_type=record.agent_type,
                        agent_id=record.agent_id,
                        call_kind=record.call_kind,
                        skill_id=record.skill_id,
                        skill_version=record.skill_version,
                        skill_content_hash=record.skill_content_hash,
                        model_id=record.model_id,
                        model_config_json=canonical_json(record.model_config),
                        request_json=canonical_json(record.request),
                        raw_response=record.raw_response,
                        structured_result_json=canonical_json(record.structured_result),
                        validation_json=canonical_json(record.validation),
                        created_at=created_at,
                    )
                )

    def commit_wave(
        self,
        plan: ValidatedCommitPlan,
        *,
        failure_injector: Callable[[str], None] | None = None,
    ) -> WaveCommitResult:
        """Atomically publish every authoritative product of one validated Wave."""

        inject = failure_injector or (lambda _stage: None)
        committed_at = self._timestamp()
        segment_id = self._id_generator()

        with Session(self._engine) as session, session.begin():
            world = session.get(WorldRow, plan.world_id)
            if world is None:
                raise ValueError(f"World '{plan.world_id}' does not exist")
            if world.current_version != plan.base_world_version:
                raise ValueError("Validated Commit Plan is stale")
            snapshot_row = session.get(SnapshotRow, plan.base_world_version)
            if snapshot_row is None:
                raise ValueError("Base Snapshot is missing")
            if sha256_text(snapshot_row.snapshot_json) != snapshot_row.checksum:
                raise ValueError("Base Snapshot checksum mismatch")
            snapshot = json.loads(snapshot_row.snapshot_json)
            if snapshot["world_id"] != plan.world_id:
                raise ValueError("Validated Commit Plan World does not match Snapshot")

            segment_order = (
                int(
                    session.scalar(select(func.max(WorldSegmentRow.segment_order))) or 0
                )
                + 1
            )
            segment_payload = {
                "schema_version": 1,
                "kind": "generation_wave",
                "session_id": plan.session_id,
                "wave_started_at_ms": plan.wave_started_at_ms,
                "wave_ended_at_ms": plan.wave_ended_at_ms,
                "proposal_ids": plan.proposal_ids,
                "events": [item.model_dump(mode="json") for item in plan.events],
                "entity_changes": [
                    item.model_dump(mode="json") for item in plan.entity_changes
                ],
                "session_intent": plan.session_intent,
                "closed_session_ids": plan.closed_session_ids,
                "successor_sessions": [
                    item.model_dump(mode="json") for item in plan.successor_sessions
                ],
                "pending_response_ids": plan.pending_response_ids,
            }
            ledger = LedgerRepository(session)
            ledger.append_segment(
                WorldSegmentRow(
                    segment_id=segment_id,
                    segment_order=segment_order,
                    world_version=plan.new_world_version,
                    segment_type="generation_wave",
                    source_trace_id=plan.source_trace_id,
                    schema_version=1,
                    payload_json=canonical_json(segment_payload),
                    committed_at=committed_at,
                )
            )
            session.flush()
            inject("segment")

            session.add(
                WorldVersionRow(
                    version=plan.new_world_version,
                    segment_id=segment_id,
                    world_time_ms=plan.wave_ended_at_ms,
                    created_at=committed_at,
                )
            )
            session.flush()
            inject("world_version")

            new_snapshot = deepcopy(snapshot)
            new_snapshot["world_version"] = plan.new_world_version
            new_snapshot["world_time_ms"] = plan.wave_ended_at_ms
            entity_revision_count = self._apply_entity_changes(
                session,
                ledger,
                plan,
                segment_id,
                new_snapshot,
            )
            session.flush()
            inject("entity_revisions")

            continuation_session_id = self._apply_session_transition(
                session, plan, new_snapshot
            )
            inject("session")

            first_event_order = (
                int(session.scalar(select(func.max(WorldEventRow.event_order))) or 0)
                + 1
            )
            recognized = EventRecognizer().recognize(
                plan,
                first_event_order=first_event_order,
                id_generator=self._id_generator,
            )
            event_ids = {item.candidate.event_key: item.event_id for item in recognized}
            for item in recognized:
                candidate = item.candidate
                ledger.append_world_event(
                    WorldEventRow(
                        event_id=item.event_id,
                        event_order=item.event_order,
                        world_version=plan.new_world_version,
                        segment_id=segment_id,
                        session_id=plan.session_id,
                        start_time_ms=candidate.start_time_ms,
                        end_time_ms=candidate.end_time_ms,
                        event_type=candidate.event_type,
                        schema_version=1,
                        payload_json=canonical_json(
                            {
                                "actor_id": candidate.actor_id,
                                "source_kind": candidate.source_kind,
                                "source_ref": candidate.source_ref,
                                "evidence_refs": candidate.evidence_refs,
                                "cause_event_ids": [
                                    event_ids[key] for key in candidate.cause_event_keys
                                ],
                                "location_id": candidate.location_id,
                                "scope_key": candidate.scope_key,
                                "fact": candidate.payload,
                            }
                        ),
                    )
                )
            session.flush()
            inject("world_events")

            observations = PerceptionProjector().project_event_observations(
                recognized, new_snapshot, base_snapshot=snapshot
            )
            for observation in observations:
                session.add(
                    AgentMemoryRow(
                        memory_id=self._id_generator(),
                        agent_id=observation.agent_id,
                        namespace="default",
                        memory_type="observation",
                        world_version=plan.new_world_version,
                        relative_time_ms=observation.relative_time_ms,
                        importance=2,
                        source=f"world_event:{observation.event_id}",
                        status=None,
                        supersedes_memory_id=None,
                        entity_tags_json="[]",
                        location_tags_json=canonical_json(
                            [observation.location_id]
                            if observation.location_id is not None
                            else []
                        ),
                        schema_version=1,
                        payload_json=canonical_json(observation.payload),
                    )
                )
            for change in plan.accepted_memory_changes:
                payload: dict[str, Any] = {"content": change.content}
                if change.supersedes_memory_id is not None:
                    payload["supersedes_memory_id"] = change.supersedes_memory_id
                session.add(
                    AgentMemoryRow(
                        memory_id=self._id_generator(),
                        agent_id=change.agent_id,
                        namespace=change.namespace,
                        memory_type=change.memory_type,
                        world_version=plan.new_world_version,
                        relative_time_ms=plan.wave_ended_at_ms,
                        importance=change.importance,
                        source=change.source,
                        status=change.status,
                        supersedes_memory_id=change.supersedes_memory_id,
                        entity_tags_json=canonical_json(sorted(change.entity_tags)),
                        location_tags_json=canonical_json(sorted(change.location_tags)),
                        schema_version=1,
                        payload_json=canonical_json(payload),
                    )
                )
            session.flush()
            inject("memory")

            snapshot_json = canonical_json(new_snapshot)
            snapshot_checksum = sha256_text(snapshot_json)
            session.add(
                SnapshotRow(
                    world_version=plan.new_world_version,
                    schema_version=1,
                    snapshot_json=snapshot_json,
                    checksum=snapshot_checksum,
                    created_at=committed_at,
                )
            )
            world.current_version = plan.new_world_version
            session.flush()
            inject("snapshot")

        return WaveCommitResult(
            world_version=plan.new_world_version,
            snapshot_checksum=snapshot_checksum,
            world_event_count=len(recognized),
            observation_count=len(observations),
            entity_revision_count=entity_revision_count,
            continuation_session_id=continuation_session_id,
        )

    def _apply_session_transition(
        self,
        session: Session,
        plan: ValidatedCommitPlan,
        snapshot: dict[str, Any],
    ) -> str | None:
        snapshot_sessions = {item["session_id"]: item for item in snapshot["sessions"]}
        closure_reason = (
            "partitioned"
            if plan.successor_sessions
            else plan.session_intent
            if plan.session_intent in {"resolved", "limit_reached"}
            else None
        )
        closed_ids = set(plan.closed_session_ids)
        if closure_reason is not None and not closed_ids:
            closed_ids.add(plan.session_id)

        for closed_id in sorted(closed_ids):
            event_session = session.get(EventSessionRow, closed_id)
            if event_session is None or event_session.status != "runnable":
                raise ValueError(f"Runnable Event Session '{closed_id}' is missing")
            event_session.status = "closed"
            event_session.closed_world_version = plan.new_world_version
            event_session.closure_reason = closure_reason
            snapshot_session = snapshot_sessions.get(closed_id)
            if snapshot_session is None:
                raise ValueError(
                    f"Event Session '{closed_id}' is missing from Snapshot"
                )
            snapshot_session["status"] = "closed"
            snapshot_session["closure_reason"] = closure_reason
            snapshot_session["closed_world_version"] = plan.new_world_version
            snapshot_session["pending_response_ids"] = []

        snapshot["runnable_session_queue"] = [
            item
            for item in snapshot["runnable_session_queue"]
            if item["session_id"] not in closed_ids
        ]
        if closed_ids:
            session.execute(
                delete(EventSessionPendingResponseRow).where(
                    EventSessionPendingResponseRow.session_id.in_(closed_ids)
                )
            )

        if plan.successor_sessions:
            next_queue_order = (
                int(session.scalar(select(func.max(EventSessionRow.queue_order))) or 0)
                + 1
            )
            for offset, successor in enumerate(plan.successor_sessions):
                if session.get(EventSessionRow, successor.session_id) is not None:
                    raise ValueError(
                        f"Successor Event Session '{successor.session_id}' already exists"
                    )
                session.add(
                    EventSessionRow(
                        session_id=successor.session_id,
                        queue_order=next_queue_order + offset,
                        status="runnable",
                        location_id=successor.location_id,
                        scope_key=successor.scope_key,
                        created_world_version=plan.new_world_version,
                        closed_world_version=None,
                        closure_reason=None,
                    )
                )
                session.flush()
                for participant_id in successor.participant_ids:
                    session.add(
                        EventSessionMemberRow(
                            session_id=successor.session_id,
                            agent_id=participant_id,
                        )
                    )
                for responder_id in successor.pending_response_ids:
                    session.add(
                        EventSessionPendingResponseRow(
                            session_id=successor.session_id,
                            responder_id=responder_id,
                        )
                    )
                queue_order = next_queue_order + offset
                snapshot_session = {
                    "session_id": successor.session_id,
                    "status": "runnable",
                    "location_id": successor.location_id,
                    "scope_key": successor.scope_key,
                    "participant_ids": successor.participant_ids,
                    "parent_session_ids": successor.parent_session_ids,
                    "pending_response_ids": successor.pending_response_ids,
                }
                snapshot["sessions"].append(snapshot_session)
                snapshot["runnable_session_queue"].append(
                    {
                        "queue_order": queue_order,
                        "session_id": successor.session_id,
                    }
                )
            snapshot["sessions"].sort(key=lambda item: item["session_id"])
            snapshot["runnable_session_queue"].sort(
                key=lambda item: item["queue_order"]
            )
            return plan.successor_sessions[0].session_id

        if not closed_ids:
            event_session = session.get(EventSessionRow, plan.session_id)
            if event_session is None or event_session.status != "runnable":
                raise ValueError("Validated Event Session is missing")
            session.execute(
                delete(EventSessionPendingResponseRow).where(
                    EventSessionPendingResponseRow.session_id == plan.session_id
                )
            )
            for responder_id in plan.pending_response_ids:
                session.add(
                    EventSessionPendingResponseRow(
                        session_id=plan.session_id,
                        responder_id=responder_id,
                    )
                )
            snapshot_sessions[plan.session_id]["pending_response_ids"] = (
                plan.pending_response_ids
            )
        return None

    def _apply_entity_changes(
        self,
        session: Session,
        ledger: LedgerRepository,
        plan: ValidatedCommitPlan,
        segment_id: str,
        snapshot: dict[str, Any],
    ) -> int:
        by_id = {item["entity_id"]: item for item in snapshot["entities"]}
        for change in plan.entity_changes:
            entity = by_id[change.entity_id]
            payload = deepcopy(entity["payload"])
            payload.setdefault("state", {}).update(change.state_patch)
            if change.location_id is not None:
                entity["location_id"] = change.location_id
                entity["scope_key"] = change.scope_key
            entity["payload"] = payload
            entity["revision_order"] += 1
            ledger.append_entity_revision(
                EntityRevisionRow(
                    entity_revision_id=self._id_generator(),
                    entity_id=entity["entity_id"],
                    entity_type=entity["entity_type"],
                    revision_order=entity["revision_order"],
                    world_version=plan.new_world_version,
                    segment_id=segment_id,
                    name=entity["name"],
                    location_id=entity.get("location_id"),
                    scope_key=entity.get("scope_key"),
                    schema_version=1,
                    payload_json=canonical_json(payload),
                )
            )
        return len(plan.entity_changes)

    def _reject_credentials(self, value: Any) -> None:
        forbidden = {"authorization", "api_key", "apikey", "token", "secret"}
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = str(key).lower().replace("-", "_")
                if normalized in forbidden:
                    raise ValueError("Generation Trace must not contain credentials")
                self._reject_credentials(child)
        elif isinstance(value, list):
            for child in value:
                self._reject_credentials(child)

    def _timestamp(self) -> str:
        value = self._clock()
        if value.utcoffset() is None:
            raise ValueError("Clock must return a timezone-aware datetime")
        return value.astimezone(UTC).isoformat(timespec="microseconds")
