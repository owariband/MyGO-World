from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import MINIMAL_SEED, REPOSITORY_ROOT, json_output, run_cli
from sqlalchemy.orm import Session

from mygo_world.db.engine import create_world_engine
from mygo_world.db.models import GenerationBatchRow
from mygo_world.errors import WorldError
from mygo_world.gateways import (
    FixtureGateway,
    ModelGeneration,
    ModelRequest,
    ModelTransportError,
)
from mygo_world.runtime import advance_world
from mygo_world.worlds import initialize_world, show_world


def _proposal(request: ModelRequest, *, invalid_actor: bool = False) -> dict[str, Any]:
    frame = request.input_payload["perception_frame"]
    return {
        "schema_version": 1,
        "proposal_id": (
            f"proposal-{request.agent_id}-w{request.input_payload['wave_number']}"
        ),
        "world_version": request.input_payload["world_version"],
        "session_id": frame["session_id"],
        "actor_id": "character-soyo" if invalid_actor else request.agent_id,
        "intent_summary": "Waits for the shared decision boundary.",
        "action": {"kind": "no_op", "reason": "No action this Wave"},
        "memory_changes": [],
    }


def _director(request: ModelRequest, *, invalid_time: bool = False) -> dict[str, Any]:
    snapshot = request.input_payload["snapshot"]
    return {
        "schema_version": 1,
        "world_version": snapshot["world_version"],
        "session_id": snapshot["sessions"][0]["session_id"],
        "wave_started_at_ms": snapshot["world_time_ms"],
        "wave_ended_at_ms": snapshot["world_time_ms"]
        + (300_001 if invalid_time else 0),
        "proposal_events": [],
        "external_events": [],
        "entity_changes": [],
        "session_intent": "keep_open",
    }


class ObservingGateway:
    model_id = "observing-fixture"
    network_request_count = 0

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.completed_characters: set[str] = set()
        self.character_contexts: list[tuple[int, int]] = []
        self.director_saw: set[str] = set()
        self.calls: list[str] = []

    def generate(self, request: ModelRequest, response_type: type[Any]) -> Any:
        with self.lock:
            self.calls.append(request.call_kind)
        if request.agent_type == "character":
            frame = request.input_payload["perception_frame"]
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                self.character_contexts.append(
                    (frame["world_version"], frame["world_time_ms"])
                )
            time.sleep(0.04)
            raw = _proposal(request)
            with self.lock:
                self.active -= 1
                self.completed_characters.add(request.agent_id)
        else:
            with self.lock:
                self.director_saw = set(self.completed_characters)
            raw = _director(request)
        structured = response_type.model_validate(raw)
        return ModelGeneration(
            request=request,
            raw_response=json.dumps(raw),
            structured=structured,
        )


class RepairGateway(ObservingGateway):
    def generate(self, request: ModelRequest, response_type: type[Any]) -> Any:
        with self.lock:
            self.calls.append(request.call_kind)
        if request.agent_type == "character":
            raw = _proposal(
                request,
                invalid_actor=(
                    request.agent_id == "character-anon"
                    and request.call_kind == "action_proposal"
                ),
            )
        else:
            raw = _director(request, invalid_time=request.call_kind == "segment_draft")
        structured = response_type.model_validate(raw)
        return ModelGeneration(
            request=request,
            raw_response=json.dumps(raw),
            structured=structured,
        )


class RetryingGateway(ObservingGateway):
    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = failures
        self.attempts: Counter[str] = Counter()

    def generate(self, request: ModelRequest, response_type: type[Any]) -> Any:
        with self.lock:
            self.calls.append(request.call_kind)
            self.attempts[request.agent_id] += 1
            attempt = self.attempts[request.agent_id]
        if request.agent_id == "character-anon" and attempt <= self.failures:
            raise ModelTransportError("temporary")
        raw = (
            _proposal(request)
            if request.agent_type == "character"
            else _director(request)
        )
        structured = response_type.model_validate(raw)
        return ModelGeneration(
            request=request,
            raw_response=json.dumps(raw),
            structured=structured,
        )


def test_batch_calls_all_characters_concurrently_from_one_boundary(
    worlds_dir: Path,
) -> None:
    initialize_world(MINIMAL_SEED, "lockstep", worlds_dir)
    gateway = ObservingGateway()

    receipt = advance_world("lockstep", worlds_dir, gateway=gateway, max_waves=1)

    assert gateway.max_active == 2
    assert gateway.character_contexts == [(1, 0), (1, 0)]
    assert gateway.director_saw == {"character-anon", "character-soyo"}
    assert receipt["run_id"]
    assert receipt["status"] == "completed"
    assert receipt["wave_count"] == 1
    assert receipt["model_call_count"] == 3
    assert receipt["warnings"] == ["MAX_WAVES_REACHED"]
    assert receipt["error_code"] is None

    database = worlds_dir / "lockstep" / "world.sqlite3"
    with sqlite3.connect(database) as connection:
        batch = connection.execute(
            "SELECT status, start_world_version, end_world_version, wave_count, "
            "request_count FROM generation_batches WHERE run_id=?",
            (receipt["run_id"],),
        ).fetchone()
        wave = connection.execute(
            "SELECT status, start_world_version, end_world_version, world_time_ms "
            "FROM generation_waves WHERE run_id=?",
            (receipt["run_id"],),
        ).fetchone()
    assert batch == ("completed", 1, 2, 1, 3)
    assert wave == ("committed", 1, 2, 0)


def test_default_global_character_capacity_is_four(
    worlds_dir: Path, tmp_path: Path
) -> None:
    raw = yaml.safe_load(MINIMAL_SEED.read_text())
    location = raw["entities"][0]
    raw["entities"] = [location]
    participants = []
    for index in range(6):
        character_id = f"character-{index}"
        participants.append(character_id)
        raw["entities"].append(
            {
                "entity_type": "character",
                "entity_id": character_id,
                "name": f"Character {index}",
                "location_id": "location-live-house",
                "scope_key": "lounge",
                "state": {},
            }
        )
    raw["sessions"][0]["participant_ids"] = participants
    raw["memories"] = []
    raw["skill_bindings"]["characters"] = {
        character_id: {
            "skill_id": "mygo.character.anon",
            "version": "1.0.0",
        }
        for character_id in participants
    }
    seed = tmp_path / "six-characters.yaml"
    seed.write_text(yaml.safe_dump(raw))
    initialize_world(seed, "capacity", worlds_dir)
    gateway = ObservingGateway()

    advance_world("capacity", worlds_dir, gateway=gateway, max_waves=1)

    assert gateway.max_active == 4


def test_semantic_repairs_are_limited_to_the_invalid_agent_call(
    worlds_dir: Path,
) -> None:
    initialize_world(MINIMAL_SEED, "repair", worlds_dir)
    gateway = RepairGateway()

    receipt = advance_world("repair", worlds_dir, gateway=gateway, max_waves=1)

    assert Counter(gateway.calls) == Counter(
        {
            "action_proposal": 2,
            "action_proposal_repair": 1,
            "segment_draft": 1,
            "segment_draft_repair": 1,
        }
    )
    assert receipt["model_call_count"] == 5
    database = worlds_dir / "repair" / "world.sqlite3"
    with sqlite3.connect(database) as connection:
        validations = [
            json.loads(row[0])
            for row in connection.execute(
                "SELECT validation_json FROM generation_traces"
            )
        ]
    assert len(validations) == 5
    assert sum(not item["ok"] for item in validations) == 2


def test_schema_failure_raw_response_is_traced_before_one_repair(
    worlds_dir: Path,
) -> None:
    initialize_world(MINIMAL_SEED, "schema-repair", worlds_dir)
    responses = {
        "character:character-anon:action_proposal": {"not": "an action proposal"},
        "character:character-anon:action_proposal_repair": {
            "schema_version": 1,
            "proposal_id": "proposal-anon",
            "world_version": 1,
            "session_id": "session-first-meeting",
            "actor_id": "character-anon",
            "intent_summary": "Does nothing.",
            "action": {"kind": "no_op", "reason": "Listening"},
            "memory_changes": [],
        },
        "character:character-soyo:action_proposal": {
            "schema_version": 1,
            "proposal_id": "proposal-soyo",
            "world_version": 1,
            "session_id": "session-first-meeting",
            "actor_id": "character-soyo",
            "intent_summary": "Does nothing.",
            "action": {"kind": "no_op", "reason": "Listening"},
            "memory_changes": [],
        },
        "director:global-director:segment_draft": {
            "schema_version": 1,
            "world_version": 1,
            "session_id": "session-first-meeting",
            "wave_started_at_ms": 0,
            "wave_ended_at_ms": 0,
            "proposal_events": [],
            "external_events": [],
            "entity_changes": [],
            "session_intent": "keep_open",
        },
    }
    gateway = FixtureGateway(responses)

    receipt = advance_world("schema-repair", worlds_dir, gateway=gateway, max_waves=1)

    assert receipt["model_call_count"] == 4
    assert len(gateway.calls) == 4
    database = worlds_dir / "schema-repair" / "world.sqlite3"
    with sqlite3.connect(database) as connection:
        failed = connection.execute(
            "SELECT raw_response, structured_result_json, validation_json "
            "FROM generation_traces WHERE call_kind='action_proposal'"
        ).fetchone()
    assert failed is not None
    assert json.loads(failed[0]) == {"not": "an action proposal"}
    assert json.loads(failed[1]) == {}
    assert json.loads(failed[2])["diagnostics"][0]["code"] == "MODEL_SCHEMA_INVALID"


def test_transport_retries_twice_then_fails_without_committing(
    worlds_dir: Path,
) -> None:
    initialize_world(MINIMAL_SEED, "retry-fail", worlds_dir)
    gateway = RetryingGateway(failures=3)

    with pytest.raises(WorldError) as captured:
        advance_world("retry-fail", worlds_dir, gateway=gateway)

    assert captured.value.code == "MODEL_TRANSPORT_FAILED"
    assert captured.value.receipt is not None
    assert captured.value.receipt["status"] == "failed"
    assert gateway.attempts["character-anon"] == 3
    assert show_world("retry-fail", worlds_dir)["world_version"] == 1


def test_retryable_transport_error_recovers_within_two_retries(
    worlds_dir: Path,
) -> None:
    initialize_world(MINIMAL_SEED, "retry-ok", worlds_dir)
    gateway = RetryingGateway(failures=2)

    receipt = advance_world("retry-ok", worlds_dir, gateway=gateway, max_waves=1)

    assert gateway.attempts["character-anon"] == 3
    assert receipt["status"] == "completed"
    assert receipt["model_call_count"] == 5


def test_later_wave_failure_preserves_prior_wait_commit(worlds_dir: Path) -> None:
    initialize_world(MINIMAL_SEED, "partial-batch", worlds_dir)

    class LaterWaveFailureGateway(ObservingGateway):
        def generate(self, request: ModelRequest, response_type: type[Any]) -> Any:
            wave_number = request.input_payload["wave_number"]
            if (
                wave_number == 2
                and request.agent_id == "character-anon"
                and request.agent_type == "character"
            ):
                raise ModelTransportError("second Wave unavailable")
            if request.agent_type == "character":
                raw = _proposal(request)
                if request.agent_id == "character-anon":
                    raw["intent_summary"] = "Waits for one second."
                    raw["action"] = {
                        "kind": "wait",
                        "duration_ms": 1_000,
                        "reason": "Listening",
                    }
            else:
                snapshot = request.input_payload["snapshot"]
                proposal = request.input_payload["proposals"][0]
                actor = next(
                    item
                    for item in snapshot["entities"]
                    if item["entity_id"] == proposal["actor_id"]
                )
                raw = {
                    "schema_version": 1,
                    "world_version": snapshot["world_version"],
                    "session_id": snapshot["sessions"][0]["session_id"],
                    "wave_started_at_ms": snapshot["world_time_ms"],
                    "wave_ended_at_ms": snapshot["world_time_ms"] + 1_000,
                    "proposal_events": [
                        {
                            "event_key": f"wait-wave-{wave_number}",
                            "event_type": "wait",
                            "actor_id": proposal["actor_id"],
                            "start_time_ms": snapshot["world_time_ms"],
                            "end_time_ms": snapshot["world_time_ms"] + 1_000,
                            "cause_event_keys": [],
                            "source_kind": "action_proposal",
                            "source_ref": proposal["proposal_id"],
                            "evidence_refs": [],
                            "location_id": actor["location_id"],
                            "scope_key": actor["scope_key"],
                            "payload": {
                                "intent_summary": proposal["intent_summary"],
                                "duration_ms": 1_000,
                                "reason": "Listening",
                            },
                        }
                    ],
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

    with pytest.raises(WorldError) as captured:
        advance_world("partial-batch", worlds_dir, gateway=LaterWaveFailureGateway())

    assert captured.value.code == "MODEL_TRANSPORT_FAILED"
    assert captured.value.receipt is not None
    assert captured.value.receipt["start_world_version"] == 1
    assert captured.value.receipt["end_world_version"] == 2
    assert captured.value.receipt["wave_count"] == 2
    shown = show_world("partial-batch", worlds_dir)
    assert shown["world_version"] == 2
    assert shown["world_time_ms"] == 1_000


def test_request_budget_counts_calls_and_fails_before_director(
    worlds_dir: Path,
) -> None:
    initialize_world(MINIMAL_SEED, "budget", worlds_dir)
    gateway = ObservingGateway()

    with pytest.raises(WorldError) as captured:
        advance_world("budget", worlds_dir, gateway=gateway, request_budget=2)

    assert captured.value.code == "REQUEST_BUDGET_EXHAUSTED"
    assert captured.value.receipt is not None
    assert captured.value.receipt["model_call_count"] == 2
    assert gateway.calls == ["action_proposal", "action_proposal"]
    assert show_world("budget", worlds_dir)["world_version"] == 1


def test_cli_budget_failure_returns_nonzero_batch_receipt(worlds_dir: Path) -> None:
    initialize_world(MINIMAL_SEED, "cli-budget", worlds_dir)

    result = run_cli(
        "advance",
        "--world-id",
        "cli-budget",
        "--worlds-dir",
        str(worlds_dir),
        "--request-budget",
        "2",
        "--json",
    )
    receipt = json_output(result)

    assert result.returncode != 0
    assert receipt["status"] == "failed"
    assert receipt["error_code"] == "REQUEST_BUDGET_EXHAUSTED"
    assert receipt["start_world_version"] == receipt["end_world_version"] == 1
    assert receipt["error"]["code"] == "REQUEST_BUDGET_EXHAUSTED"


def test_pre_cancelled_batch_is_durable_and_does_not_call_provider(
    worlds_dir: Path,
) -> None:
    initialize_world(MINIMAL_SEED, "cancelled", worlds_dir)
    gateway = ObservingGateway()
    cancelled = threading.Event()
    cancelled.set()

    with pytest.raises(WorldError) as captured:
        advance_world(
            "cancelled", worlds_dir, gateway=gateway, cancellation_event=cancelled
        )

    assert captured.value.code == "BATCH_CANCELLED"
    assert captured.value.receipt is not None
    assert captured.value.receipt["status"] == "cancelled"
    assert captured.value.receipt["wave_count"] == 0
    assert gateway.calls == []


def test_sigterm_cancels_in_flight_provider_requests(worlds_dir: Path) -> None:
    initialize_world(MINIMAL_SEED, "signal-cancel", worlds_dir)
    requests_started = threading.Event()
    release_responses = threading.Event()
    request_count = 0
    request_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            nonlocal request_count
            length = int(self.headers["Content-Length"])
            self.rfile.read(length)
            with request_lock:
                request_count += 1
                if request_count == 2:
                    requests_started.set()
            release_responses.wait(timeout=5)

        def log_message(self, _format: str, *_args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    environment = {
        **os.environ,
        "MYGO_MODEL_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
        "MYGO_MODEL_API_KEY": "test-secret",
        "MYGO_MODEL_ID": "test-model",
        "MYGO_MODEL_PARAMETERS_JSON": "{}",
        "MYGO_MODEL_TIMEOUT_SECONDS": "120",
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "mygo_world",
            "advance",
            "--world-id",
            "signal-cancel",
            "--worlds-dir",
            str(worlds_dir),
            "--gateway",
            "provider",
            "--json",
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert requests_started.wait(timeout=3)
        started = time.monotonic()
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=2)
        elapsed = time.monotonic() - started
        receipt = json.loads(stdout)

        assert process.returncode != 0, stderr
        assert elapsed < 2
        assert receipt["status"] == "cancelled"
        assert receipt["error"]["code"] == "BATCH_CANCELLED"
    finally:
        release_responses.set()
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
        server.shutdown()
        server_thread.join(timeout=5)
        server.server_close()


def test_next_batch_marks_stale_running_batch_interrupted(worlds_dir: Path) -> None:
    initialize_world(MINIMAL_SEED, "interrupted", worlds_dir)
    database = worlds_dir / "interrupted" / "world.sqlite3"
    engine = create_world_engine(database)
    try:
        with Session(engine) as session, session.begin():
            session.add(
                GenerationBatchRow(
                    run_id="stale-run",
                    world_id="interrupted",
                    session_id="session-first-meeting",
                    status="running",
                    start_world_version=1,
                    end_world_version=1,
                    wave_count=0,
                    request_count=0,
                    warnings_json="[]",
                    error_code=None,
                    created_at="2026-01-01T00:00:00+00:00",
                    updated_at="2026-01-01T00:00:00+00:00",
                )
            )
    finally:
        engine.dispose()

    advance_world("interrupted", worlds_dir)

    with sqlite3.connect(database) as connection:
        stale = connection.execute(
            "SELECT status, error_code FROM generation_batches WHERE run_id='stale-run'"
        ).fetchone()
    assert stale == ("interrupted", "BATCH_INTERRUPTED")


def test_all_no_op_waves_only_commit_limit_reached_control_segment(
    worlds_dir: Path,
) -> None:
    initialize_world(MINIMAL_SEED, "no-op", worlds_dir)

    class NoOpGateway(ObservingGateway):
        def generate(self, request: ModelRequest, response_type: type[Any]) -> Any:
            raw = (
                _proposal(request)
                if request.agent_type == "character"
                else {**_director(request), "session_intent": "keep_open"}
            )
            structured = response_type.model_validate(raw)
            return ModelGeneration(
                request=request,
                raw_response=json.dumps(raw),
                structured=structured,
            )

    receipt = advance_world("no-op", worlds_dir, gateway=NoOpGateway(), max_waves=2)
    shown = show_world("no-op", worlds_dir)

    assert receipt["wave_count"] == 2
    assert receipt["warnings"] == ["MAX_WAVES_REACHED"]
    assert shown["world_version"] == 2
    assert shown["world_time_ms"] == 0
    assert shown["world_event_count"] == 0
    database = worlds_dir / "no-op" / "world.sqlite3"
    with sqlite3.connect(database) as connection:
        statuses = connection.execute(
            "SELECT status FROM generation_waves ORDER BY wave_number"
        ).fetchall()
        closure = connection.execute(
            "SELECT status, closure_reason FROM event_sessions"
        ).fetchone()
    assert statuses == [("no_op",), ("committed",)]
    assert closure == ("closed", "limit_reached")
