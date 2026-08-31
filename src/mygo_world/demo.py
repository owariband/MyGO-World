from __future__ import annotations

import json
import random
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import yaml

from mygo_world.broadcasting import render_world
from mygo_world.canonical import canonical_json
from mygo_world.canonical_export import write_canonical_export
from mygo_world.errors import WorldAlreadyExistsError, WorldError
from mygo_world.gateways import FixtureGateway, FixtureResponse
from mygo_world.runtime import advance_world
from mygo_world.worlds import WorldPaths, initialize_world, validate_world_id

DEFAULT_FIXTURE_VERSION = "v1"
DEFAULT_FIXTURES_DIR = Path(__file__).with_name("fixtures") / "demo"


class DeterministicIdGenerator:
    def __init__(self, seed: int) -> None:
        self._random = random.Random(seed)

    def __call__(self) -> str:
        return str(uuid.UUID(int=self._random.getrandbits(128), version=4))


def _lineage_session_id(
    *,
    world_id: str,
    world_version: int,
    index: int,
    parent_ids: list[str],
    participant_ids: list[str],
    location_id: str,
    scope_key: str,
) -> str:
    identity = canonical_json(
        {
            "world_id": world_id,
            "world_version": world_version,
            "parents": parent_ids,
            "participants": participant_ids,
            "location_id": location_id,
            "scope_key": scope_key,
        }
    )
    digest = sha256(identity.encode("utf-8")).hexdigest()[:16]
    return f"session-lineage-{world_version}-{index}-{digest}"


def _substitute(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _substitute(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [_substitute(item, replacements) for item in value]
    if isinstance(value, str):
        result = value
        for key, replacement in replacements.items():
            result = result.replace("{{" + key + "}}", replacement)
        return result
    return value


@dataclass(frozen=True)
class DemoFixture:
    root: Path
    fixture_id: str
    version: str
    seed_path: Path
    skills_dir: Path
    asset_manifest_path: Path
    webgal_template: Path
    clock_value: datetime
    random_seed: int
    steps: tuple[dict[str, Any], ...]
    gateway: FixtureGateway

    @classmethod
    def load(cls, root: Path, *, world_id: str) -> DemoFixture:
        manifest_path = root / "fixture.yaml"
        try:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            calls = yaml.safe_load(
                (root / manifest["calls"]).read_text(encoding="utf-8")
            )
        except (OSError, KeyError, TypeError, yaml.YAMLError) as exc:
            raise WorldError(
                "DEMO_FIXTURE_INVALID", f"Cannot load Demo Fixture '{root}': {exc}"
            ) from exc
        if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
            raise WorldError(
                "DEMO_FIXTURE_INVALID",
                "Demo Fixture manifest must use schema_version 1",
            )
        if not isinstance(calls, dict) or calls.get("schema_version") != 1:
            raise WorldError(
                "DEMO_FIXTURE_INVALID",
                "Fixture call manifest must use schema_version 1",
            )

        replacements = {
            "world_id": world_id,
            "anon_successor_session_id": _lineage_session_id(
                world_id=world_id,
                world_version=2,
                index=1,
                parent_ids=["session-first-meeting"],
                participant_ids=["character-anon"],
                location_id="location-live-house",
                scope_key="stage",
            ),
            "soyo_successor_session_id": _lineage_session_id(
                world_id=world_id,
                world_version=2,
                index=2,
                parent_ids=["session-first-meeting"],
                participant_ids=["character-soyo"],
                location_id="location-live-house",
                scope_key="lounge",
            ),
        }
        responses: dict[str, FixtureResponse] = {}
        try:
            entries = calls["calls"]
            for entry in entries:
                key = (
                    f"{entry['agent_type']}:{entry['agent_id']}:"
                    f"{entry['generation_batch_id']}:"
                    f"{entry['generation_wave']}:{entry['call_kind']}"
                )
                if key in responses:
                    raise WorldError(
                        "DEMO_FIXTURE_INVALID", f"Duplicate Fixture call key '{key}'"
                    )
                response_path = root / entry["response"]
                body = _substitute(
                    json.loads(response_path.read_text(encoding="utf-8")),
                    replacements,
                )
                responses[key] = FixtureResponse(
                    body=body,
                    expected_input_hash=entry.get("expected_input_sha256"),
                )
            clock_value = datetime.fromisoformat(manifest["clock"])
            if clock_value.tzinfo is None:
                raise ValueError("clock must include a timezone")
            steps = tuple(manifest["steps"])
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            if isinstance(exc, WorldError):
                raise
            raise WorldError("DEMO_FIXTURE_INVALID", str(exc)) from exc

        return cls(
            root=root,
            fixture_id=str(manifest["fixture_id"]),
            version=str(manifest["version"]),
            seed_path=root / manifest["scenario"],
            skills_dir=root / manifest["skills_dir"],
            asset_manifest_path=root / manifest["asset_manifest"],
            webgal_template=root / manifest["webgal_template"],
            clock_value=clock_value.astimezone(UTC),
            random_seed=int(manifest["random_seed"]),
            steps=steps,
            gateway=FixtureGateway(
                responses, require_input_hashes=True, normalize_inputs=True
            ),
        )


def fixture_root(version: str = DEFAULT_FIXTURE_VERSION) -> Path:
    return DEFAULT_FIXTURES_DIR / version


def run_demo(
    world_id: str,
    *,
    worlds_dir: Path,
    webgal_root: Path,
    artifact_root: Path,
    export_path: Path,
    fixture_dir: Path | None = None,
) -> dict[str, Any]:
    """Run the checked-in Fixture through init, advance and render entry points."""

    validate_world_id(world_id)
    paths = WorldPaths(worlds_dir, world_id)
    if paths.world_dir.exists():
        raise WorldAlreadyExistsError(world_id)

    fixture = DemoFixture.load(
        fixture_dir or fixture_root(DEFAULT_FIXTURE_VERSION), world_id=world_id
    )
    id_generator = DeterministicIdGenerator(fixture.random_seed)

    def clock() -> datetime:
        return fixture.clock_value

    shutil.copytree(fixture.webgal_template, webgal_root, dirs_exist_ok=True)
    initialize_world(
        fixture.seed_path,
        world_id,
        worlds_dir,
        clock=clock,
        id_generator=id_generator,
        skills_dir=fixture.skills_dir,
    )

    advance_receipts: list[dict[str, Any]] = []
    render_receipts: list[dict[str, Any]] = []
    for step in fixture.steps:
        kind = step.get("command")
        if kind == "advance":
            advance_receipts.append(
                advance_world(
                    world_id,
                    worlds_dir,
                    gateway=fixture.gateway,
                    gateway_kind="fixture",
                    run_id=str(step["run_id"]),
                    max_waves=int(step["max_waves"]),
                    request_budget=int(step.get("request_budget", 40)),
                    max_character_concurrency=int(step.get("character_concurrency", 4)),
                    clock=clock,
                    id_generator=id_generator,
                    skills_dir=fixture.skills_dir,
                )
            )
        elif kind == "render":
            render_receipts.append(
                render_world(
                    world_id,
                    worlds_dir,
                    asset_manifest_path=fixture.asset_manifest_path,
                    webgal_root=webgal_root,
                    artifact_root=artifact_root,
                    gateway=fixture.gateway,
                    gateway_kind="fixture",
                    clock=clock,
                    id_generator=id_generator,
                    skills_dir=fixture.skills_dir,
                )
            )
        else:
            raise WorldError(
                "DEMO_FIXTURE_INVALID", f"Unknown Demo step command '{kind}'"
            )

    unused = fixture.gateway.unused_response_keys
    if unused:
        raise WorldError(
            "DEMO_FIXTURE_INCOMPLETE",
            "Fixture declared unused responses: " + ", ".join(unused),
        )
    exported = write_canonical_export(world_id, worlds_dir, export_path)
    rendered = [
        item
        for receipt in render_receipts
        if receipt["status"] == "rendered"
        for item in receipt["renders"]
    ]
    batch_ids = [receipt["run_id"] for receipt in advance_receipts]
    broadcast_ids = [
        receipt["broadcast_run_id"]
        for receipt in render_receipts
        if receipt["status"] == "rendered"
    ]
    return {
        "command": "demo",
        "status": "completed",
        "fixture": {"fixture_id": fixture.fixture_id, "version": fixture.version},
        "world_id": world_id,
        "generation_batch_id": batch_ids[0],
        "generation_batch_ids": batch_ids,
        "target_world_version": exported["world"]["current_version"],
        "snapshot_checksum": exported["snapshot"]["checksum"],
        "broadcast_run_id": broadcast_ids[-1],
        "broadcast_run_ids": broadcast_ids,
        "renders": [
            {
                "render_id": item["render_id"],
                "scene_path": item["scene_path"],
                "artifact_path": str(
                    artifact_root.resolve()
                    / world_id
                    / item["render_id"]
                    / item["content_hash"]
                ),
                "content_hash": item["content_hash"],
            }
            for item in rendered
        ],
        "canonical_export_path": str(export_path.resolve()),
    }
