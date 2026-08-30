from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from mygo_world.db.models import (
    EntityRevisionRow,
    WorldEventRow,
    WorldSegmentRow,
)


class LedgerRepository:
    """Append/read-only access to the authoritative World Ledger."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def append_segment(self, row: WorldSegmentRow) -> None:
        self._session.add(row)

    def append_entity_revision(self, row: EntityRevisionRow) -> None:
        self._session.add(row)

    def append_world_event(self, row: WorldEventRow) -> None:
        self._session.add(row)

    def list_segments(self) -> Sequence[WorldSegmentRow]:
        return self._session.scalars(
            select(WorldSegmentRow).order_by(WorldSegmentRow.segment_order)
        ).all()

    def list_entity_revisions(self) -> Sequence[EntityRevisionRow]:
        return self._session.scalars(
            select(EntityRevisionRow).order_by(
                EntityRevisionRow.entity_id,
                EntityRevisionRow.revision_order,
            )
        ).all()

    def list_world_events(self) -> Sequence[WorldEventRow]:
        return self._session.scalars(
            select(WorldEventRow).order_by(WorldEventRow.event_order)
        ).all()
