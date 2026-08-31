from __future__ import annotations

import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from mygo_world.live_demo import run_live_demo
from mygo_world.telemetry import configure_telemetry

REPOSITORY_ROOT = Path(__file__).parents[1]


def _response(body: dict[str, Any]) -> dict[str, Any]:
    system = body["messages"][0]["content"]
    payload = json.loads(body["messages"][1]["content"])
    if "mygo.character." in system:
        frame = payload["perception_frame"]
        version = frame["world_version"]
        actor = frame["character_id"]
        action = (
            {
                "kind": "interact",
                "target_id": "object-set-list",
                "description": "Reviews the opening song on the set list.",
            }
            if version == 1
            else {"kind": "no_op", "reason": "The set list is settled."}
        )
        return {
            "schema_version": 1,
            "proposal_id": f"proposal-{actor}-v{version}",
            "world_version": version,
            "session_id": frame["session_id"],
            "actor_id": actor,
            "intent_summary": "Reviews the set list.",
            "action": action,
            "memory_changes": [],
        }
    if "mygo.director.live" in system:
        version = payload["world_version"]
        proposals = payload["proposals"]
        if version > 1:
            return {
                "schema_version": 1,
                "world_version": version,
                "session_id": proposals[0]["session_id"],
                "wave_started_at_ms": payload["world_time_ms"],
                "wave_ended_at_ms": payload["world_time_ms"],
                "proposal_events": [],
                "external_events": [],
                "entity_changes": [],
                "session_intent": "resolved",
            }
        events = []
        for index, proposal in enumerate(proposals):
            start = payload["world_time_ms"] + index * 1000
            events.append(
                {
                    "event_key": f"event-{proposal['actor_id']}",
                    "event_type": "interact",
                    "actor_id": proposal["actor_id"],
                    "start_time_ms": start,
                    "end_time_ms": start + 500,
                    "cause_event_keys": [],
                    "source_kind": "action_proposal",
                    "source_ref": proposal["proposal_id"],
                    "evidence_refs": [],
                    "location_id": "location-ring-lounge",
                    "scope_key": "lounge",
                    "payload": {
                        "intent_summary": proposal["intent_summary"],
                        "target_id": proposal["action"]["target_id"],
                        "description": proposal["action"]["description"],
                    },
                }
            )
        return {
            "schema_version": 1,
            "world_version": version,
            "session_id": proposals[0]["session_id"],
            "wave_started_at_ms": payload["world_time_ms"],
            "wave_ended_at_ms": payload["world_time_ms"] + 2000,
            "proposal_events": events,
            "external_events": [],
            "entity_changes": [],
            "session_intent": "keep_open",
        }
    events = payload["events"]
    frontier = set(payload["frontier_event_ids"])
    return {
        "schema_version": 1,
        "world_id": payload["world_id"],
        "target_world_version": payload["world_version"],
        "dispositions": [
            {"event_id": item["event_id"], "status": "included"}
            for item in events
            if item["event_id"] in frontier
        ],
        "renders": [
            {
                "render_id": "render-live",
                "title": "Set list",
                "estimated_play_ms": 5000,
                "beats": [
                    {
                        "beat_id": "chapter",
                        "type": "chapter",
                        "title": "Set list",
                        "source_event_ids": [],
                    },
                    {
                        "beat_id": "audio",
                        "type": "stop_bgm",
                        "fade_ms": 0,
                        "source_event_ids": [],
                    },
                    {
                        "beat_id": "background",
                        "type": "background",
                        "asset_id": "background-rehearsal-room",
                        "source_event_ids": [],
                    },
                    *[
                        {
                            "beat_id": f"event-{index}",
                            "type": "narration",
                            "text": item["fact"]["description"],
                            "source_event_ids": [item["event_id"]],
                        }
                        for index, item in enumerate(events, start=1)
                        if item["event_id"] in frontier
                    ],
                ],
            }
        ],
    }


def test_live_harness_runs_production_path_and_audits_outputs(tmp_path: Path) -> None:
    exporter = InMemorySpanExporter()
    configure_telemetry(exporter)
    request_count = 0
    request_bodies: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            nonlocal request_count
            request_count += 1
            length = int(self.headers["Content-Length"])
            request = json.loads(self.rfile.read(length))
            request_bodies.append(request)
            result = json.dumps(
                {
                    "choices": [
                        {"message": {"content": json.dumps(_response(request))}}
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                    },
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(result)))
            self.end_headers()
            self.wfile.write(result)

        def log_message(self, _format: str, *_args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env_file = tmp_path / ".env.local"
    env_file.write_text(
        f"""MYGO_MODEL_BASE_URL=http://127.0.0.1:{server.server_port}/v1
MYGO_MODEL_API_KEY=offline-test-secret
MYGO_MODEL_ID=offline-test-model
MYGO_MODEL_STRUCTURED_OUTPUT_MODE=json_schema
MYGO_MODEL_PARAMETERS_JSON={{"temperature":0}}
""",
        encoding="utf-8",
    )
    output_dir = tmp_path / "live"
    webgal_root = tmp_path / "webgal"
    shutil.copytree(
        REPOSITORY_ROOT / "examples" / "assets" / "minimal" / "webgal",
        webgal_root,
    )
    try:
        receipt = run_live_demo(
            "offline-live",
            output_dir=output_dir,
            webgal_root=webgal_root,
            asset_manifest_path=(
                REPOSITORY_ROOT / "examples" / "assets" / "minimal" / "manifest.yaml"
            ),
            env_file=env_file,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert receipt["status"] == "completed"
    assert receipt["provider_request_count"] == request_count == 7
    assert all(item["model"] == "offline-test-model" for item in request_bodies)
    assert all(item["temperature"] == 0 for item in request_bodies)
    assert receipt["session_closure_reason"] == "resolved"
    serialized = (output_dir / "generation-traces.json").read_text(encoding="utf-8")
    assert "offline-test-secret" not in serialized
    assert "PRIVATE_ANON" in serialized
    assert "PRIVATE_SOYO" in serialized
    assert '"total_tokens":15' in serialized
    assert '"transport_attempts":1' in serialized
    trace.get_tracer_provider().force_flush()
    spans = exporter.get_finished_spans()
    names = {item.name for item in spans}
    assert {
        "mygo.advance",
        "mygo.generation_wave",
        "mygo.model.character",
        "mygo.model.director",
        "mygo.render",
        "mygo.model.broadcast",
    } <= names
    span_text = repr([item.attributes for item in spans])
    assert "offline-test-secret" not in span_text
    assert "PRIVATE_ANON" not in span_text
    assert "PRIVATE_SOYO" not in span_text
