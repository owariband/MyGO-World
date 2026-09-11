"""WorldRef-bound SQLite persistence for committed EventEntry history."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    and_,
    or_,
    select,
    update,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from agent_runtime.sqlite import (
    Base,
    ProjectDatabaseIdentityError,
    require_session_project,
)
from agent_runtime.world.contracts import CommitPosition, DeliveryChannel, WorldRef
from agent_runtime.world.entries import (
    ActionEntry,
    AudienceMode,
    DialogueEntry,
    EntryRelationKind,
    EventEntry,
    EventEntryLink,
    EventEntryRecipient,
    EventEntrySourceKind,
    InteractionRequest,
    InteractionRequestStatus,
)
from agent_runtime.world.storage import EventSessionRow


class EventEntryStorageError(RuntimeError):
    """Base error for one World-bound EventEntry persistence operation."""


class EventEntryNotFoundError(EventEntryStorageError):
    """The requested committed Entry or request does not exist."""


class EventEntryConflictError(EventEntryStorageError):
    """A committed Entry or request changed or violates its lifecycle."""


class EventEntryOwnershipError(EventEntryStorageError):
    """Input history does not belong to the Store's bound WorldRef."""


class EventEntryRow(Base):
    __tablename__ = "event_entries"
    __table_args__ = (
        CheckConstraint("status = 'committed'", name="ck_event_entries_status"),
        CheckConstraint(
            "entry_kind IN ('dialogue', 'action')",
            name="ck_event_entries_kind",
        ),
        CheckConstraint(
            "source_kind = 'character_proposal'",
            name="ck_event_entries_source_kind",
        ),
        CheckConstraint("source_index = 0", name="ck_event_entries_source_index"),
        CheckConstraint("world_version >= 1", name="ck_event_entries_world_version"),
        CheckConstraint("entry_index = 0", name="ck_event_entries_entry_index"),
        CheckConstraint("topology_version >= 1", name="ck_event_entries_topology_version"),
        CheckConstraint("length(text) > 0", name="ck_event_entries_text"),
        CheckConstraint(
            "(entry_kind = 'dialogue' AND target_agent_id IS NOT NULL "
            "AND target_object_id IS NULL AND operation_id IS NULL) OR "
            "(entry_kind = 'action' AND target_agent_id IS NULL "
            "AND target_object_id IS NOT NULL AND operation_id IS NOT NULL)",
            name="ck_event_entries_target",
        ),
        CheckConstraint(
            "(entry_kind = 'dialogue' AND delivery_channel = 'direct' "
            "AND audience_mode = 'session') OR "
            "(entry_kind = 'dialogue' AND delivery_channel = 'whisper' "
            "AND audience_mode = 'explicit') OR "
            "(entry_kind = 'action' AND delivery_channel = 'public' "
            "AND audience_mode = 'session')",
            name="ck_event_entries_delivery",
        ),
        ForeignKeyConstraint(
            ["world_id", "root_session_id_at_commit"],
            ["event_sessions.world_id", "event_sessions.session_id"],
            name="fk_event_entries_root_session",
        ),
        ForeignKeyConstraint(
            ["world_id", "actor_agent_id"],
            ["agent_world_states.world_id", "agent_world_states.agent_id"],
            name="fk_event_entries_actor",
        ),
        ForeignKeyConstraint(
            ["world_id", "target_agent_id"],
            ["agent_world_states.world_id", "agent_world_states.agent_id"],
            name="fk_event_entries_target_agent",
        ),
        ForeignKeyConstraint(
            ["world_id", "target_object_id"],
            ["objects.world_id", "objects.object_id"],
            name="fk_event_entries_target_object",
        ),
        UniqueConstraint(
            "world_id",
            "world_version",
            "entry_index",
            name="uq_event_entries_commit_position",
        ),
        UniqueConstraint(
            "world_id",
            "source_kind",
            "source_id",
            "source_index",
            name="uq_event_entries_source",
        ),
        Index(
            "ix_event_entries_session_order",
            "world_id",
            "root_session_id_at_commit",
            "topology_version",
            "world_version",
            "entry_index",
        ),
    )

    world_id: Mapped[str] = mapped_column(
        ForeignKey("worlds.world_id", name="fk_event_entries_world"),
        primary_key=True,
    )
    entry_id: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    entry_kind: Mapped[str] = mapped_column(Text, nullable=False)
    source_kind: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_index: Mapped[int] = mapped_column(Integer, nullable=False)
    world_version: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_index: Mapped[int] = mapped_column(Integer, nullable=False)
    root_session_id_at_commit: Mapped[str] = mapped_column(Text, nullable=False)
    topology_version: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_agent_id: Mapped[str] = mapped_column(Text, nullable=False)
    target_agent_id: Mapped[str | None] = mapped_column(Text)
    target_object_id: Mapped[str | None] = mapped_column(Text)
    operation_id: Mapped[str | None] = mapped_column(Text)
    audience_mode: Mapped[str] = mapped_column(Text, nullable=False)
    delivery_channel: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class EventEntryLinkRow(Base):
    __tablename__ = "event_entry_links"
    __table_args__ = (
        CheckConstraint(
            "relation_kind IN ('previous', 'reply', 'cause')",
            name="ck_event_entry_links_kind",
        ),
        CheckConstraint("relation_order >= 0", name="ck_event_entry_links_order"),
        ForeignKeyConstraint(
            ["world_id", "entry_id"],
            ["event_entries.world_id", "event_entries.entry_id"],
            name="fk_event_entry_links_entry",
        ),
        ForeignKeyConstraint(
            ["world_id", "related_entry_id"],
            ["event_entries.world_id", "event_entries.entry_id"],
            name="fk_event_entry_links_related",
        ),
        UniqueConstraint(
            "world_id",
            "entry_id",
            "relation_kind",
            "relation_order",
            name="uq_event_entry_links_order",
        ),
        Index(
            "uq_event_entry_links_reply",
            "world_id",
            "entry_id",
            unique=True,
            sqlite_where=sql_text("relation_kind = 'reply'"),
        ),
    )

    world_id: Mapped[str] = mapped_column(Text, primary_key=True)
    entry_id: Mapped[str] = mapped_column(Text, primary_key=True)
    relation_kind: Mapped[str] = mapped_column(Text, primary_key=True)
    related_entry_id: Mapped[str] = mapped_column(Text, primary_key=True)
    relation_order: Mapped[int] = mapped_column(Integer, nullable=False)


class EventEntryRecipientRow(Base):
    __tablename__ = "event_entry_recipients"
    __table_args__ = (
        ForeignKeyConstraint(
            ["world_id", "entry_id"],
            ["event_entries.world_id", "event_entries.entry_id"],
            name="fk_event_entry_recipients_entry",
        ),
        ForeignKeyConstraint(
            ["world_id", "agent_id"],
            ["agent_world_states.world_id", "agent_world_states.agent_id"],
            name="fk_event_entry_recipients_agent",
        ),
    )

    world_id: Mapped[str] = mapped_column(Text, primary_key=True)
    entry_id: Mapped[str] = mapped_column(Text, primary_key=True)
    agent_id: Mapped[str] = mapped_column(Text, primary_key=True)


class InteractionRequestRow(Base):
    __tablename__ = "interaction_requests"
    __table_args__ = (
        CheckConstraint("request_kind = 'response'", name="ck_interaction_requests_kind"),
        CheckConstraint(
            "status IN ('pending', 'resolved')",
            name="ck_interaction_requests_status",
        ),
        CheckConstraint(
            "(status = 'pending' AND resolution_entry_id IS NULL) OR "
            "(status = 'resolved' AND resolution_entry_id IS NOT NULL)",
            name="ck_interaction_requests_resolution",
        ),
        CheckConstraint(
            "requester_agent_id <> recipient_agent_id",
            name="ck_interaction_requests_distinct_agents",
        ),
        CheckConstraint(
            "updated_world_version >= 1",
            name="ck_interaction_requests_world_version",
        ),
        ForeignKeyConstraint(
            ["world_id", "request_entry_id"],
            ["event_entries.world_id", "event_entries.entry_id"],
            name="fk_interaction_requests_request_entry",
        ),
        ForeignKeyConstraint(
            ["world_id", "requester_agent_id"],
            ["agent_world_states.world_id", "agent_world_states.agent_id"],
            name="fk_interaction_requests_requester",
        ),
        ForeignKeyConstraint(
            ["world_id", "recipient_agent_id"],
            ["agent_world_states.world_id", "agent_world_states.agent_id"],
            name="fk_interaction_requests_recipient",
        ),
        ForeignKeyConstraint(
            ["world_id", "resolution_entry_id"],
            ["event_entries.world_id", "event_entries.entry_id"],
            name="fk_interaction_requests_resolution_entry",
        ),
        Index(
            "ix_interaction_requests_pending",
            "world_id",
            "recipient_agent_id",
            "status",
            "updated_world_version",
        ),
    )

    world_id: Mapped[str] = mapped_column(Text, primary_key=True)
    request_entry_id: Mapped[str] = mapped_column(Text, primary_key=True)
    request_kind: Mapped[str] = mapped_column(Text, nullable=False)
    requester_agent_id: Mapped[str] = mapped_column(Text, nullable=False)
    recipient_agent_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    resolution_entry_id: Mapped[str | None] = mapped_column(Text)
    updated_world_version: Mapped[int] = mapped_column(Integer, nullable=False)


class EventEntryStore:
    """Append and query committed history for exactly one immutable WorldRef."""

    def __init__(self, world_ref: WorldRef) -> None:
        self._world_ref = WorldRef.model_validate(world_ref, strict=True)

    @property
    def world_ref(self) -> WorldRef:
        return self._world_ref

    def append(
        self,
        session: Session,
        entry: EventEntry,
        *,
        links: tuple[EventEntryLink, ...] = (),
        recipients: tuple[EventEntryRecipient, ...],
    ) -> None:
        """Stage one committed Entry and its immutable graph/visibility snapshot."""

        self._require_database_project(session)
        validated_entry = _validate_entry(entry)
        validated_links = tuple(EventEntryLink.model_validate(item, strict=True) for item in links)
        validated_recipients = tuple(
            EventEntryRecipient.model_validate(item, strict=True) for item in recipients
        )
        self._require_owner(validated_entry.world_ref)
        for item in (*validated_links, *validated_recipients):
            self._require_owner(item.world_ref)
            if item.entry_id != validated_entry.entry_id:
                raise ValueError("Entry links and recipients must belong to the appended Entry")
        recipient_ids = tuple(item.agent_id for item in validated_recipients)
        if len(recipient_ids) != len(set(recipient_ids)):
            raise ValueError("EventEntry recipient Agent IDs must be unique")
        self._require_recipient_snapshot(session, validated_entry, recipient_ids)

        session.add(self._entry_row(validated_entry))
        # These mapped rows deliberately have no ORM relationships. Flush the
        # parent first so SQLite sees it before the composite-FK children.
        session.flush()
        session.add_all(
            EventEntryLinkRow(
                world_id=self._world_ref.world_id,
                entry_id=item.entry_id,
                relation_kind=item.relation_kind.value,
                related_entry_id=item.related_entry_id,
                relation_order=item.relation_order,
            )
            for item in validated_links
        )
        session.add_all(
            EventEntryRecipientRow(
                world_id=self._world_ref.world_id,
                entry_id=item.entry_id,
                agent_id=item.agent_id,
            )
            for item in validated_recipients
        )

    def create_request(self, session: Session, request: InteractionRequest) -> None:
        self._require_database_project(session)
        validated = InteractionRequest.model_validate(request, strict=True)
        self._require_owner(validated.world_ref)
        if validated.status is not InteractionRequestStatus.PENDING:
            raise ValueError("an InteractionRequest must be created pending")
        session.add(
            InteractionRequestRow(
                world_id=self._world_ref.world_id,
                request_entry_id=validated.request_entry_id,
                request_kind=validated.request_kind,
                requester_agent_id=validated.requester_agent_id,
                recipient_agent_id=validated.recipient_agent_id,
                status=validated.status.value,
                resolution_entry_id=None,
                updated_world_version=validated.updated_world_version,
            )
        )

    def resolve_request(
        self,
        session: Session,
        *,
        request_entry_id: str,
        resolution_entry_id: str,
        updated_world_version: int,
    ) -> InteractionRequest:
        """Resolve one pending request; the database verifies the reply relation."""

        self._require_database_project(session)
        updated_request_entry_id = session.scalar(
            update(InteractionRequestRow)
            .where(
                InteractionRequestRow.world_id == self._world_ref.world_id,
                InteractionRequestRow.request_entry_id == request_entry_id,
                InteractionRequestRow.status == InteractionRequestStatus.PENDING.value,
            )
            .values(
                status=InteractionRequestStatus.RESOLVED.value,
                resolution_entry_id=resolution_entry_id,
                updated_world_version=updated_world_version,
            )
            .returning(InteractionRequestRow.request_entry_id)
        )
        if updated_request_entry_id is None:
            raise EventEntryConflictError(
                f'pending InteractionRequest "{request_entry_id}" does not exist'
            )
        row = session.get(
            InteractionRequestRow,
            {
                "world_id": self._world_ref.world_id,
                "request_entry_id": request_entry_id,
            },
        )
        if row is None:
            raise EventEntryConflictError(
                f'InteractionRequest "{request_entry_id}" disappeared after resolution'
            )
        return self._request(row)

    def get(self, session: Session, entry_id: str) -> EventEntry:
        self._require_database_project(session)
        row = session.get(
            EventEntryRow,
            {"world_id": self._world_ref.world_id, "entry_id": entry_id},
        )
        if row is None:
            raise EventEntryNotFoundError(f'EventEntry "{entry_id}" does not exist')
        return self._entry(row)

    def get_by_source(
        self,
        session: Session,
        *,
        source_id: str,
        source_kind: EventEntrySourceKind = EventEntrySourceKind.CHARACTER_PROPOSAL,
        source_index: int = 0,
    ) -> EventEntry | None:
        self._require_database_project(session)
        row = session.scalar(
            select(EventEntryRow).where(
                EventEntryRow.world_id == self._world_ref.world_id,
                EventEntryRow.source_kind == source_kind.value,
                EventEntryRow.source_id == source_id,
                EventEntryRow.source_index == source_index,
            )
        )
        return None if row is None else self._entry(row)

    def latest_for_root(
        self,
        session: Session,
        root_session_id: str,
    ) -> EventEntry | None:
        self._require_database_project(session)
        row = session.scalar(
            select(EventEntryRow)
            .where(
                EventEntryRow.world_id == self._world_ref.world_id,
                EventEntryRow.root_session_id_at_commit == root_session_id,
            )
            .order_by(
                EventEntryRow.world_version.desc(),
                EventEntryRow.entry_index.desc(),
            )
            .limit(1)
        )
        return None if row is None else self._entry(row)

    def list_visible_after(
        self,
        session: Session,
        agent_id: str,
        *,
        after: CommitPosition | None = None,
    ) -> tuple[EventEntry, ...]:
        """Read committed Entries whose historical recipient snapshot includes an Agent."""

        self._require_database_project(session)
        statement = (
            select(EventEntryRow)
            .join(
                EventEntryRecipientRow,
                and_(
                    EventEntryRecipientRow.world_id == EventEntryRow.world_id,
                    EventEntryRecipientRow.entry_id == EventEntryRow.entry_id,
                ),
            )
            .where(
                EventEntryRow.world_id == self._world_ref.world_id,
                EventEntryRecipientRow.agent_id == agent_id,
            )
        )
        if after is not None:
            cursor = CommitPosition.model_validate(after, strict=True)
            statement = statement.where(
                or_(
                    EventEntryRow.world_version > cursor.world_version,
                    and_(
                        EventEntryRow.world_version == cursor.world_version,
                        EventEntryRow.entry_index > cursor.entry_index,
                    ),
                )
            )
        rows = session.scalars(
            statement.order_by(EventEntryRow.world_version, EventEntryRow.entry_index)
        ).all()
        return tuple(self._entry(row) for row in rows)

    def links_for(
        self,
        session: Session,
        entry_ids: Iterable[str],
    ) -> tuple[EventEntryLink, ...]:
        """Load graph edges owned by the requested committed Entries."""

        self._require_database_project(session)
        ids = tuple(dict.fromkeys(entry_ids))
        if not ids:
            return ()
        rows = session.scalars(
            select(EventEntryLinkRow)
            .where(
                EventEntryLinkRow.world_id == self._world_ref.world_id,
                EventEntryLinkRow.entry_id.in_(ids),
            )
            .order_by(
                EventEntryLinkRow.entry_id,
                EventEntryLinkRow.relation_kind,
                EventEntryLinkRow.relation_order,
                EventEntryLinkRow.related_entry_id,
            )
        ).all()
        return tuple(
            EventEntryLink(
                world_ref=self._world_ref,
                entry_id=row.entry_id,
                relation_kind=EntryRelationKind(row.relation_kind),
                related_entry_id=row.related_entry_id,
                relation_order=row.relation_order,
            )
            for row in rows
        )

    def get_pending_request(
        self,
        session: Session,
        request_entry_id: str,
        recipient_agent_id: str,
    ) -> InteractionRequest | None:
        self._require_database_project(session)
        row = session.scalar(
            select(InteractionRequestRow).where(
                InteractionRequestRow.world_id == self._world_ref.world_id,
                InteractionRequestRow.request_entry_id == request_entry_id,
                InteractionRequestRow.recipient_agent_id == recipient_agent_id,
                InteractionRequestRow.status == InteractionRequestStatus.PENDING.value,
            )
        )
        return None if row is None else self._request(row)

    def get_request(self, session: Session, request_entry_id: str) -> InteractionRequest:
        """Load one request in either pending or resolved state."""

        self._require_database_project(session)
        row = session.get(
            InteractionRequestRow,
            {
                "world_id": self._world_ref.world_id,
                "request_entry_id": request_entry_id,
            },
        )
        if row is None:
            raise EventEntryNotFoundError(f'InteractionRequest "{request_entry_id}" does not exist')
        return self._request(row)

    def pending_requests_for(
        self,
        session: Session,
        recipient_agent_id: str,
    ) -> tuple[InteractionRequest, ...]:
        self._require_database_project(session)
        rows = session.scalars(
            select(InteractionRequestRow)
            .where(
                InteractionRequestRow.world_id == self._world_ref.world_id,
                InteractionRequestRow.recipient_agent_id == recipient_agent_id,
                InteractionRequestRow.status == InteractionRequestStatus.PENDING.value,
            )
            .order_by(
                InteractionRequestRow.updated_world_version,
                InteractionRequestRow.request_entry_id,
            )
        ).all()
        return tuple(self._request(row) for row in rows)

    def _entry_row(self, entry: EventEntry) -> EventEntryRow:
        target_agent_id = entry.target_agent_id if isinstance(entry, DialogueEntry) else None
        target_object_id = entry.target_object_id if isinstance(entry, ActionEntry) else None
        operation_id = entry.operation_id if isinstance(entry, ActionEntry) else None
        return EventEntryRow(
            world_id=self._world_ref.world_id,
            entry_id=entry.entry_id,
            status=entry.status,
            entry_kind=entry.entry_kind,
            source_kind=entry.source_kind,
            source_id=entry.source_id,
            source_index=entry.source_index,
            world_version=entry.commit_position.world_version,
            entry_index=entry.commit_position.entry_index,
            root_session_id_at_commit=entry.root_session_id_at_commit,
            topology_version=entry.topology_version,
            actor_agent_id=entry.actor_agent_id,
            target_agent_id=target_agent_id,
            target_object_id=target_object_id,
            operation_id=operation_id,
            audience_mode=(
                entry.audience_mode.value
                if isinstance(entry.audience_mode, AudienceMode)
                else entry.audience_mode
            ),
            delivery_channel=(
                entry.delivery_channel.value
                if isinstance(entry, DialogueEntry)
                else entry.delivery_channel
            ),
            occurred_at=entry.occurred_at.isoformat(),
            text=entry.text,
            created_at=entry.created_at.isoformat(),
        )

    def _entry(self, row: EventEntryRow) -> EventEntry:
        if row.entry_kind == "dialogue":
            if row.target_agent_id is None:
                raise ValueError("persisted DialogueEntry has no target Agent")
            return DialogueEntry(
                world_ref=self._world_ref,
                entry_id=row.entry_id,
                status="committed",
                source_kind="character_proposal",
                source_id=row.source_id,
                source_index=0,
                commit_position=CommitPosition(
                    world_version=row.world_version,
                    entry_index=row.entry_index,
                ),
                root_session_id_at_commit=row.root_session_id_at_commit,
                topology_version=row.topology_version,
                actor_agent_id=row.actor_agent_id,
                target_agent_id=row.target_agent_id,
                audience_mode=AudienceMode(row.audience_mode),
                delivery_channel=DeliveryChannel(row.delivery_channel),
                occurred_at=datetime.fromisoformat(row.occurred_at),
                text=row.text,
                created_at=datetime.fromisoformat(row.created_at),
            )
        if row.entry_kind == "action":
            if row.target_object_id is None or row.operation_id is None:
                raise ValueError("persisted ActionEntry has no Object operation")
            return ActionEntry(
                world_ref=self._world_ref,
                entry_id=row.entry_id,
                status="committed",
                source_kind="character_proposal",
                source_id=row.source_id,
                source_index=0,
                commit_position=CommitPosition(
                    world_version=row.world_version,
                    entry_index=row.entry_index,
                ),
                root_session_id_at_commit=row.root_session_id_at_commit,
                topology_version=row.topology_version,
                actor_agent_id=row.actor_agent_id,
                target_object_id=row.target_object_id,
                operation_id=row.operation_id,
                audience_mode="session",
                delivery_channel="public",
                occurred_at=datetime.fromisoformat(row.occurred_at),
                text=row.text,
                created_at=datetime.fromisoformat(row.created_at),
            )
        raise ValueError(f'unknown persisted EventEntry kind "{row.entry_kind}"')

    def _request(self, row: InteractionRequestRow) -> InteractionRequest:
        return InteractionRequest(
            world_ref=self._world_ref,
            request_entry_id=row.request_entry_id,
            request_kind="response",
            requester_agent_id=row.requester_agent_id,
            recipient_agent_id=row.recipient_agent_id,
            status=InteractionRequestStatus(row.status),
            resolution_entry_id=row.resolution_entry_id,
            updated_world_version=row.updated_world_version,
        )

    def _require_owner(self, world_ref: WorldRef) -> None:
        if world_ref != self._world_ref:
            raise EventEntryOwnershipError(
                f"history belongs to {world_ref!r}, not Store {self._world_ref!r}"
            )

    def _require_recipient_snapshot(
        self,
        session: Session,
        entry: EventEntry,
        recipient_ids: tuple[str, ...],
    ) -> None:
        partition = session.scalars(
            select(EventSessionRow).where(
                EventSessionRow.world_id == self._world_ref.world_id,
                EventSessionRow.root_session_id == entry.root_session_id_at_commit,
            )
        ).all()
        if not partition:
            raise ValueError("EventEntry rootSessionId does not identify a current partition")
        if {node.topology_version for node in partition} != {entry.topology_version}:
            raise ValueError("EventEntry topologyVersion does not match its current partition")

        partition_agents = {node.agent_id for node in partition}
        participants = {entry.actor_agent_id}
        if isinstance(entry, DialogueEntry):
            participants.add(entry.target_agent_id)
        if not participants.issubset(partition_agents):
            raise ValueError("Character EventEntry cannot cross an EventSession partition")
        if isinstance(entry, DialogueEntry) and entry.audience_mode is AudienceMode.EXPLICIT:
            expected = participants
        else:
            expected = partition_agents
        if set(recipient_ids) != expected:
            raise ValueError("EventEntry recipients do not match its committed audience")

    def _require_database_project(self, session: Session) -> None:
        try:
            require_session_project(session, self._world_ref.project_id)
        except ProjectDatabaseIdentityError as error:
            raise EventEntryOwnershipError(
                f"Store Project {self._world_ref.project_id!r} does not match database identity"
            ) from error


def _validate_entry(entry: EventEntry) -> EventEntry:
    if isinstance(entry, DialogueEntry):
        return DialogueEntry.model_validate(entry, strict=True)
    return ActionEntry.model_validate(entry, strict=True)
