from __future__ import annotations

import os
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import wraps
from typing import TYPE_CHECKING, Any, TypeVar

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SpanExporter,
)

if TYPE_CHECKING:
    from mygo_world.gateways import ModelGeneration, ModelRequest

ResultT = TypeVar("ResultT")

_TRACER = trace.get_tracer("mygo_world")
_CONFIGURATION_LOCK = threading.Lock()
_CONFIGURED = False


def configure_telemetry(exporter: SpanExporter | None = None) -> None:
    """Configure an optional exporter; no configuration leaves tracing as a no-op."""

    global _CONFIGURED
    if exporter is None and os.environ.get("MYGO_OTEL_EXPORTER") != "console":
        return
    with _CONFIGURATION_LOCK:
        if _CONFIGURED:
            return
        provider = TracerProvider()
        provider.add_span_processor(
            BatchSpanProcessor(exporter or ConsoleSpanExporter())
        )
        trace.set_tracer_provider(provider)
        _CONFIGURED = True


def _safe_attributes(values: dict[str, Any]) -> dict[str, Any]:
    allowed: dict[str, Any] = {}
    for key, value in values.items():
        if value is None:
            continue
        if isinstance(value, (str, bool, int, float)):
            allowed[key] = value
    return allowed


@contextmanager
def operation_span(name: str, attributes: dict[str, Any]) -> Iterator[Any]:
    configure_telemetry()
    with _TRACER.start_as_current_span(
        name,
        attributes=_safe_attributes(attributes),
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        try:
            yield span
        except BaseException as exc:
            span.set_attribute("mygo.result", "error")
            span.set_attribute("mygo.error.type", type(exc).__name__)
            raise


@contextmanager
def model_call_span(request: ModelRequest, attempt: int) -> Iterator[Any]:
    payload = request.input_payload
    with operation_span(
        f"mygo.model.{request.agent_type}",
        {
            "mygo.agent.kind": request.agent_type,
            "mygo.agent.id": request.agent_id,
            "mygo.call.kind": request.call_kind,
            "mygo.model.id": request.model_id,
            "mygo.attempt": attempt,
            "mygo.run.id": payload.get("run_id"),
            "mygo.wave.number": payload.get("wave_number"),
            "mygo.world.version": payload.get("world_version"),
        },
    ) as span:
        yield span


def record_generation(span: Any, generation: ModelGeneration[Any]) -> None:
    span.set_attribute("mygo.result", "ok")
    if generation.latency_ms is not None:
        span.set_attribute("mygo.latency_ms", generation.latency_ms)
    for key, value in (generation.usage or {}).items():
        span.set_attribute(f"gen_ai.usage.{key}", value)


def traced_operation(
    name: str,
) -> Callable[[Callable[..., ResultT]], Callable[..., ResultT]]:
    def decorate(function: Callable[..., ResultT]) -> Callable[..., ResultT]:
        @wraps(function)
        def wrapped(*args: Any, **kwargs: Any) -> ResultT:
            world_id = args[0] if args else kwargs.get("world_id")
            with operation_span(name, {"mygo.world.id": world_id}) as span:
                result = function(*args, **kwargs)
                if isinstance(result, dict):
                    span.set_attribute("mygo.result", str(result.get("status", "ok")))
                    for key in (
                        "run_id",
                        "broadcast_run_id",
                        "wave_count",
                        "model_call_count",
                    ):
                        value = result.get(key)
                        if isinstance(value, (str, int)):
                            span.set_attribute(f"mygo.{key.replace('_', '.')}", value)
                return result

        return wrapped

    return decorate


_ACTIVE_WAVE_SPANS: dict[str, tuple[Any, Any]] = {}
_ACTIVE_WAVE_LOCK = threading.Lock()


def start_wave_span(
    wave_id: str,
    *,
    run_id: str,
    session_id: str,
    wave_number: int,
    world_version: int,
) -> None:
    manager = operation_span(
        "mygo.generation_wave",
        {
            "mygo.run.id": run_id,
            "mygo.session.id": session_id,
            "mygo.wave.number": wave_number,
            "mygo.world.version": world_version,
        },
    )
    span = manager.__enter__()
    with _ACTIVE_WAVE_LOCK:
        _ACTIVE_WAVE_SPANS[wave_id] = (manager, span)


def finish_wave_span(wave_id: str, *, result: str) -> None:
    with _ACTIVE_WAVE_LOCK:
        active = _ACTIVE_WAVE_SPANS.pop(wave_id, None)
    if active is None:
        return
    manager, span = active
    span.set_attribute("mygo.result", result)
    manager.__exit__(None, None, None)
