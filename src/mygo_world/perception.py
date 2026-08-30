from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from mygo_world.contracts import (
    CandidateEvent,
    PerceivedEntity,
    PerceivedMemory,
    PerceptionFrame,
    ReachableDestination,
)
from mygo_world.db.models import AgentMemoryRow


def _public_state(state: dict[str, Any], character_id: str) -> dict[str, Any]:
    public = {
        key: value
        for key, value in state.items()
        if key not in {"private", "private_by_agent"}
    }
    per_agent = state.get("private_by_agent")
    if isinstance(per_agent, dict) and isinstance(per_agent.get(character_id), dict):
        public.update(per_agent[character_id])
    return public


def _entity_state(entity: dict[str, Any], character_id: str) -> dict[str, Any]:
    payload = entity.get("payload", {})
    state = payload.get("state", {}) if isinstance(payload, dict) else {}
    return _public_state(state, character_id) if isinstance(state, dict) else {}


class PerceptionProjector:
    """Deterministic permission boundary for model input and committed observations."""

    def project_frame(
        self,
        snapshot: dict[str, Any],
        *,
        session_id: str,
        character_id: str,
        memories: list[AgentMemoryRow],
    ) -> PerceptionFrame:
        session = next(
            (
                item
                for item in snapshot["sessions"]
                if item["session_id"] == session_id and item["status"] == "runnable"
            ),
            None,
        )
        if session is None:
            raise ValueError(
                f"Runnable Event Session '{session_id}' is not in Snapshot"
            )
        if character_id not in session["participant_ids"]:
            raise ValueError(
                f"Character '{character_id}' is not a participant of '{session_id}'"
            )
        character = next(
            (
                item
                for item in snapshot["entities"]
                if item["entity_id"] == character_id
                and item["entity_type"] == "character"
            ),
            None,
        )
        if character is None:
            raise ValueError(f"Character '{character_id}' is missing from Snapshot")

        location_id = character["location_id"]
        scope_key = character["scope_key"]
        visible = []
        for entity in snapshot["entities"]:
            is_location = entity["entity_id"] == location_id
            colocated = (
                entity.get("location_id") == location_id
                and entity.get("scope_key") == scope_key
            )
            if not (is_location or colocated):
                continue
            visible.append(
                PerceivedEntity(
                    entity_id=entity["entity_id"],
                    entity_type=entity["entity_type"],
                    name=entity["name"],
                    location_id=entity.get("location_id"),
                    scope_key=entity.get("scope_key"),
                    state=_entity_state(entity, character_id),
                )
            )

        location = next(
            item for item in snapshot["entities"] if item["entity_id"] == location_id
        )
        scopes = location.get("payload", {}).get("scopes", [])
        current_scope = next(
            (item for item in scopes if item["scope_key"] == scope_key), None
        )
        reachable = (
            [] if current_scope is None else current_scope["reachable_destinations"]
        )

        perceived_memories = []
        for memory in sorted(memories, key=lambda item: item.memory_id):
            # Filtering here is defense in depth: a repository mistake cannot leak another
            # Character's private Memory into the model input.
            if memory.agent_id != character_id:
                continue
            perceived_memories.append(
                PerceivedMemory(
                    memory_id=memory.memory_id,
                    agent_id=memory.agent_id,
                    namespace=memory.namespace,
                    memory_type=memory.memory_type,
                    relative_time_ms=memory.relative_time_ms,
                    importance=memory.importance,
                    payload=json.loads(memory.payload_json),
                )
            )

        return PerceptionFrame(
            world_id=snapshot["world_id"],
            world_version=snapshot["world_version"],
            world_time_ms=snapshot["world_time_ms"],
            session_id=session_id,
            character_id=character_id,
            location_id=location_id,
            scope_key=scope_key,
            participant_ids=sorted(session["participant_ids"]),
            visible_entities=sorted(visible, key=lambda item: item.entity_id),
            reachable_destinations=[
                ReachableDestination.model_validate(item)
                for item in sorted(
                    reachable,
                    key=lambda item: (item["location_id"], item["scope_key"]),
                )
            ],
            memories=perceived_memories,
        )

    def project_event_observations(
        self,
        events: list[RecognizedEvent],
        snapshot: dict[str, Any],
    ) -> list[ProjectedObservation]:
        observations: list[ProjectedObservation] = []
        characters = [
            item for item in snapshot["entities"] if item["entity_type"] == "character"
        ]
        for event in events:
            allowed = event.candidate.payload.get("visible_to_character_ids")
            for character in characters:
                if (
                    character.get("location_id") != event.candidate.location_id
                    or character.get("scope_key") != event.candidate.scope_key
                ):
                    continue
                if isinstance(allowed, list) and character["entity_id"] not in allowed:
                    continue
                payload = {
                    key: value
                    for key, value in event.candidate.payload.items()
                    if key not in {"private", "visible_to_character_ids"}
                }
                observations.append(
                    ProjectedObservation(
                        agent_id=character["entity_id"],
                        event_id=event.event_id,
                        relative_time_ms=event.candidate.end_time_ms,
                        payload={
                            "content": payload,
                            "event_type": event.candidate.event_type,
                            "source_event_id": event.event_id,
                            "source_kind": event.candidate.source_kind,
                        },
                    )
                )
        return observations


@dataclass(frozen=True)
class RecognizedEvent:
    event_id: str
    event_order: int
    candidate: CandidateEvent


@dataclass(frozen=True)
class ProjectedObservation:
    agent_id: str
    event_id: str
    relative_time_ms: int
    payload: dict[str, Any]
