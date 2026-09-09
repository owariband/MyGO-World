"""Strict, deterministic loading for one Project's opening Scenario seed."""

from __future__ import annotations

import json
import math
import re
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Literal, Protocol, Self, cast

import yaml
from pydantic import (
    AwareDatetime,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode, Node
from yaml.resolver import BaseResolver

from agent_runtime.agent.memory import MemoryKind
from agent_runtime.agent.personact.errors import ManifestDecodeError
from agent_runtime.agent.personact.manifest import Manifest, load_manifest
from agent_runtime.common.strict_json import loads_strict_json
from agent_runtime.model import StrictModel

ProjectIdentifier = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9-]*$"),
]
ScenarioIdentifier = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$"),
]
NonEmptyText = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]
NonNegativeScore = Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class ScenarioError(ValueError):
    """Base error for a malformed or inconsistent Project Scenario."""


class ScenarioDecodeError(ScenarioError):
    """The Project files could not be decoded into strict contracts."""


class ScenarioReferenceError(ScenarioError):
    """The decoded Project files disagree or contain invalid references."""


class LocationSeed(StrictModel):
    id: ScenarioIdentifier
    name: NonEmptyText
    description: NonEmptyText


class ObjectSeed(StrictModel):
    id: ScenarioIdentifier
    name: NonEmptyText
    kind: ScenarioIdentifier
    description: NonEmptyText
    location_id: ScenarioIdentifier
    owner_agent_id: ScenarioIdentifier | None = None
    state: NonEmptyText


class FactSubject(StrictModel):
    kind: Literal["world", "location", "object", "agent"]
    id: ScenarioIdentifier | None = None

    @model_validator(mode="after")
    def _validate_identity(self) -> Self:
        if self.kind == "world" and self.id is not None:
            raise ValueError("world fact subject cannot carry an id")
        if self.kind != "world" and self.id is None:
            raise ValueError(f"{self.kind} fact subject requires an id")
        return self


class WorldFactSeed(StrictModel):
    id: ScenarioIdentifier
    subject: FactSubject
    predicate: ScenarioIdentifier
    object: NonEmptyText | None = None
    content: NonEmptyText


class AgentSeed(StrictModel):
    agent_id: ScenarioIdentifier
    location_id: ScenarioIdentifier
    session_id: ScenarioIdentifier
    public_status: NonEmptyText | None = None


class InitialPartition(StrictModel):
    root_session_id: ScenarioIdentifier
    member_session_ids: Annotated[tuple[ScenarioIdentifier, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _validate_members(self) -> Self:
        _require_unique(self.member_session_ids, "partition member session ids")
        if self.root_session_id not in self.member_session_ids:
            raise ValueError("initial partition must contain its root session")
        return self


class AllAgents(StrictModel):
    kind: Literal["all"] = "all"


class ExplicitAgents(StrictModel):
    kind: Literal["explicit"] = "explicit"
    agent_ids: Annotated[tuple[ScenarioIdentifier, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _validate_agents(self) -> Self:
        _require_unique(self.agent_ids, "explicit knowledge recipient ids")
        return self


KnowledgeRecipients = Annotated[AllAgents | ExplicitAgents, Field(discriminator="kind")]


class KnowledgeSeed(StrictModel):
    id: ScenarioIdentifier
    kind: MemoryKind
    subject: NonEmptyText
    predicate: NonEmptyText
    object: NonEmptyText | None = None
    content: NonEmptyText
    poignancy: NonNegativeScore
    tags: tuple[ScenarioIdentifier, ...] = ()
    recipients: KnowledgeRecipients

    @model_validator(mode="after")
    def _validate_tags(self) -> Self:
        _require_unique(self.tags, "knowledge tags")
        return self


class ScenarioSeed(StrictModel):
    """Opening public state and explicitly routed private knowledge."""

    format_version: Literal[1]
    project_id: ProjectIdentifier
    seed_id: ScenarioIdentifier
    version: Annotated[int, Field(ge=1)]
    world_time: AwareDatetime
    locations: Annotated[tuple[LocationSeed, ...], Field(min_length=1)]
    objects: tuple[ObjectSeed, ...] = ()
    public_facts: tuple[WorldFactSeed, ...] = ()
    agents: Annotated[tuple[AgentSeed, ...], Field(min_length=1)]
    initial_partitions: Annotated[tuple[InitialPartition, ...], Field(min_length=1)]
    knowledge: tuple[KnowledgeSeed, ...] = ()

    @model_validator(mode="after")
    def _validate_references(self) -> Self:
        location_ids = tuple(location.id for location in self.locations)
        object_ids = tuple(item.id for item in self.objects)
        agent_ids = tuple(agent.agent_id for agent in self.agents)
        session_ids = tuple(agent.session_id for agent in self.agents)
        _require_unique(location_ids, "location ids")
        _require_unique(object_ids, "object ids")
        _require_unique(tuple(fact.id for fact in self.public_facts), "public fact ids")
        _require_unique(agent_ids, "Scenario agent ids")
        _require_unique(session_ids, "stable session ids")
        _require_unique(tuple(item.id for item in self.knowledge), "knowledge ids")

        known_locations = frozenset(location_ids)
        known_objects = frozenset(object_ids)
        known_agents = frozenset(agent_ids)
        for item in self.objects:
            if item.location_id not in known_locations:
                raise ValueError(f'object "{item.id}" references unknown location')
            if item.owner_agent_id is not None and item.owner_agent_id not in known_agents:
                raise ValueError(f'object "{item.id}" references unknown owner agent')
        for agent in self.agents:
            if agent.location_id not in known_locations:
                raise ValueError(f'agent "{agent.agent_id}" references unknown location')
        for fact in self.public_facts:
            subject_id = fact.subject.id
            known_subjects = {
                "location": known_locations,
                "object": known_objects,
                "agent": known_agents,
            }
            if fact.subject.kind != "world" and subject_id not in known_subjects[fact.subject.kind]:
                raise ValueError(f'fact "{fact.id}" references unknown {fact.subject.kind}')

        covered_sessions: set[str] = set()
        for partition in self.initial_partitions:
            members = set(partition.member_session_ids)
            if not covered_sessions.isdisjoint(members):
                raise ValueError("initial partitions must not overlap")
            covered_sessions.update(members)
        if covered_sessions != set(session_ids):
            raise ValueError("initial partitions must cover every stable session exactly once")

        for item in self.knowledge:
            if isinstance(item.recipients, ExplicitAgents):
                unknown = set(item.recipients.agent_ids) - known_agents
                if unknown:
                    raise ValueError(
                        f'knowledge "{item.id}" references unknown recipient "{min(unknown)}"'
                    )
        return self


class LoadedScenario(StrictModel):
    """Validated Project inputs plus their canonical Scenario identity."""

    manifest: Manifest
    seed: ScenarioSeed
    seed_hash: Digest


def load_project_scenario(projects_root: Path, project_id: str) -> LoadedScenario:
    """Load and cross-check one Project without opening its Runtime database."""

    canonical_project_id = _validate_project_id(project_id)
    root = projects_root.resolve()
    project_dir = root / canonical_project_id
    if not root.is_dir():
        raise ScenarioDecodeError(f"projects root does not exist: {root}")
    if project_dir.is_symlink() or not project_dir.is_dir() or project_dir.resolve() != project_dir:
        raise ScenarioDecodeError(f"Project directory is unavailable or unsafe: {project_dir}")

    project_path = _project_file(project_dir, "project.json")
    manifest_path = _project_file(project_dir, "agents.json")
    scenario_path = _project_file(project_dir, "scenario.yaml")
    renderer_project_id = _load_renderer_project_id(project_path)
    try:
        manifest = load_manifest(manifest_path)
    except ManifestDecodeError as error:
        raise ScenarioDecodeError(f"NPC manifest is invalid: {manifest_path}") from error
    seed = load_scenario_seed(scenario_path)

    identities = (renderer_project_id, manifest.project_id, seed.project_id)
    if any(identity != canonical_project_id for identity in identities):
        raise ScenarioReferenceError(
            "Project directory, project.json, agents.json, and scenario.yaml ids must match"
        )
    _validate_manifest_and_seed(manifest, seed)
    return LoadedScenario(manifest=manifest, seed=seed, seed_hash=scenario_seed_hash(seed))


def load_scenario_seed(path: Path) -> ScenarioSeed:
    """Decode YAML once, reject non-JSON values, then apply strict JSON validation."""

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ScenarioDecodeError(f"cannot read Scenario seed: {path}") from error
    try:
        raw = yaml.load(text, Loader=_UniqueKeyLoader)
        _require_json_value(raw, seen=set())
        encoded = json.dumps(raw, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        seed = ScenarioSeed.model_validate_json(
            encoded,
            strict=True,
            extra="forbid",
            by_alias=True,
            by_name=False,
        )
    except (yaml.YAMLError, TypeError, ValueError, ValidationError) as error:
        raise ScenarioDecodeError(f"Scenario seed is invalid: {path}") from error
    return _canonical_seed(seed)


def scenario_seed_hash(seed: ScenarioSeed) -> str:
    """Hash semantic, normalized Scenario content instead of source YAML bytes."""

    validated = ScenarioSeed.model_validate(seed, strict=True)
    canonical = _canonical_seed(validated).model_dump_json(by_alias=True, exclude_none=False)
    return sha256(canonical.encode("utf-8")).hexdigest()


def _validate_project_id(project_id: str) -> str:
    if re.fullmatch(r"[a-z][a-z0-9-]{0,63}", project_id) is None:
        raise ScenarioReferenceError(
            "Project id must start with a lowercase letter and contain only lowercase "
            "letters, numbers, or '-'"
        )
    return project_id


def _project_file(project_dir: Path, filename: str) -> Path:
    path = project_dir / filename
    if path.is_symlink() or path.resolve().parent != project_dir:
        raise ScenarioDecodeError(f"Project file is unavailable or unsafe: {path}")
    return path


def _load_renderer_project_id(path: Path) -> str:
    try:
        raw = loads_strict_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise ScenarioDecodeError(f"project identity is invalid: {path}") from error
    if not isinstance(raw, dict):
        raise ScenarioDecodeError(f"project identity must be a JSON object: {path}")
    project_id = cast(dict[object, object], raw).get("id")
    if not isinstance(project_id, str):
        raise ScenarioDecodeError(f"project identity requires a string id: {path}")
    return project_id


def _validate_manifest_and_seed(manifest: Manifest, seed: ScenarioSeed) -> None:
    manifest_agent_ids = tuple(agent.id for agent in manifest.agents)
    _require_unique(manifest_agent_ids, "Manifest agent ids", ScenarioReferenceError)
    scenario_agent_ids = tuple(agent.agent_id for agent in seed.agents)
    if set(manifest_agent_ids) != set(scenario_agent_ids):
        raise ScenarioReferenceError("Scenario agents must exactly match Manifest agents")

    known_agents = frozenset(manifest_agent_ids)
    for agent in manifest.agents:
        for relationship in agent.persona.relationships:
            if relationship.target_id not in known_agents:
                raise ScenarioReferenceError(
                    f'agent "{agent.id}" references unknown relationship target '
                    f'"{relationship.target_id}"'
                )

    memory_ids_by_agent: dict[str, set[str]] = {}
    for agent in manifest.agents:
        seed_ids = tuple(item.id for item in agent.memory.seeds)
        _require_unique(
            seed_ids,
            f'Manifest memory ids for agent "{agent.id}"',
            ScenarioReferenceError,
        )
        memory_ids_by_agent[agent.id] = set(seed_ids)
    for item in seed.knowledge:
        recipients = (
            known_agents
            if isinstance(item.recipients, AllAgents)
            else frozenset(item.recipients.agent_ids)
        )
        for agent_id in recipients:
            if item.id in memory_ids_by_agent[agent_id]:
                raise ScenarioReferenceError(
                    f'memory id "{item.id}" conflicts for agent "{agent_id}"'
                )
            memory_ids_by_agent[agent_id].add(item.id)


def _canonical_seed(seed: ScenarioSeed) -> ScenarioSeed:
    knowledge = tuple(
        KnowledgeSeed(
            id=item.id,
            kind=item.kind,
            subject=item.subject,
            predicate=item.predicate,
            object=item.object,
            content=item.content,
            poignancy=0.0 if item.poignancy == 0.0 else item.poignancy,
            tags=tuple(sorted(item.tags)),
            recipients=(
                item.recipients
                if isinstance(item.recipients, AllAgents)
                else ExplicitAgents(agent_ids=tuple(sorted(item.recipients.agent_ids)))
            ),
        )
        for item in sorted(seed.knowledge, key=lambda value: value.id)
    )
    partitions = tuple(
        InitialPartition(
            root_session_id=item.root_session_id,
            member_session_ids=tuple(sorted(item.member_session_ids)),
        )
        for item in sorted(seed.initial_partitions, key=lambda value: value.root_session_id)
    )
    return ScenarioSeed(
        format_version=seed.format_version,
        project_id=seed.project_id,
        seed_id=seed.seed_id,
        version=seed.version,
        world_time=seed.world_time,
        locations=tuple(sorted(seed.locations, key=lambda value: value.id)),
        objects=tuple(sorted(seed.objects, key=lambda value: value.id)),
        public_facts=tuple(sorted(seed.public_facts, key=lambda value: value.id)),
        agents=tuple(sorted(seed.agents, key=lambda value: value.agent_id)),
        initial_partitions=partitions,
        knowledge=knowledge,
    )


def _require_unique(
    values: tuple[str, ...],
    label: str,
    error_type: type[ValueError] = ValueError,
) -> None:
    if len(values) != len(set(values)):
        raise error_type(f"{label} must be unique")


def _require_json_value(value: object, *, seen: set[int]) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Scenario numbers must be finite")
        return
    if isinstance(value, list):
        items = cast(list[object], value)
        identity = id(items)
        if identity in seen:
            raise ValueError("Scenario YAML aliases cannot form cycles")
        seen.add(identity)
        for item in items:
            _require_json_value(item, seen=seen)
        seen.remove(identity)
        return
    if isinstance(value, dict):
        items = cast(dict[object, object], value)
        identity = id(items)
        if identity in seen:
            raise ValueError("Scenario YAML aliases cannot form cycles")
        seen.add(identity)
        for key, item in items.items():
            if not isinstance(key, str):
                raise TypeError("Scenario mapping keys must be strings")
            _require_json_value(item, seen=seen)
        seen.remove(identity)
        return
    raise ValueError(f"Scenario contains non-JSON value: {type(value).__name__}")


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


class _YamlObjectConstructor(Protocol):
    def construct_object(self, node: Node, deep: bool = False) -> object: ...


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    mapping: dict[object, object] = {}
    constructor = cast(_YamlObjectConstructor, loader)
    for key_node, value_node in node.value:
        key = constructor.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as error:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from error
        if duplicate:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = constructor.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)
