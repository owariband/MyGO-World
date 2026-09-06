from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from conftest import MINIMAL_SEED, json_output, run_cli
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from mygo_world.canonical import canonical_json
from mygo_world.contracts import (
    ActionProposal,
    PerceptionFrame,
    SegmentDraft,
)
from mygo_world.db.engine import create_world_engine
from mygo_world.db.models import AgentMemoryRow, SnapshotRow
from mygo_world.gateways import (
    FixtureGateway,
    ModelGateway,
    ModelRequest,
    OpenAICompatibleGateway,
)
from mygo_world.perception import PerceptionProjector
from mygo_world.recognizer import EventRecognizer
from mygo_world.runtime import _default_fixture_responses, advance_world
from mygo_world.validators import ProposalValidator, SegmentValidator
from mygo_world.worlds import initialize_world, show_world


def _load_frame(worlds_dir: Path, world_id: str = "validation") -> PerceptionFrame:
    initialize_world(MINIMAL_SEED, world_id, worlds_dir)
    database = worlds_dir / world_id / "world.sqlite3"
    engine = create_world_engine(database)
    try:
        with Session(engine) as session:
            snapshot = json.loads(session.get(SnapshotRow, 1).snapshot_json)  # type: ignore[union-attr]
            memories = list(session.scalars(select(AgentMemoryRow)))
        return PerceptionProjector().project_frame(
            snapshot,
            session_id="session-first-meeting",
            character_id="character-anon",
            memories=memories,
        )
    finally:
        engine.dispose()


def _valid_proposal(frame: PerceptionFrame) -> ActionProposal:
    return ActionProposal.model_validate(
        _default_fixture_responses(
            snapshot={
                "world_version": frame.world_version,
                "world_time_ms": frame.world_time_ms,
                "entities": [
                    {
                        "entity_id": item.entity_id,
                        "entity_type": item.entity_type,
                        "location_id": item.location_id,
                        "scope_key": item.scope_key,
                    }
                    for item in frame.visible_entities
                ],
            },
            session_id=frame.session_id,
            actor_id=frame.character_id,
            participant_ids=frame.participant_ids,
        )["character:character-anon:action_proposal"]
    )


def _valid_draft(frame: PerceptionFrame) -> SegmentDraft:
    return SegmentDraft.model_validate(
        _default_fixture_responses(
            snapshot={
                "world_version": frame.world_version,
                "world_time_ms": frame.world_time_ms,
                "entities": [
                    {
                        "entity_id": item.entity_id,
                        "entity_type": item.entity_type,
                        "location_id": item.location_id,
                        "scope_key": item.scope_key,
                    }
                    for item in frame.visible_entities
                ],
            },
            session_id=frame.session_id,
            actor_id=frame.character_id,
            participant_ids=frame.participant_ids,
        )["director:global-director:segment_draft"]
    )


def _snapshot(frame: PerceptionFrame) -> dict[str, object]:
    return {
        "world_version": frame.world_version,
        "world_time_ms": frame.world_time_ms,
        "entities": [
            {
                "entity_id": item.entity_id,
                "entity_type": item.entity_type,
                "name": item.name,
                "location_id": item.location_id,
                "scope_key": item.scope_key,
                "payload": {
                    "state": item.state,
                    "scopes": (
                        [
                            {
                                "scope_key": frame.scope_key,
                                "name": "Visible scope",
                                "reachable_destinations": [],
                            }
                        ]
                        if item.entity_type == "location"
                        else []
                    ),
                },
            }
            for item in frame.visible_entities
        ],
        "sessions": [
            {
                "session_id": frame.session_id,
                "status": "runnable",
                "participant_ids": frame.participant_ids,
            }
        ],
    }


def _codes(outcome: object) -> set[str]:
    return {item.code for item in outcome.diagnostics}  # type: ignore[attr-defined]


def test_gateways_share_contract_and_fixture_never_uses_network() -> None:
    request = ModelRequest(
        agent_type="character",
        agent_id="character-anon",
        call_kind="action_proposal",
        model_id="fixture-v1",
        skill_id="skill-anon",
        skill_version="1",
        skill_content_hash="a" * 64,
        input_payload={"world_version": 1},
        model_config={"temperature": 0},
    )
    response = {
        "schema_version": 1,
        "proposal_id": "proposal-1",
        "world_version": 1,
        "session_id": "session-1",
        "actor_id": "character-anon",
        "intent_summary": "Waits briefly.",
        "action": {"kind": "wait", "duration_ms": 1, "reason": "Thinking"},
        "memory_changes": [],
    }
    fixture = FixtureGateway({request.semantic_key: response})
    provider = OpenAICompatibleGateway(
        base_url="https://example.invalid/v1", api_key="not-sent"
    )

    assert isinstance(fixture, ModelGateway)
    assert isinstance(provider, ModelGateway)
    assert (
        fixture.generate(request, ActionProposal).structured.proposal_id == "proposal-1"
    )
    assert fixture.network_request_count == 0


def test_perception_frame_is_scope_and_memory_owner_limited(worlds_dir: Path) -> None:
    frame = _load_frame(worlds_dir, "perception")

    assert frame.world_version == 1
    assert frame.session_id == "session-first-meeting"
    assert frame.pending_response_ids == []
    assert {item.entity_id for item in frame.visible_entities} == {
        "character-anon",
        "character-soyo",
        "location-live-house",
    }
    assert {item.agent_id for item in frame.memories} == {"character-anon"}


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (
            lambda raw: raw.update(world_version=999),
            "PROPOSAL_WORLD_VERSION_MISMATCH",
        ),
        (lambda raw: raw.update(actor_id="character-soyo"), "PROPOSAL_ACTOR_MISMATCH"),
        (
            lambda raw: raw["action"].update(addressee_ids=["character-hidden"]),
            "PROPOSAL_TARGET_NOT_VISIBLE",
        ),
        (
            lambda raw: raw.update(
                action={
                    "kind": "move",
                    "location_id": "location-nowhere",
                    "scope_key": "void",
                }
            ),
            "PROPOSAL_DESTINATION_UNREACHABLE",
        ),
        (
            lambda raw: raw["memory_changes"][0].update(agent_id="character-soyo"),
            "MEMORY_OWNER_MISMATCH",
        ),
    ],
)
def test_proposal_validator_returns_stable_diagnostics(
    worlds_dir: Path, mutation: object, code: str
) -> None:
    frame = _load_frame(worlds_dir, f"proposal-{code.lower()}")
    assert ProposalValidator().validate(frame, _valid_proposal(frame)).ok
    raw = _valid_proposal(frame).model_dump(mode="json")
    mutation(raw)  # type: ignore[operator]
    invalid = ActionProposal.model_validate(raw)

    assert code in _codes(ProposalValidator().validate(frame, invalid))


def test_proposal_rejects_visible_non_character_utterance_addressee(
    worlds_dir: Path,
) -> None:
    frame = _load_frame(worlds_dir, "proposal-visible-object-addressee")
    frame_raw = frame.model_dump(mode="json")
    frame_raw["visible_entities"].append(
        {
            "entity_id": "object-set-list",
            "entity_type": "object",
            "name": "Set list",
            "location_id": frame.location_id,
            "scope_key": frame.scope_key,
            "state": {},
        }
    )
    frame_with_object = PerceptionFrame.model_validate(frame_raw)
    proposal_raw = _valid_proposal(frame_with_object).model_dump(mode="json")
    proposal_raw["action"]["addressee_ids"] = ["object-set-list"]
    proposal = ActionProposal.model_validate(proposal_raw)

    outcome = ProposalValidator().validate(frame_with_object, proposal)

    assert _codes(outcome) == {"PROPOSAL_ADDRESSEE_NOT_CHARACTER"}


def test_proposal_response_must_reference_a_perceived_event(worlds_dir: Path) -> None:
    frame = _load_frame(worlds_dir, "proposal-unperceived-response")
    proposal_raw = _valid_proposal(frame).model_dump(mode="json")
    proposal_raw["action"]["response_to_event_id"] = "event-not-perceived"
    proposal = ActionProposal.model_validate(proposal_raw)

    outcome = ProposalValidator().validate(frame, proposal)

    assert _codes(outcome) == {"PROPOSAL_RESPONSE_EVENT_NOT_PERCEIVED"}


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (
            lambda raw: raw["proposal_events"][0]["payload"].update(
                text="Rewritten dialogue"
            ),
            "SEGMENT_INTENT_MISMATCH",
        ),
        (
            lambda raw: raw["entity_changes"][0].update(entity_id="unknown"),
            "SEGMENT_STATE_TRANSITION_INVALID",
        ),
        (
            lambda raw: raw.update(wave_ended_at_ms=999_999),
            "SEGMENT_TIME_OUT_OF_BOUNDS",
        ),
        (
            lambda raw: raw["external_events"][0].update(cause_event_keys=["missing"]),
            "SEGMENT_CAUSE_INVALID",
        ),
    ],
)
def test_segment_validator_returns_stable_diagnostics(
    worlds_dir: Path, mutation: object, code: str
) -> None:
    frame = _load_frame(worlds_dir, f"segment-{code.lower()}")
    proposal = _valid_proposal(frame)
    draft = _valid_draft(frame)
    valid = SegmentValidator().validate(
        world_id=frame.world_id,
        snapshot=_snapshot(frame),
        proposals=[proposal],
        draft=draft,
        source_trace_id="trace-director",
    )
    assert valid.ok
    assert len(valid.value.events) == 2  # type: ignore[union-attr]
    assert valid.value.events[1].actor_id is None  # type: ignore[union-attr]

    raw = draft.model_dump(mode="json")
    mutation(raw)  # type: ignore[operator]
    invalid = SegmentDraft.model_validate(raw)
    outcome = SegmentValidator().validate(
        world_id=frame.world_id,
        snapshot=_snapshot(frame),
        proposals=[proposal],
        draft=invalid,
        source_trace_id="trace-director",
    )
    assert code in _codes(outcome)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda event: event.update(source_kind="proposal"),
        lambda event: event.update(
            source_ref=None,
            evidence_refs=["unrelated-evidence"],
        ),
        lambda event: event["payload"].pop("intent_summary"),
    ],
)
def test_segment_draft_rejects_incomplete_action_proposal_sources(
    worlds_dir: Path, mutation: object
) -> None:
    frame = _load_frame(worlds_dir, "segment-proposal-source-contract")
    raw = _valid_draft(frame).model_dump(mode="json")
    mutation(raw["proposal_events"][0])  # type: ignore[operator]

    with pytest.raises(ValidationError):
        SegmentDraft.model_validate(raw)


def test_segment_validator_rejects_external_character_agency(
    worlds_dir: Path,
) -> None:
    frame = _load_frame(worlds_dir, "external-agency")
    proposal = _valid_proposal(frame)
    raw = _valid_draft(frame).model_dump(mode="json")
    external = raw["external_events"][0]
    external.update(
        event_type="utterance",
        actor_id="character-soyo",
        payload={"dialogue": "A line Soyo never proposed."},
    )
    draft = SegmentDraft.model_validate(raw)

    outcome = SegmentValidator().validate(
        world_id=frame.world_id,
        snapshot=_snapshot(frame),
        proposals=[proposal],
        draft=draft,
        source_trace_id="trace-director",
    )

    assert "EXTERNAL_EVENT_CHARACTER_AGENCY" in _codes(outcome)


def test_event_recognizer_preserves_candidates_and_projection_enforces_scope(
    worlds_dir: Path,
) -> None:
    frame = _load_frame(worlds_dir, "recognizer")
    proposal = _valid_proposal(frame)
    outcome = SegmentValidator().validate(
        world_id=frame.world_id,
        snapshot=_snapshot(frame),
        proposals=[proposal],
        draft=_valid_draft(frame),
        source_trace_id="trace-director",
    )
    assert outcome.value is not None
    plan = outcome.value
    before = canonical_json([item.model_dump(mode="json") for item in plan.events])
    ids = iter(["event-1", "event-2"])

    recognized = EventRecognizer().recognize(
        plan, first_event_order=7, id_generator=ids.__next__
    )

    assert [item.event_order for item in recognized] == [7, 8]
    assert (
        canonical_json([item.model_dump(mode="json") for item in plan.events]) == before
    )
    assert recognized[0].candidate is not plan.events[0]

    snapshot = _snapshot(frame)
    snapshot["entities"].append(  # type: ignore[union-attr]
        {
            "entity_id": "character-outside",
            "entity_type": "character",
            "name": "Outside",
            "location_id": frame.location_id,
            "scope_key": "different-scope",
            "payload": {"state": {}},
        }
    )
    observations = PerceptionProjector().project_event_observations(
        recognized,
        snapshot,  # type: ignore[arg-type]
    )
    assert {item.agent_id for item in observations} == {
        "character-anon",
        "character-soyo",
    }


def test_advance_commits_one_atomic_version_with_multiple_events(
    worlds_dir: Path,
) -> None:
    initialize_world(MINIMAL_SEED, "wave", worlds_dir)
    receipt = advance_world("wave", worlds_dir)
    shown = show_world("wave", worlds_dir)
    anon_memory = show_world(
        "wave",
        worlds_dir,
        memory_agent_id="character-anon",
        memory_namespace="default",
    )
    soyo_memory = show_world(
        "wave",
        worlds_dir,
        memory_agent_id="character-soyo",
        memory_namespace="default",
    )
    database = worlds_dir / "wave" / "world.sqlite3"

    assert receipt["start_world_version"] == 1
    assert receipt["end_world_version"] == 2
    assert receipt["world_event_count"] == 2
    assert receipt["network_request_count"] == 0
    assert shown["world_version"] == 2
    assert shown["snapshot_checksum"] == receipt["snapshot_checksum"]
    assert len(shown["world_events"]) == 2
    external = next(
        item
        for item in shown["world_events"]
        if item["event_type"] == "environment_change"
    )
    assert external["payload"]["actor_id"] is None
    assert external["payload"]["source_kind"] == "director"
    assert external["payload"]["source_ref"]
    observations = [*anon_memory["observations"], *soyo_memory["observations"]]
    assert {
        item["agent_id"]
        for item in observations
        if item["world_version"] == 2
        and item["payload"].get("source_event_id") == external["event_id"]
    } == {"character-anon", "character-soyo"}

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM world_versions").fetchone() == (
            2,
        )
        assert connection.execute(
            "SELECT count(*) FROM world_events WHERE world_version=2"
        ).fetchone() == (2,)
        assert connection.execute(
            "SELECT count(*) FROM entity_revisions WHERE world_version=2"
        ).fetchone() == (1,)
        traces = connection.execute(
            "SELECT request_json, raw_response, structured_result_json, "
            "skill_id, model_id, validation_json FROM generation_traces "
            "ORDER BY agent_type"
        ).fetchall()
    assert len(traces) == 3
    assert all("api_key" not in canonical_json(row) for row in traces)
    assert all(json.loads(row[5])["ok"] for row in traces)


@pytest.mark.parametrize(
    "failure_stage",
    [
        "segment",
        "world_version",
        "entity_revisions",
        "session",
        "world_events",
        "memory",
        "snapshot",
    ],
)
def test_commit_failure_rolls_back_all_authoritative_wave_records(
    worlds_dir: Path, failure_stage: str
) -> None:
    world_id = f"rollback-{failure_stage}"
    initialize_world(MINIMAL_SEED, world_id, worlds_dir)

    def fail(stage: str) -> None:
        if stage == failure_stage:
            raise RuntimeError(f"injected at {stage}")

    with pytest.raises(RuntimeError, match="injected"):
        advance_world(world_id, worlds_dir, failure_injector=fail)

    database = worlds_dir / world_id / "world.sqlite3"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT current_version FROM worlds").fetchone() == (
            1,
        )
        assert connection.execute("SELECT count(*) FROM world_versions").fetchone() == (
            1,
        )
        assert connection.execute("SELECT count(*) FROM world_segments").fetchone() == (
            1,
        )
        assert connection.execute("SELECT count(*) FROM world_events").fetchone() == (
            0,
        )
        assert connection.execute("SELECT count(*) FROM snapshots").fetchone() == (1,)
        assert connection.execute(
            "SELECT count(*) FROM agent_memory_records"
        ).fetchone() == (1,)
        # Traces intentionally survive an authoritative commit failure for diagnosis.
        assert connection.execute(
            "SELECT count(*) FROM generation_traces"
        ).fetchone() == (3,)


def test_new_process_reads_wave_events_observations_and_checksum(
    worlds_dir: Path,
) -> None:
    initialize_world(MINIMAL_SEED, "process-wave", worlds_dir)
    advanced = run_cli(
        "advance",
        "--world-id",
        "process-wave",
        "--worlds-dir",
        str(worlds_dir),
        "--json",
    )
    shown = run_cli(
        "show",
        "--world-id",
        "process-wave",
        "--worlds-dir",
        str(worlds_dir),
        "--memory-agent-id",
        "character-anon",
        "--memory-namespace",
        "default",
        "--json",
    )
    advance_receipt = json_output(advanced)
    show_receipt = json_output(shown)

    assert advanced.returncode == shown.returncode == 0
    assert show_receipt["world_version"] == 2
    assert len(show_receipt["world_events"]) == 2
    assert any(item["world_version"] == 2 for item in show_receipt["observations"])
    assert show_receipt["snapshot_checksum"] == advance_receipt["snapshot_checksum"]
