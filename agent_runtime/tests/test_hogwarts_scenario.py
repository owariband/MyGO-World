"""M4 MyGO x Hogwarts content and bootstrap acceptance tests."""

from __future__ import annotations

import shutil
from pathlib import Path

from agent_runtime.agent.personact.compiler import (
    Catalog,
    PromptDefinition,
    ToolDefinition,
    ToolMode,
)
from agent_runtime.agent.personact.manifest import Familiarity
from agent_runtime.agent.skill import RuntimeSkillCatalog
from agent_runtime.bootstrap import create_world
from agent_runtime.event.story_line import StoryLineBuilder
from agent_runtime.scenario import ExplicitAgents, load_project_scenario
from agent_runtime.sqlite import open_project_database
from agent_runtime.world.state import WorldStatus

REPOSITORY_ROOT = Path(__file__).parents[2]
PROJECTS_ROOT = REPOSITORY_ROOT / "projects"
SKILLS_ROOT = REPOSITORY_ROOT / "content" / "skills"
PROJECT_ID = "mygo-hogwarts"
EXPECTED_AGENT_IDS = {
    "anon",
    "mutsumi",
    "nyamu",
    "rana",
    "sakiko",
    "soyo",
    "taki",
    "tomori",
    "uika",
    "umiri",
}
EXPECTED_PARTITIONS = {
    "session-tomori": {"session-tomori", "session-taki"},
    "session-soyo": {"session-soyo", "session-uika", "session-umiri"},
    "session-sakiko": {"session-sakiko", "session-nyamu"},
    "session-mutsumi": {"session-mutsumi", "session-anon", "session-rana"},
}
EXPECTED_HOUSES = {
    "tomori": "gryffindor",
    "taki": "gryffindor",
    "soyo": "hufflepuff",
    "uika": "hufflepuff",
    "umiri": "hufflepuff",
    "sakiko": "slytherin",
    "nyamu": "slytherin",
    "mutsumi": "ravenclaw",
    "anon": "ravenclaw",
    "rana": "ravenclaw",
}
EXPECTED_PRIVATE_ROUTES = {
    "uika-recognizes-sakiko-motif": {"uika"},
    "mutsumi-notices-soyo-strain": {"mutsumi"},
    "umiri-spots-taki-calibration-gap": {"umiri"},
    "nyamu-sees-stage-potential": {"nyamu"},
    "rana-hears-unstable-resonance": {"rana"},
    "anon-wants-broader-group": {"anon"},
    "anon-wants-soyo-partnership": {"anon"},
    "sakiko-estimates-cross-house-gap": {"sakiko"},
    "tomori-connects-stars-and-voices": {"tomori"},
}
EXPECTED_AU_RELATIONSHIPS = {
    ("anon", "soyo"): (
        Familiarity.CLOSE,
        100,
        "已经明确意识到自己正在暗恋爽世",
    ),
    ("soyo", "anon"): (
        Familiarity.CLOSE,
        94,
        "实际已经在暗恋她",
    ),
    ("umiri", "taki"): (
        Familiarity.CLOSE,
        96,
        "清楚自己正在明恋立希",
    ),
    ("taki", "umiri"): (
        Familiarity.CLOSE,
        91,
        "把对海铃的暗恋藏在挑剔和实际照应里",
    ),
}


def test_hogwarts_project_loads_as_strict_ten_agent_scenario() -> None:
    loaded = load_project_scenario(PROJECTS_ROOT, PROJECT_ID)

    assert loaded.manifest.project_id == loaded.seed.project_id == PROJECT_ID
    assert loaded.seed_hash == "aa9878a3b4712af09c0d31cec83b03a8ccc76eba2392ddd1aca7a4d5c3c595b6"
    assert {agent.id for agent in loaded.manifest.agents} == EXPECTED_AGENT_IDS
    assert {agent.agent_id for agent in loaded.seed.agents} == EXPECTED_AGENT_IDS
    assert {agent.session_id for agent in loaded.seed.agents} == {
        f"session-{agent_id}" for agent_id in EXPECTED_AGENT_IDS
    }
    assert {agent.location_id for agent in loaded.seed.agents} == {"room-of-requirement"}

    partitions = {
        item.root_session_id: set(item.member_session_ids)
        for item in loaded.seed.initial_partitions
    }
    assert partitions == EXPECTED_PARTITIONS

    house_facts = {
        fact.subject.id: fact.object
        for fact in loaded.seed.public_facts
        if fact.predicate == "house_membership"
    }
    assert house_facts == EXPECTED_HOUSES
    assert {item.id for item in loaded.seed.objects} == {
        "constellation-score",
        "enchanted-keyboard",
        "floating-metronome",
        "levitation-feathers",
        "resonance-calibrator",
        "resonance-cauldron",
        "self-writing-score",
    }
    assert all(len(item.operations) == 1 for item in loaded.seed.objects)
    public_context = "\n".join(item.content for item in loaded.seed.public_facts)
    assert "魔咒课" in public_context
    assert "魔药课" in public_context
    assert {
        operation.operation_id for item in loaded.seed.objects for operation in item.operations
    }.issuperset({"cast-wingardium-leviosa", "brew-resonance-draught"})


def test_hogwarts_manifest_has_complete_private_directed_relationships() -> None:
    loaded = load_project_scenario(PROJECTS_ROOT, PROJECT_ID)
    relationships = {
        (owner.id, relationship.target_id): relationship
        for owner in loaded.manifest.agents
        for relationship in owner.persona.relationships
    }

    assert len(relationships) == 90
    for owner in loaded.manifest.agents:
        targets = [relationship.target_id for relationship in owner.persona.relationships]
        assert len(targets) == 9
        assert len(set(targets)) == 9
        assert set(targets) == EXPECTED_AGENT_IDS - {owner.id}
        assert all(
            -100 <= relationship.affinity <= 100 for relationship in owner.persona.relationships
        )

    maximum_affinity = max(relationship.affinity for relationship in relationships.values())
    assert maximum_affinity == 100
    assert {
        key
        for key, relationship in relationships.items()
        if relationship.affinity == maximum_affinity
    } == {("anon", "soyo")}

    for key, (familiarity, affinity, description_fragment) in EXPECTED_AU_RELATIONSHIPS.items():
        relationship = relationships[key]
        assert relationship.familiarity is familiarity
        assert relationship.affinity == affinity
        assert description_fragment in relationship.description


def test_hogwarts_manifest_resolves_all_pinned_character_skills() -> None:
    loaded = load_project_scenario(PROJECTS_ROOT, PROJECT_ID)
    skills = RuntimeSkillCatalog.load(SKILLS_ROOT)

    resolved = {
        agent.id: skills.resolve(
            agent.character_skill.skill_id,
            agent.character_skill.version,
            agent_kind="character",
        )
        for agent in loaded.manifest.agents
    }

    assert set(resolved) == EXPECTED_AGENT_IDS
    assert {
        agent_id: (resolved[agent_id].skill_id, resolved[agent_id].version)
        for agent_id in ("anon", "uika", "sakiko", "mutsumi", "umiri", "nyamu")
    } == {
        "anon": ("mygo.character.anon", "3.1.0"),
        "uika": ("mygo.character.uika", "1.0.0"),
        "sakiko": ("mygo.character.sakiko", "1.0.0"),
        "mutsumi": ("mygo.character.mutsumi", "1.0.0"),
        "umiri": ("mygo.character.umiri", "1.0.0"),
        "nyamu": ("mygo.character.nyamu", "1.0.0"),
    }
    assert all(len(skill.content_hash) == 64 for skill in resolved.values())
    assert "暗恋爽世" in resolved["anon"].body


def test_hogwarts_bootstrap_preserves_partitions_and_private_knowledge(
    tmp_path: Path,
) -> None:
    project_target = tmp_path / "projects" / PROJECT_ID
    project_target.parent.mkdir()
    shutil.copytree(PROJECTS_ROOT / PROJECT_ID, project_target)

    loaded = create_world(
        tmp_path,
        PROJECT_ID,
        "m4-content-test",
        catalog=_catalog(),
    )

    assert loaded.world_ref.project_id == PROJECT_ID
    assert loaded.public_state.world.status is WorldStatus.PAUSED
    assert len(loaded.specs) == len(loaded.persona_states) == len(loaded.memory_streams) == 10
    assert len(loaded.public_state.sessions) == 10
    assert (tmp_path / ".runtime" / PROJECT_ID / "world.sqlite").is_file()
    for root_session_id, member_session_ids in EXPECTED_PARTITIONS.items():
        assert loaded.session_partition.members_of(root_session_id) == member_session_ids

    memory_ids_by_agent = {
        stream.agent_id: {record.id for record in stream.records}
        for stream in loaded.memory_streams
    }
    for agent_id in EXPECTED_AGENT_IDS:
        assert "shared-assignment-briefing" in memory_ids_by_agent[agent_id]
        assert "shared-session-context" in memory_ids_by_agent[agent_id]
    for memory_id, recipients in EXPECTED_PRIVATE_ROUTES.items():
        for agent_id in EXPECTED_AGENT_IDS:
            assert (memory_id in memory_ids_by_agent[agent_id]) is (agent_id in recipients)

    source = load_project_scenario(tmp_path / "projects", PROJECT_ID)
    explicit_routes = {
        item.id: set(item.recipients.agent_ids)
        for item in source.seed.knowledge
        if isinstance(item.recipients, ExplicitAgents)
    }
    assert explicit_routes == EXPECTED_PRIVATE_ROUTES

    private_relationship_descriptions = {
        relationship.description
        for agent in source.manifest.agents
        for relationship in agent.persona.relationships
    }
    database = open_project_database(tmp_path / ".runtime", PROJECT_ID, create=False)
    try:
        with database.session_factory() as session:
            storyline_json = StoryLineBuilder(loaded.world_ref).build(session).to_canonical_json()
    finally:
        database.dispose()
    public_seed_json = source.seed.model_dump_json(by_alias=True)
    public_state_json = loaded.public_state.model_dump_json(by_alias=True)
    for description in private_relationship_descriptions:
        assert description not in public_seed_json
        assert description not in public_state_json
        assert description not in storyline_json


def _catalog() -> Catalog:
    skills = RuntimeSkillCatalog.load(SKILLS_ROOT)
    return Catalog(
        tools=(
            ToolDefinition(
                id="visible_location.query",
                version="1",
                mode=ToolMode.QUERY,
            ),
        ),
        prompts=(PromptDefinition(id="personact.v1", version="1", digest="prompt-v1"),),
        skills=skills.skills,
    )
