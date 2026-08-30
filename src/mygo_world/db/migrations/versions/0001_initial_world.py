"""Create durable World foundation."""

import sqlalchemy as sa
from alembic import op

revision = "0001_durable_world"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worlds",
        sa.Column("world_id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("seed_id", sa.String(200), nullable=False),
        sa.Column("seed_version", sa.String(100), nullable=False),
        sa.Column("seed_content_hash", sa.String(64), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("calendar_anchor", sa.String(100)),
        sa.Column("created_at", sa.String(40), nullable=False),
    )
    op.create_table(
        "world_segments",
        sa.Column("segment_id", sa.String(36), primary_key=True),
        sa.Column("segment_order", sa.Integer(), nullable=False, unique=True),
        sa.Column("world_version", sa.Integer(), nullable=False, unique=True),
        sa.Column("segment_type", sa.String(32), nullable=False),
        sa.Column("source_trace_id", sa.String(36)),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("committed_at", sa.String(40), nullable=False),
    )
    op.create_table(
        "world_versions",
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column(
            "segment_id",
            sa.String(36),
            sa.ForeignKey("world_segments.segment_id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("world_time_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
    )
    op.create_table(
        "entity_revisions",
        sa.Column("entity_revision_id", sa.String(36), primary_key=True),
        sa.Column("entity_id", sa.String(200), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("revision_order", sa.Integer(), nullable=False),
        sa.Column(
            "world_version",
            sa.Integer(),
            sa.ForeignKey("world_versions.version"),
            nullable=False,
        ),
        sa.Column(
            "segment_id",
            sa.String(36),
            sa.ForeignKey("world_segments.segment_id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("location_id", sa.String(200)),
        sa.Column("scope_key", sa.String(200)),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.UniqueConstraint("entity_id", "revision_order"),
        sa.UniqueConstraint("entity_id", "world_version"),
    )
    op.create_table(
        "world_events",
        sa.Column("event_id", sa.String(36), primary_key=True),
        sa.Column("event_order", sa.Integer(), nullable=False, unique=True),
        sa.Column(
            "world_version",
            sa.Integer(),
            sa.ForeignKey("world_versions.version"),
            nullable=False,
        ),
        sa.Column(
            "segment_id",
            sa.String(36),
            sa.ForeignKey("world_segments.segment_id"),
            nullable=False,
        ),
        sa.Column(
            "session_id", sa.String(200), sa.ForeignKey("event_sessions.session_id")
        ),
        sa.Column("start_time_ms", sa.Integer(), nullable=False),
        sa.Column("end_time_ms", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
    )
    op.create_table(
        "snapshots",
        sa.Column(
            "world_version",
            sa.Integer(),
            sa.ForeignKey("world_versions.version"),
            primary_key=True,
        ),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
    )
    op.create_table(
        "event_sessions",
        sa.Column("session_id", sa.String(200), primary_key=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("location_id", sa.String(200), nullable=False),
        sa.Column("scope_key", sa.String(200), nullable=False),
        sa.Column(
            "created_world_version",
            sa.Integer(),
            sa.ForeignKey("world_versions.version"),
            nullable=False,
        ),
        sa.Column(
            "closed_world_version",
            sa.Integer(),
            sa.ForeignKey("world_versions.version"),
        ),
        sa.Column("closure_reason", sa.String(32)),
    )
    op.create_table(
        "event_session_members",
        sa.Column(
            "session_id",
            sa.String(200),
            sa.ForeignKey("event_sessions.session_id"),
            primary_key=True,
        ),
        sa.Column("agent_id", sa.String(200), primary_key=True),
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
    op.create_table(
        "agent_memory_records",
        sa.Column("memory_id", sa.String(200), primary_key=True),
        sa.Column("agent_id", sa.String(200), nullable=False),
        sa.Column("namespace", sa.String(100), nullable=False),
        sa.Column("memory_type", sa.String(32), nullable=False),
        sa.Column(
            "world_version",
            sa.Integer(),
            sa.ForeignKey("world_versions.version"),
            nullable=False,
        ),
        sa.Column("relative_time_ms", sa.Integer(), nullable=False),
        sa.Column("importance", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_agent_memory_records_agent_id", "agent_memory_records", ["agent_id"]
    )

    for table in ("world_segments", "entity_revisions", "world_events"):
        op.execute(
            f"CREATE TRIGGER {table}_reject_update BEFORE UPDATE ON {table} "
            f"BEGIN SELECT RAISE(ABORT, 'LEDGER_IMMUTABLE:{table}:UPDATE'); END"
        )
        op.execute(
            f"CREATE TRIGGER {table}_reject_delete BEFORE DELETE ON {table} "
            f"BEGIN SELECT RAISE(ABORT, 'LEDGER_IMMUTABLE:{table}:DELETE'); END"
        )


def downgrade() -> None:
    for table in ("world_events", "entity_revisions", "world_segments"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_reject_delete")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_reject_update")
    op.drop_index("ix_agent_memory_records_agent_id", table_name="agent_memory_records")
    op.drop_table("agent_memory_records")
    op.drop_table("runnable_session_queue")
    op.drop_table("event_session_members")
    op.drop_table("event_sessions")
    op.drop_table("snapshots")
    op.drop_table("world_events")
    op.drop_table("entity_revisions")
    op.drop_table("world_versions")
    op.drop_table("world_segments")
    op.drop_table("worlds")
