from __future__ import annotations

import json
from pathlib import Path

from conftest import json_output, run_cli

from mygo_world.canonical import canonical_json, sha256_text
from mygo_world.cli import _human_success
from mygo_world.contracts import ActionProposal
from mygo_world.demo import fixture_root, run_demo
from mygo_world.errors import WorldError
from mygo_world.gateways import (
    FixtureGateway,
    FixtureResponse,
    ModelRequest,
    normalize_fixture_input,
)


def _paths(root: Path, world_id: str) -> dict[str, Path]:
    return {
        "worlds_dir": root / "worlds",
        "webgal_root": root / "webgal",
        "artifact_root": root / "artifacts",
        "export_path": root / "exports" / f"{world_id}.json",
    }


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_versioned_fixture_gateway_uses_batch_wave_and_requires_hash() -> None:
    request = ModelRequest(
        agent_type="character",
        agent_id="character-anon",
        call_kind="action_proposal",
        model_id="fixture-model-v1",
        skill_id="mygo.character.anon",
        skill_version="1.0.0",
        skill_content_hash="0" * 64,
        input_payload={
            "world_id": "portable-world",
            "world_version": 1,
            "run_id": "batch-1",
            "wave_number": 2,
        },
        model_config={"temperature": 0},
    )
    body = {
        "schema_version": 1,
        "proposal_id": "proposal-1",
        "world_version": 1,
        "session_id": "session-1",
        "actor_id": "character-anon",
        "intent_summary": "Waits.",
        "action": {"kind": "no_op", "reason": "Waits"},
        "memory_changes": [],
    }
    expected_hash = sha256_text(
        canonical_json(normalize_fixture_input(request.input_payload))
    )
    gateway = FixtureGateway(
        {
            request.fixture_key: FixtureResponse(
                body=body, expected_input_hash=expected_hash
            )
        },
        require_input_hashes=True,
        normalize_inputs=True,
    )

    assert (
        gateway.generate(request, ActionProposal).structured.proposal_id == "proposal-1"
    )

    missing_hash = FixtureGateway(
        {request.fixture_key: FixtureResponse(body=body)},
        require_input_hashes=True,
    )
    try:
        missing_hash.generate(request, ActionProposal)
    except WorldError as error:
        assert error.code == "FIXTURE_INPUT_HASH_MISSING"
    else:
        raise AssertionError("strict FixtureGateway accepted a missing input hash")

    # The three-part semantic key remains the compatibility path for tickets 01-07.
    legacy = FixtureGateway({request.semantic_key: body})
    assert (
        legacy.generate(request, ActionProposal).structured.proposal_id == "proposal-1"
    )


def test_demo_is_byte_deterministic_and_exercises_domain_boundaries(
    tmp_path: Path,
) -> None:
    world_id = "repeatable-demo"
    first_paths = _paths(tmp_path / "first", world_id)
    second_paths = _paths(tmp_path / "second", world_id)

    first = run_demo(world_id, **first_paths)
    second = run_demo(world_id, **second_paths)

    assert first["generation_batch_ids"] == ["demo-batch-001", "demo-batch-002"]
    assert first["target_world_version"] == 4
    assert first["snapshot_checksum"] == second["snapshot_checksum"]
    assert [item["content_hash"] for item in first["renders"]] == [
        item["content_hash"] for item in second["renders"]
    ]
    assert (
        first_paths["export_path"].read_bytes()
        == second_paths["export_path"].read_bytes()
    )

    first_scenes = _tree_bytes(first_paths["webgal_root"] / "game" / "scene")
    second_scenes = _tree_bytes(second_paths["webgal_root"] / "game" / "scene")
    assert first_scenes == second_scenes
    assert len(first["renders"]) == 2
    assert all(Path(item["scene_path"]).is_file() for item in first["renders"])
    assert all(Path(item["artifact_path"]).is_dir() for item in first["renders"])
    human = _human_success(first)
    assert first["world_id"] in human
    assert first["generation_batch_id"] in human
    assert first["broadcast_run_id"] in human
    assert all(item["content_hash"] in human for item in first["renders"])

    exported = json.loads(first_paths["export_path"].read_text(encoding="utf-8"))
    sessions = exported["sessions"]
    original = next(
        item
        for item in sessions["items"]
        if item["session_id"] == "session-first-meeting"
    )
    successors = [
        item
        for item in sessions["items"]
        if item["session_id"] != original["session_id"]
    ]
    assert (original["status"], original["closure_reason"]) == (
        "closed",
        "partitioned",
    )
    assert [item["queue_order"] for item in sessions["queue"]] == [1, 2, 3]
    assert [item["session_id"] for item in successors] == sorted(
        item["session_id"] for item in successors
    )
    assert (
        exported["generation"]["batches"][1]["session_id"]
        == sessions["queue"][2]["session_id"]
    )

    traces = exported["generation"]["traces"]
    character_traces = [item for item in traces if item["agent_type"] == "character"]
    assert all(item["request"]["skill_body"] for item in character_traces)
    assert all(
        memory["agent_id"] == trace["agent_id"]
        for trace in character_traces
        for memory in trace["request"]["input_payload"]["perception_frame"]["memories"]
    )
    anon_second = next(
        item
        for item in character_traces
        if item["agent_id"] == "character-anon" and item["input_world_version"] == 2
    )
    anon_contents = canonical_json(
        anon_second["request"]["input_payload"]["perception_frame"]["memories"]
    )
    assert "moving to the stage was the right choice" in anon_contents
    assert "lounge door closes" not in anon_contents
    assert "Soyo privately" not in anon_contents

    runs = exported["broadcast"]["runs"]
    assert [item["target_world_version"] for item in runs] == [3, 4]
    dispositions_by_run: dict[str, int] = {}
    for item in exported["broadcast"]["dispositions"]:
        dispositions_by_run[item["run_id"]] = (
            dispositions_by_run.get(item["run_id"], 0) + 1
        )
    assert [dispositions_by_run[item["run_id"]] for item in runs] == [4, 1]
    assert all(
        item["request"]["skill_body"]
        for item in traces
        if item["agent_type"] in {"director", "broadcast"}
    )


def test_demo_cli_receipts_and_existing_world_failure_are_safe(tmp_path: Path) -> None:
    output = tmp_path / "demo-output"
    command = (
        "demo",
        "--world-id",
        "cli-demo",
        "--output-dir",
        str(output),
    )
    created = run_cli(*command, "--json")

    assert created.returncode == 0, created.stderr
    receipt = json_output(created)
    assert receipt["command"] == "demo"
    assert receipt["generation_batch_id"] == "demo-batch-001"
    assert receipt["broadcast_run_id"]
    assert len(receipt["renders"]) == 2
    before = _tree_bytes(output)

    duplicate = run_cli(*command, "--json")

    assert duplicate.returncode == 3
    assert json_output(duplicate)["error"]["code"] == "WORLD_ALREADY_EXISTS"
    assert _tree_bytes(output) == before


def test_checked_in_fixture_is_versioned_yaml_and_json() -> None:
    root = fixture_root()
    assert root.name == "v1"
    assert (root / "fixture.yaml").is_file()
    assert (root / "scenario.yaml").is_file()
    assert (root / "calls.yaml").is_file()
    response_files = sorted((root / "responses").glob("*.json"))
    assert response_files
    assert all(
        isinstance(json.loads(path.read_text()), dict) for path in response_files
    )
