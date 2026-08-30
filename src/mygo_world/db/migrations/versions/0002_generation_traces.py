"""Add provider-neutral Generation Trace records."""

import sqlalchemy as sa
from alembic import op

revision = "0002_generation_traces"
down_revision = "0001_durable_world"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "generation_traces",
        sa.Column("trace_id", sa.String(36), primary_key=True),
        sa.Column("world_id", sa.String(64), nullable=False),
        sa.Column("input_world_version", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(200), nullable=False),
        sa.Column("agent_type", sa.String(32), nullable=False),
        sa.Column("agent_id", sa.String(200), nullable=False),
        sa.Column("call_kind", sa.String(64), nullable=False),
        sa.Column("skill_id", sa.String(200), nullable=False),
        sa.Column("skill_version", sa.String(100), nullable=False),
        sa.Column("skill_content_hash", sa.String(64), nullable=False),
        sa.Column("model_id", sa.String(200), nullable=False),
        sa.Column("model_config_json", sa.Text(), nullable=False),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.Column("raw_response", sa.Text(), nullable=False),
        sa.Column("structured_result_json", sa.Text(), nullable=False),
        sa.Column("validation_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
    )
    op.create_index("ix_generation_traces_world_id", "generation_traces", ["world_id"])


def downgrade() -> None:
    op.drop_index("ix_generation_traces_world_id", table_name="generation_traces")
    op.drop_table("generation_traces")
