"""Store serial scheduling order on Event Sessions and remove projections."""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "0008_simplify_sessions"
down_revision = "0007_recoverable_rendering"
branch_labels = None
depends_on = None

_MIGRATION_ERROR = "SESSION_PERSISTENCE_MIGRATION_FAILED"


def _fail(message: str) -> None:
    raise RuntimeError(f"{_MIGRATION_ERROR}: {message}")


def _ledger_parent_edges(connection: Any) -> set[tuple[str, str]]:
    edges: set[tuple[str, str]] = set()
    rows = connection.execute(
        sa.text(
            "SELECT segment.segment_id, segment.payload_json "
            "FROM world_segments segment "
            "JOIN world_versions version ON version.segment_id = segment.segment_id"
        )
    )
    for segment_id, payload_json in rows:
        try:
            payload = json.loads(payload_json)
        except (TypeError, json.JSONDecodeError) as exc:
            _fail(f"World Segment '{segment_id}' has invalid JSON: {exc}")
        successors = payload.get("successor_sessions", [])
        if not isinstance(successors, list):
            _fail(f"World Segment '{segment_id}' has invalid successor_sessions")
        for successor in successors:
            if not isinstance(successor, dict):
                _fail(f"World Segment '{segment_id}' has an invalid successor")
            session_id = successor.get("session_id")
            parents = successor.get("parent_session_ids", [])
            if not isinstance(session_id, str) or not isinstance(parents, list):
                _fail(f"World Segment '{segment_id}' has invalid lineage data")
            for parent_session_id in parents:
                if not isinstance(parent_session_id, str):
                    _fail(f"World Segment '{segment_id}' has invalid parent IDs")
                edges.add((session_id, parent_session_id))
    return edges


def _session_rows(connection: Any) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            sa.text(
                "SELECT session_id, status, created_world_version, "
                "closed_world_version FROM event_sessions"
            )
        ).mappings()
    ]


def _queue_rows(connection: Any) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            sa.text(
                "SELECT queue_order, session_id, enqueued_world_version, "
                "dequeued_world_version FROM runnable_session_queue"
            )
        ).mappings()
    ]


def _validate_legacy_projections(connection: Any) -> list[dict[str, Any]]:
    sessions = _session_rows(connection)
    queue = _queue_rows(connection)
    queue_by_session = {row["session_id"]: row for row in queue}
    if len(queue) != len(sessions) or len(queue_by_session) != len(sessions):
        _fail("every Event Session must have exactly one Queue row")

    orders = [row["queue_order"] for row in queue]
    if any(not isinstance(order, int) or order <= 0 for order in orders):
        _fail("queue_order must be positive")
    if len(set(orders)) != len(orders):
        _fail("queue_order must be unique")

    for session in sessions:
        queue_row = queue_by_session.get(session["session_id"])
        if queue_row is None:
            _fail(f"Event Session '{session['session_id']}' has no Queue row")
        if queue_row["enqueued_world_version"] != session["created_world_version"]:
            _fail(
                f"Event Session '{session['session_id']}' enqueue version does not "
                "match its creation version"
            )
        expected_dequeue = (
            None if session["status"] == "runnable" else session["closed_world_version"]
        )
        if session["status"] not in {"runnable", "closed"}:
            _fail(f"Event Session '{session['session_id']}' has invalid status")
        if (
            session["status"] == "runnable"
            and session["closed_world_version"] is not None
        ):
            _fail(
                f"runnable Event Session '{session['session_id']}' has a close version"
            )
        if session["status"] == "closed" and expected_dequeue is None:
            _fail(
                f"closed Event Session '{session['session_id']}' has no close version"
            )
        if queue_row["dequeued_world_version"] != expected_dequeue:
            _fail(
                f"Event Session '{session['session_id']}' dequeue version does not "
                "match its lifecycle"
            )

    persisted_edges = {
        tuple(row)
        for row in connection.execute(
            sa.text("SELECT session_id, parent_session_id FROM event_session_parents")
        )
    }
    ledger_edges = _ledger_parent_edges(connection)
    if persisted_edges != ledger_edges:
        missing = sorted(persisted_edges - ledger_edges)
        extra = sorted(ledger_edges - persisted_edges)
        _fail(
            "parent projection differs from committed Ledger lineage "
            f"(not recoverable={missing}, not projected={extra})"
        )
    return queue


def _drop_legacy_triggers() -> None:
    for trigger in (
        "event_session_parents_reject_delete",
        "event_session_parents_reject_update",
        "event_session_parents_reject_late_insert",
        "event_session_members_reject_delete",
        "event_session_members_reject_update",
        "event_session_members_reject_late_insert",
        "event_sessions_validate_update",
        "event_sessions_validate_insert",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger}")


def _create_session_state_triggers(*, validate_queue_order: bool) -> None:
    insert_queue_guard = (
        "NEW.queue_order IS NULL OR NEW.queue_order <= 0 OR "
        if validate_queue_order
        else ""
    )
    update_queue_guard = (
        "NEW.queue_order IS NULL OR NEW.queue_order <= 0 "
        "OR NEW.queue_order <> OLD.queue_order OR "
        if validate_queue_order
        else ""
    )
    op.execute(
        "CREATE TRIGGER event_sessions_validate_insert "
        "BEFORE INSERT ON event_sessions "
        f"WHEN {insert_queue_guard}NEW.status <> 'runnable' "
        "OR NEW.closure_reason IS NOT NULL "
        "OR NEW.closed_world_version IS NOT NULL "
        "BEGIN SELECT RAISE(ABORT, 'EVENT_SESSION_INVALID_INITIAL_STATE'); END"
    )
    op.execute(
        "CREATE TRIGGER event_sessions_validate_update "
        "BEFORE UPDATE ON event_sessions "
        "WHEN NEW.location_id <> OLD.location_id OR NEW.scope_key <> OLD.scope_key "
        "OR NEW.created_world_version <> OLD.created_world_version "
        f"OR {update_queue_guard}(OLD.status = 'closed' AND NEW.status <> 'closed') "
        "OR (NEW.status NOT IN ('runnable', 'closed')) "
        "OR (NEW.status = 'closed' AND (NEW.closed_world_version IS NULL "
        "OR NEW.closure_reason IS NULL OR NEW.closure_reason NOT IN "
        "('partitioned', 'resolved', 'limit_reached'))) "
        "OR (NEW.status = 'runnable' AND "
        "(NEW.closure_reason IS NOT NULL OR NEW.closed_world_version IS NOT NULL)) "
        "BEGIN SELECT RAISE(ABORT, 'EVENT_SESSION_INVALID_TRANSITION'); END"
    )


def _create_member_immutability_triggers(*, late_insert_when: str) -> None:
    op.execute(
        "CREATE TRIGGER event_session_members_reject_late_insert "
        "BEFORE INSERT ON event_session_members "
        f"WHEN {late_insert_when} "
        "BEGIN SELECT RAISE(ABORT, 'EVENT_SESSION_MEMBERS_IMMUTABLE'); END"
    )
    op.execute(
        "CREATE TRIGGER event_session_members_reject_update "
        "BEFORE UPDATE ON event_session_members "
        "BEGIN SELECT RAISE(ABORT, 'EVENT_SESSION_MEMBERS_IMMUTABLE'); END"
    )
    op.execute(
        "CREATE TRIGGER event_session_members_reject_delete "
        "BEFORE DELETE ON event_session_members "
        "BEGIN SELECT RAISE(ABORT, 'EVENT_SESSION_MEMBERS_IMMUTABLE'); END"
    )


def _create_current_triggers() -> None:
    _create_session_state_triggers(validate_queue_order=True)
    _create_member_immutability_triggers(
        late_insert_when=(
            "EXISTS (SELECT 1 FROM event_sessions event_session "
            "JOIN worlds world "
            "ON world.current_version >= event_session.created_world_version "
            "WHERE event_session.session_id = NEW.session_id)"
        )
    )


def _create_legacy_triggers() -> None:
    _create_session_state_triggers(validate_queue_order=False)
    _create_member_immutability_triggers(
        late_insert_when=(
            "EXISTS (SELECT 1 FROM runnable_session_queue "
            "WHERE session_id = NEW.session_id)"
        )
    )
    op.execute(
        "CREATE TRIGGER event_session_parents_reject_late_insert "
        "BEFORE INSERT ON event_session_parents "
        "WHEN EXISTS (SELECT 1 FROM runnable_session_queue "
        "WHERE session_id = NEW.session_id) "
        "BEGIN SELECT RAISE(ABORT, 'EVENT_SESSION_PARENTS_IMMUTABLE'); END"
    )
    op.execute(
        "CREATE TRIGGER event_session_parents_reject_update "
        "BEFORE UPDATE ON event_session_parents "
        "BEGIN SELECT RAISE(ABORT, 'EVENT_SESSION_PARENTS_IMMUTABLE'); END"
    )
    op.execute(
        "CREATE TRIGGER event_session_parents_reject_delete "
        "BEFORE DELETE ON event_session_parents "
        "BEGIN SELECT RAISE(ABORT, 'EVENT_SESSION_PARENTS_IMMUTABLE'); END"
    )


def upgrade() -> None:
    connection = op.get_bind()
    queue = _validate_legacy_projections(connection)

    op.add_column(
        "event_sessions",
        sa.Column(
            "queue_order",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    for row in queue:
        connection.execute(
            sa.text(
                "UPDATE event_sessions SET queue_order=:queue_order "
                "WHERE session_id=:session_id"
            ),
            row,
        )

    _drop_legacy_triggers()
    op.create_index(
        "uq_event_sessions_queue_order",
        "event_sessions",
        ["queue_order"],
        unique=True,
    )
    op.drop_table("event_session_parents")
    op.drop_table("runnable_session_queue")
    _create_current_triggers()


def downgrade() -> None:
    connection = op.get_bind()
    sessions = list(
        connection.execute(
            sa.text(
                "SELECT session_id, queue_order, created_world_version, "
                "closed_world_version FROM event_sessions ORDER BY queue_order"
            )
        ).mappings()
    )
    if any(row["queue_order"] is None or row["queue_order"] <= 0 for row in sessions):
        _fail("cannot downgrade Event Sessions with invalid queue_order")

    _drop_legacy_triggers()
    op.create_table(
        "event_session_parents",
        sa.Column(
            "session_id",
            sa.String(200),
            sa.ForeignKey("event_sessions.session_id"),
            primary_key=True,
        ),
        sa.Column(
            "parent_session_id",
            sa.String(200),
            sa.ForeignKey("event_sessions.session_id"),
            primary_key=True,
        ),
        sa.CheckConstraint(
            "session_id <> parent_session_id", name="ck_session_parent_not_self"
        ),
    )
    op.create_table(
        "runnable_session_queue",
        sa.Column("queue_order", sa.Integer(), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(200),
            sa.ForeignKey("event_sessions.session_id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "enqueued_world_version",
            sa.Integer(),
            sa.ForeignKey("world_versions.version"),
            nullable=False,
        ),
        sa.Column(
            "dequeued_world_version",
            sa.Integer(),
            sa.ForeignKey("world_versions.version"),
        ),
    )
    for row in sessions:
        connection.execute(
            sa.text(
                "INSERT INTO runnable_session_queue "
                "(queue_order, session_id, enqueued_world_version, "
                "dequeued_world_version) VALUES (:queue_order, :session_id, "
                ":created_world_version, :closed_world_version)"
            ),
            row,
        )
    for session_id, parent_session_id in sorted(_ledger_parent_edges(connection)):
        connection.execute(
            sa.text(
                "INSERT INTO event_session_parents "
                "(session_id, parent_session_id) VALUES (:session_id, :parent_id)"
            ),
            {"session_id": session_id, "parent_id": parent_session_id},
        )

    op.drop_index("uq_event_sessions_queue_order", table_name="event_sessions")
    op.drop_column("event_sessions", "queue_order")
    _create_legacy_triggers()
