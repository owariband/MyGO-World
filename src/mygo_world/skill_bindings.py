from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from mygo_world.committer import Clock, IdGenerator, system_clock, uuid4_id
from mygo_world.contracts import ScenarioSeed
from mygo_world.db.engine import create_world_engine, require_current_schema
from mygo_world.db.models import EntityRevisionRow, SkillBindingRow, WorldRow
from mygo_world.errors import WorldError, WorldNotFoundError
from mygo_world.skills import (
    DEFAULT_SKILLS_DIR,
    EffectiveSkill,
    RuntimeSkill,
    RuntimeSkillCatalog,
)
from mygo_world.worlds import WorldPaths, mutation_lock, validate_world_id

AgentKind = Literal["character", "director", "broadcast"]


def resolve_seed_skills(
    seed: ScenarioSeed, catalog: RuntimeSkillCatalog
) -> tuple[EffectiveSkill, ...]:
    requested: list[tuple[AgentKind, str, str, str]] = [
        (
            "character",
            character_id,
            reference.skill_id,
            reference.version,
        )
        for character_id, reference in sorted(seed.skill_bindings.characters.items())
    ]
    requested.extend(
        [
            (
                "director",
                "global-director",
                seed.skill_bindings.director.skill_id,
                seed.skill_bindings.director.version,
            ),
            (
                "broadcast",
                "global-broadcast",
                seed.skill_bindings.broadcast.skill_id,
                seed.skill_bindings.broadcast.version,
            ),
        ]
    )
    return tuple(
        _effective(
            agent_kind,
            agent_id,
            catalog.resolve(skill_id, version, agent_kind=agent_kind),
        )
        for agent_kind, agent_id, skill_id, version in requested
    )


def load_effective_skills(
    engine: Engine,
    *,
    world_id: str,
    catalog: RuntimeSkillCatalog,
) -> dict[tuple[str, str], EffectiveSkill]:
    with Session(engine) as session:
        rows = list(
            session.scalars(
                select(SkillBindingRow)
                .where(SkillBindingRow.world_id == world_id)
                .order_by(SkillBindingRow.binding_order)
            )
        )
    latest: dict[tuple[str, str], SkillBindingRow] = {}
    for row in rows:
        latest[(row.agent_kind, row.agent_id)] = row
    result: dict[tuple[str, str], EffectiveSkill] = {}
    for key, row in latest.items():
        skill = catalog.resolve(
            row.skill_id,
            row.skill_version,
            agent_kind=row.agent_kind,  # type: ignore[arg-type]
        )
        if skill.content_hash != row.skill_content_hash:
            raise WorldError(
                "SKILL_CONTENT_HASH_MISMATCH",
                f"Runtime Skill '{row.skill_id}@{row.skill_version}' content changed "
                "without a declared version change",
            )
        result[key] = _effective(
            row.agent_kind,  # type: ignore[arg-type]
            row.agent_id,
            skill,
        )
    return result


def require_effective_skill(
    bindings: dict[tuple[str, str], EffectiveSkill],
    agent_kind: AgentKind,
    agent_id: str,
) -> EffectiveSkill:
    try:
        return bindings[(agent_kind, agent_id)]
    except KeyError as exc:
        raise WorldError(
            "SKILL_BINDING_MISSING",
            f"No Runtime Skill is bound for {agent_kind} Agent '{agent_id}'",
        ) from exc


def bind_character_skill(
    world_id: str,
    worlds_dir: Path,
    *,
    character_id: str,
    skill_id: str,
    skill_version: str,
    operator: str,
    reason: str,
    skills_dir: Path = DEFAULT_SKILLS_DIR,
    clock: Clock = system_clock,
    id_generator: IdGenerator = uuid4_id,
) -> dict[str, object]:
    validate_world_id(world_id)
    if not operator.strip() or not reason.strip():
        raise WorldError("SKILL_BIND_INVALID", "operator and reason must not be empty")
    paths = WorldPaths(worlds_dir, world_id)
    if not paths.database.is_file():
        raise WorldNotFoundError(world_id)
    catalog = RuntimeSkillCatalog.load(skills_dir)
    new_skill = catalog.resolve(skill_id, skill_version, agent_kind="character")

    with mutation_lock(paths.mutation_lock):
        engine = create_world_engine(paths.database)
        try:
            require_current_schema(paths.database, engine)
            bindings = load_effective_skills(engine, world_id=world_id, catalog=catalog)
            previous = require_effective_skill(bindings, "character", character_id)
            with Session(engine) as session, session.begin():
                world = session.get(WorldRow, world_id)
                if world is None:
                    raise WorldNotFoundError(world_id)
                character = session.scalar(
                    select(EntityRevisionRow).where(
                        EntityRevisionRow.entity_id == character_id,
                        EntityRevisionRow.entity_type == "character",
                    )
                )
                if character is None:
                    raise WorldError(
                        "CHARACTER_NOT_FOUND",
                        f"Character '{character_id}' does not exist in World '{world_id}'",
                    )
                order = (
                    int(
                        session.scalar(
                            select(func.max(SkillBindingRow.binding_order)).where(
                                SkillBindingRow.world_id == world_id
                            )
                        )
                        or 0
                    )
                    + 1
                )
                bound_at = _timestamp(clock())
                binding_id = id_generator()
                session.add(
                    SkillBindingRow(
                        binding_id=binding_id,
                        binding_order=order,
                        world_id=world_id,
                        agent_kind="character",
                        agent_id=character_id,
                        previous_skill_id=previous.skill_id,
                        previous_skill_version=previous.version,
                        previous_skill_content_hash=previous.content_hash,
                        skill_id=new_skill.skill_id,
                        skill_version=new_skill.version,
                        skill_content_hash=new_skill.content_hash,
                        operator=operator.strip(),
                        reason=reason.strip(),
                        bound_at=bound_at,
                    )
                )
                world_version = world.current_version
            return {
                "command": "skill-bind",
                "status": "bound",
                "world_id": world_id,
                "world_version": world_version,
                "character_id": character_id,
                "binding_id": binding_id,
                "operator": operator.strip(),
                "reason": reason.strip(),
                "previous_skill": {
                    "skill_id": previous.skill_id,
                    "version": previous.version,
                    "content_hash": previous.content_hash,
                },
                "new_skill": {
                    "skill_id": new_skill.skill_id,
                    "version": new_skill.version,
                    "content_hash": new_skill.content_hash,
                },
                "bound_at": bound_at,
                "database_path": str(paths.database),
            }
        finally:
            engine.dispose()


def _effective(
    agent_kind: AgentKind, agent_id: str, skill: RuntimeSkill
) -> EffectiveSkill:
    return EffectiveSkill(
        agent_kind=agent_kind,
        agent_id=agent_id,
        skill_id=skill.skill_id,
        version=skill.version,
        content_hash=skill.content_hash,
        body=skill.body,
    )


def _timestamp(value: datetime) -> str:
    if value.utcoffset() is None:
        raise ValueError("Clock must return a timezone-aware datetime")
    return value.astimezone(UTC).isoformat(timespec="microseconds")
