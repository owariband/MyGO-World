from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from mygo_world.errors import WorldError
from mygo_world.runtime import advance_world
from mygo_world.worlds import initialize_world, show_world


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mygo-world")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="initialize a durable World")
    init_parser.add_argument("--world-id", required=True)
    init_parser.add_argument("--seed", required=True, type=Path)
    init_parser.add_argument("--worlds-dir", type=Path, default=Path(".mygo/worlds"))
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
    advance_parser.add_argument(
        "--gateway", choices=("fixture", "provider"), default="fixture"
    )
    advance_parser.add_argument("--json", action="store_true", dest="as_json")
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
    if receipt["status"] == "no_work":
        return f"World {receipt['world_id']} has no runnable Event Session"
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
            receipt = initialize_world(args.seed, args.world_id, args.worlds_dir)
        elif args.command == "show":
            receipt = show_world(args.world_id, args.worlds_dir)
        else:
            receipt = advance_world(
                args.world_id,
                args.worlds_dir,
                gateway_kind=args.gateway,
            )
    except WorldError as error:
        receipt = {
            "command": args.command,
            "status": "error",
            "error": {"code": error.code, "message": error.message},
        }
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
