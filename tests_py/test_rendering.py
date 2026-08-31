from __future__ import annotations

import json
import shutil
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import MINIMAL_SEED, REPOSITORY_ROOT, json_output, run_cli

from mygo_world.broadcasting import _default_fixture_plan, render_world
from mygo_world.canonical import canonical_json, sha256_text
from mygo_world.contracts import BroadcastEvent, BroadcastPlan
from mygo_world.errors import WorldError
from mygo_world.gateways import ModelGeneration, ModelRequest
from mygo_world.rendering import (
    RenderCompiler,
    RenderGateway,
    RenderPlanInvalid,
    RenderPlanner,
    load_asset_manifest,
)
from mygo_world.runtime import advance_world
from mygo_world.worlds import initialize_world

ASSET_FIXTURE = REPOSITORY_ROOT / "examples" / "assets" / "minimal"
ASSET_MANIFEST = ASSET_FIXTURE / "manifest.yaml"


class PlanGateway:
    model_id = "render-plan-fixture"
    network_request_count = 0

    def __init__(self, builder=None) -> None:
        self.builder = builder or _plan_from_request
        self.calls: list[ModelRequest] = []

    def generate(self, request: ModelRequest, response_type: type[Any]):
        self.calls.append(request)
        raw = self.builder(request)
        return ModelGeneration(
            request=request,
            raw_response=canonical_json(raw),
            structured=response_type.model_validate(raw),
        )


class BlockingPlanGateway(PlanGateway):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def generate(self, request: ModelRequest, response_type: type[Any]):
        self.entered.set()
        if not self.release.wait(timeout=10):
            raise RuntimeError("timed out waiting to release Broadcast fixture")
        return super().generate(request, response_type)


class ExplodingGateway:
    model_id = "must-not-run"
    network_request_count = 0

    def generate(self, request: ModelRequest, response_type: type[Any]):
        raise AssertionError("ModelGateway must not be called")


def _plan_from_request(request: ModelRequest) -> dict[str, Any]:
    frontier_ids = set(request.input_payload["frontier_event_ids"])
    frontier = [
        BroadcastEvent.model_validate(item)
        for item in request.input_payload["events"]
        if item["event_id"] in frontier_ids
    ]
    return _default_fixture_plan(
        world_id=request.input_payload["world_id"],
        target_world_version=request.input_payload["world_version"],
        frontier=frontier,
        manifest=load_asset_manifest(ASSET_MANIFEST),
    )


def _multi_render_plan(request: ModelRequest) -> dict[str, Any]:
    frontier_ids = set(request.input_payload["frontier_event_ids"])
    frontier = [
        BroadcastEvent.model_validate(item)
        for item in request.input_payload["events"]
        if item["event_id"] in frontier_ids
    ]
    dispositions: list[dict[str, Any]] = []
    renders: list[dict[str, Any]] = []
    for index, event in enumerate(frontier, start=1):
        partial = _default_fixture_plan(
            world_id=request.input_payload["world_id"],
            target_world_version=request.input_payload["world_version"],
            frontier=[event],
            manifest=load_asset_manifest(ASSET_MANIFEST),
        )
        dispositions.extend(partial["dispositions"])
        for render in partial["renders"]:
            render["render_id"] = (
                f"render-v{request.input_payload['world_version']}-{index:03d}"
            )
            renders.append(render)
    return {
        "schema_version": 1,
        "world_id": request.input_payload["world_id"],
        "target_world_version": request.input_payload["world_version"],
        "dispositions": dispositions,
        "renders": renders,
    }


def _plan_omitting_last_event(request: ModelRequest) -> dict[str, Any]:
    plan = _plan_from_request(request)
    omitted_id = request.input_payload["frontier_event_ids"][-1]
    for disposition in plan["dispositions"]:
        if disposition["event_id"] == omitted_id:
            disposition.update(
                status="omitted",
                reason="Deferred because the scene is already complete.",
            )
    for render in plan["renders"]:
        render["beats"] = [
            beat
            for beat in render["beats"]
            if omitted_id not in beat["source_event_ids"]
        ]
    return plan


@pytest.fixture
def webgal_root(tmp_path: Path) -> Path:
    destination = tmp_path / "webgal"
    shutil.copytree(ASSET_FIXTURE / "webgal", destination)
    shutil.rmtree(destination / "game" / "scene" / "generated", ignore_errors=True)
    return destination


def _advanced_world(worlds_dir: Path, world_id: str = "render-world") -> Path:
    initialize_world(MINIMAL_SEED, world_id, worlds_dir)
    advance_world(world_id, worlds_dir)
    return worlds_dir / world_id / "world.sqlite3"


def _events() -> list[BroadcastEvent]:
    return [
        BroadcastEvent(
            event_id="event-utterance",
            event_order=1,
            world_version=2,
            session_id="session-a",
            event_type="utterance",
            actor_id="character-anon",
            start_time_ms=100,
            end_time_ms=200,
            location_id="location-live-house",
            scope_key="lounge",
            fact={"text": "分号; 反斜线\\ 原文"},
        ),
        BroadcastEvent(
            event_id="event-environment",
            event_order=2,
            world_version=2,
            session_id="session-b",
            event_type="environment_change",
            actor_id=None,
            start_time_ms=200,
            end_time_ms=300,
            location_id="location-live-house",
            scope_key="lounge",
            fact={"description": "灯光暗了下来。"},
        ),
    ]


def _plan() -> dict[str, object]:
    return {
        "schema_version": 1,
        "world_id": "render-world",
        "target_world_version": 2,
        "dispositions": [
            {"event_id": "event-utterance", "status": "included"},
            {"event_id": "event-environment", "status": "included"},
        ],
        "renders": [
            {
                "render_id": "render-v2-001",
                "title": "八类 Beat",
                "estimated_play_ms": 12_000,
                "beats": [
                    {
                        "beat_id": "chapter",
                        "type": "chapter",
                        "title": "第一章",
                        "source_event_ids": [],
                    },
                    {
                        "beat_id": "bgm",
                        "type": "bgm",
                        "asset_id": "bgm-quiet-rehearsal",
                        "source_event_ids": [],
                    },
                    {
                        "beat_id": "background",
                        "type": "background",
                        "asset_id": "background-rehearsal-room",
                        "source_event_ids": [],
                    },
                    {
                        "beat_id": "show",
                        "type": "show",
                        "character_id": "character-anon",
                        "model_asset_id": "model-anon-test",
                        "position": "left",
                        "motion": "greet",
                        "expression": "smile",
                        "entrance_effect": "fadeIn",
                        "source_event_ids": [],
                    },
                    {
                        "beat_id": "dialogue",
                        "type": "dialogue",
                        "character_id": "character-anon",
                        "model_asset_id": "model-anon-test",
                        "text": "分号; 反斜线\\ 原文",
                        "source_event_ids": ["event-utterance"],
                    },
                    {
                        "beat_id": "narration",
                        "type": "narration",
                        "text": "灯光暗了下来。",
                        "source_event_ids": ["event-environment"],
                    },
                    {
                        "beat_id": "hide",
                        "type": "hide",
                        "position": "left",
                        "source_event_ids": [],
                    },
                    {
                        "beat_id": "stop",
                        "type": "stop_bgm",
                        "source_event_ids": [],
                    },
                ],
            }
        ],
    }


def _plan_jobs(raw: dict[str, object], webgal_root: Path):
    events = _events()
    return RenderPlanner().plan(
        BroadcastPlan.model_validate(raw),
        world_id="render-world",
        target_world_version=2,
        events=events,
        frontier_event_ids={event.event_id for event in events},
        manifest=load_asset_manifest(ASSET_MANIFEST),
        webgal_root=webgal_root,
    )


def test_render_cli_processes_committed_frontier_without_player(
    worlds_dir: Path, webgal_root: Path, tmp_path: Path
) -> None:
    database = _advanced_world(worlds_dir)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM broadcast_runs").fetchone() == (
            0,
        )
        assert connection.execute(
            "SELECT count(*) FROM generation_traces WHERE agent_type = 'broadcast'"
        ).fetchone() == (0,)
    start_scene = webgal_root / "game" / "scene" / "start.txt"
    start_scene.parent.mkdir(parents=True, exist_ok=True)
    start_scene.write_text("entry remains unchanged\n", encoding="utf-8")
    artifact_root = tmp_path / "artifacts"

    result = run_cli(
        "render",
        "--world-id",
        "render-world",
        "--worlds-dir",
        str(worlds_dir),
        "--asset-manifest",
        str(ASSET_MANIFEST),
        "--webgal-root",
        str(webgal_root),
        "--artifact-root",
        str(artifact_root),
        "--json",
    )

    assert result.returncode == 0, result.stderr
    receipt = json_output(result)
    assert receipt["status"] == "rendered"
    assert receipt["target_world_version"] == 2
    assert receipt["event_count"] == 2
    assert receipt["render_count"] == 1
    assert receipt["network_request_count"] == 0
    assert start_scene.read_text(encoding="utf-8") == "entry remains unchanged\n"
    assert not (webgal_root / "game" / "scene" / "latest.txt").exists()

    scene_path = Path(receipt["renders"][0]["scene_path"])
    script = scene_path.read_text(encoding="utf-8")
    assert "爱音:早上好，今天也一起加油吧。;" in script
    assert "changeBg:rehearsal-room.png -next;" in script
    assert receipt["renders"][0]["content_hash"] == sha256_text(script)
    assert scene_path.name.endswith(f"-{sha256_text(script)}.txt")
    assert list(artifact_root.rglob("render-job.json"))

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM broadcast_runs").fetchone() == (
            1,
        )
        assert connection.execute("SELECT count(*) FROM renders").fetchone() == (1,)
        assert connection.execute(
            "SELECT count(*) FROM broadcast_dispositions"
        ).fetchone() == (2,)
        request = json.loads(
            connection.execute(
                "SELECT request_json FROM generation_traces "
                "WHERE agent_type = 'broadcast'"
            ).fetchone()[0]
        )
        candidates = request["input_payload"]["asset_candidates"]
        assert "path" not in json.dumps(candidates)
        assert candidates["background_ids"] == ["background-rehearsal-room"]
        with pytest.raises(sqlite3.IntegrityError, match="RENDER_IMMUTABLE"):
            connection.execute("UPDATE broadcast_dispositions SET status = 'omitted'")

    with (
        sqlite3.connect(database) as connection,
        pytest.raises(sqlite3.IntegrityError, match="RENDER_IMMUTABLE"),
    ):
        connection.execute("DELETE FROM renders")

    second = run_cli(
        "render",
        "--world-id",
        "render-world",
        "--worlds-dir",
        str(worlds_dir),
        "--asset-manifest",
        str(ASSET_MANIFEST),
        "--webgal-root",
        str(webgal_root),
        "--artifact-root",
        str(artifact_root),
        "--json",
    )
    assert json_output(second)["status"] == "no_work"
    assert json_output(second)["model_call_count"] == 0


def test_render_fixes_target_version_and_leaves_newer_events_unprocessed(
    worlds_dir: Path, webgal_root: Path, tmp_path: Path
) -> None:
    database = _advanced_world(worlds_dir, "fixed-version")
    advance_world("fixed-version", worlds_dir)

    receipt = render_world(
        "fixed-version",
        worlds_dir,
        target_world_version=2,
        asset_manifest_path=ASSET_MANIFEST,
        webgal_root=webgal_root,
        artifact_root=tmp_path / "artifacts",
    )

    assert receipt["event_count"] == 2
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM broadcast_dispositions"
        ).fetchone() == (2,)
        assert connection.execute(
            "SELECT count(*) FROM world_events WHERE world_version = 3"
        ).fetchone() == (2,)


def test_planner_and_compiler_support_all_eight_beats_and_escape_content(
    webgal_root: Path,
) -> None:
    jobs = _plan_jobs(_plan(), webgal_root)
    compiled = RenderCompiler().compile(jobs[0])

    assert {beat["type"] for beat in jobs[0].beats} == {
        "chapter",
        "bgm",
        "stop_bgm",
        "background",
        "show",
        "hide",
        "dialogue",
        "narration",
    }
    assert "bgm:quiet-rehearsal.ogg -volume=70 -enter=1000;" in compiled.script
    assert (
        "changeFigure:anon/model.json -enter=fadeIn -expression=smile -motion=greet -left -next;"
        in compiled.script
    )
    assert "爱音:分号\\; 反斜线\\\\ 原文;" in compiled.script
    assert "changeFigure:none -left -next;" in compiled.script
    assert compiled.content_hash == sha256_text(compiled.script)


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (
            lambda plan: plan["dispositions"].pop(),
            "BROADCAST_DISPOSITION_INCOMPLETE",
        ),
        (
            lambda plan: plan["dispositions"].append(deepcopy(plan["dispositions"][0])),
            "BROADCAST_DISPOSITION_DUPLICATE",
        ),
        (
            lambda plan: plan["renders"][0]["beats"][2].update(
                {"asset_id": "invented-background"}
            ),
            "ASSET_NOT_WHITELISTED",
        ),
        (
            lambda plan: plan["renders"][0]["beats"][4].update(
                {"text": "润色后的台词"}
            ),
            "DIALOGUE_TEXT_CHANGED",
        ),
        (
            lambda plan: plan["renders"][0]["beats"][3].update(
                {"motion": "invented-motion"}
            ),
            "LIVE2D_MOTION_INVALID",
        ),
        (
            lambda plan: plan["renders"][0]["beats"][3].update(
                {"expression": "invented-expression"}
            ),
            "LIVE2D_EXPRESSION_INVALID",
        ),
        (
            lambda plan: plan["renders"][0]["beats"][3].update(
                {"entrance_effect": "invented-effect"}
            ),
            "ENTRANCE_EFFECT_INVALID",
        ),
        (
            lambda plan: plan["renders"][0]["beats"][5].update(
                {"source_event_ids": ["event-not-committed"]}
            ),
            "BEAT_SOURCE_INVALID",
        ),
        (
            lambda plan: plan["renders"][0].update({"estimated_play_ms": 480_001}),
            "RENDER_DURATION_LIMIT_EXCEEDED",
        ),
    ],
)
def test_planner_rejects_invalid_provenance_assets_and_capabilities(
    webgal_root: Path, mutate, expected_code: str
) -> None:
    raw = deepcopy(_plan())
    mutate(raw)
    with pytest.raises(RenderPlanInvalid) as caught:
        _plan_jobs(raw, webgal_root)
    assert expected_code in {item.code for item in caught.value.diagnostics}


def test_planner_rejects_non_self_contained_stage_and_beat_limit(
    webgal_root: Path,
) -> None:
    raw = deepcopy(_plan())
    raw["renders"][0]["beats"] = raw["renders"][0]["beats"][4:6]
    raw["renders"][0]["beats"] *= 21
    for index, beat in enumerate(raw["renders"][0]["beats"]):
        beat["beat_id"] = f"beat-{index}"

    with pytest.raises(RenderPlanInvalid) as caught:
        _plan_jobs(raw, webgal_root)

    codes = {item.code for item in caught.value.diagnostics}
    assert "RENDER_BEAT_LIMIT_EXCEEDED" in codes
    assert "RENDER_BACKGROUND_MISSING" in codes
    assert "RENDER_AUDIO_STATE_MISSING" in codes
    assert "DIALOGUE_CHARACTER_NOT_VISIBLE" in codes


def test_planner_rejects_missing_manifest_file(webgal_root: Path) -> None:
    (webgal_root / "game" / "background" / "rehearsal-room.png").unlink()

    with pytest.raises(RenderPlanInvalid) as caught:
        _plan_jobs(_plan(), webgal_root)

    assert "ASSET_FILE_MISSING" in {item.code for item in caught.value.diagnostics}


def test_planner_allows_processed_history_only_as_explicit_flashback_source(
    webgal_root: Path,
) -> None:
    raw = deepcopy(_plan())
    raw["dispositions"] = [
        item for item in raw["dispositions"] if item["event_id"] == "event-environment"
    ]

    jobs = RenderPlanner().plan(
        BroadcastPlan.model_validate(raw),
        world_id="render-world",
        target_world_version=2,
        events=_events(),
        frontier_event_ids={"event-environment"},
        manifest=load_asset_manifest(ASSET_MANIFEST),
        webgal_root=webgal_root,
    )

    assert jobs[0].beats[4]["source_event_ids"] == ["event-utterance"]
    assert jobs[0].beats[5]["source_event_ids"] == ["event-environment"]


def test_publication_interruption_reuses_equal_hash_and_finalizes_database(
    worlds_dir: Path, webgal_root: Path, tmp_path: Path
) -> None:
    database = _advanced_world(worlds_dir, "recover-render")
    artifact_root = tmp_path / "artifacts"

    def interrupt(stage: str) -> None:
        if stage == "published":
            raise RuntimeError("simulated interruption")

    with pytest.raises(RuntimeError, match="simulated interruption"):
        render_world(
            "recover-render",
            worlds_dir,
            asset_manifest_path=ASSET_MANIFEST,
            webgal_root=webgal_root,
            artifact_root=artifact_root,
            failure_injector=interrupt,
        )
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM broadcast_dispositions"
        ).fetchone() == (0,)

    receipt = render_world(
        "recover-render",
        worlds_dir,
        asset_manifest_path=ASSET_MANIFEST,
        webgal_root=webgal_root,
        artifact_root=artifact_root,
    )
    assert receipt["renders"][0]["reused"] is True
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM broadcast_dispositions"
        ).fetchone() == (2,)


def test_publication_rejects_different_bytes_at_immutable_hash_path(
    worlds_dir: Path, webgal_root: Path, tmp_path: Path
) -> None:
    _advanced_world(worlds_dir, "conflict-render")

    def interrupt(stage: str) -> None:
        if stage == "published":
            raise RuntimeError("simulated interruption")

    with pytest.raises(RuntimeError):
        render_world(
            "conflict-render",
            worlds_dir,
            asset_manifest_path=ASSET_MANIFEST,
            webgal_root=webgal_root,
            artifact_root=tmp_path / "artifacts",
            failure_injector=interrupt,
        )
    scene = next((webgal_root / "game" / "scene" / "generated").rglob("*.txt"))
    scene.write_text("corrupted immutable output\n", encoding="utf-8")

    with pytest.raises(WorldError) as caught:
        render_world(
            "conflict-render",
            worlds_dir,
            asset_manifest_path=ASSET_MANIFEST,
            webgal_root=webgal_root,
            artifact_root=tmp_path / "artifacts",
        )
    assert getattr(caught.value, "code", None) == "RENDER_IMMUTABLE_CONFLICT"

    database = worlds_dir / "conflict-render" / "world.sqlite3"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM broadcast_runs").fetchone() == (
            0,
        )
        assert connection.execute("SELECT count(*) FROM renders").fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM broadcast_dispositions"
        ).fetchone() == (0,)


def test_frontier_spans_generation_batches_and_event_sessions(
    worlds_dir: Path, webgal_root: Path, tmp_path: Path
) -> None:
    raw = yaml.safe_load(MINIMAL_SEED.read_text(encoding="utf-8"))
    raw["sessions"] = [
        {
            "session_id": "session-anon",
            "location_id": "location-live-house",
            "scope_key": "lounge",
            "participant_ids": ["character-anon"],
        },
        {
            "session_id": "session-soyo",
            "location_id": "location-live-house",
            "scope_key": "lounge",
            "participant_ids": ["character-soyo"],
        },
    ]
    seed = tmp_path / "two-sessions.yaml"
    seed.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    initialize_world(seed, "cross-boundaries", worlds_dir)
    first = advance_world("cross-boundaries", worlds_dir, max_waves=1)
    second = advance_world("cross-boundaries", worlds_dir, max_waves=1)
    gateway = PlanGateway(_plan_omitting_last_event)

    receipt = render_world(
        "cross-boundaries",
        worlds_dir,
        gateway=gateway,
        asset_manifest_path=ASSET_MANIFEST,
        webgal_root=webgal_root,
        artifact_root=tmp_path / "artifacts",
    )

    assert first["run_id"] != second["run_id"]
    assert receipt["event_count"] == 4
    request_events = gateway.calls[0].input_payload["events"]
    frontier_ids = set(gateway.calls[0].input_payload["frontier_event_ids"])
    frontier = [item for item in request_events if item["event_id"] in frontier_ids]
    assert {item["world_version"] for item in frontier} == {2, 3}
    assert {item["session_id"] for item in frontier} == {
        "session-anon",
        "session-soyo",
    }
    database = worlds_dir / "cross-boundaries" / "world.sqlite3"
    with sqlite3.connect(database) as connection:
        dispositions = connection.execute(
            "SELECT status, reason FROM broadcast_dispositions ORDER BY event_id"
        ).fetchall()
    assert len(dispositions) == 4
    assert [item for item in dispositions if item[0] == "omitted"] == [
        ("omitted", "Deferred because the scene is already complete.")
    ]


def test_no_work_has_no_model_database_or_filesystem_side_effects(
    worlds_dir: Path, webgal_root: Path, tmp_path: Path
) -> None:
    database = _advanced_world(worlds_dir, "no-work-render")
    artifact_root = tmp_path / "artifacts"
    render_world(
        "no-work-render",
        worlds_dir,
        asset_manifest_path=ASSET_MANIFEST,
        webgal_root=webgal_root,
        artifact_root=artifact_root,
    )

    def counts() -> tuple[int, ...]:
        with sqlite3.connect(database) as connection:
            return tuple(
                connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in (
                    "generation_traces",
                    "broadcast_runs",
                    "renders",
                    "broadcast_dispositions",
                )
            )

    before_counts = counts()
    before_files = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for root in (artifact_root, webgal_root / "game" / "scene" / "generated")
        for path in root.rglob("*")
        if path.is_file()
    }

    receipt = render_world(
        "no-work-render",
        worlds_dir,
        gateway=ExplodingGateway(),
        asset_manifest_path=tmp_path / "missing-manifest.yaml",
        webgal_root=webgal_root,
        artifact_root=artifact_root,
        skills_dir=tmp_path / "missing-skills",
    )

    assert receipt["status"] == "no_work"
    assert receipt["model_call_count"] == 0
    assert counts() == before_counts
    after_files = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for root in (artifact_root, webgal_root / "game" / "scene" / "generated")
        for path in root.rglob("*")
        if path.is_file()
    }
    assert after_files == before_files


def test_render_lock_serializes_concurrent_consumers(
    worlds_dir: Path, webgal_root: Path, tmp_path: Path
) -> None:
    database = _advanced_world(worlds_dir, "concurrent-render")
    gateway = BlockingPlanGateway()
    second_started = threading.Event()

    def invoke(*, mark_started: bool = False) -> dict[str, Any]:
        if mark_started:
            second_started.set()
        return render_world(
            "concurrent-render",
            worlds_dir,
            gateway=gateway,
            asset_manifest_path=ASSET_MANIFEST,
            webgal_root=webgal_root,
            artifact_root=tmp_path / "artifacts",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(invoke)
        assert gateway.entered.wait(timeout=10)
        second = executor.submit(invoke, mark_started=True)
        assert second_started.wait(timeout=10)
        gateway.release.set()
        receipts = [first.result(timeout=10), second.result(timeout=10)]

    assert {item["status"] for item in receipts} == {"rendered", "no_work"}
    assert len(gateway.calls) == 1
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM broadcast_dispositions"
        ).fetchone() == (2,)


def test_render_target_stays_fixed_while_advance_commits_new_events(
    worlds_dir: Path, webgal_root: Path, tmp_path: Path
) -> None:
    database = _advanced_world(worlds_dir, "render-during-advance")
    gateway = BlockingPlanGateway()

    with ThreadPoolExecutor(max_workers=1) as executor:
        rendering = executor.submit(
            render_world,
            "render-during-advance",
            worlds_dir,
            gateway=gateway,
            asset_manifest_path=ASSET_MANIFEST,
            webgal_root=webgal_root,
            artifact_root=tmp_path / "artifacts",
        )
        assert gateway.entered.wait(timeout=10)
        advanced = advance_world("render-during-advance", worlds_dir)
        gateway.release.set()
        receipt = rendering.result(timeout=10)

    assert receipt["target_world_version"] == 2
    assert advanced["end_world_version"] == 3
    assert {
        item["world_version"] for item in gateway.calls[0].input_payload["events"]
    } == {2}
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM broadcast_dispositions"
        ).fetchone() == (2,)
        assert connection.execute(
            "SELECT count(*) FROM world_events event "
            "LEFT JOIN broadcast_dispositions disposition "
            "ON disposition.event_id = event.event_id "
            "WHERE event.world_version = 3 AND disposition.event_id IS NULL"
        ).fetchone() == (2,)


def test_planner_failure_preserves_frontier_for_retry(
    worlds_dir: Path, webgal_root: Path, tmp_path: Path
) -> None:
    database = _advanced_world(worlds_dir, "planner-retry")

    def incomplete(request: ModelRequest) -> dict[str, Any]:
        plan = _plan_from_request(request)
        plan["dispositions"].pop()
        return plan

    with pytest.raises(RenderPlanInvalid):
        render_world(
            "planner-retry",
            worlds_dir,
            gateway=PlanGateway(incomplete),
            asset_manifest_path=ASSET_MANIFEST,
            webgal_root=webgal_root,
            artifact_root=tmp_path / "artifacts",
        )
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM broadcast_runs").fetchone() == (
            0,
        )
        assert connection.execute("SELECT count(*) FROM renders").fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM broadcast_dispositions"
        ).fetchone() == (0,)

    assert (
        render_world(
            "planner-retry",
            worlds_dir,
            asset_manifest_path=ASSET_MANIFEST,
            webgal_root=webgal_root,
            artifact_root=tmp_path / "artifacts",
        )["status"]
        == "rendered"
    )


def test_compiler_failure_preserves_frontier_for_retry(
    worlds_dir: Path, webgal_root: Path, tmp_path: Path
) -> None:
    database = _advanced_world(worlds_dir, "compiler-retry")

    class FailingCompiler:
        def compile(self, job):
            raise WorldError("TEST_COMPILER_FAILURE", "injected compiler failure")

    with pytest.raises(WorldError, match="injected compiler failure"):
        render_world(
            "compiler-retry",
            worlds_dir,
            compiler=FailingCompiler(),
            asset_manifest_path=ASSET_MANIFEST,
            webgal_root=webgal_root,
            artifact_root=tmp_path / "artifacts",
        )
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM broadcast_runs").fetchone() == (
            0,
        )
        assert connection.execute("SELECT count(*) FROM renders").fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM broadcast_dispositions"
        ).fetchone() == (0,)

    assert (
        render_world(
            "compiler-retry",
            worlds_dir,
            asset_manifest_path=ASSET_MANIFEST,
            webgal_root=webgal_root,
            artifact_root=tmp_path / "artifacts",
        )["status"]
        == "rendered"
    )


def test_partial_publication_has_no_partial_database_state_and_retries(
    worlds_dir: Path, webgal_root: Path, tmp_path: Path
) -> None:
    database = _advanced_world(worlds_dir, "partial-publication")
    artifact_root = tmp_path / "artifacts"
    gateway = PlanGateway(_multi_render_plan)

    class FailSecondPublication:
        def __init__(self) -> None:
            self.delegate = RenderGateway()
            self.calls = 0

        def publish(self, compiled, *, artifact_root: Path, webgal_root: Path):
            self.calls += 1
            if self.calls == 2:
                raise WorldError("TEST_PUBLICATION_FAILURE", "second Render failed")
            return self.delegate.publish(
                compiled, artifact_root=artifact_root, webgal_root=webgal_root
            )

    with pytest.raises(WorldError, match="second Render failed"):
        render_world(
            "partial-publication",
            worlds_dir,
            gateway=gateway,
            render_gateway=FailSecondPublication(),
            asset_manifest_path=ASSET_MANIFEST,
            webgal_root=webgal_root,
            artifact_root=artifact_root,
        )
    assert len(list((webgal_root / "game" / "scene" / "generated").rglob("*.txt"))) == 1
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM broadcast_runs").fetchone() == (
            0,
        )
        assert connection.execute("SELECT count(*) FROM renders").fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM broadcast_dispositions"
        ).fetchone() == (0,)

    receipt = render_world(
        "partial-publication",
        worlds_dir,
        gateway=PlanGateway(_multi_render_plan),
        asset_manifest_path=ASSET_MANIFEST,
        webgal_root=webgal_root,
        artifact_root=artifact_root,
    )
    assert receipt["render_count"] == 2
    assert [item["reused"] for item in receipt["renders"]] == [True, False]
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT render_order FROM renders ORDER BY render_order"
        ).fetchall() == [(1,), (2,)]
        assert connection.execute(
            "SELECT count(*) FROM broadcast_dispositions"
        ).fetchone() == (2,)


def test_database_rejects_invalid_dispositions_and_duplicate_render_order(
    worlds_dir: Path, webgal_root: Path, tmp_path: Path
) -> None:
    database = _advanced_world(worlds_dir, "render-constraints")
    render_world(
        "render-constraints",
        worlds_dir,
        asset_manifest_path=ASSET_MANIFEST,
        webgal_root=webgal_root,
        artifact_root=tmp_path / "artifacts",
    )

    with sqlite3.connect(database) as connection:
        run_id, target = connection.execute(
            "SELECT run_id, target_world_version FROM broadcast_runs"
        ).fetchone()
        segment_id = connection.execute(
            "SELECT segment_id FROM world_versions WHERE version = ?", (target,)
        ).fetchone()[0]
        event_order = connection.execute(
            "SELECT max(event_order) + 1 FROM world_events"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO world_events (event_id, event_order, world_version, "
            "segment_id, session_id, start_time_ms, end_time_ms, event_type, "
            "schema_version, payload_json) VALUES "
            "('event-invalid-disposition', ?, ?, ?, NULL, 0, 0, 'test', 1, '{}')",
            (event_order, target, segment_id),
        )
        with pytest.raises(
            sqlite3.IntegrityError, match="BROADCAST_DISPOSITION_INVALID"
        ):
            connection.execute(
                "INSERT INTO broadcast_dispositions "
                "(event_id, run_id, status, reason, render_ids_json, created_at) "
                "VALUES ('event-invalid-disposition', ?, 'included', NULL, '[]', 'now')",
                (run_id,),
            )
        with pytest.raises(
            sqlite3.IntegrityError, match="BROADCAST_DISPOSITION_INVALID"
        ):
            connection.execute(
                "INSERT INTO broadcast_dispositions "
                "(event_id, run_id, status, reason, render_ids_json, created_at) "
                "VALUES ('event-invalid-disposition', ?, 'omitted', '   ', '[]', 'now')",
                (run_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            connection.execute(
                "INSERT INTO renders (render_record_id, world_id, render_id, run_id, "
                "target_world_version, render_order, content_hash, artifact_path, "
                "scene_path, created_at) VALUES "
                "('record-extra', 'render-constraints', 'render-extra', ?, ?, 1, ?, "
                "'artifact', 'scene', 'now')",
                (run_id, target, "0" * 64),
            )
