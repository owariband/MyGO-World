"""Immutable contracts for scoped Persona memory and retrieval results."""

from __future__ import annotations

import math
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Protocol, Self

from pydantic import Field, StringConstraints, field_validator, model_validator

from agent_runtime.model import StrictModel
from agent_runtime.world.contracts import Identifier, WorldRef

NonEmptyText = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]
NonNegativeScore = Annotated[float, Field(ge=0.0, allow_inf_nan=False)]


class MemoryKind(StrEnum):
    EVENT = "event"
    THOUGHT = "thought"
    CHAT = "chat"
    PLAN = "plan"


class MemoryRecord(StrictModel):
    """One append-only memory value owned by one World, Agent, and scope."""

    id: NonEmptyText
    world_ref: WorldRef
    agent_id: NonEmptyText
    scope: NonEmptyText
    kind: MemoryKind
    created_at: datetime
    last_accessed_at: datetime
    expires_at: datetime | None = None
    subject: NonEmptyText
    predicate: NonEmptyText
    object: NonEmptyText | None = None
    content: NonEmptyText
    poignancy: NonNegativeScore
    tags: tuple[NonEmptyText, ...] = ()
    source: NonEmptyText
    source_entry_id: Identifier | None = None
    evidence_ids: tuple[NonEmptyText, ...] = ()
    embedding: tuple[float, ...] = ()
    novelty_key: NonEmptyText

    @field_validator("created_at", "last_accessed_at", "expires_at")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("memory timestamps must include a timezone")
        return value

    @field_validator("embedding")
    @classmethod
    def _require_finite_embedding(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        if not all(math.isfinite(component) for component in value):
            raise ValueError("memory embedding values must be finite")
        return value

    @model_validator(mode="after")
    def _validate_times(self) -> Self:
        if self.last_accessed_at < self.created_at:
            raise ValueError("lastAccessedAt cannot precede createdAt")
        if self.expires_at is not None and self.expires_at < self.created_at:
            raise ValueError("expiresAt cannot precede createdAt")
        return self


class MemoryTouch(StrictModel):
    """An explicit request to advance one record's access timestamp."""

    memory_id: NonEmptyText
    world_ref: WorldRef
    agent_id: NonEmptyText
    scope: NonEmptyText
    accessed_at: datetime

    @field_validator("accessed_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("memory touch timestamp must include a timezone")
        return value


class MemoryRetrieval(StrictModel):
    """Pure retrieval output; callers explicitly apply the returned touches."""

    records: tuple[MemoryRecord, ...] = ()
    touches: tuple[MemoryTouch, ...] = ()

    @model_validator(mode="after")
    def _match_records_and_touches(self) -> Self:
        record_keys = tuple(
            (record.world_ref, record.agent_id, record.scope, record.id) for record in self.records
        )
        touch_keys = tuple(
            (touch.world_ref, touch.agent_id, touch.scope, touch.memory_id)
            for touch in self.touches
        )
        if record_keys != touch_keys:
            raise ValueError("retrieval touches must match record owners and IDs in result order")
        return self


class EmbeddingProvider(Protocol):
    """Narrow adapter used by ranked retrieval to embed one focal point."""

    def embed(self, text: str) -> tuple[float, ...]:
        """Return an immutable embedding vector without writing memory."""
        ...
