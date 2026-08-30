from __future__ import annotations

import json
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from mygo_world.db.models import AgentMemoryRow


class AgentMemoryRepository:
    """Owner-scoped reads for private, append-only Agent Memory."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def namespaces(self, *, agent_id: str) -> list[str]:
        return list(
            self._session.scalars(
                select(AgentMemoryRow.namespace)
                .where(AgentMemoryRow.agent_id == agent_id)
                .distinct()
                .order_by(AgentMemoryRow.namespace)
            )
        )

    def retrieve(
        self,
        *,
        agent_id: str,
        namespace: str = "default",
        entity_tags: Iterable[str] = (),
        location_tags: Iterable[str] = (),
        limit: int = 20,
    ) -> list[AgentMemoryRow]:
        if limit < 1:
            raise ValueError("Memory retrieval limit must be positive")
        # The ownership predicate is intentionally applied in SQL and again by the
        # PerceptionProjector. Callers cannot request an unscoped Memory collection.
        rows = list(
            self._session.scalars(
                select(AgentMemoryRow).where(
                    AgentMemoryRow.agent_id == agent_id,
                    AgentMemoryRow.namespace == namespace,
                )
            )
        )
        entity_set = set(entity_tags)
        location_set = set(location_tags)
        superseded = {
            row.supersedes_memory_id
            for row in rows
            if row.supersedes_memory_id is not None
        }
        active_commitments = [
            row
            for row in rows
            if row.memory_type == "commitment"
            and row.status == "active"
            and row.memory_id not in superseded
        ]

        def rank(row: AgentMemoryRow) -> tuple[int, int, int, str]:
            row_entities = set(json.loads(row.entity_tags_json))
            row_locations = set(json.loads(row.location_tags_json))
            relevance = len(entity_set & row_entities) + len(
                location_set & row_locations
            )
            return (-relevance, -row.relative_time_ms, -row.importance, row.memory_id)

        active_commitments.sort(key=rank)
        active_ids = {row.memory_id for row in active_commitments}
        others = sorted(
            (row for row in rows if row.memory_id not in active_ids), key=rank
        )
        remaining = max(0, limit - len(active_commitments))
        return [*active_commitments, *others[:remaining]]
