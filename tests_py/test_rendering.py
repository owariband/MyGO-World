from __future__ import annotations

import json
import shutil
import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest
from conftest import MINIMAL_SEED, REPOSITORY_ROOT, json_output, run_cli

from mygo_world.broadcasting import render_world
from mygo_world.canonical import sha256_text
from mygo_world.contracts import BroadcastEvent, BroadcastPlan
from mygo_world.errors import WorldError
from mygo_world.rendering import (
    RenderCompiler,
    RenderPlanInvalid,
    RenderPlanner,
    load_asset_manifest,
)
from mygo_world.runtime import advance_world
from mygo_world.worlds import initialize_world

ASSET_FIXTURE = REPOSITORY_ROOT / "examples" / "assets" / "minimal"
ASSET_MANIFEST = ASSET_FIXTURE / "manifest.yaml"


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
