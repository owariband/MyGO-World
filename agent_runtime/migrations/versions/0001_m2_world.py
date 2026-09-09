"""Create Project-bound World and Agent seed storage.

Revision ID: 0001_m2_world
Revises:
Create Date: 2026-09-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_m2_world"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_database",
        sa.Column("singleton_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint("singleton_id = 1", name="ck_project_database_singleton"),
        sa.PrimaryKeyConstraint("singleton_id"),
        sa.UniqueConstraint("project_id", name="uq_project_database_project"),
    )
    op.create_table(
        "worlds",
        sa.Column("world_id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("seed_id", sa.Text(), nullable=False),
        sa.Column("seed_version", sa.Integer(), nullable=False),
        sa.Column("seed_hash", sa.Text(), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("world_time", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint("seed_version >= 1", name="ck_worlds_seed_version"),
        sa.CheckConstraint(
            "length(seed_hash) = 64 AND seed_hash NOT GLOB '*[^0-9a-f]*'",
            name="ck_worlds_seed_hash",
        ),
        sa.CheckConstraint("current_version >= 1", name="ck_worlds_current_version"),
        sa.CheckConstraint(
            "status IN ('paused', 'running', 'ended')",
            name="ck_worlds_status",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project_database.project_id"],
            name="fk_worlds_project_database",
        ),
        sa.PrimaryKeyConstraint("world_id"),
    )
    op.create_table(
        "locations",
        sa.Column("world_id", sa.Text(), nullable=False),
        sa.Column("location_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["world_id"], ["worlds.world_id"], name="fk_locations_world"),
        sa.PrimaryKeyConstraint("world_id", "location_id"),
    )
    op.create_table(
        "agent_world_states",
        sa.Column("world_id", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("location_id", sa.Text(), nullable=False),
        sa.Column("public_status", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["world_id"], ["worlds.world_id"], name="fk_agent_world_states_world"
        ),
        sa.ForeignKeyConstraint(
            ["world_id", "location_id"],
            ["locations.world_id", "locations.location_id"],
            name="fk_agent_world_states_location",
        ),
        sa.PrimaryKeyConstraint("world_id", "agent_id"),
    )
    op.create_index(
        "ix_agent_world_states_location",
        "agent_world_states",
        ["world_id", "location_id"],
    )
    op.create_table(
        "objects",
        sa.Column("world_id", sa.Text(), nullable=False),
        sa.Column("object_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("location_id", sa.Text(), nullable=False),
        sa.Column("owner_agent_id", sa.Text(), nullable=True),
        sa.Column("state", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["world_id"], ["worlds.world_id"], name="fk_objects_world"),
        sa.ForeignKeyConstraint(
            ["world_id", "location_id"],
            ["locations.world_id", "locations.location_id"],
            name="fk_objects_location",
        ),
        sa.ForeignKeyConstraint(
            ["world_id", "owner_agent_id"],
            ["agent_world_states.world_id", "agent_world_states.agent_id"],
            name="fk_objects_owner",
        ),
        sa.PrimaryKeyConstraint("world_id", "object_id"),
    )
    op.create_index("ix_objects_location", "objects", ["world_id", "location_id"])
    op.create_table(
        "world_facts",
        sa.Column("world_id", sa.Text(), nullable=False),
        sa.Column("fact_id", sa.Text(), nullable=False),
        sa.Column("location_id", sa.Text(), nullable=True),
        sa.Column("agent_id", sa.Text(), nullable=True),
        sa.Column("object_id", sa.Text(), nullable=True),
        sa.Column("predicate", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "(location_id IS NOT NULL) + (agent_id IS NOT NULL) + (object_id IS NOT NULL) <= 1",
            name="ck_world_facts_one_owner",
        ),
        sa.ForeignKeyConstraint(["world_id"], ["worlds.world_id"], name="fk_world_facts_world"),
        sa.ForeignKeyConstraint(
            ["world_id", "location_id"],
            ["locations.world_id", "locations.location_id"],
            name="fk_world_facts_location",
        ),
        sa.ForeignKeyConstraint(
            ["world_id", "agent_id"],
            ["agent_world_states.world_id", "agent_world_states.agent_id"],
            name="fk_world_facts_agent",
        ),
        sa.ForeignKeyConstraint(
            ["world_id", "object_id"],
            ["objects.world_id", "objects.object_id"],
            name="fk_world_facts_object",
        ),
        sa.PrimaryKeyConstraint("world_id", "fact_id"),
    )
    op.create_table(
        "event_sessions",
        sa.Column("world_id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("root_session_id", sa.Text(), nullable=False),
        sa.Column("topology_version", sa.Integer(), nullable=False),
        sa.Column("updated_world_version", sa.Integer(), nullable=False),
        sa.CheckConstraint("topology_version >= 1", name="ck_event_sessions_topology_version"),
        sa.CheckConstraint(
            "updated_world_version >= 1",
            name="ck_event_sessions_updated_world_version",
        ),
        sa.ForeignKeyConstraint(["world_id"], ["worlds.world_id"], name="fk_event_sessions_world"),
        sa.ForeignKeyConstraint(
            ["world_id", "agent_id"],
            ["agent_world_states.world_id", "agent_world_states.agent_id"],
            name="fk_event_sessions_agent",
        ),
        sa.ForeignKeyConstraint(
            ["world_id", "root_session_id"],
            ["event_sessions.world_id", "event_sessions.session_id"],
            name="fk_event_sessions_root",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.PrimaryKeyConstraint("world_id", "session_id"),
        sa.UniqueConstraint("world_id", "agent_id", name="uq_event_sessions_agent"),
    )
    op.create_index(
        "ix_event_sessions_root",
        "event_sessions",
        ["world_id", "root_session_id"],
    )
    op.create_table(
        "agent_runtime_states",
        sa.Column("world_id", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("state_revision", sa.Integer(), nullable=False),
        sa.Column("spec_digest", sa.Text(), nullable=False),
        sa.Column("persona_state_json", sa.Text(), nullable=False),
        sa.CheckConstraint("state_revision >= 1", name="ck_agent_runtime_states_revision"),
        sa.CheckConstraint(
            "length(spec_digest) = 64 AND spec_digest NOT GLOB '*[^0-9a-f]*'",
            name="ck_agent_runtime_states_spec_digest",
        ),
        sa.CheckConstraint("json_valid(persona_state_json)", name="ck_agent_runtime_states_json"),
        sa.ForeignKeyConstraint(
            ["world_id", "agent_id"],
            ["agent_world_states.world_id", "agent_world_states.agent_id"],
            name="fk_agent_runtime_states_agent",
        ),
        sa.PrimaryKeyConstraint("world_id", "agent_id"),
    )
    op.create_table(
        "agent_memory_records",
        sa.Column("world_id", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("memory_id", sa.Text(), nullable=False),
        sa.Column("memory_seq", sa.Integer(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("last_accessed_at", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("predicate", sa.Text(), nullable=False),
        sa.Column("object_value", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("poignancy", sa.Float(), nullable=False),
        sa.Column("tags_json", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("evidence_ids_json", sa.Text(), nullable=False),
        sa.Column("embedding_json", sa.Text(), nullable=False),
        sa.Column("novelty_key", sa.Text(), nullable=False),
        sa.CheckConstraint("memory_seq >= 1", name="ck_agent_memory_records_sequence"),
        sa.CheckConstraint(
            "kind IN ('event', 'thought', 'chat', 'plan')",
            name="ck_agent_memory_records_kind",
        ),
        sa.CheckConstraint("poignancy >= 0", name="ck_agent_memory_records_poignancy"),
        sa.CheckConstraint("json_valid(tags_json)", name="ck_agent_memory_records_tags_json"),
        sa.CheckConstraint(
            "json_valid(evidence_ids_json)",
            name="ck_agent_memory_records_evidence_json",
        ),
        sa.CheckConstraint(
            "json_valid(embedding_json)", name="ck_agent_memory_records_embedding_json"
        ),
        sa.ForeignKeyConstraint(
            ["world_id", "agent_id"],
            ["agent_world_states.world_id", "agent_world_states.agent_id"],
            name="fk_agent_memory_records_agent",
        ),
        sa.PrimaryKeyConstraint("world_id", "agent_id", "memory_id"),
        sa.UniqueConstraint(
            "world_id",
            "agent_id",
            "memory_seq",
            name="uq_agent_memory_records_sequence",
        ),
    )
    op.create_index(
        "ix_agent_memory_records_recent",
        "agent_memory_records",
        ["world_id", "agent_id", "created_at", "memory_id"],
    )
    op.create_index(
        "ix_agent_memory_records_novelty",
        "agent_memory_records",
        ["world_id", "agent_id", "novelty_key"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_memory_records_novelty", table_name="agent_memory_records")
    op.drop_index("ix_agent_memory_records_recent", table_name="agent_memory_records")
    op.drop_table("agent_memory_records")
    op.drop_table("agent_runtime_states")
    op.drop_index("ix_event_sessions_root", table_name="event_sessions")
    op.drop_table("event_sessions")
    op.drop_table("world_facts")
    op.drop_index("ix_objects_location", table_name="objects")
    op.drop_table("objects")
    op.drop_index("ix_agent_world_states_location", table_name="agent_world_states")
    op.drop_table("agent_world_states")
    op.drop_table("locations")
    op.drop_table("worlds")
    op.drop_table("project_database")
