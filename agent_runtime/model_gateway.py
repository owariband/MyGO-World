"""Typed structured-model boundary shared by Agent strategies."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from threading import Lock
from time import perf_counter
from typing import Annotated, Literal, Protocol

from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import Field, StringConstraints, ValidationError

from agent_runtime.model import StrictModel

NonEmptyText = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class ModelRequest(StrictModel):
    """One provider-independent structured generation request."""

    call_id: NonEmptyText
    agent_kind: Literal["character", "director", "broadcast"]
    agent_id: NonEmptyText
    call_kind: NonEmptyText
    model_id: NonEmptyText
    prompt_id: NonEmptyText
    prompt_version: NonEmptyText
    prompt_digest: NonEmptyText
    skill_id: NonEmptyText
    skill_version: NonEmptyText
    skill_content_hash: Digest
    system_prompt: NonEmptyText = Field(repr=False)
    input_json: NonEmptyText = Field(repr=False)
    repair_diagnostic: NonEmptyText | None = Field(default=None, repr=False)

    @property
    def input_hash(self) -> str:
        content = f"{self.system_prompt}\n{self.input_json}\n{self.repair_diagnostic or ''}"
        return sha256(content.encode("utf-8")).hexdigest()


class ModelCallTrace(StrictModel):
    """Non-secret provenance for one physical model call."""

    call_id: NonEmptyText
    call_kind: NonEmptyText
    model_id: NonEmptyText
    prompt_id: NonEmptyText
    prompt_version: NonEmptyText
    prompt_digest: NonEmptyText
    skill_id: NonEmptyText
    skill_version: NonEmptyText
    skill_content_hash: Digest
    input_hash: Digest
    output_hash: Digest | None = None
    status: Literal[
        "succeeded",
        "invalid_output",
        "semantic_rejected",
        "transport_failed",
    ]
    diagnostic: NonEmptyText | None = None
    latency_ms: Annotated[float, Field(ge=0.0)]
    transport_attempts: Annotated[int, Field(ge=1)]


@dataclass(frozen=True, slots=True)
class ModelGeneration[ResponseT: StrictModel]:
    """A strict structured value and its non-secret call trace."""

    structured: ResponseT
    trace: ModelCallTrace


class ModelGateway(Protocol):
    """The only structured-model seam used by Agent strategies."""

    def generate[ResponseT: StrictModel](
        self,
        request: ModelRequest,
        response_type: type[ResponseT],
        config: RunnableConfig | None = None,
    ) -> ModelGeneration[ResponseT]: ...


class ModelGatewayError(RuntimeError):
    """Base error for the model boundary."""


class ModelRequestRejectedError(ModelGatewayError):
    """The request violates the configured gateway policy."""


class ModelTransportError(ModelGatewayError):
    """All configured retryable transport attempts failed."""

    def __init__(self, diagnostic: str, trace: ModelCallTrace) -> None:
        super().__init__(diagnostic)
        self.diagnostic = diagnostic
        self.trace = trace


class ModelOutputInvalidError(ModelGatewayError):
    """The model returned a value outside the requested Pydantic contract."""

    def __init__(self, diagnostic: str, trace: ModelCallTrace) -> None:
        super().__init__(diagnostic)
        self.diagnostic = diagnostic
        self.trace = trace


class LangChainModelGateway:
    """Adapt one configured LangChain ChatModel to the typed gateway."""

    def __init__(
        self,
        model: BaseChatModel,
        *,
        model_id: str,
        max_transport_attempts: int = 3,
        retryable_error_types: tuple[type[Exception], ...] = (
            TimeoutError,
            ConnectionError,
        ),
    ) -> None:
        if not model_id.strip():
            raise ValueError("model_id cannot be empty")
        if max_transport_attempts < 1:
            raise ValueError("max_transport_attempts must be positive")
        self._model = model
        self._model_id = model_id
        self._max_transport_attempts = max_transport_attempts
        self._retryable_error_types = retryable_error_types

    def generate[ResponseT: StrictModel](
        self,
        request: ModelRequest,
        response_type: type[ResponseT],
        config: RunnableConfig | None = None,
    ) -> ModelGeneration[ResponseT]:
        """Invoke structured output, retrying only configured transport errors."""

        if request.model_id != self._model_id:
            raise ModelRequestRejectedError("model request cannot override the configured model_id")
        messages = [
            SystemMessage(content=_system_message(request)),
            HumanMessage(content=request.input_json),
        ]
        started = perf_counter()
        for attempt in range(1, self._max_transport_attempts + 1):
            try:
                runnable = self._model.with_structured_output(response_type)
                raw_result: object = runnable.invoke(messages, config=config)
                structured = response_type.model_validate(raw_result, strict=True)
            except Exception as error:
                if isinstance(error, self._retryable_error_types):
                    if attempt < self._max_transport_attempts:
                        continue
                    diagnostic = (
                        f"model transport failed after {attempt} attempts: {type(error).__name__}"
                    )
                    raise ModelTransportError(
                        diagnostic,
                        _trace(
                            request,
                            status="transport_failed",
                            output_hash=None,
                            diagnostic=diagnostic,
                            latency_ms=_elapsed_ms(started),
                            transport_attempts=attempt,
                        ),
                    ) from error
                if isinstance(error, (OutputParserException, ValidationError)):
                    diagnostic = _validation_diagnostic(error)
                    trace = _trace(
                        request,
                        status="invalid_output",
                        output_hash=None,
                        diagnostic=diagnostic,
                        latency_ms=_elapsed_ms(started),
                        transport_attempts=attempt,
                    )
                    raise ModelOutputInvalidError(diagnostic, trace) from error
                if isinstance(error, NotImplementedError):
                    raise ModelRequestRejectedError(
                        "configured ChatModel does not support structured output"
                    ) from error
                raise

            output_json = structured.model_dump_json(by_alias=True, exclude_none=False)
            return ModelGeneration(
                structured=structured,
                trace=_trace(
                    request,
                    status="succeeded",
                    output_hash=sha256(output_json.encode("utf-8")).hexdigest(),
                    latency_ms=_elapsed_ms(started),
                    transport_attempts=attempt,
                ),
            )
        raise AssertionError("unreachable model gateway state")


class FixtureModelGateway:
    """Deterministic JSON responses using the same typed boundary as providers."""

    def __init__(self, responses: tuple[str, ...], *, model_id: str = "fixture-model") -> None:
        if not responses:
            raise ValueError("fixture responses cannot be empty")
        self._responses = responses
        self._model_id = model_id
        self._index = 0
        self._lock = Lock()
        self.requests: list[ModelRequest] = []

    def generate[ResponseT: StrictModel](
        self,
        request: ModelRequest,
        response_type: type[ResponseT],
        config: RunnableConfig | None = None,
    ) -> ModelGeneration[ResponseT]:
        del config
        if request.model_id != self._model_id:
            raise ModelRequestRejectedError("model request cannot override the configured model_id")
        with self._lock:
            if self._index >= len(self._responses):
                raise ModelRequestRejectedError("fixture response sequence was exhausted")
            response = self._responses[self._index]
            self._index += 1
            self.requests.append(request)
        started = perf_counter()
        try:
            structured = response_type.model_validate_json(response, strict=True)
        except ValidationError as error:
            diagnostic = _validation_diagnostic(error)
            trace = _trace(
                request,
                status="invalid_output",
                output_hash=sha256(response.encode("utf-8")).hexdigest(),
                diagnostic=diagnostic,
                latency_ms=_elapsed_ms(started),
                transport_attempts=1,
            )
            raise ModelOutputInvalidError(diagnostic, trace) from error
        output_json = structured.model_dump_json(by_alias=True, exclude_none=False)
        return ModelGeneration(
            structured=structured,
            trace=_trace(
                request,
                status="succeeded",
                output_hash=sha256(output_json.encode("utf-8")).hexdigest(),
                latency_ms=_elapsed_ms(started),
                transport_attempts=1,
            ),
        )


def _system_message(request: ModelRequest) -> str:
    repair = (
        f"\n\nRepair the previous output using this diagnostic:\n{request.repair_diagnostic}"
        if request.repair_diagnostic is not None
        else ""
    )
    return (
        f"Runtime Skill {request.skill_id}@{request.skill_version}:\n\n"
        f"{request.system_prompt}\n\n"
        "Return exactly one value matching the requested structured contract."
        f"{repair}"
    )


def _trace(
    request: ModelRequest,
    *,
    status: Literal[
        "succeeded",
        "invalid_output",
        "semantic_rejected",
        "transport_failed",
    ],
    output_hash: str | None,
    latency_ms: float,
    transport_attempts: int,
    diagnostic: str | None = None,
) -> ModelCallTrace:
    return ModelCallTrace(
        call_id=request.call_id,
        call_kind=request.call_kind,
        model_id=request.model_id,
        prompt_id=request.prompt_id,
        prompt_version=request.prompt_version,
        prompt_digest=request.prompt_digest,
        skill_id=request.skill_id,
        skill_version=request.skill_version,
        skill_content_hash=request.skill_content_hash,
        input_hash=request.input_hash,
        output_hash=output_hash,
        status=status,
        diagnostic=diagnostic,
        latency_ms=latency_ms,
        transport_attempts=transport_attempts,
    )


def _elapsed_ms(started: float) -> float:
    return max(0.0, (perf_counter() - started) * 1000.0)


def _validation_diagnostic(error: OutputParserException | ValidationError) -> str:
    if isinstance(error, ValidationError):
        fields = tuple(
            ".".join(str(part) for part in item["loc"]) or "$"
            for item in error.errors(include_url=False, include_context=False, include_input=False)
        )
        kinds = tuple(
            str(item["type"])
            for item in error.errors(
                include_url=False,
                include_context=False,
                include_input=False,
            )
        )
        details = ", ".join(f"{field}:{kind}" for field, kind in zip(fields, kinds, strict=True))
        return f"structured output failed validation ({details})"
    return f"structured output parser rejected the response ({type(error).__name__})"
