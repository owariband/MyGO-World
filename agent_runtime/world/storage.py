"""World-owned relational rows and WorldRef-bound persistence."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from agent_runtime.sqlite import (
    Base,
    ProjectDatabaseIdentityError,
    require_session_project,
)

if TYPE_CHECKING:
    from agent_runtime.event.session import SessionTransitionPlan
from agent_runtime.world.contracts import WorldRef
from agent_runtime.world.state import (
    AgentWorldState,
    EventSessionNode,
    LocationState,
    ObjectState,
    PublicWorldState,
    WorldFact,
    WorldState,
    WorldStatus,
)


class WorldStorageError(RuntimeError):
    """Base error for one World-bound relational operation."""


class WorldAlreadyExistsError(WorldStorageError):
    """The requested World ID already has persisted state."""


class WorldNotFoundError(WorldStorageError):
    """The requested World ID has no persisted state."""


class WorldOwnershipError(WorldStorageError):
    """Input state does not belong to the Store's bound WorldRef."""


class WorldCommitConflictError(WorldStorageError):
    """The World or Object no longer matches the state read for one decision."""


class ProjectDatabaseRow(Base):
    __tablename__ = "project_database"
    __table_args__ = (
        CheckConstraint("singleton_id = 1", name="ck_project_database_singleton"),
        UniqueConstraint("project_id", name="uq_project_database_project"),
    )

    singleton_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class WorldRow(Base):
    __tablename__ = "worlds"
    __table_args__ = (
        CheckConstraint("seed_version >= 1", name="ck_worlds_seed_version"),
        CheckConstraint(
            "length(seed_hash) = 64 AND seed_hash NOT GLOB '*[^0-9a-f]*'",
            name="ck_worlds_seed_hash",
        ),
        CheckConstraint("current_version >= 1", name="ck_worlds_current_version"),
        CheckConstraint("status IN ('paused', 'running', 'ended')", name="ck_worlds_status"),
        CheckConstraint("control_epoch >= 1", name="ck_worlds_control_epoch"),
        CheckConstraint("decision_seq >= 0", name="ck_worlds_decision_seq"),
        CheckConstraint("dispatch_count >= 0", name="ck_worlds_dispatch_count"),
        CheckConstraint(
            "dispatch_limit_at >= dispatch_count",
            name="ck_worlds_dispatch_limit",
        ),
        CheckConstraint(
            "(active_dispatch_count IS NULL AND active_dispatch_agent_id IS NULL) OR "
            "(active_dispatch_count IS NOT NULL AND active_dispatch_agent_id IS NOT NULL)",
            name="ck_worlds_active_dispatch_pair",
        ),
        CheckConstraint(
            "active_dispatch_count IS NULL OR active_dispatch_count <= dispatch_count",
            name="ck_worlds_active_dispatch_count",
        ),
        CheckConstraint(
            "active_dispatch_count IS NULL OR (status = 'running' AND run_owner_id IS NOT NULL)",
            name="ck_worlds_active_dispatch_running",
        ),
        CheckConstraint(
            "stopped_at_world_version IS NULL OR stopped_at_world_version <= current_version",
            name="ck_worlds_stopped_version",
        ),
    )

    world_id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("project_database.project_id", name="fk_worlds_project_database"),
        nullable=False,
    )
    seed_id: Mapped[str] = mapped_column(Text, nullable=False)
    seed_version: Mapped[int] = mapped_column(Integer, nullable=False)
    seed_hash: Mapped[str] = mapped_column(Text, nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False)
    world_time: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    control_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    decision_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dispatch_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dispatch_limit_at: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_dispatch_count: Mapped[int | None] = mapped_column(Integer)
    active_dispatch_agent_id: Mapped[str | None] = mapped_column(Text)
    run_owner_id: Mapped[str | None] = mapped_column(Text)
    stop_reason: Mapped[str | None] = mapped_column(Text)
    stopped_at_world_version: Mapped[int | None] = mapped_column(Integer)


class LocationRow(Base):
    __tablename__ = "locations"

    world_id: Mapped[str] = mapped_column(
        ForeignKey("worlds.world_id", name="fk_locations_world"), primary_key=True
    )
    location_id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)


class AgentWorldStateRow(Base):
    __tablename__ = "agent_world_states"
    __table_args__ = (
        ForeignKeyConstraint(
            ["world_id", "location_id"],
            ["locations.world_id", "locations.location_id"],
            name="fk_agent_world_states_location",
        ),
        Index("ix_agent_world_states_location", "world_id", "location_id"),
    )

    world_id: Mapped[str] = mapped_column(
        ForeignKey("worlds.world_id", name="fk_agent_world_states_world"),
        primary_key=True,
    )
    agent_id: Mapped[str] = mapped_column(Text, primary_key=True)
    location_id: Mapped[str] = mapped_column(Text, nullable=False)
    public_status: Mapped[str | None] = mapped_column(Text)


class ObjectRow(Base):
    __tablename__ = "objects"
    __table_args__ = (
        ForeignKeyConstraint(
            ["world_id", "location_id"],
            ["locations.world_id", "locations.location_id"],
            name="fk_objects_location",
        ),
        ForeignKeyConstraint(
            ["world_id", "owner_agent_id"],
            ["agent_world_states.world_id", "agent_world_states.agent_id"],
            name="fk_objects_owner",
        ),
        Index("ix_objects_location", "world_id", "location_id"),
    )

    world_id: Mapped[str] = mapped_column(
        ForeignKey("worlds.world_id", name="fk_objects_world"), primary_key=True
    )
    object_id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    location_id: Mapped[str] = mapped_column(Text, nullable=False)
    owner_agent_id: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, nullable=False)


class WorldFactRow(Base):
    __tablename__ = "world_facts"
    __table_args__ = (
        CheckConstraint(
            "(location_id IS NOT NULL) + (agent_id IS NOT NULL) + (object_id IS NOT NULL) <= 1",
            name="ck_world_facts_one_owner",
        ),
        ForeignKeyConstraint(
            ["world_id", "location_id"],
            ["locations.world_id", "locations.location_id"],
            name="fk_world_facts_location",
        ),
        ForeignKeyConstraint(
            ["world_id", "agent_id"],
            ["agent_world_states.world_id", "agent_world_states.agent_id"],
            name="fk_world_facts_agent",
        ),
        ForeignKeyConstraint(
            ["world_id", "object_id"],
            ["objects.world_id", "objects.object_id"],
            name="fk_world_facts_object",
        ),
    )

    world_id: Mapped[str] = mapped_column(
        ForeignKey("worlds.world_id", name="fk_world_facts_world"), primary_key=True
    )
    fact_id: Mapped[str] = mapped_column(Text, primary_key=True)
    location_id: Mapped[str | None] = mapped_column(Text)
    agent_id: Mapped[str | None] = mapped_column(Text)
    object_id: Mapped[str | None] = mapped_column(Text)
    predicate: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text, nullable=False)


class EventSessionRow(Base):
    __tablename__ = "event_sessions"
    __table_args__ = (
        CheckConstraint("topology_version >= 1", name="ck_event_sessions_topology_version"),
        CheckConstraint(
            "updated_world_version >= 1",
            name="ck_event_sessions_updated_world_version",
        ),
        CheckConstraint(
            "last_dispatch_count >= 0",
            name="ck_event_sessions_last_dispatch_count",
        ),
        CheckConstraint(
            "wait_for_visible_entry_after_version IS NULL OR "
            "wait_for_visible_entry_after_version >= 1",
            name="ck_event_sessions_wait_version",
        ),
        CheckConstraint(
            "consecutive_dialogue_turns >= 0",
            name="ck_event_sessions_dialogue_turns",
        ),
        ForeignKeyConstraint(
            ["world_id", "agent_id"],
            ["agent_world_states.world_id", "agent_world_states.agent_id"],
            name="fk_event_sessions_agent",
        ),
        ForeignKeyConstraint(
            ["world_id", "root_session_id"],
            ["event_sessions.world_id", "event_sessions.session_id"],
            name="fk_event_sessions_root",
            deferrable=True,
            initially="DEFERRED",
        ),
        UniqueConstraint("world_id", "agent_id", name="uq_event_sessions_agent"),
        Index("ix_event_sessions_root", "world_id", "root_session_id"),
    )

    world_id: Mapped[str] = mapped_column(
        ForeignKey("worlds.world_id", name="fk_event_sessions_world"), primary_key=True
    )
    session_id: Mapped[str] = mapped_column(Text, primary_key=True)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False)
    root_session_id: Mapped[str] = mapped_column(Text, nullable=False)
    topology_version: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_world_version: Mapped[int] = mapped_column(Integer, nullable=False)
    last_dispatch_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    wait_for_visible_entry_after_version: Mapped[int | None] = mapped_column(Integer)
    consecutive_dialogue_turns: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class WorldStore:
    """Read and write public rows for exactly one immutable WorldRef."""

    def __init__(self, world_ref: WorldRef) -> None:
        self._world_ref = WorldRef.model_validate(world_ref, strict=True)

    @property
    def world_ref(self) -> WorldRef:
        return self._world_ref

    def exists(self, session: Session) -> bool:
        self._require_database_project(session)
        statement = select(WorldRow.world_id).where(
            WorldRow.world_id == self._world_ref.world_id,
            WorldRow.project_id == self._world_ref.project_id,
        )
        return session.scalar(statement) is not None

    def insert_initial(self, session: Session, state: PublicWorldState) -> None:
        """Stage one complete public Genesis in the caller's transaction."""

        validated = PublicWorldState.model_validate(state, strict=True)
        self._require_owner(validated.world.world_ref)
        if validated.world.current_version != 1:
            raise ValueError("an initial World must start at version 1")
        if validated.world.status is not WorldStatus.PAUSED:
            raise ValueError("an initial World must start paused")
        self._require_database_project(session)

        world = validated.world
        session.add(
            WorldRow(
                world_id=world.world_ref.world_id,
                project_id=world.world_ref.project_id,
                seed_id=world.seed_id,
                seed_version=world.seed_version,
                seed_hash=world.seed_hash,
                current_version=world.current_version,
                world_time=world.world_time.isoformat(),
                status=world.status.value,
                created_at=world.created_at.isoformat(),
                control_epoch=world.control_epoch,
                decision_seq=world.decision_seq,
                dispatch_count=world.dispatch_count,
                dispatch_limit_at=world.dispatch_limit_at,
                active_dispatch_count=world.active_dispatch_count,
                active_dispatch_agent_id=world.active_dispatch_agent_id,
                run_owner_id=world.run_owner_id,
                stop_reason=world.stop_reason,
                stopped_at_world_version=world.stopped_at_world_version,
            )
        )
        try:
            session.flush()
        except IntegrityError as error:
            raise WorldAlreadyExistsError(
                f'World "{self._world_ref.world_id}" already exists'
            ) from error
        session.add_all(
            LocationRow(
                world_id=self._world_ref.world_id,
                location_id=row.location_id,
                name=row.name,
                description=row.description,
            )
            for row in validated.locations
        )
        session.flush()
        session.add_all(
            AgentWorldStateRow(
                world_id=self._world_ref.world_id,
                agent_id=row.agent_id,
                location_id=row.location_id,
                public_status=row.public_status,
            )
            for row in validated.agents
        )
        session.flush()
        session.add_all(
            ObjectRow(
                world_id=self._world_ref.world_id,
                object_id=row.object_id,
                name=row.name,
                kind=row.kind,
                description=row.description,
                location_id=row.location_id,
                owner_agent_id=row.owner_agent_id,
                state=row.state,
            )
            for row in validated.objects
        )
        session.flush()
        session.add_all(
            WorldFactRow(
                world_id=self._world_ref.world_id,
                fact_id=row.fact_id,
                location_id=row.location_id,
                agent_id=row.agent_id,
                object_id=row.object_id,
                predicate=row.predicate,
                value=row.object,
                content=row.content,
            )
            for row in validated.facts
        )
        session.flush()
        session.add_all(
            EventSessionRow(
                world_id=self._world_ref.world_id,
                session_id=row.session_id,
                agent_id=row.agent_id,
                root_session_id=row.root_session_id,
                topology_version=row.topology_version,
                updated_world_version=row.updated_world_version,
                last_dispatch_count=row.last_dispatch_count,
                wait_for_visible_entry_after_version=row.wait_for_visible_entry_after_version,
                consecutive_dialogue_turns=row.consecutive_dialogue_turns,
            )
            for row in validated.sessions
        )
        session.flush()

    def load(self, session: Session) -> PublicWorldState:
        """Load and revalidate all current public rows for the bound World."""

        self._require_database_project(session)
        world_row = session.scalar(
            select(WorldRow).where(
                WorldRow.world_id == self._world_ref.world_id,
                WorldRow.project_id == self._world_ref.project_id,
            )
        )
        if world_row is None:
            raise WorldNotFoundError(f'World "{self._world_ref.world_id}" does not exist')

        world = WorldState(
            world_ref=self._world_ref,
            seed_id=world_row.seed_id,
            seed_version=world_row.seed_version,
            seed_hash=world_row.seed_hash,
            current_version=world_row.current_version,
            world_time=_load_datetime(world_row.world_time),
            status=WorldStatus(world_row.status),
            created_at=_load_datetime(world_row.created_at),
            control_epoch=world_row.control_epoch,
            decision_seq=world_row.decision_seq,
            dispatch_count=world_row.dispatch_count,
            dispatch_limit_at=world_row.dispatch_limit_at,
            active_dispatch_count=world_row.active_dispatch_count,
            active_dispatch_agent_id=world_row.active_dispatch_agent_id,
            run_owner_id=world_row.run_owner_id,
            stop_reason=world_row.stop_reason,
            stopped_at_world_version=world_row.stopped_at_world_version,
        )
        world_id = self._world_ref.world_id
        locations = tuple(
            LocationState(
                world_ref=self._world_ref,
                location_id=row.location_id,
                name=row.name,
                description=row.description,
            )
            for row in session.scalars(
                select(LocationRow)
                .where(LocationRow.world_id == world_id)
                .order_by(LocationRow.location_id)
            )
        )
        agents = tuple(
            AgentWorldState(
                world_ref=self._world_ref,
                agent_id=row.agent_id,
                location_id=row.location_id,
                public_status=row.public_status,
            )
            for row in session.scalars(
                select(AgentWorldStateRow)
                .where(AgentWorldStateRow.world_id == world_id)
                .order_by(AgentWorldStateRow.agent_id)
            )
        )
        objects = tuple(
            ObjectState(
                world_ref=self._world_ref,
                object_id=row.object_id,
                name=row.name,
                kind=row.kind,
                description=row.description,
                location_id=row.location_id,
                owner_agent_id=row.owner_agent_id,
                state=row.state,
            )
            for row in session.scalars(
                select(ObjectRow)
                .where(ObjectRow.world_id == world_id)
                .order_by(ObjectRow.object_id)
            )
        )
        facts = tuple(
            WorldFact(
                world_ref=self._world_ref,
                fact_id=row.fact_id,
                location_id=row.location_id,
                agent_id=row.agent_id,
                object_id=row.object_id,
                predicate=row.predicate,
                object=row.value,
                content=row.content,
            )
            for row in session.scalars(
                select(WorldFactRow)
                .where(WorldFactRow.world_id == world_id)
                .order_by(WorldFactRow.fact_id)
            )
        )
        sessions = tuple(
            EventSessionNode(
                world_ref=self._world_ref,
                session_id=row.session_id,
                agent_id=row.agent_id,
                root_session_id=row.root_session_id,
                topology_version=row.topology_version,
                updated_world_version=row.updated_world_version,
                last_dispatch_count=row.last_dispatch_count,
                wait_for_visible_entry_after_version=row.wait_for_visible_entry_after_version,
                consecutive_dialogue_turns=row.consecutive_dialogue_turns,
            )
            for row in session.scalars(
                select(EventSessionRow)
                .where(EventSessionRow.world_id == world_id)
                .order_by(EventSessionRow.session_id)
            )
        )
        return PublicWorldState(
            world=world,
            locations=locations,
            agents=agents,
            objects=objects,
            facts=facts,
            sessions=sessions,
        )

    def compare_and_advance(
        self,
        session: Session,
        *,
        expected_version: int,
        expected_control_epoch: int,
        expected_decision_seq: int,
        advances_world: bool,
        dispatch_count: int | None = None,
        run_owner_id: str | None = None,
        dispatch_agent_id: str | None = None,
    ) -> tuple[int, int]:
        """Claim one accepted decision using all public stale-work fences."""

        self._require_database_project(session)
        if isinstance(expected_version, bool) or expected_version < 1:
            raise ValueError("expected_version must be a positive integer")
        if isinstance(expected_control_epoch, bool) or expected_control_epoch < 1:
            raise ValueError("expected_control_epoch must be a positive integer")
        if isinstance(expected_decision_seq, bool) or expected_decision_seq < 0:
            raise ValueError("expected_decision_seq must be a non-negative integer")

        next_version = expected_version + int(advances_world)
        next_decision_seq = expected_decision_seq + 1
        if (
            len(
                {
                    dispatch_count is None,
                    run_owner_id is None,
                    dispatch_agent_id is None,
                }
            )
            != 1
        ):
            raise ValueError("dispatch count, owner, and Agent must be supplied together")
        conditions = [
            WorldRow.world_id == self._world_ref.world_id,
            WorldRow.project_id == self._world_ref.project_id,
            WorldRow.status == WorldStatus.RUNNING.value,
            WorldRow.control_epoch == expected_control_epoch,
            WorldRow.current_version == expected_version,
            WorldRow.decision_seq == expected_decision_seq,
        ]
        values: dict[str, object] = {
            "current_version": next_version,
            "decision_seq": next_decision_seq,
        }
        if (
            dispatch_count is not None
            and run_owner_id is not None
            and dispatch_agent_id is not None
        ):
            conditions.extend(
                (
                    WorldRow.run_owner_id == run_owner_id,
                    WorldRow.active_dispatch_count == dispatch_count,
                    WorldRow.active_dispatch_agent_id == dispatch_agent_id,
                )
            )
            values.update(
                active_dispatch_count=None,
                active_dispatch_agent_id=None,
            )
        updated_world_id = session.scalar(
            update(WorldRow).where(*conditions).values(**values).returning(WorldRow.world_id)
        )
        if updated_world_id is None:
            raise WorldCommitConflictError(
                "World is not running or the version, control epoch, or decision sequence is stale"
            )
        return next_version, next_decision_seq

    def resume(
        self,
        session: Session,
        *,
        owner_id: str,
        additional_decisions: int,
    ) -> WorldState:
        """Fence any stale process and add dispatch budget while the caller owns the OS lock."""

        self._require_database_project(session)
        if not owner_id.strip():
            raise ValueError("owner_id cannot be empty")
        if isinstance(additional_decisions, bool) or additional_decisions < 1:
            raise ValueError("additional_decisions must be positive")
        row = session.scalar(
            select(WorldRow).where(
                WorldRow.world_id == self._world_ref.world_id,
                WorldRow.project_id == self._world_ref.project_id,
            )
        )
        if row is None:
            raise WorldNotFoundError(f'World "{self._world_ref.world_id}" does not exist')
        if row.status == WorldStatus.ENDED.value:
            raise WorldCommitConflictError("an ended World cannot be resumed")
        session.execute(
            update(WorldRow)
            .where(
                WorldRow.world_id == self._world_ref.world_id,
                WorldRow.control_epoch == row.control_epoch,
            )
            .values(
                status=WorldStatus.RUNNING.value,
                control_epoch=row.control_epoch + 1,
                dispatch_limit_at=row.dispatch_limit_at + additional_decisions,
                active_dispatch_count=None,
                active_dispatch_agent_id=None,
                run_owner_id=owner_id.strip(),
                stop_reason=None,
                stopped_at_world_version=None,
            )
        )
        session.flush()
        return self.load(session).world

    def pause(
        self,
        session: Session,
        *,
        reason: str,
        expected_owner_id: str | None = None,
    ) -> WorldState:
        """Persist a complete stop boundary and invalidate any late model result."""

        self._require_database_project(session)
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("pause reason cannot be empty")
        row = session.scalar(
            select(WorldRow).where(
                WorldRow.world_id == self._world_ref.world_id,
                WorldRow.project_id == self._world_ref.project_id,
            )
        )
        if row is None:
            raise WorldNotFoundError(f'World "{self._world_ref.world_id}" does not exist')
        if row.status == WorldStatus.PAUSED.value:
            return self.load(session).world
        if row.status != WorldStatus.RUNNING.value:
            raise WorldCommitConflictError("only a running World can be paused")
        if expected_owner_id is not None and row.run_owner_id != expected_owner_id:
            raise WorldCommitConflictError("World run owner changed before pause")
        conditions = [
            WorldRow.world_id == self._world_ref.world_id,
            WorldRow.status == WorldStatus.RUNNING.value,
            WorldRow.control_epoch == row.control_epoch,
        ]
        if expected_owner_id is not None:
            conditions.append(WorldRow.run_owner_id == expected_owner_id)
        updated = session.scalar(
            update(WorldRow)
            .where(*conditions)
            .values(
                status=WorldStatus.PAUSED.value,
                control_epoch=row.control_epoch + 1,
                active_dispatch_count=None,
                active_dispatch_agent_id=None,
                run_owner_id=None,
                stop_reason=normalized_reason,
                stopped_at_world_version=row.current_version,
            )
            .returning(WorldRow.world_id)
        )
        if updated is None:
            raise WorldCommitConflictError("World changed before pause could be saved")
        session.flush()
        return self.load(session).world

    def claim_dispatch(
        self,
        session: Session,
        *,
        owner_id: str,
        expected_control_epoch: int,
        expected_dispatch_count: int,
        agent_id: str,
    ) -> int:
        """Pre-charge one unique model attempt and bind it to one stable Agent node."""

        self._require_database_project(session)
        next_count = expected_dispatch_count + 1
        claimed = session.scalar(
            update(WorldRow)
            .where(
                WorldRow.world_id == self._world_ref.world_id,
                WorldRow.project_id == self._world_ref.project_id,
                WorldRow.status == WorldStatus.RUNNING.value,
                WorldRow.run_owner_id == owner_id,
                WorldRow.control_epoch == expected_control_epoch,
                WorldRow.dispatch_count == expected_dispatch_count,
                WorldRow.dispatch_count < WorldRow.dispatch_limit_at,
                WorldRow.active_dispatch_count.is_(None),
                WorldRow.active_dispatch_agent_id.is_(None),
            )
            .values(
                dispatch_count=next_count,
                active_dispatch_count=next_count,
                active_dispatch_agent_id=agent_id,
            )
            .returning(WorldRow.world_id)
        )
        if claimed is None:
            raise WorldCommitConflictError("World dispatch fence or budget changed")
        claimed_node = session.scalar(
            update(EventSessionRow)
            .where(
                EventSessionRow.world_id == self._world_ref.world_id,
                EventSessionRow.agent_id == agent_id,
            )
            .values(
                last_dispatch_count=next_count,
                wait_for_visible_entry_after_version=None,
            )
            .returning(EventSessionRow.session_id)
        )
        if claimed_node is None:
            raise WorldCommitConflictError(f'Agent "{agent_id}" has no EventSession node')
        return next_count

    def fail_dispatch(
        self,
        session: Session,
        *,
        owner_id: str,
        control_epoch: int,
        dispatch_count: int,
        agent_id: str,
    ) -> None:
        """Release one failed active token without refunding its dispatch count."""

        self._require_database_project(session)
        cleared = session.scalar(
            update(WorldRow)
            .where(
                WorldRow.world_id == self._world_ref.world_id,
                WorldRow.status == WorldStatus.RUNNING.value,
                WorldRow.run_owner_id == owner_id,
                WorldRow.control_epoch == control_epoch,
                WorldRow.active_dispatch_count == dispatch_count,
                WorldRow.active_dispatch_agent_id == agent_id,
            )
            .values(active_dispatch_count=None, active_dispatch_agent_id=None)
            .returning(WorldRow.world_id)
        )
        if cleared is None:
            raise WorldCommitConflictError("active dispatch changed before failure cleanup")

    def apply_session_transition(
        self,
        session: Session,
        transition: SessionTransitionPlan,
    ) -> None:
        """Replace affected roots with a fully validated UnionPart transition."""

        self._require_database_project(session)
        if transition.world_ref != self._world_ref:
            raise WorldOwnershipError("Session transition belongs to another WorldRef")
        before_by_session = {
            session_id: part
            for part in transition.before_parts
            for session_id in part.member_session_ids
        }
        after_by_session = {
            session_id: part
            for part in transition.after_parts
            for session_id in part.member_session_ids
        }
        for session_id in transition.affected_session_ids:
            before = before_by_session[session_id]
            after = after_by_session[session_id]
            changed = session.scalar(
                update(EventSessionRow)
                .where(
                    EventSessionRow.world_id == self._world_ref.world_id,
                    EventSessionRow.session_id == session_id,
                    EventSessionRow.root_session_id == before.root_session_id,
                    EventSessionRow.topology_version == before.topology_version,
                )
                .values(
                    root_session_id=after.root_session_id,
                    topology_version=after.topology_version,
                    updated_world_version=after.topology_version,
                    wait_for_visible_entry_after_version=None,
                )
                .returning(EventSessionRow.session_id)
            )
            if changed is None:
                raise WorldCommitConflictError("EventSession topology changed before commit")

    def finish_session_step(
        self,
        session: Session,
        *,
        agent_id: str,
        world_version: int,
        entry_kind: str | None,
        waiting: bool,
        dispatch_count: int | None,
    ) -> None:
        """Persist wakeup and consecutive-dialogue state with the decision outcome."""

        conditions = [
            EventSessionRow.world_id == self._world_ref.world_id,
            EventSessionRow.agent_id == agent_id,
        ]
        if dispatch_count is not None:
            conditions.append(EventSessionRow.last_dispatch_count == dispatch_count)
        values: dict[str, object] = {
            "wait_for_visible_entry_after_version": world_version if waiting else None,
        }
        if entry_kind == "dialogue":
            values["consecutive_dialogue_turns"] = EventSessionRow.consecutive_dialogue_turns + 1
        elif entry_kind is not None:
            values["consecutive_dialogue_turns"] = 0
        changed = session.scalar(
            update(EventSessionRow)
            .where(*conditions)
            .values(**values)
            .returning(EventSessionRow.session_id)
        )
        if changed is None:
            raise WorldCommitConflictError("EventSession scheduler state changed before commit")

    def compare_and_set_object_state(
        self,
        session: Session,
        *,
        object_id: str,
        expected_state: str,
        new_state: str,
    ) -> None:
        """Apply one trusted Object operation against the exact state it resolved from."""

        self._require_database_project(session)
        if not object_id.strip():
            raise ValueError("object_id cannot be empty")
        if not expected_state.strip() or not new_state.strip():
            raise ValueError("Object states cannot be empty")
        updated_object_id = session.scalar(
            update(ObjectRow)
            .where(
                ObjectRow.world_id == self._world_ref.world_id,
                ObjectRow.object_id == object_id,
                ObjectRow.state == expected_state,
            )
            .values(state=new_state)
            .returning(ObjectRow.object_id)
        )
        if updated_object_id is None:
            raise WorldCommitConflictError(
                f'Object "{object_id}" does not exist or no longer has state "{expected_state}"'
            )

    def require_object_interaction_context(
        self,
        session: Session,
        *,
        agent_id: str,
        object_id: str,
    ) -> None:
        """Reject an Object operation after its actor or target moved out of reach."""

        self._require_database_project(session)
        agent_location = session.scalar(
            select(AgentWorldStateRow.location_id).where(
                AgentWorldStateRow.world_id == self._world_ref.world_id,
                AgentWorldStateRow.agent_id == agent_id,
            )
        )
        object_location = session.scalar(
            select(ObjectRow.location_id).where(
                ObjectRow.world_id == self._world_ref.world_id,
                ObjectRow.object_id == object_id,
            )
        )
        if agent_location is None or object_location is None or agent_location != object_location:
            raise WorldCommitConflictError(
                f'Agent "{agent_id}" can no longer interact with Object "{object_id}"'
            )

    def _require_owner(self, world_ref: WorldRef) -> None:
        if world_ref != self._world_ref:
            raise WorldOwnershipError(
                f"state belongs to {world_ref!r}, not Store {self._world_ref!r}"
            )

    def _require_database_project(self, session: Session) -> None:
        try:
            require_session_project(session, self._world_ref.project_id)
        except ProjectDatabaseIdentityError as error:
            raise WorldOwnershipError(
                f"Store Project {self._world_ref.project_id!r} does not match database identity"
            ) from error


def _load_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)
