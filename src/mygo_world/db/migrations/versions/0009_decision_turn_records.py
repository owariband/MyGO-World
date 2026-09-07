"""Add durable Decision Turn scheduling records."""

import sqlalchemy as sa
from alembic import op

revision = "0009_decision_turns"
down_revision = "0008_simplify_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "decision_turn_records",
        sa.Column("decision_id", sa.String(36), primary_key=True),
        sa.Column("turn_order", sa.Integer(), nullable=False),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("generation_batches.run_id"),
            nullable=False,
        ),
        sa.Column(
            "wave_id",
            sa.String(36),
            sa.ForeignKey("generation_waves.wave_id"),
            nullable=False,
        ),
        sa.Column("wave_number", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(200), nullable=False),
        sa.Column(
            "base_world_version",
            sa.Integer(),
            sa.ForeignKey("world_versions.version"),
            nullable=False,
        ),
        sa.Column("selected_actor_id", sa.String(200), nullable=False),
        sa.Column("selection_source", sa.String(32), nullable=False),
        sa.Column("candidate_ids_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "resulting_world_version",
            sa.Integer(),
            sa.ForeignKey("world_versions.version"),
        ),
        sa.Column("error_code", sa.String(100)),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.UniqueConstraint("wave_id"),
        sa.UniqueConstraint("turn_order"),
        sa.CheckConstraint(
            "turn_order > 0", name="ck_decision_turn_order_positive"
        ),
        sa.CheckConstraint(
            "selection_source IN ('nominated', 'director', 'round_robin')",
            name="ck_decision_turn_selection_source",
        ),
        sa.CheckConstraint(
            "status IN ('selected', 'no_op', 'committed', 'failed')",
            name="ck_decision_turn_status",
        ),
    )
    op.create_index(
        "ix_decision_turn_records_run_id", "decision_turn_records", ["run_id"]
    )
    op.create_index(
        "ix_decision_turn_records_session_id",
        "decision_turn_records",
        ["session_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_decision_turn_records_session_id", table_name="decision_turn_records"
    )
    op.drop_index(
        "ix_decision_turn_records_run_id", table_name="decision_turn_records"
    )
    op.drop_table("decision_turn_records")
