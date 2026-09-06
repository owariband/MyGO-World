from pathlib import Path

from mygo_world.contracts import load_seed
from mygo_world.rendering import load_asset_manifest
from mygo_world.skill_bindings import resolve_seed_skills
from mygo_world.skills import RuntimeSkillCatalog

REPOSITORY_ROOT = Path(__file__).parents[1]
SCENARIO = REPOSITORY_ROOT / "examples" / "live" / "session-split-ensemble.yaml"
ASSET_MANIFEST = (
    REPOSITORY_ROOT / "examples" / "live" / "session-split-ensemble-assets.yaml"
)


def test_session_split_live_scenario_has_a_watchable_dialogue_budget() -> None:
    seed = load_seed(SCENARIO).seed
    session = seed.sessions[0]
    catalog = RuntimeSkillCatalog.load()
    skills = resolve_seed_skills(seed, catalog)
    character_skills = {
        skill.agent_id: skill.body
        for skill in skills
        if skill.agent_kind == "character"
    }

    assert len(session.participant_ids) == 5
    assert (
        sum("`utterance`" in character_skills[item] for item in session.participant_ids)
        >= 4
    )
    assert (
        sum("`move`" in character_skills[item] for item in session.participant_ids) == 1
    )
    assert (
        seed.skill_bindings.director.skill_id
        == "mygo.director.session-split-performance"
    )
    assert (
        seed.skill_bindings.broadcast.skill_id
        == "mygo.broadcast.session-split-performance"
    )

    followup = catalog.resolve(
        "mygo.character.anon-stage-followup-acceptance",
        "1.0.0",
        agent_kind="character",
    )
    assert followup.body.count("`utterance`") == 2

    manifest = load_asset_manifest(ASSET_MANIFEST)
    assert [item.asset_id for item in manifest.backgrounds] == [
        "background-ring-lounge",
        "background-ring-stage",
    ]
