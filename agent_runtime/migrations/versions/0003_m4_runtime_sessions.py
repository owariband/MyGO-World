"""Add durable dispatch, dynamic EventSessions, and StoryLine transition metadata.

Revision ID: 0003_m4_runtime_sessions
Revises: 0002_m3_event_entries
Create Date: 2026-09-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_m4_runtime_sessions"
down_revision: str | Sequence[str] | None = "0002_m3_event_entries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _add_runtime_columns()
    _drop_event_triggers()
    _detach_entry_foreign_keys(upgrade_requests=True)
    _upgrade_event_entries()
    _attach_entry_foreign_keys(m4=True)
    _create_transition_parts()
    _create_event_triggers()


def downgrade() -> None:
    _require_m3_compatible_data()
    _drop_event_triggers()
    op.drop_index("ix_transition_parts_line", table_name="event_session_transition_parts")
    op.drop_table("event_session_transition_parts")
    _detach_entry_foreign_keys(upgrade_requests=False)
    _downgrade_event_entries()
    _attach_entry_foreign_keys(m4=False)
    _drop_runtime_columns()
    _create_m3_event_triggers()


def _add_runtime_columns() -> None:
    for column in (
        sa.Column(
            "dispatch_count",
            sa.Integer(),
            sa.CheckConstraint("dispatch_count >= 0", name="ck_worlds_dispatch_count"),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "dispatch_limit_at",
            sa.Integer(),
            sa.CheckConstraint(
                "dispatch_limit_at >= dispatch_count", name="ck_worlds_dispatch_limit"
            ),
            nullable=False,
            server_default="0",
        ),
        sa.Column("run_owner_id", sa.Text(), nullable=True),
        sa.Column(
            "active_dispatch_count",
            sa.Integer(),
            sa.CheckConstraint(
                "active_dispatch_count IS NULL OR active_dispatch_count <= dispatch_count",
                name="ck_worlds_active_dispatch_count",
            ),
            nullable=True,
        ),
        sa.Column(
            "active_dispatch_agent_id",
            sa.Text(),
            sa.CheckConstraint(
                "(active_dispatch_count IS NULL AND active_dispatch_agent_id IS NULL) OR "
                "(active_dispatch_count IS NOT NULL AND active_dispatch_agent_id IS NOT NULL)",
                name="ck_worlds_active_dispatch_pair",
            ),
            sa.CheckConstraint(
                "active_dispatch_count IS NULL OR "
                "(status = 'running' AND run_owner_id IS NOT NULL)",
                name="ck_worlds_active_dispatch_running",
            ),
            nullable=True,
        ),
        sa.Column("stop_reason", sa.Text(), nullable=True),
        sa.Column(
            "stopped_at_world_version",
            sa.Integer(),
            sa.CheckConstraint(
                "stopped_at_world_version IS NULL OR stopped_at_world_version <= current_version",
                name="ck_worlds_stopped_version",
            ),
            nullable=True,
        ),
    ):
        op.add_column("worlds", column)

    for column in (
        sa.Column(
            "last_dispatch_count",
            sa.Integer(),
            sa.CheckConstraint(
                "last_dispatch_count >= 0", name="ck_event_sessions_last_dispatch_count"
            ),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "wait_for_visible_entry_after_version",
            sa.Integer(),
            sa.CheckConstraint(
                "wait_for_visible_entry_after_version IS NULL OR "
                "wait_for_visible_entry_after_version >= 1",
                name="ck_event_sessions_wait_version",
            ),
            nullable=True,
        ),
        sa.Column(
            "consecutive_dialogue_turns",
            sa.Integer(),
            sa.CheckConstraint(
                "consecutive_dialogue_turns >= 0",
                name="ck_event_sessions_dialogue_turns",
            ),
            nullable=False,
            server_default="0",
        ),
    ):
        op.add_column("event_sessions", column)


def _drop_runtime_columns() -> None:
    for name in (
        "consecutive_dialogue_turns",
        "wait_for_visible_entry_after_version",
        "last_dispatch_count",
    ):
        op.drop_column("event_sessions", name)
    for name in (
        "stopped_at_world_version",
        "stop_reason",
        "active_dispatch_agent_id",
        "active_dispatch_count",
        "run_owner_id",
        "dispatch_limit_at",
        "dispatch_count",
    ):
        op.drop_column("worlds", name)


def _detach_entry_foreign_keys(*, upgrade_requests: bool) -> None:
    with op.batch_alter_table("agent_memory_records", recreate="always") as batch_op:
        batch_op.drop_constraint("fk_agent_memory_records_source_recipient", type_="foreignkey")
    with op.batch_alter_table("agent_runtime_states", recreate="always") as batch_op:
        batch_op.drop_constraint("fk_agent_runtime_states_observation_entry", type_="foreignkey")
    with op.batch_alter_table("interaction_requests", recreate="always") as batch_op:
        batch_op.drop_constraint("fk_interaction_requests_request_entry", type_="foreignkey")
        batch_op.drop_constraint("fk_interaction_requests_resolution_entry", type_="foreignkey")
        if upgrade_requests:
            batch_op.drop_constraint("ck_interaction_requests_status", type_="check")
            batch_op.drop_constraint("ck_interaction_requests_resolution", type_="check")
            batch_op.add_column(sa.Column("cancellation_entry_id", sa.Text(), nullable=True))
            batch_op.add_column(
                sa.Column("priority_consumed_dispatch_count", sa.Integer(), nullable=True)
            )
            batch_op.create_check_constraint(
                "ck_interaction_requests_status",
                "status IN ('pending', 'resolved', 'cancelled')",
            )
            batch_op.create_check_constraint(
                "ck_interaction_requests_resolution",
                "(status = 'pending' AND resolution_entry_id IS NULL "
                "AND cancellation_entry_id IS NULL) OR "
                "(status = 'resolved' AND resolution_entry_id IS NOT NULL "
                "AND cancellation_entry_id IS NULL) OR "
                "(status = 'cancelled' AND resolution_entry_id IS NULL "
                "AND cancellation_entry_id IS NOT NULL)",
            )
            batch_op.create_check_constraint(
                "ck_interaction_requests_priority",
                "priority_consumed_dispatch_count IS NULL OR priority_consumed_dispatch_count >= 1",
            )
        else:
            batch_op.drop_constraint(
                "fk_interaction_requests_cancellation_entry", type_="foreignkey"
            )
            batch_op.drop_constraint("ck_interaction_requests_priority", type_="check")
            batch_op.drop_constraint("ck_interaction_requests_status", type_="check")
            batch_op.drop_constraint("ck_interaction_requests_resolution", type_="check")
            batch_op.create_check_constraint(
                "ck_interaction_requests_status", "status IN ('pending', 'resolved')"
            )
            batch_op.create_check_constraint(
                "ck_interaction_requests_resolution",
                "(status = 'pending' AND resolution_entry_id IS NULL) OR "
                "(status = 'resolved' AND resolution_entry_id IS NOT NULL)",
            )
            batch_op.drop_column("priority_consumed_dispatch_count")
            batch_op.drop_column("cancellation_entry_id")
    with op.batch_alter_table("event_entry_links", recreate="always") as batch_op:
        batch_op.drop_constraint("fk_event_entry_links_entry", type_="foreignkey")
        batch_op.drop_constraint("fk_event_entry_links_related", type_="foreignkey")
    with op.batch_alter_table("event_entry_recipients", recreate="always") as batch_op:
        batch_op.drop_constraint("fk_event_entry_recipients_entry", type_="foreignkey")


def _attach_entry_foreign_keys(*, m4: bool) -> None:
    with op.batch_alter_table("event_entry_links", recreate="always") as batch_op:
        batch_op.create_foreign_key(
            "fk_event_entry_links_entry",
            "event_entries",
            ["world_id", "entry_id"],
            ["world_id", "entry_id"],
        )
        batch_op.create_foreign_key(
            "fk_event_entry_links_related",
            "event_entries",
            ["world_id", "related_entry_id"],
            ["world_id", "entry_id"],
        )
    with op.batch_alter_table("event_entry_recipients", recreate="always") as batch_op:
        batch_op.create_foreign_key(
            "fk_event_entry_recipients_entry",
            "event_entries",
            ["world_id", "entry_id"],
            ["world_id", "entry_id"],
        )
    with op.batch_alter_table("interaction_requests", recreate="always") as batch_op:
        batch_op.create_foreign_key(
            "fk_interaction_requests_request_entry",
            "event_entries",
            ["world_id", "request_entry_id"],
            ["world_id", "entry_id"],
        )
        batch_op.create_foreign_key(
            "fk_interaction_requests_resolution_entry",
            "event_entries",
            ["world_id", "resolution_entry_id"],
            ["world_id", "entry_id"],
        )
        if m4:
            batch_op.create_foreign_key(
                "fk_interaction_requests_cancellation_entry",
                "event_entries",
                ["world_id", "cancellation_entry_id"],
                ["world_id", "entry_id"],
            )
    with op.batch_alter_table("agent_runtime_states", recreate="always") as batch_op:
        batch_op.create_foreign_key(
            "fk_agent_runtime_states_observation_entry",
            "event_entries",
            ["world_id", "observation_world_version", "observation_entry_index"],
            ["world_id", "world_version", "entry_index"],
        )
    with op.batch_alter_table("agent_memory_records", recreate="always") as batch_op:
        batch_op.create_foreign_key(
            "fk_agent_memory_records_source_recipient",
            "event_entry_recipients",
            ["world_id", "source_entry_id", "agent_id"],
            ["world_id", "entry_id", "agent_id"],
        )


def _upgrade_event_entries() -> None:
    with op.batch_alter_table("event_entries", recreate="always") as batch_op:
        for name in (
            "ck_event_entries_kind",
            "ck_event_entries_target",
            "ck_event_entries_delivery",
        ):
            batch_op.drop_constraint(name, type_="check")
        batch_op.add_column(sa.Column("transition_reason", sa.Text(), nullable=True))
        batch_op.create_check_constraint(
            "ck_event_entries_kind",
            "entry_kind IN ('dialogue', 'action', 'behavior', 'session_transition')",
        )
        batch_op.create_check_constraint("ck_event_entries_target", _M4_ENTRY_TARGET_CHECK)
        batch_op.create_check_constraint("ck_event_entries_delivery", _M4_ENTRY_DELIVERY_CHECK)


def _downgrade_event_entries() -> None:
    with op.batch_alter_table("event_entries", recreate="always") as batch_op:
        for name in (
            "ck_event_entries_kind",
            "ck_event_entries_target",
            "ck_event_entries_delivery",
        ):
            batch_op.drop_constraint(name, type_="check")
        batch_op.create_check_constraint(
            "ck_event_entries_kind", "entry_kind IN ('dialogue', 'action')"
        )
        batch_op.create_check_constraint("ck_event_entries_target", _M3_ENTRY_TARGET_CHECK)
        batch_op.create_check_constraint("ck_event_entries_delivery", _M3_ENTRY_DELIVERY_CHECK)
        batch_op.drop_column("transition_reason")


def _create_transition_parts() -> None:
    op.create_table(
        "event_session_transition_parts",
        sa.Column("world_id", sa.Text(), nullable=False),
        sa.Column("transition_entry_id", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("part_order", sa.Integer(), nullable=False),
        sa.Column("root_session_id", sa.Text(), nullable=False),
        sa.Column("topology_version", sa.Integer(), nullable=False),
        sa.Column("member_session_id", sa.Text(), nullable=False),
        sa.Column("member_order", sa.Integer(), nullable=False),
        sa.CheckConstraint("side IN ('before', 'after')", name="ck_transition_parts_side"),
        sa.CheckConstraint("part_order >= 0", name="ck_transition_parts_part_order"),
        sa.CheckConstraint("topology_version >= 1", name="ck_transition_parts_topology_version"),
        sa.CheckConstraint("member_order >= 0", name="ck_transition_parts_member_order"),
        sa.ForeignKeyConstraint(
            ["world_id", "transition_entry_id"],
            ["event_entries.world_id", "event_entries.entry_id"],
            name="fk_transition_parts_entry",
        ),
        sa.ForeignKeyConstraint(
            ["world_id", "root_session_id"],
            ["event_sessions.world_id", "event_sessions.session_id"],
            name="fk_transition_parts_root",
        ),
        sa.ForeignKeyConstraint(
            ["world_id", "member_session_id"],
            ["event_sessions.world_id", "event_sessions.session_id"],
            name="fk_transition_parts_member",
        ),
        sa.PrimaryKeyConstraint(
            "world_id", "transition_entry_id", "side", "part_order", "member_session_id"
        ),
        sa.UniqueConstraint(
            "world_id",
            "transition_entry_id",
            "side",
            "member_session_id",
            name="uq_transition_parts_side_member",
        ),
        sa.UniqueConstraint(
            "world_id",
            "transition_entry_id",
            "side",
            "part_order",
            "member_order",
            name="uq_transition_parts_order",
        ),
    )
    op.create_index(
        "ix_transition_parts_line",
        "event_session_transition_parts",
        ["world_id", "root_session_id", "topology_version"],
    )


def _create_event_triggers() -> None:
    for table in (
        "event_entries",
        "event_entry_links",
        "event_entry_recipients",
        "event_session_transition_parts",
    ):
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
    _create_shared_validation_triggers(m4=True)


def _create_m3_event_triggers() -> None:
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
    _create_shared_validation_triggers(m4=False)


def _create_shared_validation_triggers(*, m4: bool) -> None:
    op.execute(
        sa.text(
            "CREATE TRIGGER event_entry_links_require_earlier "
            "BEFORE INSERT ON event_entry_links BEGIN "
            "SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM event_entries current_entry "
            "JOIN event_entries related_entry ON related_entry.world_id = NEW.world_id "
            "AND related_entry.entry_id = NEW.related_entry_id "
            "WHERE current_entry.world_id = NEW.world_id "
            "AND current_entry.entry_id = NEW.entry_id "
            "AND (related_entry.world_version < current_entry.world_version OR "
            "(related_entry.world_version = current_entry.world_version "
            "AND related_entry.entry_index < current_entry.entry_index))) "
            "THEN RAISE(ABORT, 'EventEntry link must point to an earlier entry') END; END"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER interaction_requests_validate_insert "
            "BEFORE INSERT ON interaction_requests BEGIN "
            "SELECT CASE WHEN NEW.status <> 'pending' OR NEW.resolution_entry_id IS NOT NULL "
            + ("OR NEW.cancellation_entry_id IS NOT NULL " if m4 else "")
            + "THEN RAISE(ABORT, 'InteractionRequest must start pending') END; "
            "SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM event_entries source_entry "
            "WHERE source_entry.world_id = NEW.world_id "
            "AND source_entry.entry_id = NEW.request_entry_id "
            "AND source_entry.entry_kind = 'dialogue' "
            "AND source_entry.actor_agent_id = NEW.requester_agent_id "
            "AND source_entry.target_agent_id = NEW.recipient_agent_id "
            "AND source_entry.world_version = NEW.updated_world_version) "
            "THEN RAISE(ABORT, 'InteractionRequest does not match its source entry') END; END"
        )
    )
    op.execute(sa.text(_M4_REQUEST_UPDATE_TRIGGER if m4 else _M3_REQUEST_UPDATE_TRIGGER))
    op.execute(
        sa.text(
            "CREATE TRIGGER interaction_requests_no_delete BEFORE DELETE ON interaction_requests "
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
        "event_session_transition_parts_no_update",
        "event_session_transition_parts_no_delete",
        "event_entry_links_require_earlier",
        "interaction_requests_validate_insert",
        "interaction_requests_validate_resolution",
        "interaction_requests_no_delete",
    ):
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS {trigger}"))


def _require_m3_compatible_data() -> None:
    connection = op.get_bind()
    checks = (
        "SELECT count(*) FROM event_entries WHERE entry_kind NOT IN ('dialogue', 'action')",
        "SELECT count(*) FROM interaction_requests WHERE status = 'cancelled' "
        "OR priority_consumed_dispatch_count IS NOT NULL",
        "SELECT count(*) FROM event_session_transition_parts",
        "SELECT count(*) FROM worlds WHERE dispatch_count <> 0 OR dispatch_limit_at <> 0 "
        "OR active_dispatch_count IS NOT NULL OR active_dispatch_agent_id IS NOT NULL "
        "OR run_owner_id IS NOT NULL OR stop_reason IS NOT NULL "
        "OR stopped_at_world_version IS NOT NULL",
        "SELECT count(*) FROM event_sessions WHERE last_dispatch_count <> 0 "
        "OR wait_for_visible_entry_after_version IS NOT NULL "
        "OR consecutive_dialogue_turns <> 0",
    )
    if any(connection.scalar(sa.text(statement)) for statement in checks):
        raise RuntimeError("cannot downgrade a database containing M4 runtime state")


_M3_ENTRY_TARGET_CHECK = (
    "(entry_kind = 'dialogue' AND target_agent_id IS NOT NULL "
    "AND target_object_id IS NULL AND operation_id IS NULL) OR "
    "(entry_kind = 'action' AND target_agent_id IS NULL "
    "AND target_object_id IS NOT NULL AND operation_id IS NOT NULL)"
)
_M4_ENTRY_TARGET_CHECK = (
    "(entry_kind = 'dialogue' AND target_agent_id IS NOT NULL "
    "AND target_object_id IS NULL AND operation_id IS NULL AND transition_reason IS NULL) OR "
    "(entry_kind = 'action' AND target_agent_id IS NULL "
    "AND target_object_id IS NOT NULL AND operation_id IS NOT NULL "
    "AND transition_reason IS NULL) OR "
    "(entry_kind = 'behavior' AND target_agent_id IS NULL "
    "AND target_object_id IS NULL AND operation_id IS NOT NULL "
    "AND transition_reason IS NULL) OR "
    "(entry_kind = 'session_transition' AND target_object_id IS NULL "
    "AND operation_id IS NULL AND ((transition_reason = 'split' "
    "AND target_agent_id IS NULL) OR (transition_reason IN ('merge', 'transfer') "
    "AND target_agent_id IS NOT NULL)))"
)
_M3_ENTRY_DELIVERY_CHECK = (
    "(entry_kind = 'dialogue' AND delivery_channel = 'direct' "
    "AND audience_mode = 'session') OR "
    "(entry_kind = 'dialogue' AND delivery_channel = 'whisper' "
    "AND audience_mode = 'explicit') OR "
    "(entry_kind = 'action' AND delivery_channel = 'public' AND audience_mode = 'session')"
)
_M4_ENTRY_DELIVERY_CHECK = (
    _M3_ENTRY_DELIVERY_CHECK + " OR (entry_kind = 'behavior' AND delivery_channel = 'public' "
    "AND audience_mode = 'session') OR "
    "(entry_kind = 'session_transition' AND delivery_channel = 'public' "
    "AND audience_mode = 'explicit')"
)
_M3_REQUEST_UPDATE_TRIGGER = (
    "CREATE TRIGGER interaction_requests_validate_resolution "
    "BEFORE UPDATE ON interaction_requests BEGIN "
    "SELECT CASE WHEN OLD.world_id <> NEW.world_id OR OLD.request_entry_id <> NEW.request_entry_id "
    "OR OLD.request_kind <> NEW.request_kind OR OLD.requester_agent_id <> NEW.requester_agent_id "
    "OR OLD.recipient_agent_id <> NEW.recipient_agent_id OR OLD.status <> 'pending' "
    "OR NEW.status <> 'resolved' OR OLD.resolution_entry_id IS NOT NULL "
    "OR NEW.resolution_entry_id IS NULL OR NEW.updated_world_version < OLD.updated_world_version "
    "THEN RAISE(ABORT, 'invalid InteractionRequest transition') END; "
    "SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM event_entries resolution_entry "
    "JOIN event_entry_links reply_link ON reply_link.world_id = NEW.world_id "
    "AND reply_link.entry_id = NEW.resolution_entry_id AND reply_link.relation_kind = 'reply' "
    "AND reply_link.related_entry_id = NEW.request_entry_id "
    "WHERE resolution_entry.world_id = NEW.world_id "
    "AND resolution_entry.entry_id = NEW.resolution_entry_id "
    "AND resolution_entry.entry_kind = 'dialogue' "
    "AND resolution_entry.actor_agent_id = NEW.recipient_agent_id "
    "AND resolution_entry.target_agent_id = NEW.requester_agent_id "
    "AND resolution_entry.world_version = NEW.updated_world_version) "
    "THEN RAISE(ABORT, 'InteractionRequest resolution is not a valid reply') END; END"
)
_M4_REQUEST_UPDATE_TRIGGER = (
    "CREATE TRIGGER interaction_requests_validate_resolution "
    "BEFORE UPDATE ON interaction_requests BEGIN "
    "SELECT CASE WHEN OLD.world_id <> NEW.world_id OR OLD.request_entry_id <> NEW.request_entry_id "
    "OR OLD.request_kind <> NEW.request_kind OR OLD.requester_agent_id <> NEW.requester_agent_id "
    "OR OLD.recipient_agent_id <> NEW.recipient_agent_id OR OLD.status <> 'pending' OR NOT ("
    "(NEW.status = 'pending' AND OLD.priority_consumed_dispatch_count IS NULL "
    "AND NEW.priority_consumed_dispatch_count IS NOT NULL "
    "AND NEW.resolution_entry_id IS NULL AND NEW.cancellation_entry_id IS NULL "
    "AND NEW.updated_world_version = OLD.updated_world_version) OR "
    "(NEW.status = 'resolved' AND NEW.resolution_entry_id IS NOT NULL "
    "AND NEW.cancellation_entry_id IS NULL "
    "AND NEW.priority_consumed_dispatch_count IS OLD.priority_consumed_dispatch_count "
    "AND NEW.updated_world_version >= OLD.updated_world_version) OR "
    "(NEW.status = 'cancelled' AND NEW.resolution_entry_id IS NULL "
    "AND NEW.cancellation_entry_id IS NOT NULL "
    "AND NEW.priority_consumed_dispatch_count IS OLD.priority_consumed_dispatch_count "
    "AND NEW.updated_world_version >= OLD.updated_world_version)) "
    "THEN RAISE(ABORT, 'invalid InteractionRequest transition') END; "
    "SELECT CASE WHEN NEW.status = 'resolved' AND NOT EXISTS ("
    "SELECT 1 FROM event_entries resolution_entry JOIN event_entry_links reply_link "
    "ON reply_link.world_id = NEW.world_id AND reply_link.entry_id = NEW.resolution_entry_id "
    "AND reply_link.relation_kind = 'reply' "
    "AND reply_link.related_entry_id = NEW.request_entry_id "
    "WHERE resolution_entry.world_id = NEW.world_id "
    "AND resolution_entry.entry_id = NEW.resolution_entry_id "
    "AND resolution_entry.entry_kind = 'dialogue' "
    "AND resolution_entry.actor_agent_id = NEW.recipient_agent_id "
    "AND resolution_entry.target_agent_id = NEW.requester_agent_id "
    "AND resolution_entry.world_version = NEW.updated_world_version) "
    "THEN RAISE(ABORT, 'InteractionRequest resolution is not a valid reply') END; "
    "SELECT CASE WHEN NEW.status = 'cancelled' AND NOT EXISTS ("
    "SELECT 1 FROM event_entries cancellation_entry "
    "WHERE cancellation_entry.world_id = NEW.world_id "
    "AND cancellation_entry.entry_id = NEW.cancellation_entry_id "
    "AND cancellation_entry.entry_kind = 'session_transition' "
    "AND cancellation_entry.world_version = NEW.updated_world_version) "
    "THEN RAISE(ABORT, 'InteractionRequest cancellation is not a Session transition') END; END"
)
