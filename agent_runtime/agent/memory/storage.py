"""SQLite persistence for immutable, Agent-scoped Memory streams."""

from __future__ import annotations

import json
from collections import defaultdict

from pydantic import TypeAdapter
from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    select,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from agent_runtime.agent.memory.contracts import MemoryRecord
from agent_runtime.agent.memory.errors import MemoryScopeError
from agent_runtime.agent.memory.stream import MemoryStream
from agent_runtime.sqlite import Base, require_session_project
from agent_runtime.world.contracts import WorldRef

_TEXT_TUPLE = TypeAdapter(tuple[str, ...])
_FLOAT_TUPLE = TypeAdapter(tuple[float, ...])


class MemoryRow(Base):
    """One complete MemoryRecord with a stable position in its Agent stream."""

    __tablename__ = "agent_memory_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ["world_id", "agent_id"],
            ["agent_world_states.world_id", "agent_world_states.agent_id"],
            name="fk_agent_memory_records_agent",
        ),
        UniqueConstraint(
            "world_id",
            "agent_id",
            "memory_seq",
            name="uq_agent_memory_records_sequence",
        ),
        CheckConstraint(
            "memory_seq >= 1",
            name="ck_agent_memory_records_sequence",
        ),
        CheckConstraint(
            "kind IN ('event', 'thought', 'chat', 'plan')",
            name="ck_agent_memory_records_kind",
        ),
        CheckConstraint(
            "poignancy >= 0",
            name="ck_agent_memory_records_poignancy",
        ),
        CheckConstraint(
            "json_valid(tags_json)",
            name="ck_agent_memory_records_tags_json",
        ),
        CheckConstraint(
            "json_valid(evidence_ids_json)",
            name="ck_agent_memory_records_evidence_json",
        ),
        CheckConstraint(
            "json_valid(embedding_json)",
            name="ck_agent_memory_records_embedding_json",
        ),
        Index(
            "ix_agent_memory_records_recent",
            "world_id",
            "agent_id",
            "created_at",
            "memory_id",
        ),
        Index(
            "ix_agent_memory_records_novelty",
            "world_id",
            "agent_id",
            "novelty_key",
        ),
    )

    world_id: Mapped[str] = mapped_column(Text, primary_key=True)
    agent_id: Mapped[str] = mapped_column(Text, primary_key=True)
    memory_id: Mapped[str] = mapped_column(Text, primary_key=True)
    memory_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    last_accessed_at: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[str | None] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    predicate: Mapped[str] = mapped_column(Text, nullable=False)
    object_value: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    poignancy: Mapped[float] = mapped_column(Float, nullable=False)
    tags_json: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_json: Mapped[str] = mapped_column(Text, nullable=False)
    novelty_key: Mapped[str] = mapped_column(Text, nullable=False)


class MemoryStore:
    """Persist private MemoryStreams for trusted Runtime assembly."""

    def __init__(self, world_ref: WorldRef) -> None:
        self._world_ref = WorldRef.model_validate(world_ref, strict=True)

    def insert_stream(self, session: Session, stream: MemoryStream) -> None:
        """Stage one complete Agent stream in the caller's transaction."""

        require_session_project(session, self._world_ref.project_id)
        validated = MemoryStream.model_validate(stream, strict=True)
        if validated.world_ref != self._world_ref:
            raise MemoryScopeError("MemoryStream belongs to a different WorldRef")
        existing = session.scalar(
            select(func.count())
            .select_from(MemoryRow)
            .where(
                MemoryRow.world_id == self._world_ref.world_id,
                MemoryRow.agent_id == validated.agent_id,
            )
        )
        if existing:
            raise ValueError(f'MemoryStream for agent "{validated.agent_id}" already exists')

        for memory_seq, record in enumerate(validated.records, start=1):
            self._require_record_owner(record, validated)
            session.add(self._row(record, memory_seq=memory_seq))

    def load_all_for_bootstrap(self, session: Session) -> tuple[MemoryStream, ...]:
        """Restore all roles for trusted bootstrap; never expose this to an Agent."""

        require_session_project(session, self._world_ref.project_id)
        rows = session.scalars(
            select(MemoryRow)
            .where(MemoryRow.world_id == self._world_ref.world_id)
            .order_by(MemoryRow.agent_id, MemoryRow.memory_seq)
        ).all()
        by_agent: dict[str, list[MemoryRow]] = defaultdict(list)
        for row in rows:
            by_agent[row.agent_id].append(row)

        streams: list[MemoryStream] = []
        for agent_id, agent_rows in by_agent.items():
            expected = tuple(range(1, len(agent_rows) + 1))
            actual = tuple(row.memory_seq for row in agent_rows)
            if actual != expected:
                raise ValueError(f'Memory sequence for agent "{agent_id}" is not contiguous')
            records = tuple(self._record(row) for row in agent_rows)
            stream = MemoryStream(
                world_ref=self._world_ref,
                agent_id=agent_id,
                scope=records[0].scope,
                records=records,
            )
            streams.append(stream)
        return tuple(streams)

    def _row(self, record: MemoryRecord, *, memory_seq: int) -> MemoryRow:
        return MemoryRow(
            world_id=self._world_ref.world_id,
            agent_id=record.agent_id,
            memory_id=record.id,
            memory_seq=memory_seq,
            scope=record.scope,
            kind=record.kind.value,
            created_at=record.created_at.isoformat(),
            last_accessed_at=record.last_accessed_at.isoformat(),
            expires_at=(record.expires_at.isoformat() if record.expires_at is not None else None),
            subject=record.subject,
            predicate=record.predicate,
            object_value=record.object,
            content=record.content,
            poignancy=record.poignancy,
            tags_json=_dump_json(record.tags),
            source=record.source,
            evidence_ids_json=_dump_json(record.evidence_ids),
            embedding_json=_dump_json(record.embedding),
            novelty_key=record.novelty_key,
        )

    def _record(self, row: MemoryRow) -> MemoryRecord:
        payload = {
            "id": row.memory_id,
            "worldRef": self._world_ref.model_dump(mode="json", by_alias=True),
            "agentId": row.agent_id,
            "scope": row.scope,
            "kind": row.kind,
            "createdAt": row.created_at,
            "lastAccessedAt": row.last_accessed_at,
            "expiresAt": row.expires_at,
            "subject": row.subject,
            "predicate": row.predicate,
            "object": row.object_value,
            "content": row.content,
            "poignancy": row.poignancy,
            "tags": _TEXT_TUPLE.validate_json(row.tags_json, strict=True),
            "source": row.source,
            "evidenceIds": _TEXT_TUPLE.validate_json(row.evidence_ids_json, strict=True),
            "embedding": _FLOAT_TUPLE.validate_json(row.embedding_json, strict=True),
            "noveltyKey": row.novelty_key,
        }
        record = MemoryRecord.model_validate_json(
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ),
            strict=True,
        )
        if record.world_ref != self._world_ref or record.agent_id != row.agent_id:
            raise MemoryScopeError("persisted MemoryRecord ownership does not match its row")
        return record

    def _require_record_owner(self, record: MemoryRecord, stream: MemoryStream) -> None:
        if (
            record.world_ref != self._world_ref
            or record.agent_id != stream.agent_id
            or record.scope != stream.scope
        ):
            raise MemoryScopeError("MemoryRecord ownership does not match its stream")


def _dump_json(values: tuple[str, ...] | tuple[float, ...]) -> str:
    return json.dumps(
        values,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
