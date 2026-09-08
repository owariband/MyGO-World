"""Compile untrusted creator manifests into trusted PersonAct specifications."""

from __future__ import annotations

import hashlib
from enum import StrEnum

from pydantic import Field

from agent_runtime.agent.personact.errors import ManifestCompileError
from agent_runtime.agent.personact.manifest import (
    BehaviorDefinition,
    Manifest,
    MemorySeed,
    MemoryWritePolicy,
    NPCDefinition,
    PersonaDefinition,
    RetrievalPolicy,
)
from agent_runtime.agent.skill import (
    CompiledSkillReference,
    RuntimeSkill,
)
from agent_runtime.model import StrictModel
from agent_runtime.world.contracts import ProposalKind


class ToolMode(StrEnum):
    QUERY = "query"
    COMPUTE = "compute"
    MUTATE = "mutate"


class ToolDefinition(StrictModel):
    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    mode: ToolMode


class PromptDefinition(StrictModel):
    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    digest: str = Field(min_length=1)


class Catalog(StrictModel):
    tools: tuple[ToolDefinition, ...]
    prompts: tuple[PromptDefinition, ...]
    skills: tuple[RuntimeSkill, ...] = ()

    def tool(self, tool_id: str) -> ToolDefinition | None:
        return next((tool for tool in self.tools if tool.id == tool_id), None)

    def prompt(self, prompt_id: str) -> PromptDefinition | None:
        return next((prompt for prompt in self.prompts if prompt.id == prompt_id), None)

    def skill(self, skill_id: str, version: str) -> RuntimeSkill | None:
        return next(
            (
                skill
                for skill in self.skills
                if skill.skill_id == skill_id and skill.version == version
            ),
            None,
        )


class ResolvedTool(StrictModel):
    id: str
    version: str
    mode: ToolMode
    max_calls_per_run: int


class PromptRef(StrictModel):
    id: str
    version: str
    digest: str


class CompiledMemorySeed(StrictModel):
    id: str
    type: str
    content: str
    tags: tuple[str, ...]
    provenance: str


class CompiledPersonActSpec(StrictModel):
    """Resolved capabilities accepted by the runtime, not raw creator wishes."""

    format_version: int
    project_id: str
    agent_id: str
    display_name: str
    digest: str
    persona: PersonaDefinition
    memory_scope: str
    seeds: tuple[CompiledMemorySeed, ...]
    retrieval: RetrievalPolicy
    write_policy: tuple[MemoryWritePolicy, ...]
    allowed_proposal_kinds: tuple[ProposalKind, ...]
    tools: tuple[ResolvedTool, ...]
    behavior: BehaviorDefinition
    character_skill: CompiledSkillReference
    prompt: PromptRef


class _DigestSeed(StrictModel):
    id: str
    type: str
    content: str
    tags: tuple[str, ...]


class _DigestSpec(StrictModel):
    """Canonical digest input without self-referential digest/provenance fields."""

    format_version: int
    project_id: str
    agent_id: str
    display_name: str
    persona: PersonaDefinition
    memory_scope: str
    seeds: tuple[_DigestSeed, ...]
    retrieval: RetrievalPolicy
    write_policy: tuple[MemoryWritePolicy, ...]
    allowed_proposal_kinds: tuple[ProposalKind, ...]
    tools: tuple[ResolvedTool, ...]
    behavior: BehaviorDefinition
    character_skill: CompiledSkillReference
    prompt: PromptRef


def compile_manifest(manifest: Manifest, catalog: Catalog) -> tuple[CompiledPersonActSpec, ...]:
    """Validate references and narrow every creator request through trusted catalogs."""

    agent_ids = tuple(agent.id for agent in manifest.agents)
    _require_unique(agent_ids, "agent ids")
    _require_unique(tuple(tool.id for tool in catalog.tools), "catalog tool ids")
    _require_unique(tuple(prompt.id for prompt in catalog.prompts), "catalog prompt ids")
    _require_unique(
        tuple(f"{skill.skill_id}@{skill.version}" for skill in catalog.skills),
        "catalog skill versions",
    )

    known_agent_ids = frozenset(agent_ids)
    return tuple(
        _compile_agent(
            definition=agent,
            format_version=manifest.format_version,
            project_id=manifest.project_id,
            known_agent_ids=known_agent_ids,
            catalog=catalog,
        )
        for agent in manifest.agents
    )


def _compile_agent(
    definition: NPCDefinition,
    format_version: int,
    project_id: str,
    known_agent_ids: frozenset[str],
    catalog: Catalog,
) -> CompiledPersonActSpec:
    _require_unique(tuple(goal.id for goal in definition.persona.goals), "goal ids")
    _require_unique(tuple(seed.id for seed in definition.memory.seeds), "memory seed ids")
    _require_unique(
        tuple(kind.value for kind in definition.capabilities.proposal_kinds),
        "proposal kinds",
    )
    _require_unique(
        tuple(policy.value for policy in definition.memory.write_policy),
        "memory write policies",
    )
    for relationship in definition.persona.relationships:
        if relationship.target_id not in known_agent_ids:
            message = (
                f"agent {definition.id!r} references unknown relationship target "
                f"{relationship.target_id!r}"
            )
            raise ManifestCompileError(message)

    allowed_proposal_kinds = tuple(
        sorted(definition.capabilities.proposal_kinds, key=lambda kind: kind.value)
    )
    write_policy = tuple(sorted(definition.memory.write_policy, key=lambda policy: policy.value))

    resolved_tools: list[ResolvedTool] = []
    seen_tool_ids: set[str] = set()
    for request in definition.capabilities.tools:
        tool = catalog.tool(request.id)
        if tool is None:
            raise ManifestCompileError(
                f'agent "{definition.id}" requests unknown tool "{request.id}"'
            )
        if tool.mode not in {ToolMode.QUERY, ToolMode.COMPUTE}:
            raise ManifestCompileError(
                f'agent "{definition.id}" requests non-read-only tool "{request.id}"'
            )
        if request.id in seen_tool_ids:
            raise ManifestCompileError(
                f'agent "{definition.id}" requests duplicate tool "{request.id}"'
            )
        seen_tool_ids.add(request.id)
        resolved_tools.append(
            ResolvedTool(
                id=tool.id,
                version=tool.version,
                mode=tool.mode,
                max_calls_per_run=request.max_calls_per_run,
            )
        )
    resolved_tools.sort(key=lambda tool: tool.id)

    prompt = catalog.prompt(definition.prompt_profile)
    if prompt is None:
        raise ManifestCompileError(
            f'agent "{definition.id}" requests unknown prompt profile "{definition.prompt_profile}"'
        )
    prompt_ref = PromptRef(id=prompt.id, version=prompt.version, digest=prompt.digest)
    skill = catalog.skill(
        definition.character_skill.skill_id,
        definition.character_skill.version,
    )
    if skill is None:
        raise ManifestCompileError(
            f'agent "{definition.id}" requests unknown Character Skill '
            f'"{definition.character_skill.skill_id}@{definition.character_skill.version}"'
        )
    if skill.agent_kind != "character":
        raise ManifestCompileError(
            f'agent "{definition.id}" requests non-character Skill "{skill.skill_id}"'
        )
    skill_ref = CompiledSkillReference(
        skill_id=skill.skill_id,
        version=skill.version,
        content_hash=skill.content_hash,
    )
    memory_scope = f"project/{project_id}/persona/{definition.id}"

    digest_spec = _DigestSpec(
        format_version=format_version,
        project_id=project_id,
        agent_id=definition.id,
        display_name=definition.display_name,
        persona=definition.persona,
        memory_scope=memory_scope,
        seeds=tuple(_digest_seed(seed) for seed in definition.memory.seeds),
        retrieval=definition.memory.retrieval,
        write_policy=write_policy,
        allowed_proposal_kinds=allowed_proposal_kinds,
        tools=tuple(resolved_tools),
        behavior=definition.behavior,
        character_skill=skill_ref,
        prompt=prompt_ref,
    )
    digest = _stable_digest(digest_spec)

    return CompiledPersonActSpec(
        format_version=format_version,
        project_id=project_id,
        agent_id=definition.id,
        display_name=definition.display_name,
        digest=digest,
        persona=definition.persona,
        memory_scope=memory_scope,
        seeds=tuple(_compiled_seed(seed, digest) for seed in definition.memory.seeds),
        retrieval=definition.memory.retrieval,
        write_policy=write_policy,
        allowed_proposal_kinds=allowed_proposal_kinds,
        tools=tuple(resolved_tools),
        behavior=definition.behavior,
        character_skill=skill_ref,
        prompt=prompt_ref,
    )


def _digest_seed(seed: MemorySeed) -> _DigestSeed:
    return _DigestSeed(id=seed.id, type=seed.type, content=seed.content, tags=seed.tags)


def _compiled_seed(seed: MemorySeed, digest: str) -> CompiledMemorySeed:
    return CompiledMemorySeed(
        id=seed.id,
        type=seed.type,
        content=seed.content,
        tags=seed.tags,
        provenance=f"manifest:{digest}#{seed.id}",
    )


def _stable_digest(spec: _DigestSpec) -> str:
    # Pydantic preserves declared field order. Together with tuple sorting and
    # pinned Pydantic, this is the versioned canonical representation for v1.
    canonical_json = spec.model_dump_json(by_alias=True, exclude_none=False)
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def _require_unique(values: tuple[str, ...], label: str) -> None:
    if len(values) != len(set(values)):
        raise ManifestCompileError(f"{label} must be unique")
