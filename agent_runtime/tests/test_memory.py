"""Contract tests for scoped, side-effect-free Persona memory retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from agent_runtime.agent.memory import (
    MemoryKind,
    MemoryRecord,
    MemoryStream,
    MemoryTouch,
    retrieve_ranked,
    retrieve_related,
)
from agent_runtime.agent.memory.errors import (
    DuplicateMemoryError,
    MemoryScopeError,
    MemoryTouchError,
)

BASE_TIME = datetime(2026, 8, 31, 12, tzinfo=UTC)
AGENT_ID = "anon"
MEMORY_SCOPE = "project/coffee-golden/persona/anon/episodic"


@dataclass(frozen=True, slots=True)
class FixedEmbeddingProvider:
    embedding: tuple[float, ...]

    def embed(self, text: str) -> tuple[float, ...]:
        assert text == "coffee queue"
        return self.embedding


@dataclass(frozen=True, slots=True)
class UnexpectedEmbeddingProvider:
    def embed(self, text: str) -> tuple[float, ...]:
        raise AssertionError(f"empty retrieval unexpectedly embedded {text!r}")


def test_stream_append_recent_and_runtime_supplied_novelty() -> None:
    first = _record("memory-1", minute=1, novelty_key="event-1/rev-1/name")
    second = _record(
        "memory-2",
        minute=2,
        kind=MemoryKind.PLAN,
        novelty_key="event-2/rev-1/location",
    )

    empty = MemoryStream(agent_id=AGENT_ID, scope=MEMORY_SCOPE)
    stream = empty.append(first).append(second)

    assert empty.records == ()
    assert stream.records == (first, second)
    assert stream.recent(1) == (second,)
    assert stream.recent(2, kinds=(MemoryKind.EVENT,)) == (first,)
    assert not stream.is_novel("event-2/rev-1/location", recent_limit=1)
    assert stream.is_novel("event-1/rev-1/name", recent_limit=1)


def test_stream_rejects_foreign_scope_and_duplicate_ids() -> None:
    stream = MemoryStream(agent_id=AGENT_ID, scope=MEMORY_SCOPE)
    foreign_agent = _record("foreign-agent", minute=1, agent_id="soyo")
    foreign_scope = _record(
        "foreign-scope",
        minute=2,
        scope=f"{MEMORY_SCOPE}/private",
    )

    with pytest.raises(MemoryScopeError, match="outside"):
        stream.append(foreign_agent)
    with pytest.raises(MemoryScopeError, match="outside"):
        stream.append(foreign_scope)

    stream = stream.append(_record("memory-1", minute=1))
    with pytest.raises(DuplicateMemoryError, match="duplicate memory id"):
        stream.append(_record("memory-1", minute=2))


def test_retrieve_related_casefolds_or_keywords_and_preserves_stable_order() -> None:
    stream = _stream(
        _record("old-coffee", minute=1, subject="Anon", tags=("Coffee",)),
        _record("queue", minute=2, predicate="WAITS-IN", tags=("shop",)),
        _record("new-coffee", minute=3, object_="COFFEE"),
        _record("unrelated", minute=4, subject="Tomori"),
    )

    result = retrieve_related(
        stream,
        keywords=("coffee", "waits-in"),
        accessed_at=BASE_TIME + timedelta(minutes=10),
    )

    assert tuple(record.id for record in result.records) == (
        "new-coffee",
        "queue",
        "old-coffee",
    )
    assert tuple(touch.memory_id for touch in result.touches) == (
        "new-coffee",
        "queue",
        "old-coffee",
    )


def test_retrieve_ranked_combines_relevance_importance_and_recent_first_recency() -> None:
    stream = _stream(
        _record("important", minute=1, poignancy=10.0, embedding=(0.0, 1.0)),
        _record("relevant", minute=2, poignancy=0.0, embedding=(1.0, 0.0)),
        _record("recent", minute=3, poignancy=0.0, embedding=(0.0, 1.0)),
    )
    accessed_at = BASE_TIME + timedelta(minutes=10)

    combined = retrieve_ranked(
        stream,
        focal_point="coffee queue",
        embedding_provider=FixedEmbeddingProvider((1.0, 0.0)),
        accessed_at=accessed_at,
    )
    recency_only = retrieve_ranked(
        stream,
        focal_point="coffee queue",
        embedding_provider=FixedEmbeddingProvider((1.0, 0.0)),
        accessed_at=accessed_at,
        recency_weight=1.0,
        relevance_weight=0.0,
        importance_weight=0.0,
    )

    assert tuple(record.id for record in combined.records) == (
        "relevant",
        "important",
        "recent",
    )
    assert tuple(record.id for record in recency_only.records) == (
        "recent",
        "relevant",
        "important",
    )


def test_retrieve_ranked_handles_empty_stream_without_embedding_call() -> None:
    result = retrieve_ranked(
        MemoryStream(agent_id=AGENT_ID, scope=MEMORY_SCOPE),
        focal_point="coffee queue",
        embedding_provider=UnexpectedEmbeddingProvider(),
        accessed_at=BASE_TIME,
    )

    assert result.records == ()
    assert result.touches == ()


def test_retrieve_ranked_handles_zero_vectors_with_stable_order() -> None:
    stream = _stream(
        _record("older", minute=1, embedding=(0.0, 0.0)),
        _record("newer", minute=2, embedding=(0.0, 0.0)),
    )

    result = retrieve_ranked(
        stream,
        focal_point="coffee queue",
        embedding_provider=FixedEmbeddingProvider((0.0, 0.0)),
        accessed_at=BASE_TIME + timedelta(minutes=10),
        recency_weight=0.0,
        relevance_weight=1.0,
        importance_weight=0.0,
    )

    assert tuple(record.id for record in result.records) == ("newer", "older")


def test_retrieval_returns_explicit_touches_without_mutating_stream() -> None:
    original_access = BASE_TIME + timedelta(minutes=1)
    accessed_at = BASE_TIME + timedelta(minutes=10)
    stream = _stream(
        _record(
            "memory-1",
            minute=1,
            last_accessed_at=original_access,
            tags=("coffee",),
        )
    )

    result = retrieve_related(
        stream,
        keywords=("coffee",),
        accessed_at=accessed_at,
    )
    touched = stream.touch(result.touches)

    assert stream.records[0].last_accessed_at == original_access
    assert result.touches == (MemoryTouch(memory_id="memory-1", accessed_at=accessed_at),)
    assert touched.records[0].last_accessed_at == accessed_at
    assert touched is not stream

    with pytest.raises(MemoryTouchError, match="cannot move lastAccessedAt backwards"):
        touched.touch((MemoryTouch(memory_id="memory-1", accessed_at=original_access),))


def test_retrieval_excludes_future_records_instead_of_returning_backward_touches() -> None:
    accessed_at = BASE_TIME + timedelta(minutes=10)
    stream = _stream(
        _record("eligible", minute=1, tags=("coffee",)),
        _record("future-created", minute=20, tags=("coffee",)),
        _record(
            "future-accessed",
            minute=2,
            last_accessed_at=BASE_TIME + timedelta(minutes=20),
            tags=("coffee",),
        ),
    )

    related = retrieve_related(
        stream,
        keywords=("coffee",),
        accessed_at=accessed_at,
    )
    ranked = retrieve_ranked(
        stream,
        focal_point="coffee queue",
        embedding_provider=FixedEmbeddingProvider((1.0, 0.0)),
        accessed_at=accessed_at,
    )

    assert tuple(record.id for record in related.records) == ("eligible",)
    assert tuple(record.id for record in ranked.records) == ("eligible",)
    assert related.touches == (MemoryTouch(memory_id="eligible", accessed_at=accessed_at),)
    assert ranked.touches == (MemoryTouch(memory_id="eligible", accessed_at=accessed_at),)
    assert stream.touch((*related.touches,)) is not stream


def _stream(*records: MemoryRecord) -> MemoryStream:
    stream = MemoryStream(agent_id=AGENT_ID, scope=MEMORY_SCOPE)
    for record in records:
        stream = stream.append(record)
    return stream


def _record(
    memory_id: str,
    *,
    minute: int,
    agent_id: str = AGENT_ID,
    scope: str = MEMORY_SCOPE,
    kind: MemoryKind = MemoryKind.EVENT,
    last_accessed_at: datetime | None = None,
    subject: str = "Anon",
    predicate: str = "remembers",
    object_: str = "queue",
    poignancy: float = 1.0,
    tags: tuple[str, ...] = (),
    embedding: tuple[float, ...] = (1.0, 0.0),
    novelty_key: str | None = None,
) -> MemoryRecord:
    created_at = BASE_TIME + timedelta(minutes=minute)
    return MemoryRecord(
        id=memory_id,
        agent_id=agent_id,
        scope=scope,
        kind=kind,
        created_at=created_at,
        last_accessed_at=last_accessed_at or created_at,
        subject=subject,
        predicate=predicate,
        object=object_,
        content=f"content for {memory_id}",
        poignancy=poignancy,
        tags=tags,
        source=f"world-event:{memory_id}",
        evidence_ids=(f"evidence:{memory_id}",),
        embedding=embedding,
        novelty_key=novelty_key or f"novelty:{memory_id}",
    )
