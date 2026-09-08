from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import MINIMAL_SEED

from mygo_world.canonical import canonical_json
from mygo_world.contracts import ActionProposal, DirectorResolution
from mygo_world.errors import WorldError
from mygo_world.gateways import FixtureGateway
from mygo_world.runtime import advance_world
from mygo_world.segment_assembler import SegmentAssembler
from mygo_world.worlds import initialize_world, show_world


def _seed_with_object_and_stage(tmp_path: Path) -> Path:
    raw = yaml.safe_load(MINIMAL_SEED.read_text(encoding="utf-8"))
    raw["entities"][0]["scopes"] = [
        {
            "scope_key": "lounge",
            "name": "休息室",
            "reachable_destinations": [
                {"location_id": "location-live-house", "scope_key": "stage"}
            ],
        },
        {
            "scope_key": "stage",
            "name": "舞台",
            "reachable_destinations": [
                {"location_id": "location-live-house", "scope_key": "lounge"}
            ],
        },
    ]
    raw["entities"].append(
        {
            "entity_type": "object",
            "entity_id": "object-set-list",
            "name": "曲目单",
            "location_id": "location-live-house",
            "scope_key": "lounge",
            "state": {"confirmed": False},
        }
    )
    seed = tmp_path / "assembler-seed.yaml"
    seed.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return seed


def _proposal(action: dict[str, Any], *, intent: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "proposal_id": f"proposal-anon-{action['kind']}",
        "world_version": 1,
        "session_id": "session-first-meeting",
        "actor_id": "character-anon",
        "intent_summary": intent,
        "action": action,
        "memory_changes": [],
    }


def _resolution(
    *,
    elapsed_ms: int,
    outcome_summary: str | None = None,
    external_events: list[dict[str, Any]] | None = None,
    entity_changes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "elapsed_ms": elapsed_ms,
        "outcome_summary": outcome_summary,
        "external_events": external_events or [],
        "entity_changes": entity_changes or [],
        "session_intent": "keep_open",
    }


@pytest.mark.parametrize(
    ("action", "intent", "resolution", "expected_fact"),
    [
        (
            {
                "kind": "utterance",
                "text": "素世，我们先确认第一首吧。",
                "addressee_ids": ["character-soyo"],
                "expects_response": True,
                "response_to_event_id": None,
            },
            "请素世一起确认开场曲。",
            _resolution(elapsed_ms=1_200, outcome_summary="素世听见了提议。"),
            {
                "intent_summary": "请素世一起确认开场曲。",
                "text": "素世，我们先确认第一首吧。",
                "addressee_ids": ["character-soyo"],
                "expects_response": True,
                "response_to_event_id": None,
                "outcome_summary": "素世听见了提议。",
            },
        ),
        (
            {
                "kind": "interact",
                "target_id": "object-set-list",
                "description": "在曲目单上确认第一首歌。",
            },
            "确认排练的开场曲。",
            _resolution(
                elapsed_ms=2_000,
                outcome_summary="曲目单上的第一首歌被确认。",
                entity_changes=[
                    {
                        "entity_id": "object-set-list",
                        "state_patch": {"confirmed": True},
                    }
                ],
            ),
            {
                "intent_summary": "确认排练的开场曲。",
                "target_id": "object-set-list",
                "description": "在曲目单上确认第一首歌。",
                "outcome_summary": "曲目单上的第一首歌被确认。",
            },
        ),
    ],
)
def test_runtime_assembles_character_owned_action_fields(
    worlds_dir: Path,
    tmp_path: Path,
    action: dict[str, Any],
    intent: str,
    resolution: dict[str, Any],
    expected_fact: dict[str, Any],
) -> None:
    initialize_world(
        _seed_with_object_and_stage(tmp_path), "assembled-action", worlds_dir
    )
    proposal = _proposal(action, intent=intent)
    gateway = FixtureGateway(
        {
            "character:character-anon:action_proposal": proposal,
            "director:global-director:director_resolution": resolution,
        }
    )

    advance_world("assembled-action", worlds_dir, gateway=gateway, max_waves=1)

    shown = show_world("assembled-action", worlds_dir)
    event = shown["world_events"][0]
    assert event["event_type"] == action["kind"]
    assert event["payload"]["actor_id"] == "character-anon"
    assert event["payload"]["source_kind"] == "action_proposal"
    assert event["payload"]["source_ref"] == proposal["proposal_id"]
    assert event["payload"]["fact"] == expected_fact
    if action["kind"] == "interact":
        obj = next(
            item
            for item in shown["snapshot"]["entities"]
            if item["entity_id"] == "object-set-list"
        )
        assert obj["payload"]["state"]["confirmed"] is True


def test_runtime_derives_move_event_and_character_position(
    worlds_dir: Path, tmp_path: Path
) -> None:
    initialize_world(
        _seed_with_object_and_stage(tmp_path), "assembled-move", worlds_dir
    )
    proposal = _proposal(
        {
            "kind": "move",
            "location_id": "location-live-house",
            "scope_key": "stage",
        },
        intent="去舞台检查设备。",
    )
    gateway = FixtureGateway(
        {
            "character:character-anon:action_proposal": proposal,
            "director:global-director:director_resolution": _resolution(
                elapsed_ms=1_500,
                outcome_summary="爱音抵达舞台。",
            ),
        }
    )

    advance_world("assembled-move", worlds_dir, gateway=gateway, max_waves=1)

    shown = show_world("assembled-move", worlds_dir)
    event = shown["world_events"][0]
    assert event["payload"]["location_id"] == "location-live-house"
    assert event["payload"]["scope_key"] == "lounge"
    assert event["payload"]["fact"] == {
        "intent_summary": "去舞台检查设备。",
        "location_id": "location-live-house",
        "outcome_summary": "爱音抵达舞台。",
        "scope_key": "stage",
    }
    actor = next(
        item
        for item in shown["snapshot"]["entities"]
        if item["entity_id"] == "character-anon"
    )
    assert (actor["location_id"], actor["scope_key"]) == (
        "location-live-house",
        "stage",
    )


def test_no_op_resolution_creates_no_character_fact(worlds_dir: Path) -> None:
    initialize_world(MINIMAL_SEED, "assembled-no-op", worlds_dir)
    responses = {
        "character:character-anon:action_proposal": _proposal(
            {"kind": "no_op", "reason": "继续倾听。"}, intent="暂时不行动。"
        ),
        "director:global-director:director_resolution": _resolution(elapsed_ms=0),
        "character:character-soyo:action_proposal": {
            **_proposal(
                {"kind": "no_op", "reason": "继续倾听。"}, intent="暂时不行动。"
            ),
            "proposal_id": "proposal-soyo-no-op",
            "world_version": 1,
            "actor_id": "character-soyo",
        },
    }
    gateway = FixtureGateway(responses)

    receipt = advance_world("assembled-no-op", worlds_dir, gateway=gateway, max_waves=2)

    shown = show_world("assembled-no-op", worlds_dir)
    assert shown["world_event_count"] == 0
    assert shown["world_time_ms"] == 0
    with sqlite3.connect(receipt["database_path"]) as connection:
        statuses = connection.execute(
            "SELECT status FROM generation_waves ORDER BY wave_number"
        ).fetchall()
    assert statuses == [("no_op",), ("committed",)]


def test_runtime_resolves_creative_external_event_provenance_and_cause(
    worlds_dir: Path,
) -> None:
    initialize_world(MINIMAL_SEED, "assembled-external", worlds_dir)
    proposal = _proposal(
        {
            "kind": "utterance",
            "text": "开始排练吧。",
            "addressee_ids": [],
            "expects_response": False,
            "response_to_event_id": None,
        },
        intent="提议开始排练。",
    )
    resolution = _resolution(
        elapsed_ms=1_000,
        external_events=[
            {
                "event_type": "environment_change",
                "actor_id": None,
                "start_offset_ms": 1_000,
                "end_offset_ms": 1_000,
                "cause_refs": [{"kind": "proposal"}],
                "evidence_refs": [{"kind": "proposal"}],
                "location_id": "location-live-house",
                "scope_key": "lounge",
                "payload": {"description": "排练灯亮起。"},
            }
        ],
    )
    gateway = FixtureGateway(
        {
            "character:character-anon:action_proposal": proposal,
            "director:global-director:director_resolution": resolution,
        }
    )

    advance_world("assembled-external", worlds_dir, gateway=gateway, max_waves=1)

    shown = show_world("assembled-external", worlds_dir)
    proposal_event, external_event = shown["world_events"]
    assert external_event["payload"]["cause_event_ids"] == [proposal_event["event_id"]]
    assert external_event["payload"]["source_kind"] == "director"
    assert external_event["payload"]["source_ref"]
    assert external_event["payload"]["evidence_refs"] == [
        "proposal:proposal-anon-utterance"
    ]


@pytest.mark.parametrize(
    ("resolution", "code"),
    [
        (
            _resolution(
                elapsed_ms=500,
                external_events=[
                    {
                        "event_type": "environment_change",
                        "start_offset_ms": 0,
                        "end_offset_ms": 501,
                        "location_id": "location-live-house",
                        "scope_key": "lounge",
                        "payload": {},
                    }
                ],
            ),
            "DIRECTOR_RESOLUTION_EVENT_TIME_INVALID",
        ),
        (
            _resolution(
                elapsed_ms=500,
                external_events=[
                    {
                        "event_type": "environment_change",
                        "start_offset_ms": 0,
                        "end_offset_ms": 500,
                        "cause_refs": [{"kind": "external", "index": 0}],
                        "location_id": "location-live-house",
                        "scope_key": "lounge",
                        "payload": {},
                    }
                ],
            ),
            "DIRECTOR_RESOLUTION_EVENT_REFERENCE_INVALID",
        ),
        (
            _resolution(
                elapsed_ms=500,
                entity_changes=[
                    {
                        "entity_id": "character-soyo",
                        "state_patch": {},
                        "location_id": "location-live-house",
                        "scope_key": "lounge",
                    }
                ],
            ),
            "DIRECTOR_RESOLUTION_CHARACTER_MOVE_FORBIDDEN",
        ),
        (
            _resolution(
                elapsed_ms=500,
                entity_changes=[
                    {
                        "entity_id": "character-soyo",
                        "state_patch": {"mood": "calm"},
                    },
                    {
                        "entity_id": "character-soyo",
                        "state_patch": {"mood": "angry"},
                    },
                ],
            ),
            "DIRECTOR_RESOLUTION_ENTITY_CHANGE_CONFLICT",
        ),
    ],
)
def test_invalid_director_resolution_fails_after_one_repair_without_commit(
    worlds_dir: Path,
    resolution: dict[str, Any],
    code: str,
) -> None:
    world_id = f"invalid-{code.lower().replace('_', '-')[:35]}"
    initialize_world(MINIMAL_SEED, world_id, worlds_dir)
    proposal = _proposal(
        {
            "kind": "utterance",
            "text": "继续吧。",
            "addressee_ids": [],
            "expects_response": False,
            "response_to_event_id": None,
        },
        intent="请大家继续。",
    )
    gateway = FixtureGateway(
        {
            "character:character-anon:action_proposal": proposal,
            "director:global-director:director_resolution": resolution,
            "director:global-director:director_resolution_repair": resolution,
        }
    )

    with pytest.raises(WorldError) as captured:
        advance_world(world_id, worlds_dir, gateway=gateway, max_waves=1)

    assert captured.value.code == code
    assert [request.call_kind for request in gateway.calls] == [
        "action_proposal",
        "director_resolution",
        "director_resolution_repair",
    ]
    shown = show_world(world_id, worlds_dir)
    assert shown["world_version"] == 1
    assert shown["world_time_ms"] == 0
    assert shown["world_event_count"] == 0


def test_wait_must_fit_inside_director_elapsed_time(worlds_dir: Path) -> None:
    initialize_world(MINIMAL_SEED, "invalid-wait-duration", worlds_dir)
    proposal = _proposal(
        {"kind": "wait", "duration_ms": 60_000, "reason": "完整听完这一段。"},
        intent="等待六十秒。",
    )
    invalid = _resolution(elapsed_ms=59_999)
    gateway = FixtureGateway(
        {
            "character:character-anon:action_proposal": proposal,
            "director:global-director:director_resolution": invalid,
            "director:global-director:director_resolution_repair": invalid,
        }
    )

    with pytest.raises(WorldError) as captured:
        advance_world("invalid-wait-duration", worlds_dir, gateway=gateway, max_waves=1)

    assert captured.value.code == "DIRECTOR_RESOLUTION_DURATION_INVALID"
    assert show_world("invalid-wait-duration", worlds_dir)["world_version"] == 1


def test_authoritative_assembly_context_failure_is_not_director_repairable(
    worlds_dir: Path,
) -> None:
    initialize_world(MINIMAL_SEED, "invalid-assembly-context", worlds_dir)
    snapshot = show_world("invalid-assembly-context", worlds_dir)["snapshot"]
    outcome = SegmentAssembler().assemble(
        snapshot=snapshot,
        session_id="another-session",
        proposal=ActionProposal.model_validate(
            _proposal(
                {
                    "kind": "wait",
                    "duration_ms": 60_000,
                    "reason": "完整听完这一段。",
                },
                intent="等待六十秒。",
            )
        ),
        resolution=DirectorResolution.model_validate(_resolution(elapsed_ms=60_000)),
        director_trace_id="trace-director-context-invalid",
    )

    assert not outcome.ok
    assert not outcome.director_repairable
    assert [item.code for item in outcome.diagnostics] == [
        "SEGMENT_ASSEMBLY_CONTEXT_INVALID"
    ]


def test_director_repair_cannot_delete_a_wait_proposal(worlds_dir: Path) -> None:
    initialize_world(MINIMAL_SEED, "director-wait-repair", worlds_dir)
    proposal = {
        "schema_version": 1,
        "proposal_id": "proposal-anon-wait-60s",
        "world_version": 1,
        "session_id": "session-first-meeting",
        "actor_id": "character-anon",
        "intent_summary": "专注跟上节拍，把这一段完整合一遍。",
        "action": {
            "kind": "wait",
            "duration_ms": 60_000,
            "reason": "演奏中不打断，等这一段顺完。",
        },
        "memory_changes": [],
    }
    invalid_resolution = {
        "schema_version": 1,
        "world_version": 2,
        "session_id": "session-first-meeting",
        "wave_started_at_ms": 0,
        "wave_ended_at_ms": 60_000,
        "proposal_events": [],
        "event_key": "director-owned-key",
        "source_kind": "action_proposal",
        "source_ref": "rewritten-proposal",
        "payload": {"reason": "rewritten reason"},
        "elapsed_ms": 60_000,
        "outcome_summary": "爱音专注跟着节拍完成这一段。",
        "external_events": [],
        "entity_changes": [],
        "session_intent": "keep_open",
    }
    repaired_resolution = {
        "schema_version": 1,
        "elapsed_ms": 60_000,
        "outcome_summary": "爱音专注跟着节拍完成这一段。",
        "external_events": [],
        "entity_changes": [],
        "session_intent": "keep_open",
    }
    gateway = FixtureGateway(
        {
            "character:character-anon:action_proposal": proposal,
            "director:global-director:director_resolution": invalid_resolution,
            "director:global-director:director_resolution_repair": repaired_resolution,
        }
    )

    receipt = advance_world(
        "director-wait-repair", worlds_dir, gateway=gateway, max_waves=1
    )

    assert receipt["status"] == "completed"
    assert receipt["model_call_count"] == 3
    shown = show_world("director-wait-repair", worlds_dir)
    assert shown["world_version"] == 2
    assert shown["world_time_ms"] == 60_000
    assert shown["world_event_count"] == 1
    event = shown["world_events"][0]
    assert event["event_type"] == "wait"
    assert event["start_time_ms"] == 0
    assert event["end_time_ms"] == 60_000
    assert event["payload"] == {
        "actor_id": "character-anon",
        "cause_event_ids": [],
        "evidence_refs": [],
        "location_id": "location-live-house",
        "scope_key": "lounge",
        "source_kind": "action_proposal",
        "source_ref": "proposal-anon-wait-60s",
        "fact": {
            "duration_ms": 60_000,
            "intent_summary": "专注跟上节拍，把这一段完整合一遍。",
            "outcome_summary": "爱音专注跟着节拍完成这一段。",
            "reason": "演奏中不打断，等这一段顺完。",
        },
    }
    repair = next(
        request for request in gateway.calls if request.call_kind.endswith("_repair")
    )
    assert repair.call_kind == "director_resolution_repair"
    assert repair.input_payload["repair"]["previous_output"] == canonical_json(
        invalid_resolution
    )
    assert repair.input_payload["repair"]["diagnostics"][0]["code"] == (
        "MODEL_SCHEMA_INVALID"
    )
    assert {item["path"] for item in repair.input_payload["repair"]["diagnostics"]} >= {
        "world_version",
        "session_id",
        "wave_started_at_ms",
        "wave_ended_at_ms",
        "proposal_events",
        "event_key",
        "source_kind",
        "source_ref",
        "payload",
    }
    with sqlite3.connect(receipt["database_path"]) as connection:
        trace_rows = connection.execute(
            "SELECT call_kind, structured_result_json FROM generation_traces "
            "WHERE agent_type = 'director' ORDER BY created_at, trace_id"
        ).fetchall()
    assert sorted(item[0] for item in trace_rows) == [
        "director_resolution",
        "director_resolution_repair",
    ]
    structured_result = json.loads(
        next(item[1] for item in trace_rows if item[0] == "director_resolution_repair")
    )
    assert structured_result == repaired_resolution
    assert "world_version" not in structured_result
    assert "proposal_events" not in structured_result
