"""Add Event Session lineage and pending-response state."""

import sqlalchemy as sa
from alembic import op

revision = "0006_session_lineages"
down_revision = "0005_skills_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
        "event_session_pending_responses",
        sa.Column(
            "session_id",
            sa.String(200),
            sa.ForeignKey("event_sessions.session_id"),
            primary_key=True,
        ),
        sa.Column("responder_id", sa.String(200), primary_key=True),
    )
    op.execute(
        "CREATE TRIGGER event_sessions_validate_insert "
        "BEFORE INSERT ON event_sessions "
        "WHEN NEW.status <> 'runnable' OR NEW.closure_reason IS NOT NULL "
        "OR NEW.closed_world_version IS NOT NULL "
        "BEGIN SELECT RAISE(ABORT, 'EVENT_SESSION_INVALID_INITIAL_STATE'); END"
    )
    op.execute(
        "CREATE TRIGGER event_sessions_validate_update "
        "BEFORE UPDATE ON event_sessions "
        "WHEN NEW.location_id <> OLD.location_id OR NEW.scope_key <> OLD.scope_key "
        "OR NEW.created_world_version <> OLD.created_world_version "
        "OR (OLD.status = 'closed' AND NEW.status <> 'closed') "
        "OR (NEW.status NOT IN ('runnable', 'closed')) "
        "OR (NEW.status = 'closed' AND (NEW.closed_world_version IS NULL "
        "OR NEW.closure_reason IS NULL OR NEW.closure_reason NOT IN "
        "('partitioned', 'resolved', 'limit_reached'))) "
        "OR (NEW.status = 'runnable' AND "
        "(NEW.closure_reason IS NOT NULL OR NEW.closed_world_version IS NOT NULL)) "
        "BEGIN SELECT RAISE(ABORT, 'EVENT_SESSION_INVALID_TRANSITION'); END"
    )
    op.execute(
        "CREATE TRIGGER event_session_members_reject_late_insert "
        "BEFORE INSERT ON event_session_members "
        "WHEN EXISTS (SELECT 1 FROM runnable_session_queue "
        "WHERE session_id = NEW.session_id) "
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


def downgrade() -> None:
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
    op.drop_table("event_session_pending_responses")
    op.drop_table("event_session_parents")
