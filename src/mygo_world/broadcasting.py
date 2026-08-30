from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from mygo_world.canonical import canonical_json
from mygo_world.committer import (
    Clock,
    GenerationTraceRecord,
    IdGenerator,
    WorldCommitter,
    system_clock,
    uuid4_id,
)
from mygo_world.contracts import (
    AssetManifest,
    BroadcastEvent,
    BroadcastPlan,
    CompiledRender,
)
from mygo_world.db.engine import create_world_engine, require_current_schema
from mygo_world.db.models import (
    BroadcastDispositionRow,
    BroadcastRunRow,
    RenderRow,
    WorldEventRow,
    WorldRow,
    WorldVersionRow,
)
from mygo_world.errors import WorldError, WorldNotFoundError
from mygo_world.gateways import (
    FixtureGateway,
    ModelGateway,
    ModelGeneration,
    ModelRequest,
    OpenAICompatibleGateway,
)
from mygo_world.rendering import (
    RenderCompiler,
    RenderGateway,
    RenderPlanInvalid,
    RenderPlanner,
    load_asset_manifest,
)
from mygo_world.skill_bindings import load_effective_skills, require_effective_skill
from mygo_world.skills import DEFAULT_SKILLS_DIR, RuntimeSkillCatalog
from mygo_world.worlds import WorldPaths, mutation_lock, validate_world_id


def _broadcast_event(row: WorldEventRow) -> BroadcastEvent:
    payload = json.loads(row.payload_json)
    return BroadcastEvent(
        event_id=row.event_id,
        event_order=row.event_order,
        world_version=row.world_version,
        event_type=row.event_type,
        actor_id=payload.get("actor_id"),
        start_time_ms=row.start_time_ms,
        end_time_ms=row.end_time_ms,
        location_id=payload["location_id"],
        scope_key=payload["scope_key"],
        fact=payload["fact"],
    )


def _asset_candidates(manifest: AssetManifest) -> dict[str, Any]:
    return {
        "manifest_id": manifest.manifest_id,
        "manifest_version": manifest.version,
        "background_ids": [item.asset_id for item in manifest.backgrounds],
        "bgm_ids": [item.asset_id for item in manifest.bgms],
        "live2d_models": [
            {
                "asset_id": item.asset_id,
                "character_id": item.character_id,
                "motions": item.motions,
                "expressions": item.expressions,
                "entrance_effects": item.entrance_effects,
            }
            for item in manifest.live2d_models
        ],
    }


def _default_fixture_plan(
    *,
    world_id: str,
    target_world_version: int,
    frontier: list[BroadcastEvent],
    manifest: AssetManifest,
) -> dict[str, Any]:
    models_by_character = {item.character_id: item for item in manifest.live2d_models}
    included: list[BroadcastEvent] = []
    dispositions: list[dict[str, Any]] = []
    for event in frontier:
        if (
            event.event_type == "utterance"
            and event.actor_id not in models_by_character
        ):
            dispositions.append(
                {
                    "event_id": event.event_id,
                    "status": "omitted",
                    "reason": "No approved Live2D model is available for the speaker.",
                }
            )
        else:
            included.append(event)
            dispositions.append({"event_id": event.event_id, "status": "included"})

    renders: list[dict[str, Any]] = []
    if included:
        chunks: list[list[BroadcastEvent]] = []
        current: list[BroadcastEvent] = []
        current_beat_count = 3
        for event in included:
            event_beat_count = 2 if event.event_type == "utterance" else 1
            if current and current_beat_count + event_beat_count > 40:
                chunks.append(current)
                current = []
                current_beat_count = 3
            current.append(event)
            current_beat_count += event_beat_count
        if current:
            chunks.append(current)

        for render_index, chunk in enumerate(chunks, start=1):
            beats: list[dict[str, Any]] = [
                {
                    "beat_id": "chapter",
                    "type": "chapter",
                    "title": f"World Version {target_world_version}",
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
                    "asset_id": manifest.backgrounds[0].asset_id,
                    "source_event_ids": [],
                },
            ]
            for event_index, event in enumerate(chunk, start=1):
                if event.event_type == "utterance" and event.actor_id is not None:
                    model = models_by_character[event.actor_id]
                    beats.extend(
                        [
                            {
                                "beat_id": f"show-{event_index}",
                                "type": "show",
                                "character_id": event.actor_id,
                                "model_asset_id": model.asset_id,
                                "position": "center",
                                "source_event_ids": [],
                            },
                            {
                                "beat_id": f"event-{event_index}",
                                "type": "dialogue",
                                "character_id": event.actor_id,
                                "model_asset_id": model.asset_id,
                                "text": event.fact["text"],
                                "source_event_ids": [event.event_id],
                            },
                        ]
                    )
                else:
                    text = event.fact.get("description") or event.fact.get("text")
                    if not text:
                        text = canonical_json(event.fact)
                    beats.append(
                        {
                            "beat_id": f"event-{event_index}",
                            "type": "narration",
                            "text": text,
                            "source_event_ids": [event.event_id],
                        }
                    )
            renders.append(
                {
                    "render_id": (f"render-v{target_world_version}-{render_index:03d}"),
                    "title": f"World Version {target_world_version}",
                    "estimated_play_ms": max(2500, len(chunk) * 3000),
                    "beats": beats,
                }
            )
    return {
        "schema_version": 1,
        "world_id": world_id,
        "target_world_version": target_world_version,
        "dispositions": dispositions,
        "renders": renders,
    }


def _trace_record(
    *,
    trace_id: str,
    world_id: str,
    generation: ModelGeneration[BroadcastPlan],
    validation: dict[str, Any],
) -> GenerationTraceRecord:
    request = generation.request
    return GenerationTraceRecord(
        trace_id=trace_id,
        world_id=world_id,
        input_world_version=int(request.input_payload["world_version"]),
        session_id="broadcast",
        agent_type=request.agent_type,
        agent_id=request.agent_id,
        call_kind=request.call_kind,
        skill_id=request.skill_id,
        skill_version=request.skill_version,
        skill_content_hash=request.skill_content_hash,
        model_id=request.model_id,
        model_config=request.model_config,
        request=request.trace_payload(),
        raw_response=generation.raw_response,
        structured_result=generation.structured.model_dump(mode="json"),
        validation=validation,
    )


def render_world(
    world_id: str,
    worlds_dir: Path,
    *,
    asset_manifest_path: Path,
    webgal_root: Path,
    artifact_root: Path = Path(".mygo/renders"),
    target_world_version: int | None = None,
    gateway: ModelGateway | None = None,
    gateway_kind: str = "fixture",
    planner: RenderPlanner | None = None,
    compiler: RenderCompiler | None = None,
    render_gateway: RenderGateway | None = None,
    clock: Clock = system_clock,
    id_generator: IdGenerator = uuid4_id,
    failure_injector: Callable[[str], None] | None = None,
    skills_dir: Path = DEFAULT_SKILLS_DIR,
) -> dict[str, Any]:
    validate_world_id(world_id)
    paths = WorldPaths(worlds_dir, world_id)
    if not paths.database.is_file():
        raise WorldNotFoundError(world_id)
    manifest = load_asset_manifest(asset_manifest_path)
    inject = failure_injector or (lambda _stage: None)

    with mutation_lock(paths.render_lock):
        engine = create_world_engine(paths.database)
        try:
            require_current_schema(paths.database, engine)
            bindings = load_effective_skills(
                engine,
                world_id=world_id,
                catalog=RuntimeSkillCatalog.load(skills_dir),
            )
            broadcast_skill = require_effective_skill(
                bindings, "broadcast", "global-broadcast"
            )
            with Session(engine) as session:
                world = session.get(WorldRow, world_id)
                if world is None:
                    raise WorldNotFoundError(world_id)
                target = (
                    world.current_version
                    if target_world_version is None
                    else target_world_version
                )
                if (
                    target > world.current_version
                    or session.get(WorldVersionRow, target) is None
                ):
                    raise WorldError(
                        "WORLD_VERSION_NOT_FOUND",
                        f"World Version {target} does not exist in World '{world_id}'",
                    )
                event_rows = list(
                    session.scalars(
                        select(WorldEventRow)
                        .where(WorldEventRow.world_version <= target)
                        .order_by(WorldEventRow.event_order)
                    )
                )
                processed = set(
                    session.scalars(select(BroadcastDispositionRow.event_id))
                )

            events = [_broadcast_event(row) for row in event_rows]
            frontier = [event for event in events if event.event_id not in processed]
            if not frontier:
                return {
                    "command": "render",
                    "status": "no_work",
                    "world_id": world_id,
                    "target_world_version": target,
                    "model_call_count": 0,
                    "render_count": 0,
                }

            if gateway is None:
                if gateway_kind == "fixture":
                    gateway = FixtureGateway(
                        {
                            "broadcast:global-broadcast:broadcast_plan": _default_fixture_plan(
                                world_id=world_id,
                                target_world_version=target,
                                frontier=frontier,
                                manifest=manifest,
                            )
                        }
                    )
                elif gateway_kind == "provider":
                    try:
                        gateway = OpenAICompatibleGateway.from_environment()
                    except ValueError as exc:
                        raise WorldError(
                            "GATEWAY_CONFIGURATION_INVALID", str(exc)
                        ) from exc
                else:
                    raise WorldError(
                        "GATEWAY_INVALID", f"Unknown gateway '{gateway_kind}'"
                    )

            request = ModelRequest(
                agent_type="broadcast",
                agent_id="global-broadcast",
                call_kind="broadcast_plan",
                model_id=getattr(gateway, "model_id", "fixture-model-v1"),
                skill_id=broadcast_skill.skill_id,
                skill_version=broadcast_skill.version,
                skill_content_hash=broadcast_skill.content_hash,
                input_payload={
                    "world_id": world_id,
                    "world_version": target,
                    "frontier_event_ids": [event.event_id for event in frontier],
                    "events": [event.model_dump(mode="json") for event in events],
                    "asset_candidates": _asset_candidates(manifest),
                },
                model_config={"temperature": 0},
                skill_body=broadcast_skill.body,
            )
            generation = gateway.generate(request, BroadcastPlan)
            trace_id = id_generator()
            try:
                jobs = (planner or RenderPlanner()).plan(
                    generation.structured,
                    world_id=world_id,
                    target_world_version=target,
                    events=events,
                    frontier_event_ids={event.event_id for event in frontier},
                    manifest=manifest,
                    webgal_root=webgal_root,
                )
            except RenderPlanInvalid as exc:
                WorldCommitter(
                    engine, clock=clock, id_generator=id_generator
                ).record_generation_traces(
                    [
                        _trace_record(
                            trace_id=trace_id,
                            world_id=world_id,
                            generation=generation,
                            validation={
                                "ok": False,
                                "diagnostics": [
                                    item.model_dump(mode="json")
                                    for item in exc.diagnostics
                                ],
                            },
                        )
                    ]
                )
                raise

            WorldCommitter(
                engine, clock=clock, id_generator=id_generator
            ).record_generation_traces(
                [
                    _trace_record(
                        trace_id=trace_id,
                        world_id=world_id,
                        generation=generation,
                        validation={"ok": True, "diagnostics": []},
                    )
                ]
            )
            compiled = [(compiler or RenderCompiler()).compile(job) for job in jobs]
            publisher = render_gateway or RenderGateway()
            published: list[tuple[CompiledRender, Path, bool]] = []
            for item in compiled:
                scene_path, reused = publisher.publish(
                    item, artifact_root=artifact_root, webgal_root=webgal_root
                )
                published.append((item, scene_path, reused))
            inject("published")

            run_id = id_generator()
            created_at = clock().astimezone(UTC).isoformat(timespec="microseconds")
            render_ids_by_event: dict[str, list[str]] = {
                event.event_id: [] for event in frontier
            }
            for item in compiled:
                for beat in item.job.beats:
                    for event_id in beat.get("source_event_ids", []):
                        if event_id in render_ids_by_event:
                            render_ids_by_event[event_id].append(item.job.render_id)

            with Session(engine) as session, session.begin():
                already_processed = set(
                    session.scalars(
                        select(BroadcastDispositionRow.event_id).where(
                            BroadcastDispositionRow.event_id.in_(render_ids_by_event)
                        )
                    )
                )
                if already_processed:
                    raise WorldError(
                        "BROADCAST_FRONTIER_CONSUMED",
                        "Another Broadcast Run already consumed part of this Event frontier",
                    )
                session.add(
                    BroadcastRunRow(
                        run_id=run_id,
                        world_id=world_id,
                        target_world_version=target,
                        source_trace_id=trace_id,
                        plan_json=canonical_json(
                            generation.structured.model_dump(mode="json")
                        ),
                        created_at=created_at,
                    )
                )
                session.flush()
                for order, (item, scene_path, _reused) in enumerate(published, start=1):
                    artifact_path = (
                        artifact_root.resolve()
                        / world_id
                        / item.job.render_id
                        / item.content_hash
                    )
                    session.add(
                        RenderRow(
                            render_record_id=id_generator(),
                            world_id=world_id,
                            render_id=item.job.render_id,
                            run_id=run_id,
                            target_world_version=target,
                            render_order=order,
                            content_hash=item.content_hash,
                            artifact_path=str(artifact_path),
                            scene_path=str(scene_path),
                            created_at=created_at,
                        )
                    )
                dispositions = {
                    item.event_id: item for item in generation.structured.dispositions
                }
                for event in frontier:
                    disposition = dispositions[event.event_id]
                    session.add(
                        BroadcastDispositionRow(
                            event_id=event.event_id,
                            run_id=run_id,
                            status=disposition.status,
                            reason=disposition.reason,
                            render_ids_json=canonical_json(
                                list(dict.fromkeys(render_ids_by_event[event.event_id]))
                            ),
                            created_at=created_at,
                        )
                    )
                inject("database")

            return {
                "command": "render",
                "status": "rendered",
                "world_id": world_id,
                "target_world_version": target,
                "broadcast_run_id": run_id,
                "event_count": len(frontier),
                "included_event_count": sum(
                    item.status == "included"
                    for item in generation.structured.dispositions
                ),
                "omitted_event_count": sum(
                    item.status == "omitted"
                    for item in generation.structured.dispositions
                ),
                "render_count": len(compiled),
                "renders": [
                    {
                        "render_id": item.job.render_id,
                        "content_hash": item.content_hash,
                        "scene_path": str(scene_path),
                        "reused": reused,
                    }
                    for item, scene_path, reused in published
                ],
                "model_call_count": 1,
                "gateway": gateway_kind,
                "network_request_count": getattr(
                    gateway, "network_request_count", None
                ),
            }
        finally:
            engine.dispose()
