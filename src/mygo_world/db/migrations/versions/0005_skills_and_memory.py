"""Persist immutable Runtime Skill bindings and typed Agent Memory metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0005_skills_memory"
down_revision = "0004_generation_batches"
branch_labels = None
depends_on = None


def _immutable(table: str, prefix: str) -> None:
    op.execute(
        f"CREATE TRIGGER {table}_reject_update BEFORE UPDATE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, '{prefix}:{table}:UPDATE'); END"
    )
    op.execute(
        f"CREATE TRIGGER {table}_reject_delete BEFORE DELETE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, '{prefix}:{table}:DELETE'); END"
    )


def upgrade() -> None:
    with op.batch_alter_table("agent_memory_records") as batch:
        batch.add_column(
            sa.Column("source", sa.String(200), nullable=False, server_default="legacy")
        )
        batch.add_column(sa.Column("status", sa.String(32)))
        batch.add_column(sa.Column("supersedes_memory_id", sa.String(200)))
        batch.add_column(
            sa.Column(
                "entity_tags_json", sa.Text(), nullable=False, server_default="[]"
            )
        )
        batch.add_column(
            sa.Column(
                "location_tags_json", sa.Text(), nullable=False, server_default="[]"
            )
        )
        batch.create_foreign_key(
            "fk_memory_supersedes",
            "agent_memory_records",
            ["supersedes_memory_id"],
            ["memory_id"],
        )
        batch.create_unique_constraint("uq_memory_supersedes", ["supersedes_memory_id"])
        batch.create_check_constraint(
            "ck_memory_type",
            "memory_type IN ('observation', 'belief', 'commitment', 'reflection')",
        )
        batch.create_check_constraint(
            "ck_memory_status",
            "status IS NULL OR status IN ('active', 'completed', 'cancelled')",
        )
        batch.create_check_constraint(
            "ck_memory_status_type",
            "(memory_type = 'commitment') OR status IS NULL",
        )
    _immutable("agent_memory_records", "MEMORY_IMMUTABLE")
    op.execute(
        "CREATE TRIGGER agent_memory_records_validate_supersedes "
        "BEFORE INSERT ON agent_memory_records "
        "WHEN NEW.supersedes_memory_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM agent_memory_records previous WHERE "
        "previous.memory_id = NEW.supersedes_memory_id AND "
        "previous.agent_id = NEW.agent_id AND "
        "previous.namespace = NEW.namespace AND "
        "previous.memory_type = NEW.memory_type) "
        "BEGIN SELECT RAISE(ABORT, 'MEMORY_SUPERSEDES_SCOPE_MISMATCH'); END"
    )
    op.execute(
        "CREATE TRIGGER agent_memory_records_validate_terminal_commitment "
        "BEFORE INSERT ON agent_memory_records "
        "WHEN NEW.memory_type = 'commitment' AND "
        "NEW.status IN ('completed', 'cancelled') AND "
        "NEW.supersedes_memory_id IS NULL "
        "BEGIN SELECT RAISE(ABORT, 'COMMITMENT_PREDECESSOR_REQUIRED'); END"
    )

    op.create_table(
        "skill_bindings",
        sa.Column("binding_id", sa.String(200), primary_key=True),
        sa.Column("binding_order", sa.Integer(), nullable=False),
        sa.Column(
            "world_id", sa.String(64), sa.ForeignKey("worlds.world_id"), nullable=False
        ),
        sa.Column("agent_kind", sa.String(32), nullable=False),
        sa.Column("agent_id", sa.String(200), nullable=False),
        sa.Column("previous_skill_id", sa.String(200)),
        sa.Column("previous_skill_version", sa.String(100)),
        sa.Column("previous_skill_content_hash", sa.String(64)),
        sa.Column("skill_id", sa.String(200), nullable=False),
        sa.Column("skill_version", sa.String(100), nullable=False),
        sa.Column("skill_content_hash", sa.String(64), nullable=False),
        sa.Column("operator", sa.String(200), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("bound_at", sa.String(40), nullable=False),
        sa.UniqueConstraint("world_id", "binding_order"),
        sa.CheckConstraint("agent_kind IN ('character', 'director', 'broadcast')"),
    )
    op.create_index("ix_skill_bindings_world_id", "skill_bindings", ["world_id"])
    _immutable("skill_bindings", "SKILL_BINDING_IMMUTABLE")


def downgrade() -> None:
    for table in ("skill_bindings", "agent_memory_records"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_reject_delete")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_reject_update")
    op.execute("DROP TRIGGER IF EXISTS agent_memory_records_validate_supersedes")
    op.execute(
        "DROP TRIGGER IF EXISTS agent_memory_records_validate_terminal_commitment"
    )
    op.drop_index("ix_skill_bindings_world_id", table_name="skill_bindings")
    op.drop_table("skill_bindings")
    with op.batch_alter_table("agent_memory_records") as batch:
        batch.drop_constraint("ck_memory_status_type", type_="check")
        batch.drop_constraint("ck_memory_status", type_="check")
        batch.drop_constraint("ck_memory_type", type_="check")
        batch.drop_constraint("fk_memory_supersedes", type_="foreignkey")
        batch.drop_constraint("uq_memory_supersedes", type_="unique")
        batch.drop_column("location_tags_json")
        batch.drop_column("entity_tags_json")
        batch.drop_column("supersedes_memory_id")
        batch.drop_column("status")
        batch.drop_column("source")
