"""Deterministic, side-effect-free Persona memory retrieval."""

from __future__ import annotations

import math
from datetime import datetime

from agent_runtime.agent.memory.contracts import (
    EmbeddingProvider,
    MemoryKind,
    MemoryRecord,
    MemoryRetrieval,
    MemoryTouch,
)
from agent_runtime.agent.memory.stream import MemoryStream

DEFAULT_RANKED_KINDS = (MemoryKind.EVENT, MemoryKind.THOUGHT)


def retrieve_related(
    stream: MemoryStream,
    *,
    keywords: tuple[str, ...],
    accessed_at: datetime,
    limit: int | None = None,
    kinds: tuple[MemoryKind, ...] | None = None,
) -> MemoryRetrieval:
    """OR-match case-folded tags and SPO terms in newest-first order."""

    _require_timezone(accessed_at)
    if limit is not None:
        _require_non_negative("limit", limit)
        if limit == 0:
            return MemoryRetrieval()
    normalized_keywords = _normalize_keywords(keywords)
    if not normalized_keywords:
        return MemoryRetrieval()

    allowed_kinds = frozenset(kinds) if kinds is not None else None
    selected: list[MemoryRecord] = []
    for record in reversed(stream.records):
        if not _is_eligible(record, accessed_at, allowed_kinds):
            continue
        record_keywords = frozenset(
            term.casefold()
            for term in (record.subject, record.predicate, record.object, *record.tags)
            if term is not None
        )
        if normalized_keywords.isdisjoint(record_keywords):
            continue
        selected.append(record)
        if limit is not None and len(selected) == limit:
            break

    records = tuple(selected)
    return MemoryRetrieval(
        records=records,
        touches=_touches(records, accessed_at),
    )


def retrieve_ranked(
    stream: MemoryStream,
    *,
    focal_point: str,
    embedding_provider: EmbeddingProvider,
    accessed_at: datetime,
    limit: int = 30,
    recency_decay: float = 0.99,
    recency_weight: float = 0.5,
    relevance_weight: float = 3.0,
    importance_weight: float = 2.0,
    kinds: tuple[MemoryKind, ...] = DEFAULT_RANKED_KINDS,
) -> MemoryRetrieval:
    """Rank memories by normalized recency, cosine relevance, and importance."""

    _require_timezone(accessed_at)
    _require_non_negative("limit", limit)
    _require_unit_interval("recency_decay", recency_decay)
    _require_weight("recency_weight", recency_weight)
    _require_weight("relevance_weight", relevance_weight)
    _require_weight("importance_weight", importance_weight)
    if limit == 0:
        return MemoryRetrieval()

    allowed_kinds = frozenset(kinds)
    candidates = [
        record
        for record in reversed(stream.records)
        if _is_eligible(record, accessed_at, allowed_kinds)
    ]
    if not candidates:
        return MemoryRetrieval()

    # reversed(stream.records) gives the stable newest-append tie break. Python's
    # stable sort retains it when access timestamps are equal.
    candidates.sort(key=lambda record: record.last_accessed_at, reverse=True)
    focal_embedding = embedding_provider.embed(focal_point)
    _validate_embedding(focal_embedding, "focal embedding")

    recency = _normalize(tuple(recency_decay ** (index + 1) for index in range(len(candidates))))
    relevance = _normalize(
        tuple(_cosine_similarity(record.embedding, focal_embedding) for record in candidates)
    )
    importance = _normalize(tuple(record.poignancy for record in candidates))

    scored = [
        (
            record,
            recency_weight * recency[index]
            + relevance_weight * relevance[index]
            + importance_weight * importance[index],
        )
        for index, record in enumerate(candidates)
    ]
    scored.sort(key=lambda item: item[1], reverse=True)
    records = tuple(record for record, _ in scored[:limit])
    return MemoryRetrieval(
        records=records,
        touches=_touches(records, accessed_at),
    )


def _is_eligible(
    record: MemoryRecord,
    accessed_at: datetime,
    allowed_kinds: frozenset[MemoryKind] | None,
) -> bool:
    return (
        record.created_at <= accessed_at
        and record.last_accessed_at <= accessed_at
        and (allowed_kinds is None or record.kind in allowed_kinds)
        and (record.expires_at is None or record.expires_at > accessed_at)
    )


def _normalize_keywords(keywords: tuple[str, ...]) -> frozenset[str]:
    normalized: set[str] = set()
    for keyword in keywords:
        stripped = keyword.strip()
        if not stripped:
            raise ValueError("retrieval keywords cannot be empty")
        normalized.add(stripped.casefold())
    return frozenset(normalized)


def _normalize(values: tuple[float, ...]) -> tuple[float, ...]:
    if not values:
        return ()
    minimum = min(values)
    maximum = max(values)
    if maximum == minimum:
        return tuple(0.5 for _ in values)
    value_range = maximum - minimum
    return tuple((value - minimum) / value_range for value in values)


def _cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    _validate_embedding(left, "memory embedding")
    if not left or not right:
        return 0.0
    if len(left) != len(right):
        raise ValueError("memory and focal embeddings must have equal dimensions")

    left_norm = math.sqrt(sum(component * component for component in left))
    right_norm = math.sqrt(sum(component * component for component in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    dot_product = sum(
        left_component * right_component
        for left_component, right_component in zip(left, right, strict=True)
    )
    return dot_product / (left_norm * right_norm)


def _touches(
    records: tuple[MemoryRecord, ...],
    accessed_at: datetime,
) -> tuple[MemoryTouch, ...]:
    return tuple(MemoryTouch(memory_id=record.id, accessed_at=accessed_at) for record in records)


def _validate_embedding(embedding: tuple[float, ...], label: str) -> None:
    if not all(math.isfinite(component) for component in embedding):
        raise ValueError(f"{label} values must be finite")


def _require_timezone(value: datetime) -> None:
    if value.utcoffset() is None:
        raise ValueError("retrieval timestamp must include a timezone")


def _require_non_negative(label: str, value: int) -> None:
    if value < 0:
        raise ValueError(f"{label} cannot be negative")


def _require_unit_interval(label: str, value: float) -> None:
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{label} must be finite and between 0 and 1")


def _require_weight(label: str, value: float) -> None:
    if not math.isfinite(value) or value < 0.0:
        raise ValueError(f"{label} must be finite and non-negative")
