"""Add immutable Broadcast disposition and Render metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0003_broadcast_renders"
down_revision = "0002_generation_traces"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "broadcast_runs",
        sa.Column("run_id", sa.String(36), primary_key=True),
        sa.Column("world_id", sa.String(64), nullable=False),
        sa.Column(
            "target_world_version",
            sa.Integer(),
            sa.ForeignKey("world_versions.version"),
            nullable=False,
        ),
        sa.Column(
            "source_trace_id",
            sa.String(36),
            sa.ForeignKey("generation_traces.trace_id"),
            nullable=False,
        ),
        sa.Column("plan_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
    )
    op.create_index("ix_broadcast_runs_world_id", "broadcast_runs", ["world_id"])
    op.create_table(
        "renders",
        sa.Column("render_record_id", sa.String(36), primary_key=True),
        sa.Column("world_id", sa.String(64), nullable=False),
        sa.Column("render_id", sa.String(200), nullable=False),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("broadcast_runs.run_id"),
            nullable=False,
        ),
        sa.Column(
            "target_world_version",
            sa.Integer(),
            sa.ForeignKey("world_versions.version"),
            nullable=False,
        ),
        sa.Column("render_order", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("artifact_path", sa.Text(), nullable=False),
        sa.Column("scene_path", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.UniqueConstraint("world_id", "render_id"),
    )
    op.create_index("ix_renders_world_id", "renders", ["world_id"])
    op.create_table(
        "broadcast_dispositions",
        sa.Column(
            "event_id",
            sa.String(36),
            sa.ForeignKey("world_events.event_id"),
            primary_key=True,
        ),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("broadcast_runs.run_id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("render_ids_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.CheckConstraint("status IN ('included', 'omitted')"),
    )
    for table in ("broadcast_runs", "renders", "broadcast_dispositions"):
        op.execute(
            f"CREATE TRIGGER {table}_reject_update BEFORE UPDATE ON {table} "
            f"BEGIN SELECT RAISE(ABORT, 'RENDER_IMMUTABLE:{table}:UPDATE'); END"
        )
        op.execute(
            f"CREATE TRIGGER {table}_reject_delete BEFORE DELETE ON {table} "
            f"BEGIN SELECT RAISE(ABORT, 'RENDER_IMMUTABLE:{table}:DELETE'); END"
        )


def downgrade() -> None:
    for table in ("broadcast_dispositions", "renders", "broadcast_runs"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_reject_delete")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_reject_update")
    op.drop_table("broadcast_dispositions")
    op.drop_index("ix_renders_world_id", table_name="renders")
    op.drop_table("renders")
    op.drop_index("ix_broadcast_runs_world_id", table_name="broadcast_runs")
    op.drop_table("broadcast_runs")
