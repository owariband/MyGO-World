"""Contract tests for scoped, side-effect-free Persona memory retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from agent_runtime.agent.memory import (
    MemoryKind,
    MemoryRecord,
    MemoryRetrieval,
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
from agent_runtime.world.contracts import WorldRef

BASE_TIME = datetime(2026, 8, 31, 12, tzinfo=UTC)
AGENT_ID = "anon"
MEMORY_SCOPE = "project/coffee-golden/persona/anon/episodic"
WORLD_REF = WorldRef(project_id="coffee-golden", world_id="world-1")
FOREIGN_OWNERS = (
    (WorldRef(project_id="other-project", world_id="world-1"), AGENT_ID, MEMORY_SCOPE),
    (WorldRef(project_id="coffee-golden", world_id="world-2"), AGENT_ID, MEMORY_SCOPE),
    (WORLD_REF, "soyo", MEMORY_SCOPE),
    (WORLD_REF, AGENT_ID, f"{MEMORY_SCOPE}/private"),
)


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

    empty = MemoryStream(world_ref=WORLD_REF, agent_id=AGENT_ID, scope=MEMORY_SCOPE)
    stream = empty.append(first).append(second)

    assert empty.records == ()
    assert stream.records == (first, second)
    assert stream.recent(1) == (second,)
    assert stream.recent(2, kinds=(MemoryKind.EVENT,)) == (first,)
    assert not stream.is_novel("event-2/rev-1/location", recent_limit=1)
    assert stream.is_novel("event-1/rev-1/name", recent_limit=1)


def test_stream_rejects_foreign_scope_and_duplicate_ids() -> None:
    stream = MemoryStream(world_ref=WORLD_REF, agent_id=AGENT_ID, scope=MEMORY_SCOPE)
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
        MemoryStream(world_ref=WORLD_REF, agent_id=AGENT_ID, scope=MEMORY_SCOPE),
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
    assert result.touches == (_touch("memory-1", accessed_at),)
    assert touched.records[0].last_accessed_at == accessed_at
    assert touched is not stream

    with pytest.raises(MemoryTouchError, match="cannot move lastAccessedAt backwards"):
        touched.touch((_touch("memory-1", original_access),))


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
    assert related.touches == (_touch("eligible", accessed_at),)
    assert ranked.touches == (_touch("eligible", accessed_at),)
    assert stream.touch((*related.touches,)) is not stream


@pytest.mark.parametrize(("world_ref", "agent_id", "scope"), FOREIGN_OWNERS)
def test_constructor_and_append_reject_foreign_owner_before_matching_id(
    world_ref: WorldRef, agent_id: str, scope: str
) -> None:
    record = _record("same-id", minute=1)
    stream = _stream(record)
    foreign = _record("same-id", minute=1, world_ref=world_ref, agent_id=agent_id, scope=scope)

    with pytest.raises(ValidationError, match="outside"):
        MemoryStream(
            world_ref=WORLD_REF,
            agent_id=AGENT_ID,
            scope=MEMORY_SCOPE,
            records=(record, foreign),
        )
    with pytest.raises(MemoryScopeError, match="outside"):
        stream.append(foreign)

    assert stream.records == (record,)


@pytest.mark.parametrize(("world_ref", "agent_id", "scope"), FOREIGN_OWNERS)
@pytest.mark.parametrize("memory_id", ("same-id", "unknown-id"))
def test_touch_rejects_foreign_owner_before_id_checks_without_partial_updates(
    world_ref: WorldRef, agent_id: str, scope: str, memory_id: str
) -> None:
    record = _record("same-id", minute=1)
    stream = _stream(record)
    accessed_at = BASE_TIME + timedelta(minutes=10)
    foreign = _touch(memory_id, accessed_at, world_ref=world_ref, agent_id=agent_id, scope=scope)

    with pytest.raises(MemoryScopeError, match="outside"):
        stream.touch((_touch("same-id", accessed_at), foreign))

    assert stream.records == (record,)
    assert stream.records[0].last_accessed_at == record.last_accessed_at


@pytest.mark.parametrize(("world_ref", "agent_id", "scope"), FOREIGN_OWNERS)
def test_retrieval_matches_full_record_and_touch_owner(
    world_ref: WorldRef, agent_id: str, scope: str
) -> None:
    record = _record("same-id", minute=1)
    foreign = _touch(
        "same-id",
        BASE_TIME + timedelta(minutes=10),
        world_ref=world_ref,
        agent_id=agent_id,
        scope=scope,
    )

    with pytest.raises(ValidationError, match="owners and IDs"):
        MemoryRetrieval(records=(record,), touches=(foreign,))


def test_memory_identity_round_trip_and_rebuilding_preserve_owner() -> None:
    record = _record("memory-1", minute=1, tags=("coffee",))
    stream = _stream(record)
    restored = MemoryStream.model_validate_json(stream.model_dump_json(), strict=True)
    assert restored == stream
    assert restored.model_dump(mode="json")["worldRef"] == {
        "projectId": "coffee-golden",
        "worldId": "world-1",
    }

    related = retrieve_related(
        restored, keywords=("coffee",), accessed_at=BASE_TIME + timedelta(minutes=10)
    )
    ranked = retrieve_ranked(
        restored,
        focal_point="coffee queue",
        embedding_provider=FixedEmbeddingProvider((1.0, 0.0)),
        accessed_at=BASE_TIME + timedelta(minutes=10),
    )
    assert related == ranked
    assert MemoryRetrieval.model_validate_json(related.model_dump_json(), strict=True) == related
    touched = restored.touch(related.touches)
    assert touched.world_ref == touched.records[0].world_ref == WORLD_REF
    assert touched.agent_id == touched.records[0].agent_id == AGENT_ID
    assert touched.scope == touched.records[0].scope == MEMORY_SCOPE
    assert related.touches[0].world_ref == WORLD_REF
    assert related.touches[0].agent_id == AGENT_ID
    assert related.touches[0].scope == MEMORY_SCOPE
    assert touched.records[0].last_accessed_at > record.last_accessed_at
    assert restored.records[0].last_accessed_at == record.last_accessed_at
    with pytest.raises(ValidationError, match="frozen"):
        restored.world_ref = WorldRef(project_id="coffee-golden", world_id="world-2")


def test_independent_worlds_can_reuse_memory_ids_without_cross_touch() -> None:
    other_world = WorldRef(project_id="coffee-golden", world_id="world-2")
    first = _stream(_record("same-id", minute=1, tags=("coffee",)))
    second = _stream(
        _record("same-id", minute=1, world_ref=other_world, tags=("coffee",)),
        world_ref=other_world,
    )
    result = retrieve_related(
        second, keywords=("coffee",), accessed_at=BASE_TIME + timedelta(minutes=10)
    )
    touched = second.touch(result.touches)

    assert touched.world_ref == other_world
    assert touched.records[0].id == first.records[0].id
    assert touched.records[0].last_accessed_at > first.records[0].last_accessed_at
    with pytest.raises(MemoryScopeError, match="outside"):
        first.touch(result.touches)


def test_memory_contracts_require_explicit_identity() -> None:
    record = _record("memory-1", minute=1)
    record_data = record.model_dump()
    record_data.pop("worldRef")
    with pytest.raises(ValidationError, match="worldRef"):
        MemoryRecord.model_validate(record_data, strict=True)

    with pytest.raises(ValidationError, match="worldRef"):
        MemoryStream.model_validate({"agentId": AGENT_ID, "scope": MEMORY_SCOPE}, strict=True)

    touch = _touch("memory-1", BASE_TIME + timedelta(minutes=2))
    for field in ("worldRef", "agentId", "scope"):
        touch_data = touch.model_dump()
        touch_data.pop(field)
        with pytest.raises(ValidationError, match=field):
            MemoryTouch.model_validate(touch_data, strict=True)


def test_retrieval_matches_ids_and_order_in_addition_to_owner() -> None:
    first = _record("memory-1", minute=1)
    second = _record("memory-2", minute=2)
    accessed_at = BASE_TIME + timedelta(minutes=10)
    with pytest.raises(ValidationError, match="owners and IDs"):
        MemoryRetrieval(
            records=(first, second),
            touches=(_touch("memory-2", accessed_at), _touch("memory-1", accessed_at)),
        )
    with pytest.raises(ValidationError, match="owners and IDs"):
        MemoryRetrieval(records=(first,), touches=())


def test_owner_checks_preserve_existing_touch_failure_rules() -> None:
    record = _record("memory-1", minute=1)
    stream = _stream(record)
    touch = _touch("memory-1", BASE_TIME + timedelta(minutes=10))
    with pytest.raises(DuplicateMemoryError, match="unique"):
        stream.touch((touch, touch))
    with pytest.raises(MemoryTouchError, match="unknown"):
        stream.touch((_touch("unknown-id", touch.accessed_at),))
    assert stream.touch(()) == stream
    assert stream.records == (record,)


def _stream(*records: MemoryRecord, world_ref: WorldRef = WORLD_REF) -> MemoryStream:
    stream = MemoryStream(world_ref=world_ref, agent_id=AGENT_ID, scope=MEMORY_SCOPE)
    for record in records:
        stream = stream.append(record)
    return stream


def _record(
    memory_id: str,
    *,
    minute: int,
    world_ref: WorldRef = WORLD_REF,
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
        world_ref=world_ref,
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


def _touch(
    memory_id: str,
    accessed_at: datetime,
    *,
    world_ref: WorldRef = WORLD_REF,
    agent_id: str = AGENT_ID,
    scope: str = MEMORY_SCOPE,
) -> MemoryTouch:
    return MemoryTouch(
        memory_id=memory_id,
        accessed_at=accessed_at,
        world_ref=world_ref,
        agent_id=agent_id,
        scope=scope,
    )
