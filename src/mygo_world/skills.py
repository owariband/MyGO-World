from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mygo_world.canonical import sha256_bytes
from mygo_world.errors import WorldError

DEFAULT_SKILLS_DIR = Path(__file__).resolve().parents[2] / "content" / "skills"
_SKILL_ID_PATTERN = r"^[a-z][a-z0-9._:-]*$"
_FRONTMATTER = re.compile(r"\A---\s*\r?\n(.*?)\r?\n---\s*\r?\n(.*)\Z", re.DOTALL)


class RuntimeSkillMetadata(BaseModel):
    """Only creative identity belongs in Runtime Skill frontmatter."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    skill_id: str = Field(pattern=_SKILL_ID_PATTERN)
    version: str = Field(min_length=1, max_length=100)
    agent_kind: Literal["character", "director", "broadcast"]


@dataclass(frozen=True)
class RuntimeSkill:
    skill_id: str
    version: str
    agent_kind: Literal["character", "director", "broadcast"]
    body: str
    content_hash: str
    path: Path


@dataclass(frozen=True)
class EffectiveSkill:
    agent_kind: Literal["character", "director", "broadcast"]
    agent_id: str
    skill_id: str
    version: str
    content_hash: str
    body: str


class RuntimeSkillCatalog:
    def __init__(self, skills: list[RuntimeSkill]) -> None:
        by_key: dict[tuple[str, str], RuntimeSkill] = {}
        for skill in skills:
            key = (skill.skill_id, skill.version)
            if key in by_key:
                raise WorldError(
                    "SKILL_DUPLICATE",
                    f"Runtime Skill '{skill.skill_id}@{skill.version}' is duplicated",
                )
            by_key[key] = skill
        self._by_key = by_key

    @classmethod
    def load(cls, skills_dir: Path = DEFAULT_SKILLS_DIR) -> RuntimeSkillCatalog:
        root = skills_dir.resolve()
        if not root.is_dir():
            raise WorldError(
                "SKILL_DIRECTORY_NOT_FOUND",
                f"Runtime Skill directory '{root}' does not exist",
            )
        return cls([load_runtime_skill(path) for path in sorted(root.rglob("*.md"))])

    def resolve(
        self,
        skill_id: str,
        version: str,
        *,
        agent_kind: Literal["character", "director", "broadcast"] | None = None,
    ) -> RuntimeSkill:
        skill = self._by_key.get((skill_id, version))
        if skill is None:
            raise WorldError(
                "SKILL_NOT_FOUND",
                f"Runtime Skill '{skill_id}@{version}' was not found",
            )
        if agent_kind is not None and skill.agent_kind != agent_kind:
            raise WorldError(
                "SKILL_KIND_MISMATCH",
                f"Runtime Skill '{skill_id}@{version}' is '{skill.agent_kind}', "
                f"not '{agent_kind}'",
            )
        return skill


def load_runtime_skill(path: Path) -> RuntimeSkill:
    try:
        content = path.read_bytes()
        text = content.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise WorldError(
            "SKILL_INVALID", f"Cannot read Runtime Skill '{path}': {exc}"
        ) from exc
    match = _FRONTMATTER.fullmatch(text)
    if match is None:
        raise WorldError(
            "SKILL_INVALID",
            f"Runtime Skill '{path}' must contain YAML frontmatter and a Markdown body",
        )
    try:
        raw = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise WorldError(
            "SKILL_INVALID", f"Runtime Skill '{path}' has invalid YAML: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise WorldError(
            "SKILL_INVALID", f"Runtime Skill '{path}' frontmatter must be a mapping"
        )
    try:
        metadata = RuntimeSkillMetadata.model_validate(raw)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(item) for item in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise WorldError(
            "SKILL_INVALID", f"Runtime Skill '{path}' metadata is invalid: {details}"
        ) from exc
    body = match.group(2).strip()
    if not body:
        raise WorldError(
            "SKILL_INVALID", f"Runtime Skill '{path}' Markdown body must not be empty"
        )
    return RuntimeSkill(
        skill_id=metadata.skill_id,
        version=metadata.version,
        agent_kind=metadata.agent_kind,
        body=body,
        content_hash=sha256_bytes(content),
        path=path.resolve(),
    )
