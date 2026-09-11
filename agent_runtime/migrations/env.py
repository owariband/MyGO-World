"""Alembic environment for per-Project Runtime databases."""

from __future__ import annotations

from alembic import context
from sqlalchemy import Connection, engine_from_config, event, pool

from agent_runtime.agent.memory.storage import MemoryRow
from agent_runtime.agent.personact.storage import AgentRuntimeStateRow
from agent_runtime.sqlite import Base, configure_sqlite_connection
from agent_runtime.world.entry_storage import (
    EventEntryLinkRow,
    EventEntryRecipientRow,
    EventEntryRow,
    InteractionRequestRow,
)
from agent_runtime.world.storage import (
    AgentWorldStateRow,
    EventSessionRow,
    LocationRow,
    ObjectRow,
    ProjectDatabaseRow,
    WorldFactRow,
    WorldRow,
)

config = context.config
target_metadata = Base.metadata

# Importing every mapped row above keeps Alembic autogenerate honest for later
# migrations. The tuple also makes that registration purpose explicit to linters.
_MAPPED_ROWS = (
    ProjectDatabaseRow,
    WorldRow,
    LocationRow,
    AgentWorldStateRow,
    ObjectRow,
    WorldFactRow,
    EventSessionRow,
    EventEntryRow,
    EventEntryLinkRow,
    EventEntryRecipientRow,
    InteractionRequestRow,
    AgentRuntimeStateRow,
    MemoryRow,
)


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_online_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        transactional_ddl=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        if not isinstance(supplied_connection, Connection):
            raise TypeError("Alembic connection attribute must be a SQLAlchemy Connection")
        _run_online_migrations(supplied_connection)
        return

    connectable = engine_from_config(
        config.get_section(config.config_ini_section) or {},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={"autocommit": False},
    )
    event.listen(connectable, "connect", configure_sqlite_connection)
    with connectable.begin() as connection:
        _run_online_migrations(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
