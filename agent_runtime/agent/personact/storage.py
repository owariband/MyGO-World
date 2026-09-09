"""SQLite persistence for current, private PersonAct state."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Integer, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from agent_runtime.agent.personact.state import PersonaState
from agent_runtime.sqlite import Base, require_session_project
from agent_runtime.world.contracts import WorldRef


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
    )

    world_id: Mapped[str] = mapped_column(Text, primary_key=True)
    agent_id: Mapped[str] = mapped_column(Text, primary_key=True)
    state_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    spec_digest: Mapped[str] = mapped_column(Text, nullable=False)
    persona_state_json: Mapped[str] = mapped_column(Text, nullable=False)


@dataclass(frozen=True, slots=True)
class StoredPersonaState:
    """One validated state plus its persistence revision and compiled Spec identity."""

    state_revision: int
    spec_digest: str
    state: PersonaState


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
            )
        )

    def load_all_for_bootstrap(self, session: Session) -> tuple[StoredPersonaState, ...]:
        """Restore all roles for trusted bootstrap; never expose this to an Agent."""

        require_session_project(session, self._world_ref.project_id)
        rows = session.scalars(
            select(AgentRuntimeStateRow)
            .where(AgentRuntimeStateRow.world_id == self._world_ref.world_id)
            .order_by(AgentRuntimeStateRow.agent_id)
        ).all()
        restored: list[StoredPersonaState] = []
        for row in rows:
            state = PersonaState.model_validate_json(row.persona_state_json, strict=True)
            if state.world_ref != self._world_ref or state.agent_id != row.agent_id:
                raise ValueError("persisted PersonaState ownership does not match its row")
            restored.append(
                StoredPersonaState(
                    state_revision=row.state_revision,
                    spec_digest=row.spec_digest,
                    state=state,
                )
            )
        return tuple(restored)
