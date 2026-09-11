"""Add committed EventEntry history and single-step commit cursors.

Revision ID: 0002_m3_event_entries
Revises: 0001_m2_world
Create Date: 2026-09-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_m3_event_entries"
down_revision: str | Sequence[str] | None = "0001_m2_world"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "worlds",
        sa.Column(
            "control_epoch",
            sa.Integer(),
            sa.CheckConstraint(
                "control_epoch >= 1",
                name="ck_worlds_control_epoch",
            ),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column(
        "worlds",
        sa.Column(
            "decision_seq",
            sa.Integer(),
            sa.CheckConstraint(
                "decision_seq >= 0",
                name="ck_worlds_decision_seq",
            ),
            nullable=False,
            server_default="0",
        ),
    )

    _create_event_entries()
    _create_event_entry_links()
    _create_event_entry_recipients()
    _create_interaction_requests()
    _create_event_triggers()

    with op.batch_alter_table("agent_runtime_states") as batch_op:
        batch_op.add_column(sa.Column("observation_world_version", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("observation_entry_index", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("last_decision_id", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("last_decision_outcome", sa.Text(), nullable=True))
        batch_op.create_check_constraint(
            "ck_agent_runtime_states_cursor",
            "(observation_world_version IS NULL AND observation_entry_index IS NULL) OR "
            "(observation_world_version >= 1 AND observation_entry_index >= 0)",
        )
        batch_op.create_check_constraint(
            "ck_agent_runtime_states_last_decision",
            "(last_decision_id IS NULL AND last_decision_outcome IS NULL) OR "
            "(last_decision_id IS NOT NULL AND last_decision_outcome IN "
            "('applied', 'not_applied', 'wait', 'no_op'))",
        )
        batch_op.create_foreign_key(
            "fk_agent_runtime_states_observation_entry",
            "event_entries",
            ["world_id", "observation_world_version", "observation_entry_index"],
            ["world_id", "world_version", "entry_index"],
        )

    with op.batch_alter_table("agent_memory_records") as batch_op:
        batch_op.add_column(sa.Column("source_entry_id", sa.Text(), nullable=True))
        batch_op.create_foreign_key(
            "fk_agent_memory_records_source_recipient",
            "event_entry_recipients",
            ["world_id", "source_entry_id", "agent_id"],
            ["world_id", "entry_id", "agent_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_memory_records") as batch_op:
        batch_op.drop_constraint(
            "fk_agent_memory_records_source_recipient",
            type_="foreignkey",
        )
        batch_op.drop_column("source_entry_id")

    with op.batch_alter_table("agent_runtime_states") as batch_op:
        batch_op.drop_constraint(
            "fk_agent_runtime_states_observation_entry",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "ck_agent_runtime_states_last_decision",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_agent_runtime_states_cursor",
            type_="check",
        )
        batch_op.drop_column("last_decision_outcome")
        batch_op.drop_column("last_decision_id")
        batch_op.drop_column("observation_entry_index")
        batch_op.drop_column("observation_world_version")

    _drop_event_triggers()
    op.drop_index("ix_interaction_requests_pending", table_name="interaction_requests")
    op.drop_table("interaction_requests")
    op.drop_table("event_entry_recipients")
    op.drop_index("uq_event_entry_links_reply", table_name="event_entry_links")
    op.drop_table("event_entry_links")
    op.drop_index("ix_event_entries_session_order", table_name="event_entries")
    op.drop_table("event_entries")

    op.drop_column("worlds", "decision_seq")
    op.drop_column("worlds", "control_epoch")


def _create_event_entries() -> None:
    op.create_table(
        "event_entries",
        sa.Column("world_id", sa.Text(), nullable=False),
        sa.Column("entry_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("entry_kind", sa.Text(), nullable=False),
        sa.Column("source_kind", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("source_index", sa.Integer(), nullable=False),
        sa.Column("world_version", sa.Integer(), nullable=False),
        sa.Column("entry_index", sa.Integer(), nullable=False),
        sa.Column("root_session_id_at_commit", sa.Text(), nullable=False),
        sa.Column("topology_version", sa.Integer(), nullable=False),
        sa.Column("actor_agent_id", sa.Text(), nullable=False),
        sa.Column("target_agent_id", sa.Text(), nullable=True),
        sa.Column("target_object_id", sa.Text(), nullable=True),
        sa.Column("operation_id", sa.Text(), nullable=True),
        sa.Column("audience_mode", sa.Text(), nullable=False),
        sa.Column("delivery_channel", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint("status = 'committed'", name="ck_event_entries_status"),
        sa.CheckConstraint(
            "entry_kind IN ('dialogue', 'action')",
            name="ck_event_entries_kind",
        ),
        sa.CheckConstraint(
            "source_kind = 'character_proposal'",
            name="ck_event_entries_source_kind",
        ),
        sa.CheckConstraint("source_index = 0", name="ck_event_entries_source_index"),
        sa.CheckConstraint("world_version >= 1", name="ck_event_entries_world_version"),
        sa.CheckConstraint("entry_index = 0", name="ck_event_entries_entry_index"),
        sa.CheckConstraint("topology_version >= 1", name="ck_event_entries_topology_version"),
        sa.CheckConstraint("length(text) > 0", name="ck_event_entries_text"),
        sa.CheckConstraint(
            "(entry_kind = 'dialogue' AND target_agent_id IS NOT NULL "
            "AND target_object_id IS NULL AND operation_id IS NULL) OR "
            "(entry_kind = 'action' AND target_agent_id IS NULL "
            "AND target_object_id IS NOT NULL AND operation_id IS NOT NULL)",
            name="ck_event_entries_target",
        ),
        sa.CheckConstraint(
            "(entry_kind = 'dialogue' AND delivery_channel = 'direct' "
            "AND audience_mode = 'session') OR "
            "(entry_kind = 'dialogue' AND delivery_channel = 'whisper' "
            "AND audience_mode = 'explicit') OR "
            "(entry_kind = 'action' AND delivery_channel = 'public' "
            "AND audience_mode = 'session')",
            name="ck_event_entries_delivery",
        ),
        sa.ForeignKeyConstraint(
            ["world_id"],
            ["worlds.world_id"],
            name="fk_event_entries_world",
        ),
        sa.ForeignKeyConstraint(
            ["world_id", "root_session_id_at_commit"],
            ["event_sessions.world_id", "event_sessions.session_id"],
            name="fk_event_entries_root_session",
        ),
        sa.ForeignKeyConstraint(
            ["world_id", "actor_agent_id"],
            ["agent_world_states.world_id", "agent_world_states.agent_id"],
            name="fk_event_entries_actor",
        ),
        sa.ForeignKeyConstraint(
            ["world_id", "target_agent_id"],
            ["agent_world_states.world_id", "agent_world_states.agent_id"],
            name="fk_event_entries_target_agent",
        ),
        sa.ForeignKeyConstraint(
            ["world_id", "target_object_id"],
            ["objects.world_id", "objects.object_id"],
            name="fk_event_entries_target_object",
        ),
        sa.PrimaryKeyConstraint("world_id", "entry_id"),
        sa.UniqueConstraint(
            "world_id",
            "world_version",
            "entry_index",
            name="uq_event_entries_commit_position",
        ),
        sa.UniqueConstraint(
            "world_id",
            "source_kind",
            "source_id",
            "source_index",
            name="uq_event_entries_source",
        ),
    )
    op.create_index(
        "ix_event_entries_session_order",
        "event_entries",
        [
            "world_id",
            "root_session_id_at_commit",
            "topology_version",
            "world_version",
            "entry_index",
        ],
    )


def _create_event_entry_links() -> None:
    op.create_table(
        "event_entry_links",
        sa.Column("world_id", sa.Text(), nullable=False),
        sa.Column("entry_id", sa.Text(), nullable=False),
        sa.Column("relation_kind", sa.Text(), nullable=False),
        sa.Column("related_entry_id", sa.Text(), nullable=False),
        sa.Column("relation_order", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "relation_kind IN ('previous', 'reply', 'cause')",
            name="ck_event_entry_links_kind",
        ),
        sa.CheckConstraint("relation_order >= 0", name="ck_event_entry_links_order"),
        sa.ForeignKeyConstraint(
            ["world_id", "entry_id"],
            ["event_entries.world_id", "event_entries.entry_id"],
            name="fk_event_entry_links_entry",
        ),
        sa.ForeignKeyConstraint(
            ["world_id", "related_entry_id"],
            ["event_entries.world_id", "event_entries.entry_id"],
            name="fk_event_entry_links_related",
        ),
        sa.PrimaryKeyConstraint("world_id", "entry_id", "relation_kind", "related_entry_id"),
        sa.UniqueConstraint(
            "world_id",
            "entry_id",
            "relation_kind",
            "relation_order",
            name="uq_event_entry_links_order",
        ),
    )
    op.create_index(
        "uq_event_entry_links_reply",
        "event_entry_links",
        ["world_id", "entry_id"],
        unique=True,
        sqlite_where=sa.text("relation_kind = 'reply'"),
    )


def _create_event_entry_recipients() -> None:
    op.create_table(
        "event_entry_recipients",
        sa.Column("world_id", sa.Text(), nullable=False),
        sa.Column("entry_id", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["world_id", "entry_id"],
            ["event_entries.world_id", "event_entries.entry_id"],
            name="fk_event_entry_recipients_entry",
        ),
        sa.ForeignKeyConstraint(
            ["world_id", "agent_id"],
            ["agent_world_states.world_id", "agent_world_states.agent_id"],
            name="fk_event_entry_recipients_agent",
        ),
        sa.PrimaryKeyConstraint("world_id", "entry_id", "agent_id"),
    )


def _create_interaction_requests() -> None:
    op.create_table(
        "interaction_requests",
        sa.Column("world_id", sa.Text(), nullable=False),
        sa.Column("request_entry_id", sa.Text(), nullable=False),
        sa.Column("request_kind", sa.Text(), nullable=False),
        sa.Column("requester_agent_id", sa.Text(), nullable=False),
        sa.Column("recipient_agent_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("resolution_entry_id", sa.Text(), nullable=True),
        sa.Column("updated_world_version", sa.Integer(), nullable=False),
        sa.CheckConstraint("request_kind = 'response'", name="ck_interaction_requests_kind"),
        sa.CheckConstraint(
            "status IN ('pending', 'resolved')",
            name="ck_interaction_requests_status",
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND resolution_entry_id IS NULL) OR "
            "(status = 'resolved' AND resolution_entry_id IS NOT NULL)",
            name="ck_interaction_requests_resolution",
        ),
        sa.CheckConstraint(
            "requester_agent_id <> recipient_agent_id",
            name="ck_interaction_requests_distinct_agents",
        ),
        sa.CheckConstraint(
            "updated_world_version >= 1",
            name="ck_interaction_requests_world_version",
        ),
        sa.ForeignKeyConstraint(
            ["world_id", "request_entry_id"],
            ["event_entries.world_id", "event_entries.entry_id"],
            name="fk_interaction_requests_request_entry",
        ),
        sa.ForeignKeyConstraint(
            ["world_id", "requester_agent_id"],
            ["agent_world_states.world_id", "agent_world_states.agent_id"],
            name="fk_interaction_requests_requester",
        ),
        sa.ForeignKeyConstraint(
            ["world_id", "recipient_agent_id"],
            ["agent_world_states.world_id", "agent_world_states.agent_id"],
            name="fk_interaction_requests_recipient",
        ),
        sa.ForeignKeyConstraint(
            ["world_id", "resolution_entry_id"],
            ["event_entries.world_id", "event_entries.entry_id"],
            name="fk_interaction_requests_resolution_entry",
        ),
        sa.PrimaryKeyConstraint("world_id", "request_entry_id"),
    )
    op.create_index(
        "ix_interaction_requests_pending",
        "interaction_requests",
        ["world_id", "recipient_agent_id", "status", "updated_world_version"],
    )


def _create_event_triggers() -> None:
    for table in ("event_entries", "event_entry_links", "event_entry_recipients"):
        op.execute(
            sa.text(
                f"CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'committed EventEntry history is append-only'); END"
            )
        )
        op.execute(
            sa.text(
                f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'committed EventEntry history is append-only'); END"
            )
        )

    op.execute(
        sa.text(
            "CREATE TRIGGER event_entry_links_require_earlier "
            "BEFORE INSERT ON event_entry_links BEGIN "
            "SELECT CASE WHEN NOT EXISTS ("
            "SELECT 1 FROM event_entries current_entry "
            "JOIN event_entries related_entry "
            "ON related_entry.world_id = NEW.world_id "
            "AND related_entry.entry_id = NEW.related_entry_id "
            "WHERE current_entry.world_id = NEW.world_id "
            "AND current_entry.entry_id = NEW.entry_id "
            "AND (related_entry.world_version < current_entry.world_version "
            "OR (related_entry.world_version = current_entry.world_version "
            "AND related_entry.entry_index < current_entry.entry_index))"
            ") THEN RAISE(ABORT, 'EventEntry link must point to an earlier entry') END; "
            "END"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER interaction_requests_validate_insert "
            "BEFORE INSERT ON interaction_requests BEGIN "
            "SELECT CASE WHEN NEW.status <> 'pending' OR NEW.resolution_entry_id IS NOT NULL "
            "THEN RAISE(ABORT, 'InteractionRequest must start pending') END; "
            "SELECT CASE WHEN NOT EXISTS ("
            "SELECT 1 FROM event_entries source_entry "
            "WHERE source_entry.world_id = NEW.world_id "
            "AND source_entry.entry_id = NEW.request_entry_id "
            "AND source_entry.entry_kind = 'dialogue' "
            "AND source_entry.actor_agent_id = NEW.requester_agent_id "
            "AND source_entry.target_agent_id = NEW.recipient_agent_id "
            "AND source_entry.world_version = NEW.updated_world_version"
            ") THEN RAISE(ABORT, 'InteractionRequest does not match its source entry') END; "
            "END"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER interaction_requests_validate_resolution "
            "BEFORE UPDATE ON interaction_requests BEGIN "
            "SELECT CASE WHEN OLD.world_id <> NEW.world_id "
            "OR OLD.request_entry_id <> NEW.request_entry_id "
            "OR OLD.request_kind <> NEW.request_kind "
            "OR OLD.requester_agent_id <> NEW.requester_agent_id "
            "OR OLD.recipient_agent_id <> NEW.recipient_agent_id "
            "OR OLD.status <> 'pending' OR NEW.status <> 'resolved' "
            "OR OLD.resolution_entry_id IS NOT NULL OR NEW.resolution_entry_id IS NULL "
            "OR NEW.updated_world_version < OLD.updated_world_version "
            "THEN RAISE(ABORT, 'invalid InteractionRequest transition') END; "
            "SELECT CASE WHEN NOT EXISTS ("
            "SELECT 1 FROM event_entries resolution_entry "
            "JOIN event_entry_links reply_link "
            "ON reply_link.world_id = NEW.world_id "
            "AND reply_link.entry_id = NEW.resolution_entry_id "
            "AND reply_link.relation_kind = 'reply' "
            "AND reply_link.related_entry_id = NEW.request_entry_id "
            "WHERE resolution_entry.world_id = NEW.world_id "
            "AND resolution_entry.entry_id = NEW.resolution_entry_id "
            "AND resolution_entry.entry_kind = 'dialogue' "
            "AND resolution_entry.actor_agent_id = NEW.recipient_agent_id "
            "AND resolution_entry.target_agent_id = NEW.requester_agent_id "
            "AND resolution_entry.world_version = NEW.updated_world_version"
            ") THEN RAISE(ABORT, 'InteractionRequest resolution is not a valid reply') END; "
            "END"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER interaction_requests_no_delete "
            "BEFORE DELETE ON interaction_requests "
            "BEGIN SELECT RAISE(ABORT, 'InteractionRequest history cannot be deleted'); END"
        )
    )


def _drop_event_triggers() -> None:
    for trigger in (
        "event_entries_no_update",
        "event_entries_no_delete",
        "event_entry_links_no_update",
        "event_entry_links_no_delete",
        "event_entry_recipients_no_update",
        "event_entry_recipients_no_delete",
        "event_entry_links_require_earlier",
        "interaction_requests_validate_insert",
        "interaction_requests_validate_resolution",
        "interaction_requests_no_delete",
    ):
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS {trigger}"))
