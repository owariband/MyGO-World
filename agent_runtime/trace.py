"""Local, opt-in execution logs; never world facts or recovery checkpoints."""

from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import AwareDatetime, Field, JsonValue

from agent_runtime.model import StrictModel
from agent_runtime.world.contracts import WorldRef

logger = logging.getLogger(__name__)
AgentKind = Literal["character", "director", "broadcast"]


class TraceRecord(StrictModel):
    seq: Annotated[int, Field(ge=1)]
    recorded_at: AwareDatetime
    trace_id: str
    world_ref: WorldRef
    agent_kind: AgentKind
    agent_id: str
    event: str
    data: dict[str, JsonValue]


class LocalTrace:
    """One exclusively created JSONL file for one World in one process.

    Debug explicitly opts into private content and console output. Each record
    is flushed, but not fsynced. An I/O failure warns and disables this writer;
    it must not turn an already-published private decision into a failed call.
    """

    def __init__(self, projects_root: Path, world_ref: WorldRef, *, debug: bool = False) -> None:
        self._world_ref = WorldRef.model_validate(world_ref, strict=True)
        self.debug = debug
        self._lock = Lock()
        self._seq = 0
        self._failed = False
        # Keep canonical project slugs readable. Other IDs use a disjoint hex
        # namespace, preserving identity even on case-insensitive filesystems.
        components = [
            value if re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value) else f"~{value.encode().hex()}"
            for value in (world_ref.project_id, world_ref.world_id)
        ]
        directory = projects_root.resolve() / components[0] / ".runtime" / "traces" / components[1]
        if directory.resolve() != directory:
            raise ValueError("trace directories cannot traverse symlinks")
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"{uuid4().hex}.jsonl"
        self._stream = self.path.open("x", encoding="utf-8")
        self.path.chmod(0o600)

    @property
    def world_ref(self) -> WorldRef:
        return self._world_ref

    def __enter__(self) -> LocalTrace:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            try:
                self._stream.close()
            except OSError as error:
                logger.warning("Local trace close failed (%s)", type(error).__name__)

    def write(
        self,
        event: str,
        data: dict[str, object],
        *,
        trace_id: str,
        agent_kind: AgentKind,
        agent_id: str,
    ) -> None:
        with self._lock:
            if self._failed:
                return
            try:
                record = TraceRecord.model_validate_json(
                    json.dumps(
                        {
                            "seq": self._seq + 1,
                            "recordedAt": datetime.now(UTC).isoformat(),
                            "traceId": trace_id,
                            "worldRef": self._world_ref.model_dump(mode="json"),
                            "agentKind": agent_kind,
                            "agentId": agent_id,
                            "event": event,
                            "data": data,
                        },
                        allow_nan=False,
                    ),
                    strict=True,
                )
                line = record.model_dump_json() + "\n"
            except (TypeError, ValueError) as error:
                logger.warning("Local trace record skipped (%s)", type(error).__name__)
                return
            try:
                self._stream.write(line)
                self._stream.flush()
                self._seq += 1
                if self.debug:
                    sys.stderr.write(line)
            except (OSError, ValueError) as error:
                self._failed = True
                logger.warning("Local trace writer disabled (%s)", type(error).__name__)


@dataclass(frozen=True, slots=True)
class _TraceContext:
    log: LocalTrace
    trace_id: str
    agent_kind: AgentKind
    agent_id: str


_current_trace: ContextVar[_TraceContext | None] = ContextVar("local_agent_trace", default=None)


def trace_debug_enabled() -> bool:
    """Avoid building private debug payloads when there is no debug trace."""

    context = _current_trace.get()
    return context is not None and context.log.debug


@contextmanager
def trace_scope(
    log: LocalTrace | None,
    *,
    world_ref: WorldRef,
    agent_kind: AgentKind,
    agent_id: str,
) -> Generator[None, None, None]:
    """Bind trusted identity for a decision, including rejection and interruption."""

    if log is not None and log.world_ref != world_ref:
        raise ValueError("trace log belongs to a different WorldRef")
    context = None if log is None else _TraceContext(log, uuid4().hex, agent_kind, agent_id)
    token = _current_trace.set(context)
    started = perf_counter()
    try:
        record_trace("decision.start")
        yield
    except BaseException as error:
        record_trace(
            "decision.error",
            {"errorType": type(error).__name__, "latencyMs": (perf_counter() - started) * 1000},
        )
        raise
    else:
        record_trace("decision.end", {"latencyMs": (perf_counter() - started) * 1000})
    finally:
        _current_trace.reset(token)


def record_trace(
    event: str,
    data: dict[str, object] | None = None,
    *,
    content: object | None = None,
    world_ref: WorldRef | None = None,
    agent_id: str | None = None,
) -> None:
    """Append a selected payload; never serialize arbitrary SDK objects or errors."""

    context = _current_trace.get()
    if context is None:
        return
    if (world_ref is not None and world_ref != context.log.world_ref) or (
        agent_id is not None and agent_id != context.agent_id
    ):
        logger.warning("Local trace identity mismatch; record skipped")
        return
    payload = dict(data or {})
    if context.log.debug and content is not None:
        payload["content"] = content
    context.log.write(
        event,
        payload,
        trace_id=context.trace_id,
        agent_kind=context.agent_kind,
        agent_id=context.agent_id,
    )
