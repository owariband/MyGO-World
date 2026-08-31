from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mygo_world.broadcasting import render_world
from mygo_world.canonical import canonical_json, sha256_bytes
from mygo_world.canonical_export import export_world
from mygo_world.contracts import load_seed
from mygo_world.errors import WorldError
from mygo_world.gateways import OpenAICompatibleGateway
from mygo_world.rendering import (
    load_asset_manifest,
    validate_asset_manifest_files,
)
from mygo_world.runtime import advance_world
from mygo_world.skill_bindings import resolve_seed_skills
from mygo_world.skills import DEFAULT_SKILLS_DIR, RuntimeSkillCatalog
from mygo_world.worlds import WorldPaths, initialize_world, validate_world_id

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LIVE_SCENARIO = PROJECT_ROOT / "examples" / "live" / "scenario.yaml"
DEFAULT_LIVE_ASSET_MANIFEST = (
    PROJECT_ROOT / "examples" / "live" / "assets" / "manifest.yaml"
)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{canonical_json(value)}\n", encoding="utf-8")


def _preflight(
    *,
    world_id: str,
    worlds_dir: Path,
    scenario_path: Path,
    asset_manifest_path: Path,
    webgal_root: Path,
    skills_dir: Path,
    env_file: Path | None,
) -> OpenAICompatibleGateway:
    validate_world_id(world_id)
    try:
        gateway = OpenAICompatibleGateway.from_environment(env_file=env_file)
    except ValueError as exc:
        raise WorldError("LIVE_CONFIGURATION_INVALID", str(exc)) from exc
    if not webgal_root.is_dir():
        raise WorldError(
            "LIVE_WEBGAL_ROOT_INVALID",
            "WEBGAL_ROOT must identify an existing WebGAL project directory",
        )
    if WorldPaths(worlds_dir, world_id).database.exists():
        raise WorldError(
            "WORLD_ALREADY_EXISTS",
            f"World '{world_id}' already exists; Live acceptance requires a new World ID",
        )
    loaded = load_seed(scenario_path)
    resolve_seed_skills(loaded.seed, RuntimeSkillCatalog.load(skills_dir))
    manifest = load_asset_manifest(asset_manifest_path)
    validate_asset_manifest_files(manifest, webgal_root)
    return gateway


def _assert_memory_isolation(traces: list[dict[str, Any]]) -> None:
    markers = {
        "character-anon": ("PRIVATE_ANON", "PRIVATE_SOYO"),
        "character-soyo": ("PRIVATE_SOYO", "PRIVATE_ANON"),
    }
    for character_id, (own, other) in markers.items():
        requests = [
            item["request"]
            for item in traces
            if item["agent_type"] == "character" and item["agent_id"] == character_id
        ]
        if not requests:
            raise WorldError(
                "LIVE_TRACE_INVALID", f"No Character Trace exists for '{character_id}'"
            )
        for request in requests:
            serialized = canonical_json(request)
            if own not in serialized or other in serialized:
                raise WorldError(
                    "LIVE_MEMORY_ISOLATION_FAILED",
                    f"Private Memory projection is invalid for '{character_id}'",
                )


def _assert_character_actions(
    traces: list[dict[str, Any]], events: list[dict[str, Any]]
) -> dict[str, str]:
    committed_sources = {
        item["payload"].get("source_ref")
        for item in events
        if item["payload"].get("source_kind") == "action_proposal"
    }
    accepted: dict[str, str] = {}
    for item in traces:
        if item["agent_type"] != "character" or not item["validation"].get("ok"):
            continue
        result = item["structured_result"]
        if result.get("action", {}).get("kind") == "no_op":
            continue
        proposal_id = result.get("proposal_id")
        if proposal_id in committed_sources:
            accepted[item["agent_id"]] = proposal_id
    required = {"character-anon", "character-soyo"}
    if set(accepted) != required:
        raise WorldError(
            "LIVE_CHARACTER_ACTION_MISSING",
            "Anon and Soyo must each have a validated, committed Action Proposal",
        )
    if not any(
        item["agent_type"] == "director" and item["validation"].get("ok")
        for item in traces
    ):
        raise WorldError(
            "LIVE_DIRECTOR_TRACE_MISSING", "No valid Director Trace exists"
        )
    return accepted


def _verify_published_renders(
    render_receipt: dict[str, Any], domain: dict[str, Any]
) -> list[dict[str, Any]]:
    rows = {item["render_id"]: item for item in domain["broadcast"]["renders"]}
    verified: list[dict[str, Any]] = []
    for item in render_receipt.get("renders", []):
        path = Path(item["scene_path"])
        try:
            digest = sha256_bytes(path.read_bytes())
        except OSError as exc:
            raise WorldError(
                "LIVE_RENDER_MISSING",
                f"Published Render '{item['render_id']}' is missing",
            ) from exc
        persisted = rows.get(item["render_id"])
        if (
            digest != item["content_hash"]
            or persisted is None
            or persisted["content_hash"] != digest
        ):
            raise WorldError(
                "LIVE_RENDER_HASH_MISMATCH",
                f"Published Render '{item['render_id']}' failed SHA-256 verification",
            )
        verified.append(
            {
                "render_id": item["render_id"],
                "path": str(path.resolve()),
                "sha256": digest,
            }
        )
    if not verified:
        raise WorldError("LIVE_RENDER_MISSING", "Live Broadcast produced no Render")
    return verified


def run_live_demo(
    world_id: str,
    *,
    output_dir: Path,
    webgal_root: Path | None = None,
    scenario_path: Path = DEFAULT_LIVE_SCENARIO,
    asset_manifest_path: Path = DEFAULT_LIVE_ASSET_MANIFEST,
    skills_dir: Path = DEFAULT_SKILLS_DIR,
    env_file: Path | None = None,
) -> dict[str, Any]:
    """Run and audit the explicitly requested paid Live acceptance path."""

    configured_webgal_root = webgal_root
    if configured_webgal_root is None:
        value = os.environ.get("WEBGAL_ROOT", "").strip()
        if not value:
            raise WorldError("LIVE_CONFIGURATION_INVALID", "WEBGAL_ROOT is required")
        configured_webgal_root = Path(value)
    worlds_dir = output_dir / "worlds"
    artifact_root = output_dir / "render-artifacts"
    gateway = _preflight(
        world_id=world_id,
        worlds_dir=worlds_dir,
        scenario_path=scenario_path,
        asset_manifest_path=asset_manifest_path,
        webgal_root=configured_webgal_root,
        skills_dir=skills_dir,
        env_file=env_file,
    )

    init_receipt = initialize_world(
        scenario_path, world_id, worlds_dir, skills_dir=skills_dir
    )
    advance_receipt = advance_world(
        world_id,
        worlds_dir,
        gateway=gateway,
        gateway_kind="provider",
        max_waves=6,
        request_budget=40,
        skills_dir=skills_dir,
    )
    render_receipt = render_world(
        world_id,
        worlds_dir,
        asset_manifest_path=asset_manifest_path,
        webgal_root=configured_webgal_root,
        artifact_root=artifact_root,
        gateway=gateway,
        gateway_kind="provider",
        request_budget=6,
        skills_dir=skills_dir,
    )

    domain = export_world(world_id, worlds_dir)
    traces = domain["generation"]["traces"]
    try:
        gateway.assert_no_credentials(
            canonical_json(
                {
                    "advance": advance_receipt,
                    "render": render_receipt,
                    "traces": traces,
                }
            )
        )
    except ValueError as exc:
        raise WorldError("LIVE_SECRET_DISCLOSURE", str(exc)) from exc
    _assert_memory_isolation(traces)
    accepted = _assert_character_actions(traces, domain["ledger"]["events"])

    session = next(
        item
        for item in domain["sessions"]["items"]
        if item["session_id"] == "session-live-set-list"
    )
    if session["status"] != "closed" or session["closure_reason"] != "resolved":
        raise WorldError(
            "LIVE_SESSION_NOT_RESOLVED",
            "The target Event Session did not end naturally with 'resolved'",
        )
    if (
        advance_receipt["wave_count"] > 6
        or "MAX_WAVES_REACHED" in advance_receipt["warnings"]
    ):
        raise WorldError(
            "LIVE_SESSION_NOT_RESOLVED",
            "The target Event Session reached its Wave limit",
        )
    verified_renders = _verify_published_renders(render_receipt, domain)

    provider_request_count = gateway.network_request_count
    receipt = {
        "command": "live-demo",
        "status": "completed",
        "world_id": world_id,
        "generation_batch_id": advance_receipt["run_id"],
        "final_world_version": advance_receipt["end_world_version"],
        "session_closure_reason": session["closure_reason"],
        "provider_request_count": provider_request_count,
        "broadcast_run_id": render_receipt["broadcast_run_id"],
        "accepted_action_proposals": accepted,
        "renders": verified_renders,
        "completed_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    _write_json(
        output_dir / "cli-receipts.json",
        {"init": init_receipt, "advance": advance_receipt, "render": render_receipt},
    )
    _write_json(output_dir / "canonical-domain.json", domain)
    _write_json(output_dir / "generation-traces.json", traces)
    _write_json(output_dir / "render-metadata.json", domain["broadcast"])
    _write_json(output_dir / "receipt.json", receipt)
    return receipt
