from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from mygo_world.canonical import sha256_bytes
from mygo_world.errors import SeedInvalidError


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ReachableDestination(StrictModel):
    location_id: str = Field(min_length=1)
    scope_key: str = Field(min_length=1)


class InteractionScopeSeed(StrictModel):
    scope_key: str = Field(min_length=1)
    name: str = Field(min_length=1)
    reachable_destinations: list[ReachableDestination] = Field(default_factory=list)


class LocationSeed(StrictModel):
    entity_type: Literal["location"]
    entity_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    scopes: list[InteractionScopeSeed] = Field(min_length=1)
    state: dict[str, Any] = Field(default_factory=dict)


class CharacterSeed(StrictModel):
    entity_type: Literal["character"]
    entity_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    location_id: str = Field(min_length=1)
    scope_key: str = Field(min_length=1)
    state: dict[str, Any] = Field(default_factory=dict)


class ObjectSeed(StrictModel):
    entity_type: Literal["object"]
    entity_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    location_id: str | None = None
    scope_key: str | None = None
    state: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def location_and_scope_are_paired(self) -> ObjectSeed:
        if (self.location_id is None) != (self.scope_key is None):
            raise ValueError("location_id and scope_key must be provided together")
        return self


EntitySeed = Annotated[
    LocationSeed | CharacterSeed | ObjectSeed,
    Field(discriminator="entity_type"),
]


class EventSessionSeed(StrictModel):
    session_id: str = Field(min_length=1)
    location_id: str = Field(min_length=1)
    scope_key: str = Field(min_length=1)
    participant_ids: list[str] = Field(min_length=1)


class MemorySeed(StrictModel):
    memory_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    namespace: str = Field(default="default", min_length=1)
    memory_type: Literal["observation", "belief", "commitment", "reflection"]
    content: str = Field(min_length=1)
    relative_time_ms: int = Field(default=0, ge=0)
    importance: int = Field(default=1, ge=1, le=5)
    entity_tags: list[str] = Field(default_factory=list)
    location_tags: list[str] = Field(default_factory=list)
    source: str = Field(default="scenario_seed", min_length=1)


class ScenarioSeed(StrictModel):
    schema_version: Literal[1]
    seed_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    name: str = Field(min_length=1)
    world_time_ms: int = Field(default=0, ge=0)
    calendar_anchor: str | None = None
    entities: list[EntitySeed] = Field(min_length=1)
    sessions: list[EventSessionSeed] = Field(min_length=1)
    memories: list[MemorySeed] = Field(default_factory=list)

    @field_validator("calendar_anchor")
    @classmethod
    def calendar_anchor_has_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("calendar_anchor must be an ISO 8601 datetime") from exc
        if parsed.utcoffset() is None:
            raise ValueError("calendar_anchor must include a timezone offset")
        return value

    @model_validator(mode="after")
    def validate_references(self) -> ScenarioSeed:
        entity_ids = [entity.entity_id for entity in self.entities]
        if len(entity_ids) != len(set(entity_ids)):
            raise ValueError("entity_id values must be unique")

        session_ids = [session.session_id for session in self.sessions]
        if len(session_ids) != len(set(session_ids)):
            raise ValueError("session_id values must be unique")

        memory_ids = [memory.memory_id for memory in self.memories]
        if len(memory_ids) != len(set(memory_ids)):
            raise ValueError("memory_id values must be unique")

        locations = {
            entity.entity_id: {scope.scope_key for scope in entity.scopes}
            for entity in self.entities
            if isinstance(entity, LocationSeed)
        }
        characters = {
            entity.entity_id: entity
            for entity in self.entities
            if isinstance(entity, CharacterSeed)
        }
        if not locations:
            raise ValueError("at least one location entity is required")
        if not characters:
            raise ValueError("at least one character entity is required")

        def require_scope(location_id: str, scope_key: str, path: str) -> None:
            if location_id not in locations:
                raise ValueError(f"{path} references unknown location '{location_id}'")
            if scope_key not in locations[location_id]:
                raise ValueError(
                    f"{path} references unknown scope '{location_id}/{scope_key}'"
                )

        for entity in self.entities:
            if isinstance(entity, CharacterSeed):
                require_scope(entity.location_id, entity.scope_key, entity.entity_id)
            elif isinstance(entity, ObjectSeed) and entity.location_id is not None:
                require_scope(
                    entity.location_id, entity.scope_key or "", entity.entity_id
                )
            elif isinstance(entity, LocationSeed):
                for scope in entity.scopes:
                    for destination in scope.reachable_destinations:
                        require_scope(
                            destination.location_id,
                            destination.scope_key,
                            f"{entity.entity_id}/{scope.scope_key}",
                        )

        for session in self.sessions:
            require_scope(session.location_id, session.scope_key, session.session_id)
            if len(session.participant_ids) != len(set(session.participant_ids)):
                raise ValueError(
                    f"session '{session.session_id}' participant_ids must be unique"
                )
            for participant_id in session.participant_ids:
                character = characters.get(participant_id)
                if character is None:
                    raise ValueError(
                        f"session '{session.session_id}' references unknown character "
                        f"'{participant_id}'"
                    )
                if (character.location_id, character.scope_key) != (
                    session.location_id,
                    session.scope_key,
                ):
                    raise ValueError(
                        f"session '{session.session_id}' participant '{participant_id}' "
                        "is outside the session scope"
                    )

        for memory in self.memories:
            if memory.agent_id not in characters:
                raise ValueError(
                    f"memory '{memory.memory_id}' references unknown character "
                    f"'{memory.agent_id}'"
                )
        return self


class LoadedSeed(StrictModel):
    seed: ScenarioSeed
    content_hash: str


def load_seed(path: Path) -> LoadedSeed:
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise SeedInvalidError(f"Cannot read Scenario Seed '{path}': {exc}") from exc

    try:
        raw = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise SeedInvalidError(f"Scenario Seed is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise SeedInvalidError("Scenario Seed root must be a mapping")

    try:
        seed = ScenarioSeed.model_validate(raw)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(item) for item in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise SeedInvalidError(details) from exc
    return LoadedSeed(seed=seed, content_hash=sha256_bytes(content))
