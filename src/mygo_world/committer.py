from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from mygo_world.canonical import canonical_json, sha256_text
from mygo_world.contracts import CharacterSeed, LoadedSeed, LocationSeed, ObjectSeed
from mygo_world.db.models import (
    AgentMemoryRow,
    EntityRevisionRow,
    EventSessionMemberRow,
    EventSessionRow,
    RunnableSessionQueueRow,
    SnapshotRow,
    WorldEventRow,
    WorldRow,
    WorldSegmentRow,
    WorldVersionRow,
)
from mygo_world.repositories import LedgerRepository

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


@dataclass(frozen=True)
class GenesisCommitResult:
    world_version: int
    snapshot_checksum: str


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
            session.add(
                WorldRow(
                    world_id=plan.world_id,
                    name=seed.name,
                    seed_id=seed.seed_id,
                    seed_version=seed.version,
                    seed_content_hash=plan.loaded_seed.content_hash,
                    current_version=1,
                    calendar_anchor=seed.calendar_anchor,
                    created_at=created_at,
                )
            )
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
                session.add(
                    RunnableSessionQueueRow(
                        queue_order=queue_order,
                        session_id=event_session.session_id,
                        enqueued_world_version=1,
                        dequeued_world_version=None,
                    )
                )

            for memory in seed.memories:
                session.add(
                    AgentMemoryRow(
                        memory_id=memory.memory_id,
                        agent_id=memory.agent_id,
                        namespace=memory.namespace,
                        memory_type=memory.memory_type,
                        world_version=1,
                        relative_time_ms=memory.relative_time_ms,
                        importance=memory.importance,
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

            session.add(
                SnapshotRow(
                    world_version=1,
                    schema_version=1,
                    snapshot_json=snapshot_json,
                    checksum=snapshot_checksum,
                    created_at=created_at,
                )
            )
            event_count = session.scalar(
                select(func.count()).select_from(WorldEventRow)
            )
            if event_count != 0:
                raise AssertionError("Genesis must not create World Events")

        return GenesisCommitResult(
            world_version=1,
            snapshot_checksum=snapshot_checksum,
        )

    def _timestamp(self) -> str:
        value = self._clock()
        if value.utcoffset() is None:
            raise ValueError("Clock must return a timezone-aware datetime")
        return value.astimezone(UTC).isoformat(timespec="microseconds")
