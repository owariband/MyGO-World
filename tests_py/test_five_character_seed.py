from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from conftest import REPOSITORY_ROOT

from mygo_world.contracts import load_seed
from mygo_world.gateways import FixtureGateway
from mygo_world.rendering import load_asset_manifest
from mygo_world.runtime import advance_world
from mygo_world.skill_bindings import resolve_seed_skills
from mygo_world.skills import RuntimeSkillCatalog
from mygo_world.worlds import initialize_world, show_world

FIVE_CHARACTER_SEED = (
    REPOSITORY_ROOT / "examples" / "scenarios" / "mygo-five-character.yaml"
)
FIVE_CHARACTER_MANIFEST = (
    REPOSITORY_ROOT / "examples" / "assets" / "mygo-five" / "manifest.yaml"
)
SKILLS_DIR = REPOSITORY_ROOT / "content" / "skills"
CHARACTER_IDS = (
    "character-anon",
    "character-soyo",
    "character-tomori",
    "character-rana",
    "character-taki",
)
PRIVATE_MARKERS = {
    character_id: f"PRIVATE_{character_id.removeprefix('character-').upper()}"
    for character_id in CHARACTER_IDS
}


def _offline_wave_responses() -> dict[str, dict[str, object]]:
    responses: dict[str, dict[str, object]] = {}
    proposal_events: list[dict[str, object]] = []
    for index, character_id in enumerate(CHARACTER_IDS, start=1):
        proposal_id = f"proposal-{character_id}-wave-1"
        text = f"{character_id} 提出了自己的编排意见。"
        proposal = {
            "schema_version": 1,
            "proposal_id": proposal_id,
            "world_version": 1,
            "session_id": "session-ring-arrangement",
            "actor_id": character_id,
            "intent_summary": "就共同的排练编排给出明确意见。",
            "action": {
                "kind": "utterance",
                "text": text,
                "addressee_ids": [],
                "expects_response": False,
                "response_to_event_id": None,
            },
            "memory_changes": [],
        }
        responses[f"character:{character_id}:action_proposal"] = proposal
        proposal_events.append(
            {
                "event_key": f"event-{character_id}-opinion",
                "event_type": "utterance",
                "actor_id": character_id,
                "start_time_ms": index * 100,
                "end_time_ms": index * 100 + 50,
                "cause_event_keys": [],
                "source_kind": "action_proposal",
                "source_ref": proposal_id,
                "evidence_refs": [],
                "location_id": "location-ring-lounge",
                "scope_key": "lounge",
                "payload": {
                    "intent_summary": proposal["intent_summary"],
                    "text": text,
                    "addressee_ids": [],
                    "expects_response": False,
                    "response_to_event_id": None,
                },
            }
        )
    responses["director:global-director:segment_draft"] = {
        "schema_version": 1,
        "world_version": 1,
        "session_id": "session-ring-arrangement",
        "wave_started_at_ms": 0,
        "wave_ended_at_ms": 600,
        "proposal_events": proposal_events,
        "external_events": [],
        "entity_changes": [],
        "session_intent": "keep_open",
    }
    return responses


def test_five_character_content_loads_through_production_parsers() -> None:
    loaded = load_seed(FIVE_CHARACTER_SEED)
    bindings = resolve_seed_skills(loaded.seed, RuntimeSkillCatalog.load(SKILLS_DIR))
    skills = {item.skill_id: item for item in bindings}

    assert {skill_id: skills[skill_id].version for skill_id in skills} == {
        "mygo.character.anon": "3.0.0",
        "mygo.character.soyo": "2.0.0",
        "mygo.character.tomori": "1.0.0",
        "mygo.character.rana": "1.0.0",
        "mygo.character.taki": "1.0.0",
        "mygo.director.default": "2.0.0",
        "mygo.broadcast.default": "2.0.0",
    }
    expected_headings = (
        "## 核心气质",
        "## 核心驱动力",
        "## 判断与行动倾向",
        "## 关系与连续性",
        "## 表达风格",
    )
    for skill_id in (
        "mygo.character.tomori",
        "mygo.character.rana",
        "mygo.character.taki",
    ):
        assert all(heading in skills[skill_id].body for heading in expected_headings)
        for forbidden in (
            "World Version",
            "world_version",
            "session_id",
            "actor_id",
            "source_kind",
            "character-",
            "proposal_id",
            "object-set-list",
        ):
            assert forbidden not in skills[skill_id].body

    manifest = load_asset_manifest(FIVE_CHARACTER_MANIFEST)
    asset_ids = [
        item.asset_id
        for item in [*manifest.backgrounds, *manifest.bgms, *manifest.live2d_models]
    ]
    assert len(asset_ids) == len(set(asset_ids))
    assert {item.character_id: item.path for item in manifest.live2d_models} == {
        "character-anon": "anon/live_default/model.json",
        "character-soyo": "soyo/live_default/model.json",
        "character-tomori": "tomori/live_default/model.json",
        "character-rana": "rana/live_default/model.json",
        "character-taki": "taki/live_default/model.json",
    }
    for model in manifest.live2d_models:
        prefix = f"{model.character_id.removeprefix('character-')}/"
        assert model.motions
        assert model.expressions
        assert all(item.startswith(prefix) for item in model.motions)
        assert all(item.startswith(prefix) for item in model.expressions)


def test_five_character_seed_initializes_and_reopens_persisted_state(
    worlds_dir: Path,
) -> None:
    receipt = initialize_world(FIVE_CHARACTER_SEED, "five-character-init", worlds_dir)
    shown = show_world("five-character-init", worlds_dir)

    assert receipt["seed"]["seed_id"] == "mygo-five-character-rehearsal"
    assert {
        item["agent_id"]: (item["skill_id"], item["version"])
        for item in receipt["skill_bindings"]
        if item["agent_kind"] == "character"
    } == {
        "character-anon": ("mygo.character.anon", "3.0.0"),
        "character-soyo": ("mygo.character.soyo", "2.0.0"),
        "character-tomori": ("mygo.character.tomori", "1.0.0"),
        "character-rana": ("mygo.character.rana", "1.0.0"),
        "character-taki": ("mygo.character.taki", "1.0.0"),
    }
    entities = shown["snapshot"]["entities"]
    assert {
        item["entity_id"] for item in entities if item["entity_type"] == "character"
    } == set(CHARACTER_IDS)
    assert {
        item["entity_id"] for item in entities if item["entity_type"] == "object"
    } == {"object-arrangement-board", "object-rehearsal-recording"}
    assert len(shown["snapshot"]["sessions"]) == 1
    session = shown["snapshot"]["sessions"][0]
    assert session["session_id"] == "session-ring-arrangement"
    assert session["location_id"] == "location-ring-lounge"
    assert session["scope_key"] == "lounge"
    assert session["status"] == "runnable"
    assert set(session["participant_ids"]) == set(CHARACTER_IDS)
    assert session["pending_response_ids"] == []
    assert session["parent_session_ids"] == []

    for character_id in CHARACTER_IDS:
        memories = show_world(
            "five-character-init",
            worlds_dir,
            memory_agent_id=character_id,
            memory_namespace="default",
        )["memories"]
        assert len(memories) == 2
        assert {item["memory_type"] for item in memories} == {"observation", "belief"}
        assert all(item["agent_id"] == character_id for item in memories)
        assert all(
            item["location_tags"] == ["location-ring-lounge"] for item in memories
        )
        assert all(item["entity_tags"] for item in memories)
        assert all(
            PRIVATE_MARKERS[character_id] in item["payload"]["content"]
            for item in memories
        )


def test_offline_five_character_wave_preserves_lockstep_memory_and_provenance(
    worlds_dir: Path,
) -> None:
    initialize_world(FIVE_CHARACTER_SEED, "five-character-wave", worlds_dir)
    gateway = FixtureGateway(_offline_wave_responses())

    receipt = advance_world(
        "five-character-wave",
        worlds_dir,
        gateway=gateway,
        max_waves=1,
    )
    shown = show_world("five-character-wave", worlds_dir)

    character_calls = [call for call in gateway.calls if call.agent_type == "character"]
    assert len(character_calls) == 5
    assert {call.input_payload["world_version"] for call in character_calls} == {1}
    for call in character_calls:
        frame = call.input_payload["perception_frame"]
        contents = [item["payload"]["content"] for item in frame["memories"]]
        assert contents
        assert all(PRIVATE_MARKERS[call.agent_id] in content for content in contents)
        assert not any(
            marker in content
            for owner, marker in PRIVATE_MARKERS.items()
            if owner != call.agent_id
            for content in contents
        )

    assert receipt["status"] == "completed"
    assert receipt["start_world_version"] == 1
    assert receipt["end_world_version"] == 2
    assert receipt["wave_count"] == 1
    assert receipt["model_call_count"] == 6
    assert receipt["world_event_count"] == 5
    assert receipt["network_request_count"] == 0
    assert shown["world_version"] == 2
    assert shown["world_event_count"] == 5

    database = worlds_dir / "five-character-wave" / "world.sqlite3"
    with sqlite3.connect(database) as connection:
        events = connection.execute(
            "SELECT segment_id, payload_json FROM world_events "
            "WHERE world_version=2 ORDER BY event_order"
        ).fetchall()
        character_traces = connection.execute(
            "SELECT structured_result_json FROM generation_traces "
            "WHERE agent_type='character'"
        ).fetchall()
    assert len(events) == 5
    assert len({row[0] for row in events}) == 1
    assert {json.loads(row[1])["source_ref"] for row in events} == {
        f"proposal-{character_id}-wave-1" for character_id in CHARACTER_IDS
    }
    assert {json.loads(row[0])["proposal_id"] for row in character_traces} == {
        f"proposal-{character_id}-wave-1" for character_id in CHARACTER_IDS
    }
