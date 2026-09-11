"""Pure operations over one immutable, Agent-scoped memory stream."""

from __future__ import annotations

from typing import Self

from pydantic import model_validator

from agent_runtime.agent.memory.contracts import MemoryKind, MemoryRecord, MemoryTouch
from agent_runtime.agent.memory.errors import (
    DuplicateMemoryError,
    MemoryScopeError,
    MemoryTouchError,
)
from agent_runtime.model import StrictModel
from agent_runtime.world.contracts import WorldRef


class MemoryStream(StrictModel):
    """Append-only records bound to one exact World, Agent, and scope."""

    world_ref: WorldRef
    agent_id: str
    scope: str
    records: tuple[MemoryRecord, ...] = ()

    @model_validator(mode="after")
    def _validate_records(self) -> Self:
        if not self.agent_id.strip():
            raise ValueError("memory stream agentId cannot be empty")
        if not self.scope.strip():
            raise ValueError("memory stream scope cannot be empty")

        memory_ids: set[str] = set()
        for record in self.records:
            if (
                record.world_ref != self.world_ref
                or record.agent_id != self.agent_id
                or record.scope != self.scope
            ):
                raise ValueError(
                    f'memory "{record.id}" is outside World "{self.world_ref.world_id}", '
                    f'agent "{self.agent_id}" '
                    f'and scope "{self.scope}"'
                )
            if record.id in memory_ids:
                raise ValueError(f'duplicate memory id "{record.id}"')
            memory_ids.add(record.id)
        return self

    def append(self, record: MemoryRecord) -> MemoryStream:
        """Return a stream with one validated record appended at the tail."""

        validated = MemoryRecord.model_validate(record, strict=True)
        if (
            validated.world_ref != self.world_ref
            or validated.agent_id != self.agent_id
            or validated.scope != self.scope
        ):
            raise MemoryScopeError(
                f'memory "{validated.id}" is outside World "{self.world_ref.world_id}", '
                f'agent "{self.agent_id}" '
                f'and scope "{self.scope}"'
            )
        if any(existing.id == validated.id for existing in self.records):
            raise DuplicateMemoryError(f'duplicate memory id "{validated.id}"')
        return MemoryStream(
            world_ref=self.world_ref,
            agent_id=self.agent_id,
            scope=self.scope,
            records=(*self.records, validated),
        )

    def touch(self, touches: tuple[MemoryTouch, ...]) -> MemoryStream:
        """Return a stream with explicitly selected access timestamps advanced."""

        validated_touches = tuple(
            MemoryTouch.model_validate(touch, strict=True) for touch in touches
        )
        for touch in validated_touches:
            if (
                touch.world_ref != self.world_ref
                or touch.agent_id != self.agent_id
                or touch.scope != self.scope
            ):
                raise MemoryScopeError(
                    f'touch for memory "{touch.memory_id}" is outside '
                    f'World "{self.world_ref.world_id}", agent "{self.agent_id}" '
                    f'and scope "{self.scope}"'
                )
        touch_ids = tuple(touch.memory_id for touch in validated_touches)
        if len(touch_ids) != len(set(touch_ids)):
            raise DuplicateMemoryError("memory touch ids must be unique")

        known_ids = frozenset(record.id for record in self.records)
        unknown_ids = tuple(memory_id for memory_id in touch_ids if memory_id not in known_ids)
        if unknown_ids:
            raise MemoryTouchError(f'unknown memory id "{unknown_ids[0]}"')

        touched_records: list[MemoryRecord] = []
        for record in self.records:
            touch = next(
                (item for item in validated_touches if item.memory_id == record.id),
                None,
            )
            if touch is None:
                touched_records.append(record)
                continue
            if touch.accessed_at < record.last_accessed_at:
                raise MemoryTouchError(
                    f'touch for memory "{record.id}" cannot move lastAccessedAt backwards'
                )
            touched_records.append(_touch_record(record, touch))

        return MemoryStream(
            world_ref=self.world_ref,
            agent_id=self.agent_id,
            scope=self.scope,
            records=tuple(touched_records),
        )

    def recent(
        self,
        limit: int,
        *,
        kinds: tuple[MemoryKind, ...] | None = None,
    ) -> tuple[MemoryRecord, ...]:
        """Return newest-appended records first with deterministic tie behavior."""

        _require_non_negative_limit(limit)
        if limit == 0:
            return ()
        allowed_kinds = frozenset(kinds) if kinds is not None else None
        matching = (
            record
            for record in reversed(self.records)
            if allowed_kinds is None or record.kind in allowed_kinds
        )
        return tuple(record for _, record in zip(range(limit), matching, strict=False))

    def is_novel(
        self,
        novelty_key: str,
        *,
        recent_limit: int | None,
        kinds: tuple[MemoryKind, ...] | None = None,
    ) -> bool:
        """Check a Runtime-supplied novelty identity against recent writes."""

        if not novelty_key.strip():
            raise ValueError("novelty_key cannot be empty")
        records = self.records if recent_limit is None else self.recent(recent_limit, kinds=kinds)
        allowed_kinds = frozenset(kinds) if kinds is not None else None
        return all(
            record.novelty_key != novelty_key
            for record in records
            if allowed_kinds is None or record.kind in allowed_kinds
        )


def _touch_record(record: MemoryRecord, touch: MemoryTouch) -> MemoryRecord:
    return MemoryRecord(
        id=record.id,
        world_ref=record.world_ref,
        agent_id=record.agent_id,
        scope=record.scope,
        kind=record.kind,
        created_at=record.created_at,
        last_accessed_at=touch.accessed_at,
        expires_at=record.expires_at,
        subject=record.subject,
        predicate=record.predicate,
        object=record.object,
        content=record.content,
        poignancy=record.poignancy,
        tags=record.tags,
        source=record.source,
        source_entry_id=record.source_entry_id,
        evidence_ids=record.evidence_ids,
        embedding=record.embedding,
        novelty_key=record.novelty_key,
    )


def _require_non_negative_limit(limit: int) -> None:
    if limit < 0:
        raise ValueError("memory limit cannot be negative")
