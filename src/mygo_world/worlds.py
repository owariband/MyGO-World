from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mygo_world.canonical import sha256_text
from mygo_world.committer import (
    Clock,
    GenesisCommitPlan,
    IdGenerator,
    WorldCommitter,
    system_clock,
    uuid4_id,
)
from mygo_world.contracts import LoadedSeed, load_seed
from mygo_world.db.engine import (
    create_world_engine,
    require_current_schema,
    upgrade_to_head,
)
from mygo_world.db.models import AgentMemoryRow, SnapshotRow, WorldEventRow, WorldRow
from mygo_world.errors import (
    SeedInvalidError,
    WorldAlreadyExistsError,
    WorldError,
    WorldNotFoundError,
)
from mygo_world.skills import DEFAULT_SKILLS_DIR, RuntimeSkillCatalog

WORLD_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class WorldPaths:
    def __init__(self, worlds_dir: Path, world_id: str) -> None:
        self.worlds_dir = worlds_dir.resolve()
        self.world_dir = self.worlds_dir / world_id
        self.database = self.world_dir / "world.sqlite3"
        self.mutation_lock = self.world_dir / "mutation.lock"
        self.render_lock = self.world_dir / "render.lock"


def validate_world_id(world_id: str) -> None:
    if not WORLD_ID_PATTERN.fullmatch(world_id):
        raise SeedInvalidError(
            "world_id must start with an ASCII letter or digit and contain only "
            "letters, digits, '.', '_' or '-' (maximum 64 characters)"
        )


@contextmanager
def mutation_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _materialize(
    path: Path,
    world_id: str,
    loaded: LoadedSeed,
    *,
    clock: Clock,
    id_generator: IdGenerator,
    skill_bindings: tuple[Any, ...],
) -> dict[str, Any]:
    upgrade_to_head(path)
    engine = create_world_engine(path)
    seed = loaded.seed

    try:
        result = WorldCommitter(
            engine,
            clock=clock,
            id_generator=id_generator,
        ).commit_genesis(
            GenesisCommitPlan(
                world_id=world_id,
                loaded_seed=loaded,
                skill_bindings=skill_bindings,
            )
        )
    finally:
        engine.dispose()

    return {
        "command": "init",
        "status": "created",
        "world_id": world_id,
        "world_version": result.world_version,
        "snapshot_checksum": result.snapshot_checksum,
        "seed": {
            "seed_id": seed.seed_id,
            "version": seed.version,
            "content_hash": loaded.content_hash,
        },
        "database_path": str(path.resolve()),
        "skill_bindings": [
            {
                "agent_kind": item.agent_kind,
                "agent_id": item.agent_id,
                "skill_id": item.skill_id,
                "version": item.version,
                "content_hash": item.content_hash,
            }
            for item in skill_bindings
        ],
    }


def initialize_world(
    seed_path: Path,
    world_id: str,
    worlds_dir: Path,
    *,
    clock: Clock = system_clock,
    id_generator: IdGenerator = uuid4_id,
    skills_dir: Path = DEFAULT_SKILLS_DIR,
) -> dict[str, Any]:
    validate_world_id(world_id)
    paths = WorldPaths(worlds_dir, world_id)
    if paths.database.exists():
        raise WorldAlreadyExistsError(world_id)
    loaded = load_seed(seed_path)
    # Imported lazily to keep the World lifecycle module independent from the
    # operator-facing binding command, which itself uses WorldPaths and its lock.
    from mygo_world.skill_bindings import resolve_seed_skills

    try:
        skill_bindings = resolve_seed_skills(
            loaded.seed, RuntimeSkillCatalog.load(skills_dir)
        )
    except WorldError as exc:
        raise SeedInvalidError(exc.message) from exc

    with mutation_lock(paths.mutation_lock):
        if paths.database.exists():
            raise WorldAlreadyExistsError(world_id)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=paths.world_dir,
            prefix=".world.",
            suffix=".sqlite3.tmp",
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            receipt = _materialize(
                temporary,
                world_id,
                loaded,
                clock=clock,
                id_generator=id_generator,
                skill_bindings=skill_bindings,
            )
            with temporary.open("rb") as database_file:
                os.fsync(database_file.fileno())
            os.replace(temporary, paths.database)
            directory_fd = os.open(paths.world_dir, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except Exception:
            for suffix in ("", "-journal", "-wal", "-shm"):
                Path(f"{temporary}{suffix}").unlink(missing_ok=True)
            raise

    receipt["database_path"] = str(paths.database)
    return receipt


def show_world(world_id: str, worlds_dir: Path) -> dict[str, Any]:
    validate_world_id(world_id)
    paths = WorldPaths(worlds_dir, world_id)
    if not paths.database.is_file():
        raise WorldNotFoundError(world_id)
    engine = create_world_engine(paths.database)
    try:
        require_current_schema(paths.database, engine)
        with Session(engine) as session:
            world = session.scalar(
                select(WorldRow).where(WorldRow.world_id == world_id)
            )
            if world is None:
                raise WorldNotFoundError(world_id)
            snapshot_row = session.get(SnapshotRow, world.current_version)
            if snapshot_row is None:
                raise WorldError(
                    "SNAPSHOT_MISSING",
                    f"Snapshot for World Version {world.current_version} is missing",
                )
            calculated = sha256_text(snapshot_row.snapshot_json)
            if calculated != snapshot_row.checksum:
                raise WorldError(
                    "SNAPSHOT_CHECKSUM_MISMATCH",
                    f"Snapshot for World Version {world.current_version} failed checksum validation",
                )
            event_count = session.scalar(
                select(func.count()).select_from(WorldEventRow)
            )
            events = list(
                session.scalars(
                    select(WorldEventRow).order_by(WorldEventRow.event_order)
                )
            )
            observations = list(
                session.scalars(
                    select(AgentMemoryRow)
                    .where(AgentMemoryRow.memory_type == "observation")
                    .order_by(AgentMemoryRow.memory_id)
                )
            )
            memories = list(
                session.scalars(
                    select(AgentMemoryRow).order_by(
                        AgentMemoryRow.agent_id,
                        AgentMemoryRow.namespace,
                        AgentMemoryRow.memory_id,
                    )
                )
            )
            snapshot = json.loads(snapshot_row.snapshot_json)
            return {
                "command": "show",
                "status": "ok",
                "world_id": world.world_id,
                "world_name": world.name,
                "world_version": world.current_version,
                "world_time_ms": snapshot["world_time_ms"],
                "snapshot": snapshot,
                "snapshot_checksum": snapshot_row.checksum,
                "world_event_count": event_count,
                "world_events": [
                    {
                        "event_id": item.event_id,
                        "event_order": item.event_order,
                        "world_version": item.world_version,
                        "session_id": item.session_id,
                        "start_time_ms": item.start_time_ms,
                        "end_time_ms": item.end_time_ms,
                        "event_type": item.event_type,
                        "payload": json.loads(item.payload_json),
                    }
                    for item in events
                ],
                "observations": [
                    {
                        "memory_id": item.memory_id,
                        "agent_id": item.agent_id,
                        "world_version": item.world_version,
                        "relative_time_ms": item.relative_time_ms,
                        "payload": json.loads(item.payload_json),
                    }
                    for item in observations
                ],
                "memories": [
                    {
                        "memory_id": item.memory_id,
                        "agent_id": item.agent_id,
                        "namespace": item.namespace,
                        "memory_type": item.memory_type,
                        "world_version": item.world_version,
                        "relative_time_ms": item.relative_time_ms,
                        "importance": item.importance,
                        "entity_tags": json.loads(item.entity_tags_json),
                        "location_tags": json.loads(item.location_tags_json),
                        "source": item.source,
                        "status": item.status,
                        "supersedes_memory_id": item.supersedes_memory_id,
                        "payload": json.loads(item.payload_json),
                    }
                    for item in memories
                ],
                "seed": {
                    "seed_id": world.seed_id,
                    "version": world.seed_version,
                    "content_hash": world.seed_content_hash,
                },
                "database_path": str(paths.database),
            }
    finally:
        engine.dispose()
