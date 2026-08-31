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
    status: Literal["active", "completed", "cancelled"] | None = None
    supersedes_memory_id: str | None = None

    @model_validator(mode="after")
    def state_matches_memory_type(self) -> MemorySeed:
        if self.memory_type == "commitment":
            if self.status is None:
                self.status = "active"
        elif self.status is not None:
            raise ValueError("status is only valid for commitment Memory")
        if (
            self.memory_type not in {"belief", "commitment"}
            and self.supersedes_memory_id
        ):
            raise ValueError(
                "supersedes_memory_id is only valid for belief or commitment Memory"
            )
        return self


class SkillReference(StrictModel):
    skill_id: str = Field(pattern=r"^[a-z][a-z0-9._:-]*$")
    version: str = Field(min_length=1, max_length=100)


class ScenarioSkillBindings(StrictModel):
    characters: dict[str, SkillReference]
    director: SkillReference
    broadcast: SkillReference


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
    skill_bindings: ScenarioSkillBindings

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
        bound_characters = set(self.skill_bindings.characters)
        character_ids = set(characters)
        if bound_characters != character_ids:
            missing = sorted(character_ids - bound_characters)
            extra = sorted(bound_characters - character_ids)
            raise ValueError(
                "skill_bindings.characters must exactly match Scenario Characters "
                f"(missing={missing}, extra={extra})"
            )

        memories_by_id = {memory.memory_id: memory for memory in self.memories}
        superseded_ids = [
            memory.supersedes_memory_id
            for memory in self.memories
            if memory.supersedes_memory_id is not None
        ]
        if len(superseded_ids) != len(set(superseded_ids)):
            raise ValueError("a Memory record may only be superseded once")
        for memory in self.memories:
            if memory.supersedes_memory_id is None:
                if memory.memory_type == "commitment" and memory.status != "active":
                    raise ValueError(
                        f"memory '{memory.memory_id}' terminal commitment requires "
                        "supersedes_memory_id"
                    )
                continue
            previous = memories_by_id.get(memory.supersedes_memory_id)
            if previous is None:
                raise ValueError(
                    f"memory '{memory.memory_id}' supersedes unknown Memory "
                    f"'{memory.supersedes_memory_id}'"
                )
            if (
                previous.agent_id != memory.agent_id
                or previous.namespace != memory.namespace
                or previous.memory_type != memory.memory_type
            ):
                raise ValueError(
                    f"memory '{memory.memory_id}' may only supersede its own namespace "
                    "and Memory type"
                )
            visited = {memory.memory_id}
            cursor = previous
            while cursor.supersedes_memory_id is not None:
                if cursor.supersedes_memory_id in visited:
                    raise ValueError(
                        "Memory supersedes references must not form a cycle"
                    )
                visited.add(cursor.supersedes_memory_id)
                next_memory = memories_by_id.get(cursor.supersedes_memory_id)
                if next_memory is None:
                    break
                cursor = next_memory
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


# Generation Wave contracts deliberately stay independent from any model-provider SDK.


class PerceivedEntity(StrictModel):
    entity_id: str = Field(min_length=1)
    entity_type: Literal["location", "character", "object"]
    name: str = Field(min_length=1)
    location_id: str | None = None
    scope_key: str | None = None
    state: dict[str, Any] = Field(default_factory=dict)


class PerceivedMemory(StrictModel):
    memory_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    namespace: str = Field(min_length=1)
    memory_type: Literal["observation", "belief", "commitment", "reflection"]
    relative_time_ms: int = Field(ge=0)
    importance: int = Field(ge=1, le=5)
    source: str = Field(min_length=1)
    status: Literal["active", "completed", "cancelled"] | None = None
    supersedes_memory_id: str | None = None
    entity_tags: list[str] = Field(default_factory=list)
    location_tags: list[str] = Field(default_factory=list)
    payload: dict[str, Any]


class PerceptionFrame(StrictModel):
    schema_version: Literal[1] = 1
    world_id: str = Field(min_length=1)
    world_version: int = Field(ge=1)
    world_time_ms: int = Field(ge=0)
    session_id: str = Field(min_length=1)
    character_id: str = Field(min_length=1)
    location_id: str = Field(min_length=1)
    scope_key: str = Field(min_length=1)
    participant_ids: list[str]
    visible_entities: list[PerceivedEntity]
    reachable_destinations: list[ReachableDestination]
    memories: list[PerceivedMemory]


class UtteranceAction(StrictModel):
    kind: Literal["utterance"]
    text: str = Field(min_length=1, max_length=2000)
    addressee_ids: list[str] = Field(default_factory=list)


class MoveAction(StrictModel):
    kind: Literal["move"]
    location_id: str = Field(min_length=1)
    scope_key: str = Field(min_length=1)


class InteractAction(StrictModel):
    kind: Literal["interact"]
    target_id: str = Field(min_length=1)
    description: str = Field(min_length=1, max_length=2000)


class WaitAction(StrictModel):
    kind: Literal["wait"]
    duration_ms: int = Field(gt=0, le=300_000)
    reason: str = Field(min_length=1, max_length=500)


class NoOpAction(StrictModel):
    kind: Literal["no_op"]
    reason: str = Field(min_length=1, max_length=500)


Action = Annotated[
    UtteranceAction | MoveAction | InteractAction | WaitAction | NoOpAction,
    Field(discriminator="kind"),
]


class MemoryChangeCandidate(StrictModel):
    agent_id: str = Field(min_length=1)
    namespace: str = Field(default="default", min_length=1)
    memory_type: Literal["belief", "commitment"]
    content: str = Field(min_length=1, max_length=2000)
    importance: int = Field(default=1, ge=1, le=5)
    supersedes_memory_id: str | None = None
    entity_tags: list[str] = Field(default_factory=list)
    location_tags: list[str] = Field(default_factory=list)
    source: str = Field(default="character", min_length=1)
    status: Literal["active", "completed", "cancelled"] | None = None

    @model_validator(mode="after")
    def state_matches_memory_type(self) -> MemoryChangeCandidate:
        if self.memory_type == "commitment":
            if self.status is None:
                self.status = "active"
            if (
                self.status in {"completed", "cancelled"}
                and not self.supersedes_memory_id
            ):
                raise ValueError(
                    "completed or cancelled commitment requires supersedes_memory_id"
                )
        elif self.status is not None:
            raise ValueError("status is only valid for commitment Memory")
        return self


class ActionProposal(StrictModel):
    schema_version: Literal[1] = 1
    proposal_id: str = Field(min_length=1)
    world_version: int = Field(ge=1)
    session_id: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    intent_summary: str = Field(min_length=1, max_length=280)
    action: Action
    memory_changes: list[MemoryChangeCandidate] = Field(default_factory=list)


class EntityStateChange(StrictModel):
    entity_id: str = Field(min_length=1)
    state_patch: dict[str, Any] = Field(default_factory=dict)
    location_id: str | None = None
    scope_key: str | None = None

    @model_validator(mode="after")
    def location_and_scope_are_paired(self) -> EntityStateChange:
        if (self.location_id is None) != (self.scope_key is None):
            raise ValueError("location_id and scope_key must be provided together")
        return self


class CandidateEvent(StrictModel):
    event_key: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    actor_id: str | None = None
    start_time_ms: int = Field(ge=0)
    end_time_ms: int = Field(ge=0)
    cause_event_keys: list[str] = Field(default_factory=list)
    source_kind: str = Field(min_length=1)
    source_ref: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    location_id: str = Field(min_length=1)
    scope_key: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def has_source_evidence(self) -> CandidateEvent:
        if self.source_ref is None and not self.evidence_refs:
            raise ValueError("source_ref or evidence_refs is required")
        return self


class ExternalEventCandidate(CandidateEvent):
    """A Director-owned candidate which may have no Character actor."""

    source_kind: Literal["director", "environment", "system", "tool", "player_request"]


class SegmentDraft(StrictModel):
    schema_version: Literal[1] = 1
    world_version: int = Field(ge=1)
    session_id: str = Field(min_length=1)
    wave_started_at_ms: int = Field(ge=0)
    wave_ended_at_ms: int = Field(ge=0)
    proposal_events: list[CandidateEvent]
    external_events: list[ExternalEventCandidate] = Field(default_factory=list)
    entity_changes: list[EntityStateChange] = Field(default_factory=list)
    session_intent: Literal["keep_open", "resolved"] = "keep_open"


class SuccessorSession(StrictModel):
    session_id: str = Field(min_length=1)
    location_id: str = Field(min_length=1)
    scope_key: str = Field(min_length=1)
    participant_ids: list[str] = Field(min_length=1)
    parent_session_ids: list[str] = Field(min_length=1)
    pending_response_ids: list[str] = Field(default_factory=list)


class ValidatedCommitPlan(StrictModel):
    schema_version: Literal[1] = 1
    world_id: str = Field(min_length=1)
    base_world_version: int = Field(ge=1)
    new_world_version: int = Field(ge=2)
    session_id: str = Field(min_length=1)
    wave_started_at_ms: int = Field(ge=0)
    wave_ended_at_ms: int = Field(ge=0)
    events: list[CandidateEvent | ExternalEventCandidate]
    entity_changes: list[EntityStateChange] = Field(default_factory=list)
    accepted_memory_changes: list[MemoryChangeCandidate] = Field(default_factory=list)
    session_intent: Literal["keep_open", "partitioned", "resolved", "limit_reached"] = (
        "keep_open"
    )
    closed_session_ids: list[str] = Field(default_factory=list)
    successor_sessions: list[SuccessorSession] = Field(default_factory=list)
    pending_response_ids: list[str] = Field(default_factory=list)
    proposal_ids: list[str]
    source_trace_id: str = Field(min_length=1)


class ValidationDiagnostic(StrictModel):
    code: str = Field(min_length=1)
    path: str = Field(min_length=1)
    message: str = Field(min_length=1)


# Broadcast and Render contracts use stable domain/asset IDs. Only RenderJob carries
# paths, after deterministic validation against an Asset Manifest.


class AssetEntry(StrictModel):
    asset_id: str = Field(min_length=1)
    path: str = Field(min_length=1)


class Live2DModelAsset(AssetEntry):
    character_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    motions: list[str] = Field(default_factory=list)
    expressions: list[str] = Field(default_factory=list)
    entrance_effects: list[str] = Field(default_factory=list)


class AssetManifest(StrictModel):
    schema_version: Literal[1]
    manifest_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    backgrounds: list[AssetEntry] = Field(min_length=1)
    bgms: list[AssetEntry] = Field(default_factory=list)
    live2d_models: list[Live2DModelAsset] = Field(default_factory=list)

    @model_validator(mode="after")
    def stable_asset_ids_are_unique(self) -> AssetManifest:
        ids = [
            item.asset_id
            for item in [*self.backgrounds, *self.bgms, *self.live2d_models]
        ]
        if len(ids) != len(set(ids)):
            raise ValueError("asset_id values must be unique across the manifest")
        return self


class BroadcastEvent(StrictModel):
    event_id: str = Field(min_length=1)
    event_order: int = Field(ge=1)
    world_version: int = Field(ge=1)
    session_id: str | None = None
    event_type: str = Field(min_length=1)
    actor_id: str | None = None
    start_time_ms: int = Field(ge=0)
    end_time_ms: int = Field(ge=0)
    location_id: str = Field(min_length=1)
    scope_key: str = Field(min_length=1)
    fact: dict[str, Any]


class ChapterBeat(StrictModel):
    beat_id: str = Field(min_length=1)
    type: Literal["chapter"]
    title: str = Field(min_length=1)
    subtitle: str | None = None
    source_event_ids: list[str] = Field(default_factory=list)


class BgmBeat(StrictModel):
    beat_id: str = Field(min_length=1)
    type: Literal["bgm"]
    asset_id: str = Field(min_length=1)
    volume: int = Field(default=70, ge=0, le=100)
    fade_ms: int = Field(default=1000, ge=0, le=60_000)
    source_event_ids: list[str] = Field(default_factory=list)


class StopBgmBeat(StrictModel):
    beat_id: str = Field(min_length=1)
    type: Literal["stop_bgm"]
    fade_ms: int = Field(default=1000, ge=0, le=60_000)
    source_event_ids: list[str] = Field(default_factory=list)


class BackgroundBeat(StrictModel):
    beat_id: str = Field(min_length=1)
    type: Literal["background"]
    asset_id: str = Field(min_length=1)
    source_event_ids: list[str] = Field(default_factory=list)


class ShowBeat(StrictModel):
    beat_id: str = Field(min_length=1)
    type: Literal["show"]
    character_id: str = Field(min_length=1)
    model_asset_id: str = Field(min_length=1)
    position: Literal["left", "center", "right"]
    motion: str | None = None
    expression: str | None = None
    entrance_effect: str | None = None
    source_event_ids: list[str] = Field(default_factory=list)


class HideBeat(StrictModel):
    beat_id: str = Field(min_length=1)
    type: Literal["hide"]
    position: Literal["left", "center", "right"]
    source_event_ids: list[str] = Field(default_factory=list)


class DialogueBeat(StrictModel):
    beat_id: str = Field(min_length=1)
    type: Literal["dialogue"]
    character_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    source_event_ids: list[str] = Field(min_length=1)
    model_asset_id: str | None = None
    motion: str | None = None
    expression: str | None = None
    entrance_effect: str | None = None


class NarrationBeat(StrictModel):
    beat_id: str = Field(min_length=1)
    type: Literal["narration"]
    text: str = Field(min_length=1)
    source_event_ids: list[str] = Field(min_length=1)


Beat = Annotated[
    ChapterBeat
    | BgmBeat
    | StopBgmBeat
    | BackgroundBeat
    | ShowBeat
    | HideBeat
    | DialogueBeat
    | NarrationBeat,
    Field(discriminator="type"),
]


class BroadcastRender(StrictModel):
    render_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    title: str = Field(min_length=1)
    estimated_play_ms: int = Field(gt=0)
    beats: list[Beat] = Field(min_length=1)


class BroadcastDisposition(StrictModel):
    event_id: str = Field(min_length=1)
    status: Literal["included", "omitted"]
    reason: str | None = None

    @model_validator(mode="after")
    def omitted_events_have_a_reason(self) -> BroadcastDisposition:
        if self.status == "omitted" and not self.reason:
            raise ValueError("omitted disposition requires a reason")
        if self.status == "included" and self.reason is not None:
            raise ValueError("included disposition cannot have a reason")
        return self


class BroadcastPlan(StrictModel):
    schema_version: Literal[1] = 1
    world_id: str = Field(min_length=1)
    target_world_version: int = Field(ge=1)
    dispositions: list[BroadcastDisposition] = Field(min_length=1)
    renders: list[BroadcastRender] = Field(default_factory=list)


class RenderJob(StrictModel):
    schema_version: Literal[1] = 1
    world_id: str = Field(min_length=1)
    target_world_version: int = Field(ge=1)
    render_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    title: str = Field(min_length=1)
    estimated_play_ms: int = Field(gt=0)
    # These are planner-owned, resolved Beat dictionaries. Asset IDs are retained
    # for auditability and asset_path is relative to the corresponding WebGAL root.
    beats: list[dict[str, Any]] = Field(min_length=1)


class CompiledRender(StrictModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    job: RenderJob
    script: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
