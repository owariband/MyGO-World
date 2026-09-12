"""Strict public history contracts for committed World interactions."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from agent_runtime.model import StrictModel
from agent_runtime.world.contracts import (
    CommitPosition,
    DeliveryChannel,
    Identifier,
    WorldRef,
    WorldVersion,
)

RelationOrder = Annotated[int, Field(ge=0)]


class EventEntrySourceKind(StrEnum):
    CHARACTER_PROPOSAL = "character_proposal"


class AudienceMode(StrEnum):
    SESSION = "session"
    EXPLICIT = "explicit"


class EntryRelationKind(StrEnum):
    PREVIOUS = "previous"
    REPLY = "reply"
    CAUSE = "cause"


class SessionTransitionReason(StrEnum):
    MERGE = "merge"
    SPLIT = "split"
    TRANSFER = "transfer"


class InteractionRequestStatus(StrEnum):
    PENDING = "pending"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"


class _CommittedEntry(StrictModel):
    world_ref: WorldRef
    entry_id: Identifier
    status: Literal["committed"] = "committed"
    source_kind: Literal["character_proposal"] = "character_proposal"
    source_id: Identifier
    source_index: Literal[0] = 0
    commit_position: CommitPosition
    root_session_id_at_commit: Identifier
    topology_version: WorldVersion
    actor_agent_id: Identifier
    occurred_at: AwareDatetime
    text: Identifier
    created_at: AwareDatetime

    @model_validator(mode="after")
    def _validate_m3_position(self) -> Self:
        if self.commit_position.entry_index != 0:
            raise ValueError("M3 permits only entryIndex 0 for one Proposal")
        if self.topology_version > self.commit_position.world_version:
            raise ValueError("Entry topologyVersion cannot be newer than its commit position")
        return self


class DialogueEntry(_CommittedEntry):
    """One utterance or response that has already happened."""

    entry_kind: Literal["dialogue"] = "dialogue"
    target_agent_id: Identifier
    audience_mode: AudienceMode
    delivery_channel: DeliveryChannel

    @model_validator(mode="after")
    def _validate_delivery(self) -> Self:
        expected_audience = {
            DeliveryChannel.DIRECT: AudienceMode.SESSION,
            DeliveryChannel.WHISPER: AudienceMode.EXPLICIT,
        }.get(self.delivery_channel)
        if expected_audience is None:
            raise ValueError("Character dialogue must use direct or whisper delivery")
        if self.audience_mode is not expected_audience:
            raise ValueError(
                f"{self.delivery_channel.value} dialogue requires "
                f"{expected_audience.value} audience"
            )
        return self


class ActionEntry(_CommittedEntry):
    """One trusted Scenario object operation that has already taken effect."""

    entry_kind: Literal["action"] = "action"
    target_object_id: Identifier
    operation_id: Identifier
    audience_mode: Literal["session"] = "session"
    delivery_channel: Literal["public"] = "public"


class BehaviorEntry(_CommittedEntry):
    """One trusted self behavior selected from a World-issued affordance."""

    entry_kind: Literal["behavior"] = "behavior"
    operation_id: Identifier
    audience_mode: Literal["session"] = "session"
    delivery_channel: Literal["public"] = "public"


class SessionTransitionEntry(_CommittedEntry):
    """One committed merge, split, or actor transfer between EventSessions."""

    entry_kind: Literal["session_transition"] = "session_transition"
    transition_reason: SessionTransitionReason
    target_agent_id: Identifier | None = None
    audience_mode: Literal["explicit"] = "explicit"
    delivery_channel: Literal["public"] = "public"

    @model_validator(mode="after")
    def _validate_target(self) -> Self:
        if (
            self.transition_reason is SessionTransitionReason.SPLIT
            and self.target_agent_id is not None
        ):
            raise ValueError("split transition cannot carry a target Agent")
        if (
            self.transition_reason is not SessionTransitionReason.SPLIT
            and self.target_agent_id is None
        ):
            raise ValueError("merge or transfer transition requires a target Agent")
        return self


EventEntry = Annotated[
    DialogueEntry | ActionEntry | BehaviorEntry | SessionTransitionEntry,
    Field(discriminator="entry_kind"),
]


class EventEntryLink(StrictModel):
    world_ref: WorldRef
    entry_id: Identifier
    relation_kind: EntryRelationKind
    related_entry_id: Identifier
    relation_order: RelationOrder = 0

    @model_validator(mode="after")
    def _validate_distinct_entries(self) -> Self:
        if self.entry_id == self.related_entry_id:
            raise ValueError("an EventEntry cannot link to itself")
        return self


class EventEntryRecipient(StrictModel):
    world_ref: WorldRef
    entry_id: Identifier
    agent_id: Identifier


class InteractionRequest(StrictModel):
    """A committed Entry that still requires one Agent response."""

    world_ref: WorldRef
    request_entry_id: Identifier
    request_kind: Literal["response"] = "response"
    requester_agent_id: Identifier
    recipient_agent_id: Identifier
    status: InteractionRequestStatus
    resolution_entry_id: Identifier | None = None
    cancellation_entry_id: Identifier | None = None
    priority_consumed_dispatch_count: Annotated[int, Field(ge=1)] | None = None
    updated_world_version: WorldVersion

    @model_validator(mode="after")
    def _validate_resolution(self) -> Self:
        if self.requester_agent_id == self.recipient_agent_id:
            raise ValueError("an interaction request requires a different recipient")
        if self.status is InteractionRequestStatus.PENDING:
            if self.resolution_entry_id is not None or self.cancellation_entry_id is not None:
                raise ValueError(
                    "pending interaction request cannot have a resolution or cancellation Entry"
                )
        elif self.status is InteractionRequestStatus.RESOLVED:
            if self.resolution_entry_id is None or self.cancellation_entry_id is not None:
                raise ValueError("resolved interaction request requires only a resolutionEntryId")
        elif self.resolution_entry_id is not None or self.cancellation_entry_id is None:
            raise ValueError("cancelled interaction request requires only a cancellationEntryId")
        return self
