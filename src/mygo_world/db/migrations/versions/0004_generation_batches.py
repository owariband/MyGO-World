"""Add durable Generation Batch and Wave bookkeeping."""

import sqlalchemy as sa
from alembic import op

revision = "0004_generation_batches"
down_revision = "0003_broadcast_renders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "generation_batches",
        sa.Column("run_id", sa.String(36), primary_key=True),
        sa.Column("world_id", sa.String(64), nullable=False),
        sa.Column("session_id", sa.String(200)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("start_world_version", sa.Integer(), nullable=False),
        sa.Column("end_world_version", sa.Integer(), nullable=False),
        sa.Column("wave_count", sa.Integer(), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("warnings_json", sa.Text(), nullable=False),
        sa.Column("error_code", sa.String(100)),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
    )
    op.create_index(
        "ix_generation_batches_world_id", "generation_batches", ["world_id"]
    )
    op.create_table(
        "generation_waves",
        sa.Column("wave_id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("generation_batches.run_id"),
            nullable=False,
        ),
        sa.Column("wave_number", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(200), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("start_world_version", sa.Integer(), nullable=False),
        sa.Column("end_world_version", sa.Integer(), nullable=False),
        sa.Column("world_time_ms", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(100)),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.UniqueConstraint("run_id", "wave_number"),
    )
    op.create_index("ix_generation_waves_run_id", "generation_waves", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_generation_waves_run_id", table_name="generation_waves")
    op.drop_table("generation_waves")
    op.drop_index("ix_generation_batches_world_id", table_name="generation_batches")
    op.drop_table("generation_batches")
