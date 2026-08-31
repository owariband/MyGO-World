from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from mygo_world.broadcasting import render_world
from mygo_world.demo import DEFAULT_FIXTURE_VERSION, fixture_root, run_demo
from mygo_world.errors import WorldError
from mygo_world.evals import run_provider_evals
from mygo_world.live_demo import run_live_demo
from mygo_world.runtime import advance_world
from mygo_world.skill_bindings import bind_character_skill
from mygo_world.skills import DEFAULT_SKILLS_DIR
from mygo_world.worlds import initialize_world, show_world


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mygo-world")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="initialize a durable World")
    init_parser.add_argument("--world-id", required=True)
    init_parser.add_argument("--seed", required=True, type=Path)
    init_parser.add_argument("--worlds-dir", type=Path, default=Path(".mygo/worlds"))
    init_parser.add_argument("--skills-dir", type=Path, default=DEFAULT_SKILLS_DIR)
    init_parser.add_argument("--json", action="store_true", dest="as_json")

    show_parser = subparsers.add_parser("show", help="read a durable World Snapshot")
    show_parser.add_argument("--world-id", required=True)
    show_parser.add_argument("--worlds-dir", type=Path, default=Path(".mygo/worlds"))
    show_parser.add_argument("--json", action="store_true", dest="as_json")

    advance_parser = subparsers.add_parser(
        "advance", help="advance one deterministic Generation Wave"
    )
    advance_parser.add_argument("--world-id", required=True)
    advance_parser.add_argument("--worlds-dir", type=Path, default=Path(".mygo/worlds"))
    advance_parser.add_argument("--skills-dir", type=Path, default=DEFAULT_SKILLS_DIR)
    advance_parser.add_argument(
        "--gateway", choices=("fixture", "provider"), default="fixture"
    )
    advance_parser.add_argument("--run-id")
    advance_parser.add_argument("--max-waves", type=int, default=6)
    advance_parser.add_argument("--request-budget", type=int, default=40)
    advance_parser.add_argument("--character-concurrency", type=int, default=4)
    advance_parser.add_argument("--env-file", type=Path)
    advance_parser.add_argument("--json", action="store_true", dest="as_json")

    render_parser = subparsers.add_parser(
        "render", help="render unprocessed committed Events as immutable WebGAL scenes"
    )
    render_parser.add_argument("--world-id", required=True)
    render_parser.add_argument("--worlds-dir", type=Path, default=Path(".mygo/worlds"))
    render_parser.add_argument("--skills-dir", type=Path, default=DEFAULT_SKILLS_DIR)
    render_parser.add_argument("--world-version", type=int)
    render_parser.add_argument("--asset-manifest", required=True, type=Path)
    render_parser.add_argument("--webgal-root", required=True, type=Path)
    render_parser.add_argument(
        "--artifact-root", type=Path, default=Path(".mygo/renders")
    )
    render_parser.add_argument(
        "--gateway", choices=("fixture", "provider"), default="fixture"
    )
    render_parser.add_argument("--request-budget", type=int, default=6)
    render_parser.add_argument("--env-file", type=Path)
    render_parser.add_argument("--json", action="store_true", dest="as_json")

    live_parser = subparsers.add_parser(
        "live-demo", help="run the explicit paid Provider acceptance demo"
    )
    live_parser.add_argument("--world-id", required=True)
    live_parser.add_argument("--output-dir", type=Path, default=Path(".mygo/live"))
    live_parser.add_argument("--webgal-root", type=Path)
    live_parser.add_argument("--env-file", type=Path)
    live_parser.add_argument("--json", action="store_true", dest="as_json")

    eval_parser = subparsers.add_parser(
        "eval-provider", help="run fixed, non-authoritative Provider quality Evals"
    )
    eval_parser.add_argument("--dataset", type=Path)
    eval_parser.add_argument("--env-file", type=Path)
    eval_parser.add_argument("--json", action="store_true", dest="as_json")

    demo_parser = subparsers.add_parser(
        "demo", help="run the deterministic, offline Fixture MVP Demo"
    )
    demo_parser.add_argument("--world-id", required=True)
    demo_parser.add_argument("--output-dir", type=Path, default=Path(".mygo/demo"))
    demo_parser.add_argument("--worlds-dir", type=Path)
    demo_parser.add_argument("--webgal-root", type=Path)
    demo_parser.add_argument("--artifact-root", type=Path)
    demo_parser.add_argument("--canonical-export", type=Path)
    demo_parser.add_argument("--fixture-dir", type=Path)
    demo_parser.add_argument("--fixture-version", default=DEFAULT_FIXTURE_VERSION)
    demo_parser.add_argument("--json", action="store_true", dest="as_json")

    bind_parser = subparsers.add_parser(
        "skill-bind", help="bind a versioned Character Runtime Skill"
    )
    bind_parser.add_argument("--world-id", required=True)
    bind_parser.add_argument("--worlds-dir", type=Path, default=Path(".mygo/worlds"))
    bind_parser.add_argument("--skills-dir", type=Path, default=DEFAULT_SKILLS_DIR)
    bind_parser.add_argument("--character-id", required=True)
    bind_parser.add_argument("--skill-id", required=True)
    bind_parser.add_argument("--skill-version", required=True)
    bind_parser.add_argument("--operator", required=True)
    bind_parser.add_argument("--reason", required=True)
    bind_parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def _human_success(receipt: dict[str, Any]) -> str:
    if receipt["command"] == "init":
        return (
            f"Created World {receipt['world_id']} at version {receipt['world_version']} "
            f"(snapshot {receipt['snapshot_checksum'][:12]})"
        )
    if receipt["command"] == "show":
        return (
            f"World {receipt['world_id']} is at version {receipt['world_version']} "
            f"(snapshot {receipt['snapshot_checksum'][:12]}, "
            f"events {receipt['world_event_count']})"
        )
    if receipt["command"] == "skill-bind":
        return (
            f"Bound {receipt['character_id']} to "
            f"{receipt['new_skill']['skill_id']}@{receipt['new_skill']['version']} "
            f"in World {receipt['world_id']} (version unchanged at "
            f"{receipt['world_version']})"
        )
    if receipt["command"] == "render" and receipt["status"] == "no_work":
        return (
            f"World {receipt['world_id']} has no unprocessed Events through "
            f"version {receipt['target_world_version']}"
        )
    if receipt["status"] == "no_work":
        return f"World {receipt['world_id']} has no runnable Event Session"
    if receipt["command"] == "render":
        return (
            f"Rendered {receipt['event_count']} Events from World "
            f"{receipt['world_id']} as {receipt['render_count']} immutable scene(s)"
        )
    if receipt["command"] == "demo":
        artifacts = ", ".join(
            f"{item['render_id']}={item['content_hash']} ({item['scene_path']})"
            for item in receipt["renders"]
        )
        return (
            f"Demo World {receipt['world_id']} completed with Batches "
            f"{', '.join(receipt['generation_batch_ids'])} at version "
            f"{receipt['target_world_version']}; Broadcast Runs "
            f"{', '.join(receipt['broadcast_run_ids'])}; renders: {artifacts}"
        )
    if receipt["command"] == "live-demo":
        return (
            f"Live demo {receipt['world_id']} resolved at version "
            f"{receipt['final_world_version']} with "
            f"{receipt['provider_request_count']} Provider requests and "
            f"{len(receipt['renders'])} verified Render(s)"
        )
    if receipt["command"] == "eval-provider":
        return (
            f"Evaluated {receipt['sample_count']} fixed Provider sample(s): "
            f"{receipt['status']}"
        )
    return (
        f"Advanced World {receipt['world_id']} from version "
        f"{receipt['start_world_version']} to {receipt['end_world_version']} "
        f"({receipt['world_event_count']} events)"
    )


def _error_exit_code(error: WorldError) -> int:
    if error.code == "WORLD_ALREADY_EXISTS":
        return 3
    if error.code == "WORLD_NOT_FOUND":
        return 4
    if error.code == "SCHEMA_OUTDATED":
        return 5
    if error.code == "SEED_INVALID":
        return 2
    return 1


def run(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            receipt = initialize_world(
                args.seed,
                args.world_id,
                args.worlds_dir,
                skills_dir=args.skills_dir,
            )
        elif args.command == "show":
            receipt = show_world(args.world_id, args.worlds_dir)
        elif args.command == "advance":
            receipt = advance_world(
                args.world_id,
                args.worlds_dir,
                gateway_kind=args.gateway,
                run_id=args.run_id,
                max_waves=args.max_waves,
                request_budget=args.request_budget,
                max_character_concurrency=args.character_concurrency,
                skills_dir=args.skills_dir,
                env_file=args.env_file,
            )
        elif args.command == "render":
            receipt = render_world(
                args.world_id,
                args.worlds_dir,
                target_world_version=args.world_version,
                asset_manifest_path=args.asset_manifest,
                webgal_root=args.webgal_root,
                artifact_root=args.artifact_root,
                gateway_kind=args.gateway,
                skills_dir=args.skills_dir,
                request_budget=args.request_budget,
                env_file=args.env_file,
            )
        elif args.command == "demo":
            output_dir = args.output_dir
            receipt = run_demo(
                args.world_id,
                worlds_dir=args.worlds_dir or output_dir / "worlds",
                webgal_root=args.webgal_root or output_dir / "webgal",
                artifact_root=args.artifact_root or output_dir / "artifacts",
                export_path=(
                    args.canonical_export
                    or output_dir / "exports" / f"{args.world_id}.json"
                ),
                fixture_dir=args.fixture_dir or fixture_root(args.fixture_version),
            )
        elif args.command == "live-demo":
            receipt = run_live_demo(
                args.world_id,
                output_dir=args.output_dir,
                webgal_root=args.webgal_root,
                env_file=args.env_file,
            )
        elif args.command == "eval-provider":
            options = {"env_file": args.env_file}
            if args.dataset is not None:
                options["dataset_path"] = args.dataset
            receipt = run_provider_evals(**options)
        else:
            receipt = bind_character_skill(
                args.world_id,
                args.worlds_dir,
                character_id=args.character_id,
                skill_id=args.skill_id,
                skill_version=args.skill_version,
                operator=args.operator,
                reason=args.reason,
                skills_dir=args.skills_dir,
            )
    except WorldError as error:
        receipt = error.receipt or {
            "command": args.command,
            "status": "error",
        }
        receipt["error"] = {"code": error.code, "message": error.message}
        if args.as_json:
            print(
                json.dumps(
                    receipt, ensure_ascii=False, separators=(",", ":"), sort_keys=True
                )
            )
        else:
            print(f"Error [{error.code}]: {error.message}", file=sys.stderr)
        return _error_exit_code(error)

    if args.as_json:
        print(
            json.dumps(
                receipt, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            )
        )
    else:
        print(_human_success(receipt))
    return 0


def main() -> None:
    raise SystemExit(run())
