from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest
import yaml
from conftest import MINIMAL_SEED, REPOSITORY_ROOT
from sqlalchemy.orm import Session

from mygo_world.contracts import load_seed
from mygo_world.db.engine import create_world_engine
from mygo_world.errors import SeedInvalidError, WorldError
from mygo_world.gateways import FixtureGateway
from mygo_world.memory import AgentMemoryRepository
from mygo_world.runtime import _default_fixture_responses, advance_world
from mygo_world.skill_bindings import bind_character_skill, resolve_seed_skills
from mygo_world.skills import RuntimeSkillCatalog, load_runtime_skill
from mygo_world.worlds import initialize_world, show_world

SKILLS_DIR = REPOSITORY_ROOT / "content" / "skills"


def test_runtime_skill_frontmatter_is_strict_and_body_is_loaded(tmp_path: Path) -> None:
    skill_path = tmp_path / "skill.md"
    skill_path.write_text(
        "---\nskill_id: test.character\nversion: 1.2.3\n"
        "agent_kind: character\n---\nA concrete creative voice.\n",
        encoding="utf-8",
    )
    skill = load_runtime_skill(skill_path)
    assert skill.body == "A concrete creative voice."
    assert len(skill.content_hash) == 64

    skill_path.write_text(
        "---\nskill_id: test.character\nversion: 1.2.3\n"
        "agent_kind: character\nmodel: forbidden\n---\nBody.\n",
        encoding="utf-8",
    )
    with pytest.raises(WorldError, match="model") as error:
        load_runtime_skill(skill_path)
    assert error.value.code == "SKILL_INVALID"


def test_minimal_scenario_uses_layered_chinese_skills() -> None:
    loaded = load_seed(MINIMAL_SEED)
    bindings = resolve_seed_skills(loaded.seed, RuntimeSkillCatalog.load(SKILLS_DIR))
    skills = {item.skill_id: item for item in bindings}

    assert skills["mygo.character.anon"].version == "3.0.0"
    assert skills["mygo.character.soyo"].version == "2.0.0"
    assert skills["mygo.director.default"].version == "2.1.0"
    assert skills["mygo.broadcast.default"].version == "2.0.0"
    assert "核心驱动力" in skills["mygo.character.anon"].body
    assert "核心驱动力" in skills["mygo.character.soyo"].body
    assert "群像叙事" in skills["mygo.director.default"].body
    assert "没有行动时保持世界原状" in skills["mygo.director.default"].body
    assert "视觉小说" in skills["mygo.broadcast.default"].body

    formal_bodies = "\n".join(item.body for item in skills.values())
    for misplaced_instruction in (
        "World Version",
        "world_version",
        "session_id",
        "actor_id",
        "source_kind",
        "object-set-list",
    ):
        assert misplaced_instruction not in formal_bodies


@pytest.mark.parametrize(
    "skill_id",
    [
        "mygo.character.anon",
        "mygo.character.soyo",
        "mygo.character.tomori",
        "mygo.character.taki",
        "mygo.character.rana",
    ],
)
def test_v0_character_skills_follow_current_formal_structure(skill_id: str) -> None:
    skill = RuntimeSkillCatalog.load(SKILLS_DIR).resolve(
        skill_id, "0.0.0", agent_kind="character"
    )

    for heading in (
        "## 核心气质",
        "## 核心驱动力",
        "## 核心矛盾",
        "## 注意与判断倾向",
        "## 行动倾向",
        "## 关系与连续性",
        "## 表达风格",
    ):
        assert heading in skill.body
    for misplaced_content in (
        "## 外貌",
        "## 声音",
        "World Version",
        "world_version",
        "session_id",
        "actor_id",
    ):
        assert misplaced_content not in skill.body


@pytest.mark.parametrize(
    ("skill_id", "agent_kind", "required_headings"),
    [
        (
            "mygo.director.default",
            "director",
            ("## 叙事取向", "## 推进方式", "## 冲突与收束", "## 表达风格"),
        ),
        (
            "mygo.broadcast.default",
            "broadcast",
            ("## 编排取向", "## 节奏", "## 表达风格"),
        ),
    ],
)
def test_v0_global_skills_are_minimal_and_formal(
    skill_id: str, agent_kind: str, required_headings: tuple[str, ...]
) -> None:
    skill = RuntimeSkillCatalog.load(SKILLS_DIR).resolve(
        skill_id, "0.0.0", agent_kind=agent_kind  # type: ignore[arg-type]
    )

    assert all(heading in skill.body for heading in required_headings)
    for misplaced_content in (
        "World Version",
        "world_version",
        "session_id",
        "actor_id",
        "source_kind",
        "Pydantic",
    ):
        assert misplaced_content not in skill.body


def test_init_persists_exact_bindings_and_advance_uses_skill_body(
    worlds_dir: Path,
) -> None:
    initialized = initialize_world(MINIMAL_SEED, "skills", worlds_dir)
    assert len(initialized["skill_bindings"]) == 4
    advance_world("skills", worlds_dir)

    database = worlds_dir / "skills" / "world.sqlite3"
    with sqlite3.connect(database) as connection:
        bindings = connection.execute(
            "SELECT agent_kind, agent_id, skill_id, skill_version, "
            "skill_content_hash FROM skill_bindings ORDER BY binding_order"
        ).fetchall()
        traces = connection.execute(
            "SELECT agent_type, agent_id, skill_id, skill_version, "
            "skill_content_hash, request_json FROM generation_traces "
            "ORDER BY agent_type, agent_id"
        ).fetchall()
    assert len(bindings) == 4
    assert len(traces) == 2
    for agent_type, agent_id, skill_id, version, content_hash, request_json in traces:
        binding = next(
            item for item in bindings if item[0] == agent_type and item[1] == agent_id
        )
        assert (skill_id, version, content_hash) == binding[2:]
        assert json.loads(request_json)["skill_body"]


def test_unchanged_version_with_changed_content_fails_before_model_call(
    worlds_dir: Path, tmp_path: Path
) -> None:
    copied = tmp_path / "skills"
    shutil.copytree(SKILLS_DIR, copied)
    initialize_world(MINIMAL_SEED, "tampered-skill", worlds_dir, skills_dir=copied)
    path = copied / "characters" / "anon-3.0.0.md"
    path.write_text(path.read_text(encoding="utf-8") + "Changed.\n", encoding="utf-8")

    with pytest.raises(WorldError) as error:
        advance_world("tampered-skill", worlds_dir, skills_dir=copied)
    assert error.value.code == "SKILL_CONTENT_HASH_MISMATCH"
    with sqlite3.connect(worlds_dir / "tampered-skill" / "world.sqlite3") as connection:
        assert connection.execute(
            "SELECT count(*) FROM generation_batches"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM generation_traces"
        ).fetchone() == (0,)


def test_seed_memory_round_trips_and_repository_enforces_owner_namespace(
    worlds_dir: Path, tmp_path: Path
) -> None:
    raw = yaml.safe_load(MINIMAL_SEED.read_text(encoding="utf-8"))
    raw["memories"] = [
        {
            "memory_id": "anon-belief",
            "agent_id": "character-anon",
            "namespace": "private",
            "memory_type": "belief",
            "content": "Soyo may want to rehearse.",
            "relative_time_ms": 10,
            "importance": 3,
            "entity_tags": ["character-soyo"],
            "location_tags": ["location-live-house"],
            "source": "scenario_seed",
        },
        {
            "memory_id": "anon-commitment",
            "agent_id": "character-anon",
            "namespace": "private",
            "memory_type": "commitment",
            "content": "Invite Soyo to practice.",
            "relative_time_ms": 20,
            "importance": 5,
            "entity_tags": ["character-soyo"],
            "location_tags": ["location-live-house"],
            "source": "scenario_seed",
            "status": "active",
        },
        {
            "memory_id": "soyo-observation",
            "agent_id": "character-soyo",
            "namespace": "private",
            "memory_type": "observation",
            "content": "Anon arrived.",
            "relative_time_ms": 5,
            "importance": 2,
            "source": "scenario_seed",
        },
    ]
    seed = tmp_path / "memories.yaml"
    seed.write_text(yaml.safe_dump(raw), encoding="utf-8")
    initialize_world(seed, "memory", worlds_dir)

    public = show_world("memory", worlds_dir)
    assert "memories" not in public
    assert "observations" not in public

    shown = show_world(
        "memory",
        worlds_dir,
        memory_agent_id="character-anon",
        memory_namespace="private",
    )
    assert {item["memory_id"] for item in shown["memories"]} == {
        "anon-belief",
        "anon-commitment",
    }
    commitment = next(
        item for item in shown["memories"] if item["memory_id"] == "anon-commitment"
    )
    assert commitment["status"] == "active"
    assert commitment["entity_tags"] == ["character-soyo"]

    engine = create_world_engine(worlds_dir / "memory" / "world.sqlite3")
    try:
        with Session(engine) as session:
            repository = AgentMemoryRepository(session)
            anon = repository.retrieve(
                agent_id="character-anon",
                namespace="private",
                entity_tags=["character-soyo"],
                limit=1,
            )
            soyo = repository.retrieve(agent_id="character-soyo", namespace="private")
        assert [item.memory_id for item in anon] == ["anon-commitment"]
        assert [item.memory_id for item in soyo] == ["soyo-observation"]
    finally:
        engine.dispose()


def test_skill_bind_is_audited_without_advancing_world_and_survives_restart(
    worlds_dir: Path,
) -> None:
    initialize_world(MINIMAL_SEED, "binding", worlds_dir)
    advance_world("binding", worlds_dir)
    before = show_world("binding", worlds_dir)["world_version"]

    receipt = bind_character_skill(
        "binding",
        worlds_dir,
        character_id="character-soyo",
        skill_id="mygo.character.soyo",
        skill_version="1.0.0",
        operator="test-operator",
        reason="exercise a calmer voice",
    )
    assert receipt["world_version"] == before
    assert receipt["previous_skill"]["version"] == "2.0.0"
    assert receipt["new_skill"]["version"] == "1.0.0"

    advance_world("binding", worlds_dir)
    database = worlds_dir / "binding" / "world.sqlite3"
    with sqlite3.connect(database) as connection:
        versions = connection.execute(
            "SELECT skill_version FROM generation_traces "
            "WHERE agent_id='character-soyo' ORDER BY created_at, trace_id"
        ).fetchall()
        audit = connection.execute(
            "SELECT operator, reason, previous_skill_version, skill_version "
            "FROM skill_bindings WHERE agent_id='character-soyo' "
            "ORDER BY binding_order"
        ).fetchall()
    assert {item[0] for item in versions} == {"1.0.0"}
    assert audit[-1] == (
        "test-operator",
        "exercise a calmer voice",
        "2.0.0",
        "1.0.0",
    )


def test_belief_and_commitment_changes_append_successor_records(
    worlds_dir: Path, tmp_path: Path
) -> None:
    raw = yaml.safe_load(MINIMAL_SEED.read_text(encoding="utf-8"))
    raw["memories"] = [
        {
            "memory_id": "belief-old",
            "agent_id": "character-anon",
            "memory_type": "belief",
            "content": "Rehearsal will be tense.",
        },
        {
            "memory_id": "commitment-old",
            "agent_id": "character-anon",
            "memory_type": "commitment",
            "content": "Greet Soyo.",
            "status": "active",
        },
    ]
    seed = tmp_path / "successors.yaml"
    seed.write_text(yaml.safe_dump(raw), encoding="utf-8")
    initialize_world(seed, "successors", worlds_dir)
    snapshot = show_world("successors", worlds_dir)["snapshot"]
    responses = _default_fixture_responses(
        snapshot=snapshot,
        session_id="session-first-meeting",
        actor_id="character-anon",
        participant_ids=["character-anon", "character-soyo"],
    )
    responses["character:character-anon:action_proposal"]["memory_changes"] = [
        {
            "agent_id": "character-anon",
            "namespace": "default",
            "memory_type": "belief",
            "content": "Rehearsal may be welcoming.",
            "importance": 3,
            "supersedes_memory_id": "belief-old",
        },
        {
            "agent_id": "character-anon",
            "namespace": "default",
            "memory_type": "commitment",
            "content": "Greeted Soyo.",
            "importance": 2,
            "status": "completed",
            "supersedes_memory_id": "commitment-old",
        },
    ]
    advance_world(
        "successors",
        worlds_dir,
        gateway=FixtureGateway(responses),
        max_waves=1,
    )
    memories = show_world(
        "successors",
        worlds_dir,
        memory_agent_id="character-anon",
        memory_namespace="default",
    )["memories"]
    old_belief = next(item for item in memories if item["memory_id"] == "belief-old")
    old_commitment = next(
        item for item in memories if item["memory_id"] == "commitment-old"
    )
    assert old_belief["payload"]["content"] == "Rehearsal will be tense."
    assert old_commitment["status"] == "active"
    assert any(
        item["memory_type"] == "belief" and item["supersedes_memory_id"] == "belief-old"
        for item in memories
    )
    assert any(
        item["status"] == "completed"
        and item["supersedes_memory_id"] == "commitment-old"
        for item in memories
    )


def test_memory_and_binding_history_reject_update_and_delete(worlds_dir: Path) -> None:
    initialize_world(MINIMAL_SEED, "immutable-config", worlds_dir)
    database = worlds_dir / "immutable-config" / "world.sqlite3"
    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="MEMORY_IMMUTABLE"):
            connection.execute(
                "UPDATE agent_memory_records SET importance=5 WHERE memory_id=?",
                ("memory-anon-arrival",),
            )
        with pytest.raises(sqlite3.IntegrityError, match="SKILL_BINDING_IMMUTABLE"):
            connection.execute("DELETE FROM skill_bindings")


def test_init_rejects_skill_kind_mismatch(worlds_dir: Path, tmp_path: Path) -> None:
    raw = yaml.safe_load(MINIMAL_SEED.read_text(encoding="utf-8"))
    raw["skill_bindings"]["director"] = {
        "skill_id": "mygo.character.anon",
        "version": "1.0.0",
    }
    seed = tmp_path / "wrong-kind.yaml"
    seed.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(SeedInvalidError, match="not 'director'"):
        initialize_world(seed, "wrong-kind", worlds_dir)
