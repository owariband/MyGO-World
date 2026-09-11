"""SQLite persistence for current, private PersonAct state."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Integer,
    Text,
    and_,
    or_,
    select,
    update,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from agent_runtime.agent.personact.state import (
    DecisionOutcome,
    PersonActStateUpdate,
    PersonaState,
)
from agent_runtime.sqlite import Base, require_session_project
from agent_runtime.world.contracts import CommitPosition, WorldRef


class PersonaStateConflictError(RuntimeError):
    """The private state no longer has the revision read for one decision."""


class PersonaStateNotFoundError(RuntimeError):
    """The requested Agent has no persisted private state in this World."""


class AgentRuntimeStateRow(Base):
    """Current private state for one Agent in one World."""

    __tablename__ = "agent_runtime_states"
    __table_args__ = (
        ForeignKeyConstraint(
            ["world_id", "agent_id"],
            ["agent_world_states.world_id", "agent_world_states.agent_id"],
            name="fk_agent_runtime_states_agent",
        ),
        CheckConstraint(
            "state_revision >= 1",
            name="ck_agent_runtime_states_revision",
        ),
        CheckConstraint(
            "length(spec_digest) = 64 AND spec_digest NOT GLOB '*[^0-9a-f]*'",
            name="ck_agent_runtime_states_spec_digest",
        ),
        CheckConstraint(
            "json_valid(persona_state_json)",
            name="ck_agent_runtime_states_json",
        ),
        CheckConstraint(
            "(observation_world_version IS NULL AND observation_entry_index IS NULL) OR "
            "(observation_world_version >= 1 AND observation_entry_index >= 0)",
            name="ck_agent_runtime_states_cursor",
        ),
        CheckConstraint(
            "(last_decision_id IS NULL AND last_decision_outcome IS NULL) OR "
            "(last_decision_id IS NOT NULL AND last_decision_outcome IN "
            "('applied', 'not_applied', 'wait', 'no_op'))",
            name="ck_agent_runtime_states_last_decision",
        ),
        ForeignKeyConstraint(
            ["world_id", "observation_world_version", "observation_entry_index"],
            ["event_entries.world_id", "event_entries.world_version", "event_entries.entry_index"],
            name="fk_agent_runtime_states_observation_entry",
        ),
    )

    world_id: Mapped[str] = mapped_column(Text, primary_key=True)
    agent_id: Mapped[str] = mapped_column(Text, primary_key=True)
    state_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    spec_digest: Mapped[str] = mapped_column(Text, nullable=False)
    persona_state_json: Mapped[str] = mapped_column(Text, nullable=False)
    observation_world_version: Mapped[int | None] = mapped_column(Integer)
    observation_entry_index: Mapped[int | None] = mapped_column(Integer)
    last_decision_id: Mapped[str | None] = mapped_column(Text)
    last_decision_outcome: Mapped[str | None] = mapped_column(Text)


@dataclass(frozen=True, slots=True)
class StoredPersonaState:
    """One validated state plus its persistence revision and compiled Spec identity."""

    state_revision: int
    spec_digest: str
    state: PersonaState
    observation_cursor: CommitPosition | None = None
    last_decision_id: str | None = None
    last_decision_outcome: DecisionOutcome | None = None


class PersonaStateStore:
    """Persist private Persona state for trusted Runtime assembly."""

    def __init__(self, world_ref: WorldRef) -> None:
        self._world_ref = WorldRef.model_validate(world_ref, strict=True)

    def insert(
        self,
        session: Session,
        state: PersonaState,
        spec_digest: str,
        *,
        revision: int = 1,
    ) -> None:
        """Stage one initial state in the caller's transaction."""

        require_session_project(session, self._world_ref.project_id)
        validated = PersonaState.model_validate(state, strict=True)
        if validated.world_ref != self._world_ref:
            raise ValueError("PersonaState belongs to a different WorldRef")
        if len(spec_digest) != 64 or set(spec_digest) - set("0123456789abcdef"):
            raise ValueError("spec_digest must be a lowercase SHA-256 digest")
        if isinstance(revision, bool) or revision < 1:
            raise ValueError("revision must be a positive integer")
        identity = {"world_id": self._world_ref.world_id, "agent_id": validated.agent_id}
        if session.get(AgentRuntimeStateRow, identity) is not None:
            raise ValueError(f'PersonaState for agent "{validated.agent_id}" already exists')

        session.add(
            AgentRuntimeStateRow(
                world_id=self._world_ref.world_id,
                agent_id=validated.agent_id,
                state_revision=revision,
                spec_digest=spec_digest,
                persona_state_json=validated.model_dump_json(
                    by_alias=True,
                    exclude_none=False,
                ),
                observation_world_version=None,
                observation_entry_index=None,
                last_decision_id=None,
                last_decision_outcome=None,
            )
        )

    def load(self, session: Session, agent_id: str) -> StoredPersonaState:
        """Load one Agent's current private state without exposing any other role."""

        require_session_project(session, self._world_ref.project_id)
        row = session.get(
            AgentRuntimeStateRow,
            {"world_id": self._world_ref.world_id, "agent_id": agent_id},
        )
        if row is None:
            raise PersonaStateNotFoundError(f'PersonaState for agent "{agent_id}" does not exist')
        return self._stored(row)

    def compare_and_set(
        self,
        session: Session,
        state_update: PersonActStateUpdate,
        *,
        expected_revision: int,
    ) -> StoredPersonaState:
        """Replace one private snapshot only if its read revision is still current."""

        require_session_project(session, self._world_ref.project_id)
        validated = PersonActStateUpdate.model_validate(state_update, strict=True)
        if validated.world_ref != self._world_ref:
            raise ValueError("PersonActStateUpdate belongs to a different WorldRef")
        if isinstance(expected_revision, bool) or expected_revision < 1:
            raise ValueError("expected_revision must be a positive integer")
        cursor = validated.observation_cursor
        cursor_guard = (
            and_(
                AgentRuntimeStateRow.observation_world_version.is_(None),
                AgentRuntimeStateRow.observation_entry_index.is_(None),
            )
            if cursor is None
            else or_(
                AgentRuntimeStateRow.observation_world_version.is_(None),
                AgentRuntimeStateRow.observation_world_version < cursor.world_version,
                and_(
                    AgentRuntimeStateRow.observation_world_version == cursor.world_version,
                    AgentRuntimeStateRow.observation_entry_index <= cursor.entry_index,
                ),
            )
        )
        updated_agent_id = session.scalar(
            update(AgentRuntimeStateRow)
            .where(
                AgentRuntimeStateRow.world_id == self._world_ref.world_id,
                AgentRuntimeStateRow.agent_id == validated.agent_id,
                AgentRuntimeStateRow.state_revision == expected_revision,
                cursor_guard,
            )
            .values(
                state_revision=expected_revision + 1,
                persona_state_json=validated.state.model_dump_json(
                    by_alias=True,
                    exclude_none=False,
                ),
                observation_world_version=(cursor.world_version if cursor is not None else None),
                observation_entry_index=(cursor.entry_index if cursor is not None else None),
                last_decision_id=validated.decision_id,
                last_decision_outcome=validated.outcome.value,
            )
            .returning(AgentRuntimeStateRow.agent_id)
        )
        if updated_agent_id is None:
            raise PersonaStateConflictError(
                f'PersonaState for agent "{validated.agent_id}" is missing or stale'
            )
        return self.load(session, validated.agent_id)

    def load_all_for_bootstrap(self, session: Session) -> tuple[StoredPersonaState, ...]:
        """Restore all roles for trusted bootstrap; never expose this to an Agent."""

        require_session_project(session, self._world_ref.project_id)
        rows = session.scalars(
            select(AgentRuntimeStateRow)
            .where(AgentRuntimeStateRow.world_id == self._world_ref.world_id)
            .order_by(AgentRuntimeStateRow.agent_id)
        ).all()
        return tuple(self._stored(row) for row in rows)

    def _stored(self, row: AgentRuntimeStateRow) -> StoredPersonaState:
        state = PersonaState.model_validate_json(row.persona_state_json, strict=True)
        if state.world_ref != self._world_ref or state.agent_id != row.agent_id:
            raise ValueError("persisted PersonaState ownership does not match its row")
        if (row.observation_world_version is None) != (row.observation_entry_index is None):
            raise ValueError("persisted observation cursor is incomplete")
        if (row.last_decision_id is None) != (row.last_decision_outcome is None):
            raise ValueError("persisted last decision is incomplete")
        cursor = (
            None
            if row.observation_world_version is None
            else CommitPosition(
                world_version=row.observation_world_version,
                entry_index=_required_entry_index(row.observation_entry_index),
            )
        )
        outcome = (
            None
            if row.last_decision_outcome is None
            else DecisionOutcome(row.last_decision_outcome)
        )
        return StoredPersonaState(
            state_revision=row.state_revision,
            spec_digest=row.spec_digest,
            state=state,
            observation_cursor=cursor,
            last_decision_id=row.last_decision_id,
            last_decision_outcome=outcome,
        )


def _required_entry_index(value: int | None) -> int:
    if value is None:
        raise ValueError("persisted observation cursor is incomplete")
    return value
