"""Strict relational state for one current World."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Annotated, Self

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from agent_runtime.model import StrictModel
from agent_runtime.world.contracts import (
    ControlEpoch,
    DecisionSequence,
    Identifier,
    WorldRef,
    WorldVersion,
)

NonEmptyText = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]
ContentHash = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{64}$"),
]
PositiveVersion = Annotated[int, Field(ge=1)]


class WorldStatus(StrEnum):
    PAUSED = "paused"
    RUNNING = "running"
    ENDED = "ended"


class WorldState(StrictModel):
    """Identity and current version of one saved World."""

    world_ref: WorldRef
    seed_id: Identifier
    seed_version: PositiveVersion
    seed_hash: ContentHash
    current_version: WorldVersion
    world_time: AwareDatetime
    status: WorldStatus
    created_at: AwareDatetime
    control_epoch: ControlEpoch = 1
    decision_seq: DecisionSequence = 0


class LocationState(StrictModel):
    world_ref: WorldRef
    location_id: Identifier
    name: NonEmptyText
    description: NonEmptyText


class AgentWorldState(StrictModel):
    """Public World-owned state; Persona cognition is stored separately."""

    world_ref: WorldRef
    agent_id: Identifier
    location_id: Identifier
    public_status: NonEmptyText | None = None


class ObjectState(StrictModel):
    world_ref: WorldRef
    object_id: Identifier
    name: NonEmptyText
    kind: Identifier
    description: NonEmptyText
    location_id: Identifier
    owner_agent_id: Identifier | None = None
    state: NonEmptyText


class WorldFact(StrictModel):
    """One current public fact, optionally owned by one public entity."""

    world_ref: WorldRef
    fact_id: Identifier
    predicate: Identifier
    object: NonEmptyText | None = None
    content: NonEmptyText
    location_id: Identifier | None = None
    agent_id: Identifier | None = None
    object_id: Identifier | None = None

    @model_validator(mode="after")
    def _validate_owner(self) -> Self:
        owners = (self.location_id, self.agent_id, self.object_id)
        if sum(owner is not None for owner in owners) > 1:
            raise ValueError("a World fact can belong to at most one public entity")
        return self


class EventSessionNode(StrictModel):
    """One stable Agent node and its current flat partition root."""

    world_ref: WorldRef
    session_id: Identifier
    agent_id: Identifier
    root_session_id: Identifier
    topology_version: WorldVersion
    updated_world_version: WorldVersion


class PublicWorldState(StrictModel):
    """Fully validated current public state for one saved World."""

    world: WorldState
    locations: Annotated[tuple[LocationState, ...], Field(min_length=1)]
    agents: Annotated[tuple[AgentWorldState, ...], Field(min_length=1)]
    objects: tuple[ObjectState, ...] = ()
    facts: tuple[WorldFact, ...] = ()
    sessions: Annotated[tuple[EventSessionNode, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _validate_relations(self) -> Self:
        world_ref = self.world.world_ref
        for rows in (self.locations, self.agents, self.objects, self.facts, self.sessions):
            if any(row.world_ref != world_ref for row in rows):
                raise ValueError("all initial public state must belong to the same WorldRef")

        location_ids = _unique_ids(
            (location.location_id for location in self.locations), "location IDs"
        )
        agent_ids = _unique_ids((agent.agent_id for agent in self.agents), "agent IDs")
        object_ids = _unique_ids((item.object_id for item in self.objects), "object IDs")
        _unique_ids((fact.fact_id for fact in self.facts), "fact IDs")
        session_ids = _unique_ids((node.session_id for node in self.sessions), "EventSession IDs")

        for agent in self.agents:
            if agent.location_id not in location_ids:
                raise ValueError(f'agent "{agent.agent_id}" references an unknown location')
        for item in self.objects:
            if item.location_id not in location_ids:
                raise ValueError(f'object "{item.object_id}" references an unknown location')
            if item.owner_agent_id is not None and item.owner_agent_id not in agent_ids:
                raise ValueError(f'object "{item.object_id}" references an unknown owner')
        for fact in self.facts:
            if fact.location_id is not None and fact.location_id not in location_ids:
                raise ValueError(f'fact "{fact.fact_id}" references an unknown location')
            if fact.agent_id is not None and fact.agent_id not in agent_ids:
                raise ValueError(f'fact "{fact.fact_id}" references an unknown agent')
            if fact.object_id is not None and fact.object_id not in object_ids:
                raise ValueError(f'fact "{fact.fact_id}" references an unknown object')

        session_agent_ids = _unique_ids(
            (node.agent_id for node in self.sessions), "EventSession agent IDs"
        )
        if session_agent_ids != agent_ids:
            raise ValueError("every Agent must have exactly one stable EventSession node")
        nodes = {node.session_id: node for node in self.sessions}
        for node in self.sessions:
            if node.root_session_id not in session_ids:
                raise ValueError(f'session "{node.session_id}" references an unknown root')
            if node.topology_version > self.world.current_version:
                raise ValueError("a Session topology cannot be newer than the World")
            if node.updated_world_version > self.world.current_version:
                raise ValueError("a Session binding cannot be newer than the World")
        roots = {node.root_session_id for node in self.sessions}
        for root in roots:
            root_node = nodes[root]
            if root_node.root_session_id != root:
                raise ValueError(f'session root "{root}" must point to itself')
            versions = {
                node.topology_version for node in self.sessions if node.root_session_id == root
            }
            if len(versions) != 1:
                raise ValueError("all nodes in one partition must share a topology version")
        return self


def _unique_ids(values: Iterable[str], label: str) -> frozenset[str]:
    items = tuple(values)
    if len(items) != len(set(items)):
        raise ValueError(f"{label} must be unique")
    return frozenset(items)
