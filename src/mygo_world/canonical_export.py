from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from mygo_world.canonical import canonical_json, sha256_text
from mygo_world.db.engine import create_world_engine, require_current_schema
from mygo_world.db.models import (
    AgentMemoryRow,
    BroadcastDispositionRow,
    BroadcastRunRow,
    DecisionTurnRecordRow,
    EntityRevisionRow,
    EventSessionMemberRow,
    EventSessionRow,
    GenerationBatchRow,
    GenerationTraceRow,
    GenerationWaveRow,
    RenderRow,
    SkillBindingRow,
    SnapshotRow,
    WorldEventRow,
    WorldRow,
    WorldSegmentRow,
    WorldVersionRow,
)
from mygo_world.errors import WorldError, WorldNotFoundError
from mygo_world.worlds import WorldPaths, validate_world_id


def _row(row: Any, *, json_fields: tuple[str, ...] = ()) -> dict[str, Any]:
    value = {column.name: getattr(row, column.name) for column in row.__table__.columns}
    for field in json_fields:
        value[field.removesuffix("_json")] = json.loads(value.pop(field))
    return value


def _stable_render_path(path: str, *, marker: tuple[str, ...]) -> str:
    parts = Path(path).parts
    for index in range(len(parts) - len(marker) + 1):
        if tuple(parts[index : index + len(marker)]) == marker:
            return Path(*parts[index:]).as_posix()
    return Path(path).name


def _derived_session_parents(
    segments: list[WorldSegmentRow],
    *,
    committed_segment_ids: set[str],
) -> list[dict[str, str]]:
    edges: set[tuple[str, str]] = set()
    for segment in segments:
        if segment.segment_id not in committed_segment_ids:
            continue
        payload = json.loads(segment.payload_json)
        for successor in payload.get("successor_sessions", []):
            for parent_session_id in successor.get("parent_session_ids", []):
                edges.add((successor["session_id"], parent_session_id))
    return [
        {"session_id": session_id, "parent_session_id": parent_session_id}
        for session_id, parent_session_id in sorted(edges)
    ]


def _derived_session_queue(
    sessions: list[EventSessionRow],
) -> list[dict[str, int | str | None]]:
    return [
        {
            "queue_order": item.queue_order,
            "session_id": item.session_id,
            "enqueued_world_version": item.created_world_version,
            "dequeued_world_version": item.closed_world_version,
        }
        for item in sorted(sessions, key=lambda item: item.queue_order)
    ]


def export_world(world_id: str, worlds_dir: Path) -> dict[str, Any]:
    """Return a path-independent, stable export of persisted domain results."""

    validate_world_id(world_id)
    paths = WorldPaths(worlds_dir, world_id)
    if not paths.database.is_file():
        raise WorldNotFoundError(world_id)
    engine = create_world_engine(paths.database)
    try:
        require_current_schema(paths.database, engine)
        with Session(engine) as session:
            world = session.get(WorldRow, world_id)
            if world is None:
                raise WorldNotFoundError(world_id)
            snapshot_row = session.get(SnapshotRow, world.current_version)
            if snapshot_row is None:
                raise WorldError("SNAPSHOT_MISSING", "Current Snapshot is missing")
            if sha256_text(snapshot_row.snapshot_json) != snapshot_row.checksum:
                raise WorldError(
                    "SNAPSHOT_CHECKSUM_MISMATCH",
                    "Current Snapshot failed checksum validation",
                )

            segments = list(
                session.scalars(
                    select(WorldSegmentRow).order_by(WorldSegmentRow.segment_order)
                )
            )
            versions = list(
                session.scalars(
                    select(WorldVersionRow).order_by(WorldVersionRow.version)
                )
            )
            revisions = list(
                session.scalars(
                    select(EntityRevisionRow).order_by(
                        EntityRevisionRow.entity_id,
                        EntityRevisionRow.revision_order,
                        EntityRevisionRow.entity_revision_id,
                    )
                )
            )
            events = list(
                session.scalars(
                    select(WorldEventRow).order_by(
                        WorldEventRow.event_order, WorldEventRow.event_id
                    )
                )
            )
            memories = list(
                session.scalars(
                    select(AgentMemoryRow).order_by(
                        AgentMemoryRow.agent_id,
                        AgentMemoryRow.namespace,
                        AgentMemoryRow.relative_time_ms,
                        AgentMemoryRow.memory_id,
                    )
                )
            )
            sessions = list(
                session.scalars(
                    select(EventSessionRow).order_by(EventSessionRow.session_id)
                )
            )
            members = list(
                session.scalars(
                    select(EventSessionMemberRow).order_by(
                        EventSessionMemberRow.session_id,
                        EventSessionMemberRow.agent_id,
                    )
                )
            )
            parents = _derived_session_parents(
                segments,
                committed_segment_ids={item.segment_id for item in versions},
            )
            queue = _derived_session_queue(sessions)
            bindings = list(
                session.scalars(
                    select(SkillBindingRow).order_by(SkillBindingRow.binding_order)
                )
            )
            batches = list(
                session.scalars(
                    select(GenerationBatchRow).order_by(
                        GenerationBatchRow.created_at, GenerationBatchRow.run_id
                    )
                )
            )
            waves = list(
                session.scalars(
                    select(GenerationWaveRow).order_by(
                        GenerationWaveRow.run_id, GenerationWaveRow.wave_number
                    )
                )
            )
            decision_turns = list(
                session.scalars(
                    select(DecisionTurnRecordRow).order_by(
                        DecisionTurnRecordRow.turn_order
                    )
                )
            )
            traces = list(
                session.scalars(
                    select(GenerationTraceRow).order_by(
                        GenerationTraceRow.agent_type,
                        GenerationTraceRow.agent_id,
                        GenerationTraceRow.input_world_version,
                        GenerationTraceRow.call_kind,
                        GenerationTraceRow.trace_id,
                    )
                )
            )
            runs = list(
                session.scalars(
                    select(BroadcastRunRow).order_by(
                        BroadcastRunRow.target_world_version,
                        BroadcastRunRow.run_id,
                    )
                )
            )
            dispositions = list(
                session.scalars(
                    select(BroadcastDispositionRow).order_by(
                        BroadcastDispositionRow.event_id
                    )
                )
            )
            renders = list(
                session.scalars(
                    select(RenderRow).order_by(
                        RenderRow.target_world_version,
                        RenderRow.render_order,
                        RenderRow.render_id,
                    )
                )
            )

            render_values = []
            for item in renders:
                value = _row(item)
                value["artifact_path"] = _stable_render_path(
                    value["artifact_path"], marker=(world_id, item.render_id)
                )
                value["scene_path"] = _stable_render_path(
                    value["scene_path"], marker=("game", "scene", "generated")
                )
                render_values.append(value)

            return {
                "schema_version": 1,
                "world": _row(world),
                "ledger": {
                    "segments": [
                        _row(item, json_fields=("payload_json",)) for item in segments
                    ],
                    "versions": [_row(item) for item in versions],
                    "entity_revisions": [
                        _row(item, json_fields=("payload_json",)) for item in revisions
                    ],
                    "events": [
                        _row(item, json_fields=("payload_json",)) for item in events
                    ],
                },
                "snapshot": {
                    "world_version": snapshot_row.world_version,
                    "checksum": snapshot_row.checksum,
                    "value": json.loads(snapshot_row.snapshot_json),
                },
                "memory": [
                    _row(
                        item,
                        json_fields=(
                            "entity_tags_json",
                            "location_tags_json",
                            "payload_json",
                        ),
                    )
                    for item in memories
                ],
                "sessions": {
                    "items": [_row(item) for item in sessions],
                    "members": [_row(item) for item in members],
                    "parents": parents,
                    "queue": queue,
                },
                "skills": [_row(item) for item in bindings],
                "generation": {
                    "batches": [
                        _row(item, json_fields=("warnings_json",)) for item in batches
                    ],
                    "waves": [_row(item) for item in waves],
                    "decision_turns": [
                        _row(item, json_fields=("candidate_ids_json",))
                        for item in decision_turns
                    ],
                    "traces": [
                        _row(
                            item,
                            json_fields=(
                                "model_config_json",
                                "request_json",
                                "structured_result_json",
                                "validation_json",
                            ),
                        )
                        for item in traces
                    ],
                },
                "broadcast": {
                    "runs": [_row(item, json_fields=("plan_json",)) for item in runs],
                    "dispositions": [
                        _row(item, json_fields=("render_ids_json",))
                        for item in dispositions
                    ],
                    "renders": render_values,
                },
            }
    finally:
        engine.dispose()


def write_canonical_export(
    world_id: str, worlds_dir: Path, destination: Path
) -> dict[str, Any]:
    value = export_world(world_id, worlds_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(canonical_json(value))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return value
