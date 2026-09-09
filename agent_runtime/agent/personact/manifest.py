"""Strict creator-facing NPC manifest."""

import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from agent_runtime.agent.personact.errors import ManifestDecodeError
from agent_runtime.agent.skill import RuntimeSkillReference
from agent_runtime.common.strict_json import loads_strict_json
from agent_runtime.model import StrictModel
from agent_runtime.world.contracts import ProposalKind

AgentIdentifier = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$"),
]
NonEmptyText = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]
BoundedItems = Annotated[tuple[NonEmptyText, ...], Field(max_length=32)]


class MemoryWritePolicy(StrEnum):
    OBSERVATION = "observation"
    COMMITTED_OUTCOME = "committed_outcome"
    REFLECTION = "reflection"


class Initiative(StrEnum):
    QUIET = "quiet"
    BALANCED = "balanced"
    PROACTIVE = "proactive"


class ResponsePriority(StrEnum):
    ADDRESSED_FIRST = "addressed_first"
    GOAL_FIRST = "goal_first"


class ReflectionPolicy(StrEnum):
    OFF = "off"
    COMMITTED_OUTCOME = "committed_outcome"


class Goal(StrictModel):
    id: AgentIdentifier
    description: NonEmptyText


class Relationship(StrictModel):
    target_id: AgentIdentifier
    description: NonEmptyText


class Voice(StrictModel):
    style: NonEmptyText
    avoid: BoundedItems = ()


class PersonaDefinition(StrictModel):
    identity: NonEmptyText
    traits: BoundedItems = ()
    goals: Annotated[tuple[Goal, ...], Field(max_length=32)] = ()
    relationships: Annotated[tuple[Relationship, ...], Field(max_length=64)] = ()
    voice: Voice


class MemorySeed(StrictModel):
    id: AgentIdentifier
    type: NonEmptyText
    content: NonEmptyText
    tags: BoundedItems = ()


class RetrievalPolicy(StrictModel):
    recent_limit: Annotated[int, Field(ge=0, le=32)]
    tag_limit: Annotated[int, Field(ge=0, le=32)]


class MemoryDefinition(StrictModel):
    seeds: Annotated[tuple[MemorySeed, ...], Field(max_length=128)] = ()
    retrieval: RetrievalPolicy
    write_policy: Annotated[tuple[MemoryWritePolicy, ...], Field(max_length=3)] = ()


class ToolRequest(StrictModel):
    id: NonEmptyText
    max_calls_per_run: Annotated[int, Field(ge=1, le=4)]


class CapabilityRequest(StrictModel):
    proposal_kinds: Annotated[tuple[ProposalKind, ...], Field(min_length=1, max_length=6)]
    tools: Annotated[tuple[ToolRequest, ...], Field(max_length=16)] = ()


class BehaviorDefinition(StrictModel):
    initiative: Initiative
    response_priority: ResponsePriority
    max_context_rounds: Annotated[int, Field(ge=1, le=4)]
    reflection: ReflectionPolicy


class NPCDefinition(StrictModel):
    id: AgentIdentifier
    display_name: NonEmptyText
    persona: PersonaDefinition
    memory: MemoryDefinition
    capabilities: CapabilityRequest
    behavior: BehaviorDefinition
    character_skill: RuntimeSkillReference
    prompt_profile: NonEmptyText


class Manifest(StrictModel):
    format_version: Literal[2]
    project_id: AgentIdentifier
    agents: Annotated[tuple[NPCDefinition, ...], Field(min_length=1, max_length=128)]


def load_manifest(path: Path) -> Manifest:
    """Load one JSON value and accept only the documented camelCase surface."""

    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ManifestDecodeError(f"read NPC manifest {path}: {error}") from error

    try:
        decoded = loads_strict_json(raw)
        return Manifest.model_validate_json(
            json.dumps(
                decoded,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ),
            strict=True,
            extra="forbid",
            by_alias=True,
            by_name=False,
        )
    except (TypeError, ValueError) as error:
        raise ManifestDecodeError(f"decode NPC manifest {path}: {error}") from error
