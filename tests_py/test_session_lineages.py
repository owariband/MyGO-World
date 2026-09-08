from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import MINIMAL_SEED

from mygo_world.canonical_export import export_world
from mygo_world.contracts import ActionProposal, SegmentDraft
from mygo_world.gateways import ModelGeneration, ModelRequest
from mygo_world.runtime import advance_world
from mygo_world.validators import SegmentValidator
from mygo_world.worlds import initialize_world, show_world


def _write_seed(
    tmp_path: Path,
    *,
    participant_groups: list[list[str]],
    add_stage: bool = False,
) -> Path:
    raw = yaml.safe_load(MINIMAL_SEED.read_text())
    character_ids = sorted({item for group in participant_groups for item in group})
    characters = {
        item["entity_id"]: item
        for item in raw["entities"]
        if item["entity_type"] == "character"
    }
    for character_id in character_ids:
        if character_id not in characters:
            characters[character_id] = {
                "entity_type": "character",
                "entity_id": character_id,
                "name": character_id,
                "location_id": "location-live-house",
                "scope_key": "lounge",
                "state": {},
            }
    location = raw["entities"][0]
    if add_stage:
        location["scopes"][0]["reachable_destinations"] = [
            {"location_id": "location-live-house", "scope_key": "stage"}
        ]
        location["scopes"].append(
            {
                "scope_key": "stage",
                "name": "Stage",
                "reachable_destinations": [
                    {"location_id": "location-live-house", "scope_key": "lounge"}
                ],
            }
        )
    raw["entities"] = [location, *[characters[item] for item in character_ids]]
    raw["sessions"] = [
        {
            "session_id": f"session-{index}",
            "location_id": "location-live-house",
            "scope_key": "lounge",
            "participant_ids": group,
        }
        for index, group in enumerate(participant_groups, start=1)
    ]
    raw["memories"] = []
    raw["skill_bindings"]["characters"] = {
        character_id: {"skill_id": "mygo.character.anon", "version": "1.0.0"}
        for character_id in character_ids
    }
    path = tmp_path / "lineage.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


class SplitGateway:
    model_id = "split-fixture"
    network_request_count = 0

    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []

    def generate(self, request: ModelRequest, response_type: type[Any]) -> Any:
        if request.agent_type == "character":
            frame = request.input_payload["perception_frame"]
            self.frames.append(frame)
            move = (
                frame["session_id"] == "session-1" and request.agent_id == "character-a"
            )
            raw = {
                "schema_version": 1,
                "proposal_id": (
                    f"proposal-{request.agent_id}-{request.input_payload['wave_number']}"
                ),
                "world_version": frame["world_version"],
                "session_id": frame["session_id"],
                "actor_id": request.agent_id,
                "intent_summary": "Moves to the stage." if move else "Stays quiet.",
                "action": (
                    {
                        "kind": "move",
                        "location_id": "location-live-house",
                        "scope_key": "stage",
                    }
                    if move
                    else {"kind": "no_op", "reason": "Stays quiet"}
                ),
                "memory_changes": [],
            }
        else:
            proposals = request.input_payload["proposals"]
            moving = next(
                (item for item in proposals if item["action"]["kind"] == "move"), None
            )
            external: list[dict[str, Any]] = []
            if moving is not None:
                external.append(
                    {
                        "event_type": "environment_change",
                        "actor_id": None,
                        "location_id": "location-live-house",
                        "scope_key": "lounge",
                        "payload": {"description": "A door closes in the lounge."},
                    }
                )
            raw = {
                "schema_version": 1,
                "elapsed_ms": 200 if moving is not None else 0,
                "outcome_summary": (
                    "The Character arrives on stage." if moving is not None else None
                ),
                "external_events": external,
                "entity_changes": [],
                "session_intent": "keep_open",
            }
        structured = response_type.model_validate(raw)
        return ModelGeneration(
            request=request,
            raw_response=json.dumps(raw),
            structured=structured,
        )


class MergeGateway:
    model_id = "merge-fixture"
    network_request_count = 0

    def __init__(self) -> None:
        self.session_ids: list[str] = []

    def generate(self, request: ModelRequest, response_type: type[Any]) -> Any:
        if request.agent_type == "character":
            frame = request.input_payload["perception_frame"]
            self.session_ids.append(frame["session_id"])
            interact = (
                frame["session_id"] == "session-1" and request.agent_id == "character-a"
            )
            raw = {
                "schema_version": 1,
                "proposal_id": f"proposal-{request.agent_id}",
                "world_version": frame["world_version"],
                "session_id": frame["session_id"],
                "actor_id": request.agent_id,
                "intent_summary": "Greets C directly." if interact else "Waits.",
                "action": (
                    {
                        "kind": "interact",
                        "target_id": "character-c",
                        "description": "Greets C directly.",
                    }
                    if interact
                    else {"kind": "no_op", "reason": "Waits"}
                ),
                "memory_changes": [],
            }
        else:
            proposals = request.input_payload["proposals"]
            interaction = next(
                (item for item in proposals if item["action"]["kind"] == "interact"),
                None,
            )
            raw = {
                "schema_version": 1,
                "elapsed_ms": 100 if interaction else 0,
                "outcome_summary": ("C is directly greeted." if interaction else None),
                "external_events": [],
                "entity_changes": [],
                "session_intent": "keep_open",
            }
        structured = response_type.model_validate(raw)
        return ModelGeneration(
            request=request,
            raw_response=json.dumps(raw),
            structured=structured,
        )


class ResolvingNoOpGateway:
    model_id = "resolve-fixture"
    network_request_count = 0

    def __init__(self) -> None:
        self.calls: list[str] = []

    def generate(self, request: ModelRequest, response_type: type[Any]) -> Any:
        self.calls.append(request.call_kind)
        if request.agent_type == "character":
            frame = request.input_payload["perception_frame"]
            raw = {
                "schema_version": 1,
                "proposal_id": (
                    f"noop-{request.agent_id}-{request.input_payload['wave_number']}"
                ),
                "world_version": frame["world_version"],
                "session_id": frame["session_id"],
                "actor_id": request.agent_id,
                "intent_summary": "Waits.",
                "action": {"kind": "no_op", "reason": "Waits"},
                "memory_changes": [],
            }
        else:
            raw = {
                "schema_version": 1,
                "elapsed_ms": 0,
                "outcome_summary": None,
                "external_events": [],
                "entity_changes": [],
                "session_intent": (
                    "resolved"
                    if request.input_payload["wave_number"] == 2
                    else "keep_open"
                ),
            }
        structured = response_type.model_validate(raw)
        return ModelGeneration(
            request=request,
            raw_response=json.dumps(raw),
            structured=structured,
        )


class DialogueResolutionGateway:
    model_id = "dialogue-resolution-fixture"
    network_request_count = 0

    def generate(self, request: ModelRequest, response_type: type[Any]) -> Any:
        wave_number = request.input_payload["wave_number"]
        if request.agent_type == "character":
            frame = request.input_payload["perception_frame"]
            if wave_number == 1 and request.agent_id == "character-anon":
                action = {
                    "kind": "utterance",
                    "text": "素世，你觉得这样可以吗？",
                    "addressee_ids": ["character-soyo"],
                    "expects_response": True,
                    "response_to_event_id": None,
                }
                summary = "Asks Soyo a direct question."
            elif wave_number == 2 and request.agent_id == "character-soyo":
                response_to_event_id = next(
                    memory["payload"]["source_event_id"]
                    for memory in frame["memories"]
                    if memory["payload"].get("event_type") == "utterance"
                )
                action = {
                    "kind": "utterance",
                    "text": "嗯，这样就可以了。",
                    "addressee_ids": [],
                    "expects_response": False,
                    "response_to_event_id": response_to_event_id,
                }
                summary = "Answers Anon and closes the exchange."
            else:
                action = {"kind": "no_op", "reason": "Listens."}
                summary = "Listens."
            raw = {
                "schema_version": 1,
                "proposal_id": f"dialogue-{request.agent_id}-wave-{wave_number}",
                "world_version": frame["world_version"],
                "session_id": frame["session_id"],
                "actor_id": request.agent_id,
                "intent_summary": summary,
                "action": action,
                "memory_changes": [],
            }
        else:
            raw = {
                "schema_version": 1,
                "elapsed_ms": 100,
                "outcome_summary": "The utterance is heard.",
                "external_events": [],
                "entity_changes": [],
                "session_intent": "resolved" if wave_number == 2 else "keep_open",
            }
        structured = response_type.model_validate(raw)
        return ModelGeneration(
            request=request,
            raw_response=json.dumps(raw),
            structured=structured,
        )


def test_split_is_atomic_fifo_and_scope_isolated(
    worlds_dir: Path, tmp_path: Path
) -> None:
    seed = _write_seed(
        tmp_path,
        participant_groups=[["character-a", "character-b", "character-c"]],
        add_stage=True,
    )
    initialize_world(seed, "split", worlds_dir)
    gateway = SplitGateway()

    receipt = advance_world("split", worlds_dir, gateway=gateway, max_waves=2)

    assert receipt["status"] == "completed"
    assert receipt["wave_count"] == 2
    database = worlds_dir / "split" / "world.sqlite3"
    with sqlite3.connect(database) as connection:
        sessions = connection.execute(
            "SELECT session_id, status, closure_reason, queue_order, "
            "closed_world_version FROM event_sessions ORDER BY queue_order"
        ).fetchall()
        old_scope_observers = {
            row[0]
            for row in connection.execute(
                "SELECT agent_id FROM agent_memory_records "
                "WHERE payload_json LIKE '%A door closes in the lounge.%'"
            )
        }
    exported_sessions = export_world("split", worlds_dir)["sessions"]
    snapshot = show_world("split", worlds_dir)["snapshot"]
    parents = exported_sessions["parents"]
    queue = exported_sessions["queue"]
    successors = [item for item in sessions if item[0] != "session-1"]
    assert sessions[0][:3] == ("session-1", "closed", "partitioned")
    assert [item[2] for item in successors] == ["limit_reached", None]
    assert len(parents) == 2
    assert {item["parent_session_id"] for item in parents} == {"session-1"}
    assert [item["queue_order"] for item in queue] == [1, 2, 3]
    assert queue[1]["dequeued_world_version"] == 3
    assert queue[2]["dequeued_world_version"] is None
    snapshot_successors = [
        item for item in snapshot["sessions"] if item["session_id"] != "session-1"
    ]
    assert all(
        item["parent_session_ids"] == ["session-1"] for item in snapshot_successors
    )
    assert snapshot["runnable_session_queue"] == [
        {
            "queue_order": queue[2]["queue_order"],
            "session_id": queue[2]["session_id"],
        }
    ]
    assert old_scope_observers == {"character-b", "character-c"}

    second_wave_a = next(
        frame
        for frame in gateway.frames
        if frame["character_id"] == "character-a" and frame["world_version"] == 2
    )
    assert {item["entity_id"] for item in second_wave_a["visible_entities"]} == {
        "character-a",
        "location-live-house",
    }

    next_gateway = SplitGateway()
    next_receipt = advance_world("split", worlds_dir, gateway=next_gateway, max_waves=1)
    assert next_receipt["session_id"] == queue[2]["session_id"]
    assert {frame["character_id"] for frame in next_gateway.frames} == {"character-b"}


def test_direct_interaction_merges_sessions_but_colocation_alone_does_not(
    worlds_dir: Path, tmp_path: Path
) -> None:
    seed = _write_seed(
        tmp_path,
        participant_groups=[
            ["character-a", "character-b"],
            ["character-c", "character-d"],
        ],
    )
    initialize_world(seed, "merge", worlds_dir)
    before = show_world("merge", worlds_dir)["snapshot"]
    proposals = [
        ActionProposal.model_validate(
            {
                "proposal_id": f"noop-{actor}",
                "world_version": 1,
                "session_id": "session-1",
                "actor_id": actor,
                "intent_summary": "Waits.",
                "action": {"kind": "no_op", "reason": "Waits"},
            }
        )
        for actor in ("character-a", "character-b")
    ]
    draft = SegmentDraft.model_validate(
        {
            "world_version": 1,
            "session_id": "session-1",
            "wave_started_at_ms": 0,
            "wave_ended_at_ms": 0,
            "proposal_events": [],
            "session_intent": "keep_open",
        }
    )
    plan = (
        SegmentValidator()
        .validate(
            world_id="merge",
            snapshot=before,
            proposals=proposals,
            draft=draft,
            source_trace_id="trace-colocation",
        )
        .value
    )
    assert plan is not None
    assert plan.successor_sessions == []

    receipt = advance_world("merge", worlds_dir, gateway=MergeGateway(), max_waves=1)
    assert receipt["status"] == "completed"
    database = worlds_dir / "merge" / "world.sqlite3"
    with sqlite3.connect(database) as connection:
        closed = connection.execute(
            "SELECT session_id, closure_reason FROM event_sessions "
            "WHERE status='closed' ORDER BY session_id"
        ).fetchall()
        successor = connection.execute(
            "SELECT session_id FROM event_sessions WHERE status='runnable'"
        ).fetchone()
        assert successor is not None
        members = connection.execute(
            "SELECT agent_id FROM event_session_members WHERE session_id=? "
            "ORDER BY agent_id",
            successor,
        ).fetchall()
    assert successor is not None
    successor_id = successor[0]
    parents = [
        item["parent_session_id"]
        for item in export_world("merge", worlds_dir)["sessions"]["parents"]
        if item["session_id"] == successor_id
    ]
    assert closed == [
        ("session-1", "partitioned"),
        ("session-2", "partitioned"),
    ]
    assert members == [
        ("character-a",),
        ("character-b",),
        ("character-c",),
        ("character-d",),
    ]
    assert parents == ["session-1", "session-2"]


def test_resolved_requires_an_earlier_completed_wave() -> None:
    snapshot: dict[str, Any] = {
        "world_version": 1,
        "world_time_ms": 0,
        "entities": [
            {
                "entity_id": "location",
                "entity_type": "location",
                "location_id": None,
                "scope_key": None,
                "payload": {"scopes": [{"scope_key": "room"}]},
            },
            {
                "entity_id": "character-a",
                "entity_type": "character",
                "location_id": "location",
                "scope_key": "room",
                "payload": {},
            },
        ],
        "sessions": [
            {
                "session_id": "session-1",
                "status": "runnable",
                "location_id": "location",
                "scope_key": "room",
                "participant_ids": ["character-a"],
            }
        ],
    }
    proposal = ActionProposal.model_validate(
        {
            "proposal_id": "noop-a",
            "world_version": 1,
            "session_id": "session-1",
            "actor_id": "character-a",
            "intent_summary": "Waits.",
            "action": {"kind": "no_op", "reason": "Waits"},
        }
    )
    draft = SegmentDraft.model_validate(
        {
            "world_version": 1,
            "session_id": "session-1",
            "wave_started_at_ms": 0,
            "wave_ended_at_ms": 0,
            "proposal_events": [],
            "session_intent": "resolved",
        }
    )

    first = SegmentValidator().validate(
        world_id="world",
        snapshot=snapshot,
        proposals=[proposal],
        draft=draft,
        source_trace_id="trace",
    )
    later = SegmentValidator().validate(
        world_id="world",
        snapshot=snapshot,
        proposals=[proposal],
        draft=draft,
        source_trace_id="trace",
        completed_wave_count=1,
    )

    assert {item.code for item in first.diagnostics} == {
        "SEGMENT_SESSION_RESOLUTION_TOO_EARLY"
    }
    assert later.ok


def test_resolved_rejects_pending_response_and_key_commitment() -> None:
    snapshot: dict[str, Any] = {
        "world_version": 2,
        "world_time_ms": 0,
        "entities": [
            {
                "entity_id": "location",
                "entity_type": "location",
                "location_id": None,
                "scope_key": None,
                "payload": {"scopes": [{"scope_key": "room"}]},
            },
            {
                "entity_id": "character-a",
                "entity_type": "character",
                "location_id": "location",
                "scope_key": "room",
                "payload": {},
            },
        ],
        "sessions": [
            {
                "session_id": "session-1",
                "status": "runnable",
                "location_id": "location",
                "scope_key": "room",
                "participant_ids": ["character-a"],
            }
        ],
    }
    proposal = ActionProposal.model_validate(
        {
            "proposal_id": "noop-a",
            "world_version": 2,
            "session_id": "session-1",
            "actor_id": "character-a",
            "intent_summary": "Waits.",
            "action": {"kind": "no_op", "reason": "Waits"},
        }
    )
    draft = SegmentDraft.model_validate(
        {
            "world_version": 2,
            "session_id": "session-1",
            "wave_started_at_ms": 0,
            "wave_ended_at_ms": 0,
            "proposal_events": [],
            "session_intent": "resolved",
        }
    )
    outcome = SegmentValidator().validate(
        world_id="world",
        snapshot=snapshot,
        proposals=[proposal],
        draft=draft,
        source_trace_id="trace",
        completed_wave_count=1,
        pending_response_ids=("character-a",),
        has_unresolved_key_commitments=True,
    )
    assert {item.code for item in outcome.diagnostics} == {
        "SEGMENT_SESSION_RESPONSE_PENDING",
        "SEGMENT_SESSION_COMMITMENT_PENDING",
    }


@pytest.mark.parametrize("addressee_ids", [[], ["character-a"]])
def test_explicit_final_response_clears_pending_without_creating_another(
    addressee_ids: list[str],
) -> None:
    snapshot: dict[str, Any] = {
        "world_version": 2,
        "world_time_ms": 100,
        "entities": [
            {
                "entity_id": "location",
                "entity_type": "location",
                "location_id": None,
                "scope_key": None,
                "payload": {"scopes": [{"scope_key": "room"}]},
            },
            *[
                {
                    "entity_id": character_id,
                    "entity_type": "character",
                    "location_id": "location",
                    "scope_key": "room",
                    "payload": {},
                }
                for character_id in ("character-a", "character-b")
            ],
        ],
        "sessions": [
            {
                "session_id": "session-1",
                "status": "runnable",
                "location_id": "location",
                "scope_key": "room",
                "participant_ids": ["character-a", "character-b"],
            }
        ],
    }
    response = ActionProposal.model_validate(
        {
            "proposal_id": "response-b",
            "world_version": 2,
            "session_id": "session-1",
            "actor_id": "character-b",
            "intent_summary": "Answers without requesting another response.",
            "action": {
                "kind": "utterance",
                "text": "知道了。",
                "addressee_ids": addressee_ids,
                "expects_response": False,
                "response_to_event_id": "event-question",
            },
        }
    )
    draft = SegmentDraft.model_validate(
        {
            "world_version": 2,
            "session_id": "session-1",
            "wave_started_at_ms": 100,
            "wave_ended_at_ms": 200,
            "proposal_events": [
                {
                    "event_key": "event-response",
                    "event_type": "utterance",
                    "actor_id": "character-b",
                    "start_time_ms": 100,
                    "end_time_ms": 200,
                    "source_kind": "action_proposal",
                    "source_ref": "response-b",
                    "location_id": "location",
                    "scope_key": "room",
                    "payload": {
                        "intent_summary": response.intent_summary,
                        "text": "知道了。",
                        "addressee_ids": addressee_ids,
                        "expects_response": False,
                        "response_to_event_id": "event-question",
                    },
                }
            ],
            "session_intent": "resolved",
        }
    )

    outcome = SegmentValidator().validate(
        world_id="world",
        snapshot=snapshot,
        proposals=[response],
        draft=draft,
        source_trace_id="trace-response",
        completed_wave_count=1,
        pending_response_ids=("character-b",),
    )

    assert outcome.ok
    assert outcome.value is not None
    assert outcome.value.pending_response_ids == []


def test_explicit_question_creates_pending_response() -> None:
    snapshot: dict[str, Any] = {
        "world_version": 1,
        "world_time_ms": 0,
        "entities": [
            {
                "entity_id": "location",
                "entity_type": "location",
                "location_id": None,
                "scope_key": None,
                "payload": {"scopes": [{"scope_key": "room"}]},
            },
            *[
                {
                    "entity_id": character_id,
                    "entity_type": "character",
                    "location_id": "location",
                    "scope_key": "room",
                    "payload": {},
                }
                for character_id in ("character-a", "character-b")
            ],
        ],
        "sessions": [
            {
                "session_id": "session-1",
                "status": "runnable",
                "location_id": "location",
                "scope_key": "room",
                "participant_ids": ["character-a", "character-b"],
            }
        ],
    }
    question = ActionProposal.model_validate(
        {
            "proposal_id": "question-a",
            "world_version": 1,
            "session_id": "session-1",
            "actor_id": "character-a",
            "intent_summary": "Asks B a direct question.",
            "action": {
                "kind": "utterance",
                "text": "你觉得呢？",
                "addressee_ids": ["character-b"],
                "expects_response": True,
                "response_to_event_id": None,
            },
        }
    )
    draft = SegmentDraft.model_validate(
        {
            "world_version": 1,
            "session_id": "session-1",
            "wave_started_at_ms": 0,
            "wave_ended_at_ms": 100,
            "proposal_events": [
                {
                    "event_key": "event-question",
                    "event_type": "utterance",
                    "actor_id": "character-a",
                    "start_time_ms": 0,
                    "end_time_ms": 100,
                    "source_kind": "action_proposal",
                    "source_ref": "question-a",
                    "location_id": "location",
                    "scope_key": "room",
                    "payload": {
                        "intent_summary": question.intent_summary,
                        "text": "你觉得呢？",
                        "addressee_ids": ["character-b"],
                        "expects_response": True,
                        "response_to_event_id": None,
                    },
                }
            ],
            "session_intent": "keep_open",
        }
    )

    outcome = SegmentValidator().validate(
        world_id="world",
        snapshot=snapshot,
        proposals=[question],
        draft=draft,
        source_trace_id="trace-question",
    )

    assert outcome.ok
    assert outcome.value is not None
    assert outcome.value.pending_response_ids == ["character-b"]


def test_no_op_limit_reached_uses_one_event_free_control_segment(
    worlds_dir: Path,
) -> None:
    initialize_world(MINIMAL_SEED, "resolved-control", worlds_dir)

    gateway = ResolvingNoOpGateway()
    receipt = advance_world(
        "resolved-control",
        worlds_dir,
        gateway=gateway,
        max_waves=2,
    )

    assert receipt["status"] == "completed"
    assert receipt["wave_count"] == 2
    assert receipt["end_world_version"] == 2
    assert receipt["world_event_count"] == 0
    assert receipt["model_call_count"] == 2
    assert gateway.calls == ["action_proposal", "action_proposal"]
    database = worlds_dir / "resolved-control" / "world.sqlite3"
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT status FROM generation_waves ORDER BY wave_number"
        ).fetchall() == [("no_op",), ("committed",)]
        assert connection.execute(
            "SELECT status, closure_reason FROM event_sessions"
        ).fetchone() == ("closed", "limit_reached")
        assert connection.execute("SELECT count(*) FROM world_segments").fetchone() == (
            2,
        )
        assert connection.execute("SELECT count(*) FROM world_events").fetchone() == (
            0,
        )


def test_dialogue_response_converges_and_resolves_session(worlds_dir: Path) -> None:
    initialize_world(MINIMAL_SEED, "dialogue-resolved", worlds_dir)

    receipt = advance_world(
        "dialogue-resolved",
        worlds_dir,
        gateway=DialogueResolutionGateway(),
        max_waves=2,
    )

    assert receipt["status"] == "completed"
    assert receipt["wave_count"] == 2
    database = worlds_dir / "dialogue-resolved" / "world.sqlite3"
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT status, closure_reason FROM event_sessions"
        ).fetchone() == ("closed", "resolved")
        assert connection.execute(
            "SELECT count(*) FROM event_session_pending_responses"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT selected_actor_id, selection_source, status "
            "FROM decision_turn_records ORDER BY turn_order"
        ).fetchall() == [
            ("character-anon", "director", "committed"),
            ("character-soyo", "nominated", "committed"),
        ]


def test_split_failure_rolls_back_position_sessions_parents_and_queue(
    worlds_dir: Path, tmp_path: Path
) -> None:
    seed = _write_seed(
        tmp_path,
        participant_groups=[["character-a", "character-b", "character-c"]],
        add_stage=True,
    )
    initialize_world(seed, "split-rollback", worlds_dir)
    before = show_world("split-rollback", worlds_dir)["snapshot"]

    def fail(stage: str) -> None:
        if stage == "session":
            raise RuntimeError("injected lineage failure")

    with pytest.raises(RuntimeError, match="injected lineage failure"):
        advance_world(
            "split-rollback",
            worlds_dir,
            gateway=SplitGateway(),
            max_waves=1,
            failure_injector=fail,
        )

    database = worlds_dir / "split-rollback" / "world.sqlite3"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT current_version FROM worlds").fetchone() == (
            1,
        )
        assert connection.execute(
            "SELECT session_id, status, closure_reason, queue_order FROM event_sessions"
        ).fetchall() == [("session-1", "runnable", None, 1)]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "event_session_parents" not in tables
        assert "runnable_session_queue" not in tables
        assert connection.execute(
            "SELECT count(*) FROM event_session_pending_responses"
        ).fetchone() == (0,)
        position = connection.execute(
            "SELECT location_id, scope_key FROM entity_revisions "
            "WHERE entity_id='character-a' ORDER BY revision_order DESC LIMIT 1"
        ).fetchone()
    assert position == ("location-live-house", "lounge")
    assert show_world("split-rollback", worlds_dir)["snapshot"] == before


def test_no_work_does_not_persist_empty_batch(worlds_dir: Path) -> None:
    initialize_world(MINIMAL_SEED, "idle", worlds_dir)
    advance_world("idle", worlds_dir, gateway=SplitGateway(), max_waves=1)
    database = worlds_dir / "idle" / "world.sqlite3"
    with sqlite3.connect(database) as connection:
        before = connection.execute(
            "SELECT count(*) FROM generation_batches"
        ).fetchone()
    receipt = advance_world("idle", worlds_dir, gateway=SplitGateway(), max_waves=1)
    with sqlite3.connect(database) as connection:
        after = connection.execute("SELECT count(*) FROM generation_batches").fetchone()
    assert receipt["status"] == "no_work"
    assert before == after


def test_database_rejects_session_boundary_and_member_mutation(
    worlds_dir: Path,
) -> None:
    initialize_world(MINIMAL_SEED, "immutable-session", worlds_dir)
    database = worlds_dir / "immutable-session" / "world.sqlite3"

    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="INVALID_TRANSITION"):
            connection.execute(
                "UPDATE event_sessions SET scope_key='elsewhere' "
                "WHERE session_id='session-first-meeting'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="INVALID_TRANSITION"):
            connection.execute(
                "UPDATE event_sessions SET status='closed', closure_reason='other' "
                "WHERE session_id='session-first-meeting'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="MEMBERS_IMMUTABLE"):
            connection.execute(
                "INSERT INTO event_session_members(session_id, agent_id) "
                "VALUES ('session-first-meeting', 'late-character')"
            )
        with pytest.raises(sqlite3.IntegrityError, match="INVALID_TRANSITION"):
            connection.execute(
                "UPDATE event_sessions SET queue_order=2 "
                "WHERE session_id='session-first-meeting'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="INVALID_INITIAL_STATE"):
            connection.execute(
                "INSERT INTO event_sessions "
                "(session_id, status, location_id, scope_key, "
                "created_world_version) VALUES "
                "('missing-order', 'runnable', 'location-live-house', "
                "'lounge', 1)"
            )
