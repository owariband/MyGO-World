from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class WorldRow(Base):
    __tablename__ = "worlds"

    world_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    seed_id: Mapped[str] = mapped_column(String(200), nullable=False)
    seed_version: Mapped[str] = mapped_column(String(100), nullable=False)
    seed_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False)
    calendar_anchor: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class WorldSegmentRow(Base):
    __tablename__ = "world_segments"

    segment_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    segment_order: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    world_version: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    segment_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_trace_id: Mapped[str | None] = mapped_column(String(36))
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    committed_at: Mapped[str] = mapped_column(String(40), nullable=False)


class WorldVersionRow(Base):
    __tablename__ = "world_versions"

    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    segment_id: Mapped[str] = mapped_column(
        ForeignKey("world_segments.segment_id"), nullable=False, unique=True
    )
    world_time_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class EntityRevisionRow(Base):
    __tablename__ = "entity_revisions"
    __table_args__ = (
        UniqueConstraint("entity_id", "revision_order"),
        UniqueConstraint("entity_id", "world_version"),
    )

    entity_revision_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    entity_id: Mapped[str] = mapped_column(String(200), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    revision_order: Mapped[int] = mapped_column(Integer, nullable=False)
    world_version: Mapped[int] = mapped_column(
        ForeignKey("world_versions.version"), nullable=False
    )
    segment_id: Mapped[str] = mapped_column(
        ForeignKey("world_segments.segment_id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    location_id: Mapped[str | None] = mapped_column(String(200))
    scope_key: Mapped[str | None] = mapped_column(String(200))
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class WorldEventRow(Base):
    __tablename__ = "world_events"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_order: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    world_version: Mapped[int] = mapped_column(
        ForeignKey("world_versions.version"), nullable=False
    )
    segment_id: Mapped[str] = mapped_column(
        ForeignKey("world_segments.segment_id"), nullable=False
    )
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("event_sessions.session_id")
    )
    start_time_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_time_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class SnapshotRow(Base):
    __tablename__ = "snapshots"

    world_version: Mapped[int] = mapped_column(
        ForeignKey("world_versions.version"), primary_key=True
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class EventSessionRow(Base):
    __tablename__ = "event_sessions"

    session_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    location_id: Mapped[str] = mapped_column(String(200), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(200), nullable=False)
    created_world_version: Mapped[int] = mapped_column(
        ForeignKey("world_versions.version"), nullable=False
    )
    closed_world_version: Mapped[int | None] = mapped_column(
        ForeignKey("world_versions.version")
    )
    closure_reason: Mapped[str | None] = mapped_column(String(32))


class EventSessionMemberRow(Base):
    __tablename__ = "event_session_members"

    session_id: Mapped[str] = mapped_column(
        ForeignKey("event_sessions.session_id"), primary_key=True
    )
    agent_id: Mapped[str] = mapped_column(String(200), primary_key=True)


class EventSessionParentRow(Base):
    __tablename__ = "event_session_parents"

    session_id: Mapped[str] = mapped_column(
        ForeignKey("event_sessions.session_id"), primary_key=True
    )
    parent_session_id: Mapped[str] = mapped_column(
        ForeignKey("event_sessions.session_id"), primary_key=True
    )


class EventSessionPendingResponseRow(Base):
    __tablename__ = "event_session_pending_responses"

    session_id: Mapped[str] = mapped_column(
        ForeignKey("event_sessions.session_id"), primary_key=True
    )
    responder_id: Mapped[str] = mapped_column(String(200), primary_key=True)


class RunnableSessionQueueRow(Base):
    __tablename__ = "runnable_session_queue"

    queue_order: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("event_sessions.session_id"), nullable=False, unique=True
    )
    enqueued_world_version: Mapped[int] = mapped_column(
        ForeignKey("world_versions.version"), nullable=False
    )
    dequeued_world_version: Mapped[int | None] = mapped_column(
        ForeignKey("world_versions.version")
    )


class AgentMemoryRow(Base):
    __tablename__ = "agent_memory_records"

    memory_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    namespace: Mapped[str] = mapped_column(String(100), nullable=False)
    memory_type: Mapped[str] = mapped_column(String(32), nullable=False)
    world_version: Mapped[int] = mapped_column(
        ForeignKey("world_versions.version"), nullable=False
    )
    relative_time_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    importance: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str | None] = mapped_column(String(32))
    supersedes_memory_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_memory_records.memory_id"), unique=True
    )
    entity_tags_json: Mapped[str] = mapped_column(Text, nullable=False)
    location_tags_json: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class SkillBindingRow(Base):
    __tablename__ = "skill_bindings"
    __table_args__ = (UniqueConstraint("world_id", "binding_order"),)

    binding_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    binding_order: Mapped[int] = mapped_column(Integer, nullable=False)
    world_id: Mapped[str] = mapped_column(
        ForeignKey("worlds.world_id"), nullable=False, index=True
    )
    agent_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(200), nullable=False)
    previous_skill_id: Mapped[str | None] = mapped_column(String(200))
    previous_skill_version: Mapped[str | None] = mapped_column(String(100))
    previous_skill_content_hash: Mapped[str | None] = mapped_column(String(64))
    skill_id: Mapped[str] = mapped_column(String(200), nullable=False)
    skill_version: Mapped[str] = mapped_column(String(100), nullable=False)
    skill_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    operator: Mapped[str] = mapped_column(String(200), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    bound_at: Mapped[str] = mapped_column(String(40), nullable=False)


class GenerationTraceRow(Base):
    __tablename__ = "generation_traces"

    trace_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    world_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    input_world_version: Mapped[int] = mapped_column(Integer, nullable=False)
    session_id: Mapped[str] = mapped_column(String(200), nullable=False)
    agent_type: Mapped[str] = mapped_column(String(32), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(200), nullable=False)
    call_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    skill_id: Mapped[str] = mapped_column(String(200), nullable=False)
    skill_version: Mapped[str] = mapped_column(String(100), nullable=False)
    skill_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    model_config_json: Mapped[str] = mapped_column(Text, nullable=False)
    request_json: Mapped[str] = mapped_column(Text, nullable=False)
    raw_response: Mapped[str] = mapped_column(Text, nullable=False)
    structured_result_json: Mapped[str] = mapped_column(Text, nullable=False)
    validation_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class BroadcastRunRow(Base):
    __tablename__ = "broadcast_runs"

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    world_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_world_version: Mapped[int] = mapped_column(
        ForeignKey("world_versions.version"), nullable=False
    )
    source_trace_id: Mapped[str] = mapped_column(
        ForeignKey("generation_traces.trace_id"), nullable=False
    )
    plan_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class RenderRow(Base):
    __tablename__ = "renders"
    __table_args__ = (UniqueConstraint("world_id", "render_id"),)

    render_record_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    world_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    render_id: Mapped[str] = mapped_column(String(200), nullable=False)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("broadcast_runs.run_id"), nullable=False
    )
    target_world_version: Mapped[int] = mapped_column(
        ForeignKey("world_versions.version"), nullable=False
    )
    render_order: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_path: Mapped[str] = mapped_column(Text, nullable=False)
    scene_path: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class BroadcastDispositionRow(Base):
    __tablename__ = "broadcast_dispositions"

    event_id: Mapped[str] = mapped_column(
        ForeignKey("world_events.event_id"), primary_key=True
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("broadcast_runs.run_id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    render_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class GenerationBatchRow(Base):
    __tablename__ = "generation_batches"

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    world_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    session_id: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    start_world_version: Mapped[int] = mapped_column(Integer, nullable=False)
    end_world_version: Mapped[int] = mapped_column(Integer, nullable=False)
    wave_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warnings_json: Mapped[str] = mapped_column(Text, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class GenerationWaveRow(Base):
    __tablename__ = "generation_waves"
    __table_args__ = (UniqueConstraint("run_id", "wave_number"),)

    wave_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("generation_batches.run_id"), nullable=False, index=True
    )
    wave_number: Mapped[int] = mapped_column(Integer, nullable=False)
    session_id: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    start_world_version: Mapped[int] = mapped_column(Integer, nullable=False)
    end_world_version: Mapped[int] = mapped_column(Integer, nullable=False)
    world_time_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)
