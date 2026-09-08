"""Versioned creative skills shared by Character, Director, and Broadcast Agents."""

from __future__ import annotations

import re
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import Field, StringConstraints, ValidationError, model_validator

from agent_runtime.model import StrictModel

AgentKind = Literal["character", "director", "broadcast"]
SkillIdentifier = Annotated[
    str,
    StringConstraints(min_length=1, pattern=r"^[a-z][a-z0-9._:-]*$"),
]
NonEmptyText = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]
_FRONTMATTER = re.compile(r"\A---\s*\r?\n(.*?)\r?\n---\s*\r?\n(.*)\Z", re.DOTALL)


class RuntimeSkillMetadata(StrictModel):
    """The only creator-controlled metadata accepted from a Skill file."""

    skill_id: SkillIdentifier
    version: NonEmptyText
    agent_kind: AgentKind


class RuntimeSkillReference(StrictModel):
    """One creator-requested immutable Skill version."""

    skill_id: SkillIdentifier
    version: NonEmptyText


class CompiledSkillReference(StrictModel):
    """A trusted Skill identity pinned to exact source bytes."""

    skill_id: SkillIdentifier
    version: NonEmptyText
    content_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class RuntimeSkill(StrictModel):
    """An immutable creative instruction pinned by version and content hash."""

    skill_id: SkillIdentifier
    version: NonEmptyText
    agent_kind: AgentKind
    body: NonEmptyText
    content_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    source_path: NonEmptyText
    source_text: Annotated[str, StringConstraints(min_length=1)] = Field(
        exclude=True,
        repr=False,
    )

    @model_validator(mode="after")
    def _require_source_hash(self) -> Self:
        if self.content_hash != sha256(self.source_text.encode("utf-8")).hexdigest():
            raise ValueError("runtime skill content hash does not match its source")
        metadata, body = _parse_skill_text(self.source_text, self.source_path)
        if (metadata.skill_id, metadata.version, metadata.agent_kind, body) != (
            self.skill_id,
            self.version,
            self.agent_kind,
            self.body,
        ):
            raise ValueError("runtime skill fields do not match its source")
        return self


class RuntimeSkillCatalog(StrictModel):
    """A deterministic in-memory catalog indexed by declared ID and version."""

    skills: tuple[RuntimeSkill, ...]

    @model_validator(mode="after")
    def _require_unique_versions(self) -> Self:
        keys = tuple((skill.skill_id, skill.version) for skill in self.skills)
        if len(keys) != len(set(keys)):
            raise ValueError("runtime skill id and version pairs must be unique")
        return self

    @classmethod
    def load(cls, root: Path) -> RuntimeSkillCatalog:
        """Load every Markdown Skill below one explicit catalog root."""

        if not root.is_dir():
            raise ValueError(f"runtime skill directory does not exist: {root}")
        return cls(skills=tuple(load_runtime_skill(path) for path in sorted(root.rglob("*.md"))))

    def resolve(
        self,
        skill_id: str,
        version: str,
        *,
        agent_kind: AgentKind,
    ) -> RuntimeSkill:
        """Resolve one exact version and reject cross-Agent-kind binding."""

        skill = next(
            (item for item in self.skills if item.skill_id == skill_id and item.version == version),
            None,
        )
        if skill is None:
            raise ValueError(f"runtime skill not found: {skill_id}@{version}")
        if skill.agent_kind != agent_kind:
            raise ValueError(
                f"runtime skill {skill_id}@{version} belongs to {skill.agent_kind}, "
                f"not {agent_kind}"
            )
        return skill


def load_runtime_skill(path: Path) -> RuntimeSkill:
    """Parse strict YAML frontmatter and pin its semantic content."""

    try:
        content = path.read_bytes()
        text = content.decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ValueError(f"cannot read runtime skill: {path}") from error

    metadata, body = _parse_skill_text(text, str(path))
    return RuntimeSkill(
        skill_id=metadata.skill_id,
        version=metadata.version,
        agent_kind=metadata.agent_kind,
        body=body,
        content_hash=sha256(content).hexdigest(),
        source_path=str(path.resolve()),
        source_text=text,
    )


def _parse_skill_text(text: str, source: str) -> tuple[RuntimeSkillMetadata, str]:
    match = _FRONTMATTER.fullmatch(text)
    if match is None:
        raise ValueError(f"runtime skill requires YAML frontmatter: {source}")
    try:
        raw_metadata = yaml.safe_load(match.group(1))
        metadata = RuntimeSkillMetadata.model_validate(raw_metadata, strict=True)
    except (yaml.YAMLError, ValidationError) as error:
        raise ValueError(f"runtime skill metadata is invalid: {source}") from error
    body = match.group(2).strip()
    if not body:
        raise ValueError(f"runtime skill body cannot be empty: {source}")
    return metadata, body
