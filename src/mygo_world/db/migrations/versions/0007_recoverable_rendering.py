"""Strengthen incremental Broadcast and Render persistence invariants."""

from alembic import op

revision = "0007_recoverable_rendering"
down_revision = "0006_session_lineages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_renders_run_order",
        "renders",
        ["run_id", "render_order"],
        unique=True,
    )
    op.execute(
        "CREATE TRIGGER renders_validate_insert BEFORE INSERT ON renders "
        "WHEN NEW.render_order < 1 OR NOT EXISTS ("
        "SELECT 1 FROM broadcast_runs run WHERE run.run_id = NEW.run_id "
        "AND run.world_id = NEW.world_id "
        "AND run.target_world_version = NEW.target_world_version) "
        "BEGIN SELECT RAISE(ABORT, 'RENDER_INVALID_METADATA'); END"
    )
    op.execute(
        "CREATE TRIGGER broadcast_dispositions_validate_insert "
        "BEFORE INSERT ON broadcast_dispositions "
        "WHEN (NEW.status = 'included' AND (NEW.reason IS NOT NULL "
        "OR NOT json_valid(NEW.render_ids_json) "
        "OR json_type(NEW.render_ids_json) <> 'array' "
        "OR json_array_length(NEW.render_ids_json) < 1)) "
        "OR (NEW.status = 'omitted' AND (NEW.reason IS NULL "
        "OR length(trim(NEW.reason)) = 0 "
        "OR NOT json_valid(NEW.render_ids_json) "
        "OR json_type(NEW.render_ids_json) <> 'array' "
        "OR json_array_length(NEW.render_ids_json) <> 0)) "
        "BEGIN SELECT RAISE(ABORT, 'BROADCAST_DISPOSITION_INVALID'); END"
    )
    op.execute(
        "CREATE TRIGGER broadcast_dispositions_validate_target "
        "BEFORE INSERT ON broadcast_dispositions "
        "WHEN NOT EXISTS ("
        "SELECT 1 FROM world_events event "
        "JOIN broadcast_runs run ON run.run_id = NEW.run_id "
        "WHERE event.event_id = NEW.event_id "
        "AND event.world_version <= run.target_world_version) "
        "BEGIN SELECT RAISE(ABORT, 'BROADCAST_EVENT_OUTSIDE_TARGET'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS broadcast_dispositions_validate_target")
    op.execute("DROP TRIGGER IF EXISTS broadcast_dispositions_validate_insert")
    op.execute("DROP TRIGGER IF EXISTS renders_validate_insert")
    op.drop_index("uq_renders_run_order", table_name="renders")
