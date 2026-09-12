"""Committed StoryLine projection for one saved World."""

from __future__ import annotations

import json
import os
import tempfile
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, model_validator
from sqlalchemy.orm import Session

from agent_runtime.event.session import SessionPartSnapshot
from agent_runtime.model import StrictModel
from agent_runtime.sqlite import ProjectDatabase
from agent_runtime.world.contracts import (
    CommitPosition,
    DeliveryChannel,
    Identifier,
    WorldRef,
    WorldVersion,
)
from agent_runtime.world.entries import (
    ActionEntry,
    BehaviorEntry,
    DialogueEntry,
    EventEntry,
    EventEntryLink,
    SessionTransitionEntry,
    SessionTransitionReason,
)
from agent_runtime.world.entry_storage import EventEntryStore
from agent_runtime.world.state import EventSessionNode, WorldStatus
from agent_runtime.world.storage import WorldStore

EntryKind = Literal["dialogue", "action", "behavior", "session_transition"]
MemberIds = Annotated[tuple[Identifier, ...], Field(min_length=1)]


class StoryLineBuildError(RuntimeError):
    """Committed rows cannot form one complete, isolated StoryLine view."""


class StoryLineKey(StrictModel):
    root_session_id: Identifier
    topology_version: WorldVersion


class StoryLineTransitionPart(StrictModel):
    line_key: StoryLineKey
    member_agent_ids: MemberIds

    @model_validator(mode="after")
    def _validate_members(self) -> Self:
        if len(self.member_agent_ids) != len(set(self.member_agent_ids)):
            raise ValueError("transition part Agent IDs must be unique")
        return self


class StoryLineTransition(StrictModel):
    reason: SessionTransitionReason
    before_parts: Annotated[tuple[StoryLineTransitionPart, ...], Field(min_length=1)]
    after_parts: Annotated[tuple[StoryLineTransitionPart, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _validate_partition(self) -> Self:
        before_members = _partition_members(self.before_parts, "before")
        after_members = _partition_members(self.after_parts, "after")
        if before_members != after_members:
            raise ValueError("StoryLine transition must preserve the affected Agents")
        before_shape = {frozenset(part.member_agent_ids) for part in self.before_parts}
        after_shape = {frozenset(part.member_agent_ids) for part in self.after_parts}
        if before_shape == after_shape:
            raise ValueError("StoryLine transition must change the partition")
        expected_counts = {
            SessionTransitionReason.MERGE: (2, 1),
            SessionTransitionReason.SPLIT: (1, 2),
            SessionTransitionReason.TRANSFER: (2, 2),
        }[self.reason]
        if (len(self.before_parts), len(self.after_parts)) != expected_counts:
            raise ValueError(f"{self.reason.value} transition has invalid part counts")
        after_versions = {part.line_key.topology_version for part in self.after_parts}
        if len(after_versions) != 1:
            raise ValueError("transition after parts must share one topology version")
        after_version = next(iter(after_versions))
        if any(part.line_key.topology_version >= after_version for part in self.before_parts):
            raise ValueError("transition before topology must be older than after topology")
        return self


class StoryEntry(StrictModel):
    """Viewer-facing subset of one committed EventEntry and its history edges."""

    world_ref: WorldRef
    entry_id: Identifier
    entry_kind: EntryKind
    commit_position: CommitPosition
    root_session_id_at_commit: Identifier
    topology_version: WorldVersion
    actor_agent_id: Identifier
    occurred_at: AwareDatetime
    text: Identifier
    recipient_agent_ids: MemberIds
    links: tuple[EventEntryLink, ...] = ()
    target_agent_id: Identifier | None = None
    target_object_id: Identifier | None = None
    operation_id: Identifier | None = None
    delivery_channel: DeliveryChannel
    transition: StoryLineTransition | None = None

    @model_validator(mode="after")
    def _validate_entry(self) -> Self:
        recipients = self.recipient_agent_ids
        if len(recipients) != len(set(recipients)):
            raise ValueError("StoryEntry recipient Agent IDs must be unique")
        if self.actor_agent_id not in recipients:
            raise ValueError("StoryEntry recipients must include the actor")
        if self.topology_version > self.commit_position.world_version:
            raise ValueError("StoryEntry topology cannot be newer than its commit")
        relation_orders: dict[str, list[int]] = defaultdict(list)
        seen_links: set[tuple[str, str]] = set()
        for link in self.links:
            if link.world_ref != self.world_ref or link.entry_id != self.entry_id:
                raise ValueError("StoryEntry links must belong to the same Entry and World")
            key = (link.relation_kind.value, link.related_entry_id)
            if key in seen_links:
                raise ValueError("StoryEntry links must be unique")
            seen_links.add(key)
            relation_orders[link.relation_kind.value].append(link.relation_order)
        for relation_kind, orders in relation_orders.items():
            if sorted(orders) != list(range(len(orders))):
                raise ValueError(f"{relation_kind} relation orders must be contiguous from zero")

        if self.entry_kind == "dialogue":
            if (
                self.target_agent_id is None
                or self.target_object_id is not None
                or self.operation_id is not None
                or self.transition is not None
                or self.delivery_channel not in {DeliveryChannel.DIRECT, DeliveryChannel.WHISPER}
            ):
                raise ValueError("dialogue StoryEntry has invalid target or delivery fields")
        elif self.entry_kind == "action":
            if (
                self.target_agent_id is not None
                or self.target_object_id is None
                or self.operation_id is None
                or self.transition is not None
                or self.delivery_channel is not DeliveryChannel.PUBLIC
            ):
                raise ValueError("action StoryEntry has invalid operation or delivery fields")
        elif self.entry_kind == "behavior":
            if (
                self.target_agent_id is not None
                or self.target_object_id is not None
                or self.operation_id is None
                or self.transition is not None
                or self.delivery_channel is not DeliveryChannel.PUBLIC
            ):
                raise ValueError("behavior StoryEntry has invalid operation or delivery fields")
        elif (
            self.target_object_id is not None
            or self.operation_id is not None
            or self.transition is None
            or self.delivery_channel is not DeliveryChannel.PUBLIC
        ):
            raise ValueError("session transition StoryEntry has invalid transition fields")
        elif self.transition.reason is SessionTransitionReason.SPLIT:
            if self.target_agent_id is not None:
                raise ValueError("split StoryEntry cannot carry a target Agent")
        elif self.target_agent_id is None:
            raise ValueError("merge or transfer StoryEntry requires a target Agent")
        return self

    @property
    def line_key(self) -> StoryLineKey:
        return StoryLineKey(
            root_session_id=self.root_session_id_at_commit,
            topology_version=self.topology_version,
        )


class StoryLine(StrictModel):
    key: StoryLineKey
    member_agent_ids: MemberIds
    parent_line_keys: tuple[StoryLineKey, ...] = ()
    child_line_keys: tuple[StoryLineKey, ...] = ()
    entries: tuple[StoryEntry, ...] = ()

    @model_validator(mode="after")
    def _validate_line(self) -> Self:
        if len(self.member_agent_ids) != len(set(self.member_agent_ids)):
            raise ValueError("StoryLine member Agent IDs must be unique")
        for label, keys in (
            ("parent", self.parent_line_keys),
            ("child", self.child_line_keys),
        ):
            if len(keys) != len(set(keys)):
                raise ValueError(f"StoryLine {label} keys must be unique")
            if self.key in keys:
                raise ValueError(f"StoryLine cannot be its own {label}")
        previous: CommitPosition | None = None
        members = set(self.member_agent_ids)
        for entry in self.entries:
            if entry.line_key != self.key:
                raise ValueError("StoryEntry belongs to a different StoryLine")
            if entry.actor_agent_id not in members:
                raise ValueError("StoryEntry actor must belong to its StoryLine")
            if entry.transition is None:
                if entry.target_agent_id is not None and entry.target_agent_id not in members:
                    raise ValueError("dialogue target must belong to its StoryLine")
                expected_recipients = (
                    {entry.actor_agent_id, entry.target_agent_id}
                    if entry.delivery_channel is DeliveryChannel.WHISPER
                    else members
                )
                if set(entry.recipient_agent_ids) != expected_recipients:
                    raise ValueError(
                        "ordinary StoryEntry recipients do not match StoryLine visibility"
                    )
            if previous is not None and _position(entry.commit_position) <= _position(previous):
                raise ValueError("StoryLine Entries must be in increasing commit order")
            previous = entry.commit_position
        return self


class StageView(StrictModel):
    """Deterministic public projection consumed by the M4 Viewer."""

    format_version: Literal[1] = 1
    world_ref: WorldRef
    built_through: CommitPosition | None
    status: WorldStatus
    story_lines: Annotated[tuple[StoryLine, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _validate_graph(self) -> Self:
        line_by_key = {line.key: line for line in self.story_lines}
        if len(line_by_key) != len(self.story_lines):
            raise ValueError("StageView StoryLine keys must be unique")

        declared_edges: set[tuple[StoryLineKey, StoryLineKey]] = set()
        entries: list[StoryEntry] = []
        for line in self.story_lines:
            for parent_key in line.parent_line_keys:
                parent = line_by_key.get(parent_key)
                if parent is None:
                    raise ValueError("StoryLine references an unknown parent")
                if line.key not in parent.child_line_keys:
                    raise ValueError("StoryLine parent/child links must be reciprocal")
                if not set(parent.member_agent_ids).intersection(line.member_agent_ids):
                    raise ValueError("StoryLine parent and child must share an Agent")
                if parent.key.topology_version >= line.key.topology_version:
                    raise ValueError("StoryLine child topology must be newer than its parent")
                declared_edges.add((parent.key, line.key))
            for child_key in line.child_line_keys:
                child = line_by_key.get(child_key)
                if child is None or line.key not in child.parent_line_keys:
                    raise ValueError("StoryLine child/parent links must be reciprocal")
            entries.extend(line.entries)

        entry_by_id = {entry.entry_id: entry for entry in entries}
        if len(entry_by_id) != len(entries):
            raise ValueError("StageView Entry IDs must be unique")
        positions = [_position(entry.commit_position) for entry in entries]
        if len(set(positions)) != len(positions):
            raise ValueError("StageView commit positions must be unique")
        if not entries:
            if self.built_through is not None:
                raise ValueError("builtThrough must be empty without committed Entries")
        else:
            if self.built_through is None or _position(self.built_through) != max(positions):
                raise ValueError("builtThrough must equal the latest committed Entry")

        transition_edges: set[tuple[StoryLineKey, StoryLineKey]] = set()
        for entry in entries:
            if entry.world_ref != self.world_ref:
                raise ValueError("StoryEntry belongs to another WorldRef")
            for link in entry.links:
                related = entry_by_id.get(link.related_entry_id)
                if related is None:
                    raise ValueError("StoryEntry link references an Entry outside the StageView")
                if _position(related.commit_position) >= _position(entry.commit_position):
                    raise ValueError("StoryEntry link must reference an earlier commit")
            if entry.transition is None:
                continue
            parts = (*entry.transition.before_parts, *entry.transition.after_parts)
            for part in parts:
                line = line_by_key.get(part.line_key)
                if line is None:
                    raise ValueError("transition part references an unknown StoryLine")
                if set(part.member_agent_ids) != set(line.member_agent_ids):
                    raise ValueError("transition part members do not match their StoryLine")
            actor_after = next(
                (part for part in entry.transition.after_parts if part.line_key == entry.line_key),
                None,
            )
            if actor_after is None or entry.actor_agent_id not in actor_after.member_agent_ids:
                raise ValueError("transition Entry must belong to its actor's after StoryLine")
            after_versions = {
                part.line_key.topology_version for part in entry.transition.after_parts
            }
            if after_versions != {entry.commit_position.world_version}:
                raise ValueError("transition after topology must equal its commit World version")
            if any(
                part.line_key.topology_version >= entry.commit_position.world_version
                for part in entry.transition.before_parts
            ):
                raise ValueError("transition before topology must be older than its commit")
            affected_agents = {
                agent_id
                for part in entry.transition.before_parts
                for agent_id in part.member_agent_ids
            }
            if set(entry.recipient_agent_ids) != affected_agents:
                raise ValueError("transition recipients must equal all affected Agents")
            if (
                entry.target_agent_id is not None
                and entry.target_agent_id not in actor_after.member_agent_ids
            ):
                raise ValueError("transition target must belong to the actor's after StoryLine")
            for before in entry.transition.before_parts:
                for after in entry.transition.after_parts:
                    if set(before.member_agent_ids).intersection(after.member_agent_ids):
                        transition_edges.add((before.line_key, after.line_key))
        if declared_edges != transition_edges:
            raise ValueError("StoryLine lineage must exactly match committed transitions")
        return self

    def to_canonical_json(self) -> str:
        payload = self.model_dump(mode="json", by_alias=True, exclude_none=True)
        if self.built_through is None:
            payload["builtThrough"] = None
        return (
            json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                indent=2,
            )
            + "\n"
        )


class StoryLineBuilder:
    """Build one committed StageView without opening or committing transactions."""

    def __init__(self, world_ref: WorldRef) -> None:
        self._world_ref = WorldRef.model_validate(world_ref, strict=True)

    @property
    def world_ref(self) -> WorldRef:
        return self._world_ref

    def build(
        self,
        session: Session,
        *,
        as_of: CommitPosition | None = None,
    ) -> StageView:
        """Read one latest or historical graph through the caller's transaction.

        A cutoff does not invent historical values for mutable World status.
        """

        world = WorldStore(self._world_ref).load(session)
        cutoff = None if as_of is None else CommitPosition.model_validate(as_of, strict=True)
        if cutoff is not None and _position(cutoff) > (world.world.current_version, 0):
            raise ValueError("as_of cannot be newer than the current World position")

        store = EventEntryStore(self._world_ref)
        all_entries = store.list_all(session)
        entries = tuple(
            entry
            for entry in all_entries
            if cutoff is None or _position(entry.commit_position) <= _position(cutoff)
        )
        entry_ids = tuple(entry.entry_id for entry in entries)
        recipients = store.recipients_for(session, entry_ids)
        links_by_entry: dict[str, list[EventEntryLink]] = defaultdict(list)
        for link in store.links_for(session, entry_ids):
            links_by_entry[link.entry_id].append(link)
        transition_plans = {
            entry.entry_id: store.transition_for(session, entry)
            for entry in all_entries
            if isinstance(entry, SessionTransitionEntry)
        }

        session_to_agent = {node.session_id: node.agent_id for node in world.sessions}
        frontier_by_session = {
            node.session_id: StoryLineKey(
                root_session_id=node.root_session_id,
                topology_version=node.topology_version,
            )
            for node in world.sessions
        }
        if cutoff is not None:
            for entry in reversed(all_entries):
                if _position(entry.commit_position) <= _position(cutoff):
                    break
                if not isinstance(entry, SessionTransitionEntry):
                    continue
                transition = transition_plans[entry.entry_id]
                for part in transition.after_parts:
                    key = StoryLineKey(
                        root_session_id=part.root_session_id,
                        topology_version=part.topology_version,
                    )
                    current_members = {
                        session_id
                        for session_id, current_key in frontier_by_session.items()
                        if current_key == key
                    }
                    if current_members != set(part.member_session_ids):
                        raise StoryLineBuildError(
                            f'future transition "{entry.entry_id}" does not match its frontier'
                        )
                for part in transition.before_parts:
                    key = StoryLineKey(
                        root_session_id=part.root_session_id,
                        topology_version=part.topology_version,
                    )
                    for session_id in part.member_session_ids:
                        if session_id not in frontier_by_session:
                            raise StoryLineBuildError(
                                f'transition references unknown stable Session node "{session_id}"'
                            )
                        frontier_by_session[session_id] = key

        members_by_line: dict[StoryLineKey, tuple[str, ...]] = {}
        current_parts: dict[StoryLineKey, list[EventSessionNode]] = defaultdict(list)
        for node in world.sessions:
            current_parts[frontier_by_session[node.session_id]].append(node)
        for key, nodes in current_parts.items():
            _remember_members(
                members_by_line,
                key,
                tuple(sorted(node.agent_id for node in nodes)),
            )

        transition_views: dict[str, StoryLineTransition] = {}
        lineage_edges: set[tuple[StoryLineKey, StoryLineKey]] = set()
        for entry in entries:
            if not isinstance(entry, SessionTransitionEntry):
                continue
            entry_id = entry.entry_id
            transition = transition_plans[entry_id]
            before = tuple(
                _transition_part(part, session_to_agent) for part in transition.before_parts
            )
            after = tuple(
                _transition_part(part, session_to_agent) for part in transition.after_parts
            )
            transition_view = StoryLineTransition(
                reason=transition.reason,
                before_parts=before,
                after_parts=after,
            )
            transition_views[entry_id] = transition_view
            for part in (*before, *after):
                _remember_members(members_by_line, part.line_key, part.member_agent_ids)
            for before_part in before:
                for after_part in after:
                    if set(before_part.member_agent_ids).intersection(after_part.member_agent_ids):
                        lineage_edges.add((before_part.line_key, after_part.line_key))

        story_entries: dict[StoryLineKey, list[StoryEntry]] = defaultdict(list)
        for entry in entries:
            key = StoryLineKey(
                root_session_id=entry.root_session_id_at_commit,
                topology_version=entry.topology_version,
            )
            if key not in members_by_line:
                raise StoryLineBuildError(
                    f'Entry "{entry.entry_id}" has no current or transition membership snapshot'
                )
            recipient_ids = recipients.get(entry.entry_id, ())
            if not recipient_ids:
                raise StoryLineBuildError(f'Entry "{entry.entry_id}" has no recipients')
            story_entries[key].append(
                _story_entry(
                    entry,
                    recipient_ids=recipient_ids,
                    links=tuple(links_by_entry[entry.entry_id]),
                    transition=transition_views.get(entry.entry_id),
                )
            )

        parents: dict[StoryLineKey, set[StoryLineKey]] = defaultdict(set)
        children: dict[StoryLineKey, set[StoryLineKey]] = defaultdict(set)
        for parent, child in lineage_edges:
            parents[child].add(parent)
            children[parent].add(child)
        ordered_keys = sorted(
            members_by_line,
            key=lambda item: (item.topology_version, item.root_session_id),
        )
        lines = tuple(
            StoryLine(
                key=key,
                member_agent_ids=members_by_line[key],
                parent_line_keys=tuple(sorted(parents[key], key=_line_order)),
                child_line_keys=tuple(sorted(children[key], key=_line_order)),
                entries=tuple(story_entries[key]),
            )
            for key in ordered_keys
        )
        return StageView(
            world_ref=self._world_ref,
            built_through=entries[-1].commit_position if entries else None,
            status=world.world.status,
            story_lines=lines,
        )


def export_storyline(
    database: ProjectDatabase,
    world_ref: WorldRef,
    output_path: Path,
) -> None:
    """Export one transactionally consistent StageView as canonical JSON."""

    validated_world_ref = WorldRef.model_validate(world_ref, strict=True)
    target = Path(output_path)
    if _same_existing_file(target, database.path):
        raise ValueError("StoryLine output cannot replace its Project database")
    with database.session_factory() as session:
        stage_view = StoryLineBuilder(validated_world_ref).build(session)
    _write_private_atomic(target, stage_view.to_canonical_json())


def _story_entry(
    entry: EventEntry,
    *,
    recipient_ids: tuple[str, ...],
    links: tuple[EventEntryLink, ...],
    transition: StoryLineTransition | None,
) -> StoryEntry:
    target_agent_id = (
        entry.target_agent_id
        if isinstance(entry, (DialogueEntry, SessionTransitionEntry))
        else None
    )
    target_object_id = entry.target_object_id if isinstance(entry, ActionEntry) else None
    operation_id = entry.operation_id if isinstance(entry, (ActionEntry, BehaviorEntry)) else None
    return StoryEntry(
        world_ref=entry.world_ref,
        entry_id=entry.entry_id,
        entry_kind=entry.entry_kind,
        commit_position=entry.commit_position,
        root_session_id_at_commit=entry.root_session_id_at_commit,
        topology_version=entry.topology_version,
        actor_agent_id=entry.actor_agent_id,
        occurred_at=entry.occurred_at,
        text=entry.text,
        recipient_agent_ids=recipient_ids,
        links=links,
        target_agent_id=target_agent_id,
        target_object_id=target_object_id,
        operation_id=operation_id,
        delivery_channel=DeliveryChannel(entry.delivery_channel),
        transition=transition,
    )


def _transition_part(
    part: SessionPartSnapshot,
    session_to_agent: dict[str, str],
) -> StoryLineTransitionPart:
    try:
        members = tuple(
            sorted(session_to_agent[session_id] for session_id in part.member_session_ids)
        )
    except KeyError as error:
        raise StoryLineBuildError(
            f'transition references unknown stable Session node "{error.args[0]}"'
        ) from error
    return StoryLineTransitionPart(
        line_key=StoryLineKey(
            root_session_id=part.root_session_id,
            topology_version=part.topology_version,
        ),
        member_agent_ids=members,
    )


def _remember_members(
    result: dict[StoryLineKey, tuple[str, ...]],
    key: StoryLineKey,
    members: tuple[str, ...],
) -> None:
    existing = result.setdefault(key, members)
    if existing != members:
        raise StoryLineBuildError(
            f"StoryLine {key.root_session_id}@{key.topology_version} has conflicting members"
        )


def _partition_members(
    parts: Iterable[StoryLineTransitionPart],
    side: str,
) -> frozenset[str]:
    result: set[str] = set()
    keys: set[StoryLineKey] = set()
    for part in parts:
        if part.line_key in keys:
            raise ValueError(f"{side} transition StoryLine keys must be unique")
        members = set(part.member_agent_ids)
        if not result.isdisjoint(members):
            raise ValueError(f"{side} transition parts must not overlap")
        keys.add(part.line_key)
        result.update(members)
    return frozenset(result)


def _position(value: CommitPosition) -> tuple[int, int]:
    return value.world_version, value.entry_index


def _same_existing_file(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(left, right)
    except FileNotFoundError:
        return False


def _write_private_atomic(target: Path, content: str) -> None:
    """Publish a complete private artifact without following a target symlink."""

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    temporary_path = Path(temporary_name)
    stream = None
    try:
        os.fchmod(descriptor, 0o600)
        stream = os.fdopen(descriptor, "w", encoding="utf-8")
        descriptor = -1
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
        stream.close()
        stream = None
        os.replace(temporary_path, target)
        target.chmod(0o600)
    finally:
        if stream is not None:
            stream.close()
        if descriptor >= 0:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)


def _line_order(value: StoryLineKey) -> tuple[int, str]:
    return value.topology_version, value.root_session_id
