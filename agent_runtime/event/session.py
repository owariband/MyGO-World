"""Pure EventSession partition transitions built on the generic UnionPart."""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import Field, model_validator

from agent_runtime.common.union_part import UnionPart
from agent_runtime.model import StrictModel
from agent_runtime.world.contracts import Identifier, WorldRef, WorldVersion
from agent_runtime.world.entries import SessionTransitionReason
from agent_runtime.world.state import EventSessionNode, PublicWorldState


class SessionTransitionError(ValueError):
    """A requested join or leave is invalid for the committed partition."""


class SessionPartSnapshot(StrictModel):
    """One immutable partition side recorded with a transition Entry."""

    root_session_id: Identifier
    topology_version: WorldVersion
    member_session_ids: Annotated[tuple[Identifier, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _validate_members(self) -> Self:
        if len(self.member_session_ids) != len(set(self.member_session_ids)):
            raise ValueError("Session part member IDs must be unique")
        if self.root_session_id not in self.member_session_ids:
            raise ValueError("Session part must contain its root")
        return self


class SessionTransitionPlan(StrictModel):
    """A complete replacement of only the affected current partitions."""

    world_ref: WorldRef
    actor_agent_id: Identifier
    target_agent_id: Identifier | None = None
    reason: SessionTransitionReason
    before_parts: Annotated[tuple[SessionPartSnapshot, ...], Field(min_length=1)]
    after_parts: Annotated[tuple[SessionPartSnapshot, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _validate_transition(self) -> Self:
        before_members = _covered(self.before_parts, "before")
        after_members = _covered(self.after_parts, "after")
        if before_members != after_members:
            raise ValueError("Session transition must preserve exactly the affected nodes")
        next_versions = {part.topology_version for part in self.after_parts}
        if len(next_versions) != 1:
            raise ValueError("all after parts must share one topology version")
        if max(part.topology_version for part in self.before_parts) >= next_versions.pop():
            raise ValueError("after topology version must be newer than every before part")
        if self.reason is SessionTransitionReason.SPLIT and self.target_agent_id is not None:
            raise ValueError("split transition cannot carry a target Agent")
        if self.reason is not SessionTransitionReason.SPLIT and self.target_agent_id is None:
            raise ValueError("merge or transfer transition requires a target Agent")
        return self

    @property
    def affected_session_ids(self) -> tuple[str, ...]:
        return tuple(sorted(_covered(self.before_parts, "before")))


def plan_join(
    state: PublicWorldState,
    *,
    actor_agent_id: str,
    target_agent_id: str,
) -> SessionTransitionPlan:
    """Move only the actor into the target's current part, retaining target root."""

    public = PublicWorldState.model_validate(state, strict=True)
    if actor_agent_id == target_agent_id:
        raise SessionTransitionError("an Agent cannot join itself")
    actor = _node_for_agent(public.sessions, actor_agent_id)
    target = _node_for_agent(public.sessions, target_agent_id)
    if actor.root_session_id == target.root_session_id:
        raise SessionTransitionError("actor and target already share an EventSession")

    partition = _partition(public.sessions)
    actor_members = partition.members_of(actor.root_session_id)
    target_members = partition.members_of(target.root_session_id)
    before = tuple(
        sorted(
            (
                _snapshot(public.sessions, actor.root_session_id, actor_members),
                _snapshot(public.sessions, target.root_session_id, target_members),
            ),
            key=lambda item: item.root_session_id,
        )
    )

    if len(actor_members) == 1:
        partition.merge(
            keep_root=target.root_session_id,
            merged_roots=(actor.root_session_id,),
        )
        reason = SessionTransitionReason.MERGE
        after_roots = (target.root_session_id,)
    else:
        remaining = actor_members - {actor.session_id}
        remaining_root = (
            min(remaining) if actor.session_id == actor.root_session_id else actor.root_session_id
        )
        partition.split(
            actor.root_session_id,
            {
                remaining_root: remaining,
                actor.session_id: (actor.session_id,),
            },
        )
        partition.merge(
            keep_root=target.root_session_id,
            merged_roots=(actor.session_id,),
        )
        reason = SessionTransitionReason.TRANSFER
        after_roots = (remaining_root, target.root_session_id)

    next_version = public.world.current_version + 1
    after = tuple(
        SessionPartSnapshot(
            root_session_id=root,
            topology_version=next_version,
            member_session_ids=tuple(sorted(partition.members_of(root))),
        )
        for root in sorted(after_roots)
    )
    return SessionTransitionPlan(
        world_ref=public.world.world_ref,
        actor_agent_id=actor_agent_id,
        target_agent_id=target_agent_id,
        reason=reason,
        before_parts=before,
        after_parts=after,
    )


def plan_leave(
    state: PublicWorldState,
    *,
    actor_agent_id: str,
) -> SessionTransitionPlan:
    """Split the actor into its stable singleton node."""

    public = PublicWorldState.model_validate(state, strict=True)
    actor = _node_for_agent(public.sessions, actor_agent_id)
    partition = _partition(public.sessions)
    members = partition.members_of(actor.root_session_id)
    if len(members) == 1:
        raise SessionTransitionError("a singleton Agent cannot leave its EventSession")
    before = (_snapshot(public.sessions, actor.root_session_id, members),)
    remaining = members - {actor.session_id}
    remaining_root = (
        min(remaining) if actor.session_id == actor.root_session_id else actor.root_session_id
    )
    partition.split(
        actor.root_session_id,
        {
            remaining_root: remaining,
            actor.session_id: (actor.session_id,),
        },
    )
    next_version = public.world.current_version + 1
    after = tuple(
        SessionPartSnapshot(
            root_session_id=root,
            topology_version=next_version,
            member_session_ids=tuple(sorted(partition.members_of(root))),
        )
        for root in sorted((remaining_root, actor.session_id))
    )
    return SessionTransitionPlan(
        world_ref=public.world.world_ref,
        actor_agent_id=actor_agent_id,
        reason=SessionTransitionReason.SPLIT,
        before_parts=before,
        after_parts=after,
    )


def _partition(nodes: tuple[EventSessionNode, ...]) -> UnionPart[str]:
    result = UnionPart(node.session_id for node in nodes)
    by_root: dict[str, list[str]] = {}
    for node in nodes:
        by_root.setdefault(node.root_session_id, []).append(node.session_id)
    for root, members in sorted(by_root.items()):
        result.merge(
            keep_root=root,
            merged_roots=tuple(sorted(member for member in members if member != root)),
        )
    return result


def _snapshot(
    nodes: tuple[EventSessionNode, ...],
    root: str,
    members: frozenset[str],
) -> SessionPartSnapshot:
    versions = {node.topology_version for node in nodes if node.session_id in members}
    if len(versions) != 1:
        raise SessionTransitionError("one current partition has inconsistent topology versions")
    return SessionPartSnapshot(
        root_session_id=root,
        topology_version=versions.pop(),
        member_session_ids=tuple(sorted(members)),
    )


def _node_for_agent(nodes: tuple[EventSessionNode, ...], agent_id: str) -> EventSessionNode:
    node = next((item for item in nodes if item.agent_id == agent_id), None)
    if node is None:
        raise SessionTransitionError(f'Agent "{agent_id}" does not exist in this World')
    return node


def _covered(parts: tuple[SessionPartSnapshot, ...], label: str) -> frozenset[str]:
    covered: set[str] = set()
    roots: set[str] = set()
    for part in parts:
        if part.root_session_id in roots:
            raise ValueError(f"{label} Session roots must be unique")
        members = set(part.member_session_ids)
        if not covered.isdisjoint(members):
            raise ValueError(f"{label} Session parts must not overlap")
        roots.add(part.root_session_id)
        covered.update(members)
    return frozenset(covered)
