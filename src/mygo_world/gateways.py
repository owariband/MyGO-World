from __future__ import annotations

import http.client
import json
import math
import os
import re
import socket
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, ValidationError

from mygo_world.canonical import canonical_json, sha256_text
from mygo_world.errors import WorldError

ResponseT = TypeVar("ResponseT", bound=BaseModel)

_LINEAGE_SESSION_PATTERN = re.compile(r"^(session-lineage-\d+-\d+)-[0-9a-f]{16}$")
_JSON_FENCE_PATTERN = re.compile(
    r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.IGNORECASE | re.DOTALL
)
_RESERVED_MODEL_PARAMETERS = frozenset(
    {
        "authorization",
        "messages",
        "model",
        "n",
        "response_format",
        "stream",
        "tool_choice",
        "tools",
    }
)
_SECRET_PARAMETER_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "token",
)
_USAGE_FIELDS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "input_tokens",
    "output_tokens",
    "cost",
    "total_cost",
)


def normalize_fixture_input(value: Any) -> Any:
    """Remove only portable run identity from a Fixture request projection."""

    if isinstance(value, dict):
        return {
            key: "$WORLD_ID" if key == "world_id" else normalize_fixture_input(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [normalize_fixture_input(item) for item in value]
    if isinstance(value, str):
        match = _LINEAGE_SESSION_PATTERN.fullmatch(value)
        if match is not None:
            return f"{match.group(1)}-$DIGEST"
    return value


class ModelTransportError(RuntimeError):
    """A provider failure which is safe to retry without changing the prompt."""


class ModelRequestRejectedError(RuntimeError):
    """A non-retryable provider response."""


class ModelRequestCancelledError(RuntimeError):
    """A Provider request cancelled by the Runtime."""


class ModelOutputInvalidError(ValueError):
    """A response reached the model boundary but did not match its contract."""

    def __init__(
        self, request: ModelRequest, raw_response: str, diagnostic: str
    ) -> None:
        super().__init__(diagnostic)
        self.request = request
        self.raw_response = raw_response
        self.diagnostic = diagnostic


@dataclass(frozen=True)
class ModelRequest:
    agent_type: str
    agent_id: str
    call_kind: str
    model_id: str
    skill_id: str
    skill_version: str
    skill_content_hash: str
    input_payload: dict[str, Any]
    model_config: dict[str, Any]
    skill_body: str = ""

    @property
    def semantic_key(self) -> str:
        return f"{self.agent_type}:{self.agent_id}:{self.call_kind}"

    @property
    def fixture_key(self) -> str:
        """Return the versioned Fixture address for this exact model call."""

        batch_id = str(
            self.input_payload.get(
                "run_id", f"broadcast-v{self.input_payload.get('world_version', 0)}"
            )
        )
        wave_number = int(self.input_payload.get("wave_number", 0))
        return (
            f"{self.agent_type}:{self.agent_id}:{batch_id}:"
            f"{wave_number}:{self.call_kind}"
        )

    @property
    def input_hash(self) -> str:
        return sha256_text(canonical_json(self.input_payload))

    def trace_payload(self) -> dict[str, Any]:
        return {
            "agent_type": self.agent_type,
            "agent_id": self.agent_id,
            "call_kind": self.call_kind,
            "model_id": self.model_id,
            "skill_id": self.skill_id,
            "skill_version": self.skill_version,
            "skill_content_hash": self.skill_content_hash,
            "skill_body": self.skill_body,
            "input_payload": self.input_payload,
            "model_config": self.model_config,
            "input_hash": self.input_hash,
        }


@dataclass(frozen=True)
class ModelGeneration[ResponseT]:
    request: ModelRequest
    raw_response: str
    structured: ResponseT
    usage: dict[str, int | float] | None = None
    latency_ms: float | None = None
    transport_attempts: int = 1


@runtime_checkable
class ModelGateway(Protocol):
    """The only model-call seam used by Character, Director and Broadcast."""

    def generate(
        self, request: ModelRequest, response_type: type[ResponseT]
    ) -> ModelGeneration[ResponseT]: ...


@runtime_checkable
class CancellableModelGateway(Protocol):
    """Optional model-call seam for adapters that can abort active transport."""

    def generate_cancellable(
        self,
        request: ModelRequest,
        response_type: type[ResponseT],
        cancellation_event: threading.Event,
    ) -> ModelGeneration[ResponseT]: ...


@dataclass(frozen=True)
class FixtureResponse:
    body: dict[str, Any]
    expected_input_hash: str | None = None


class FixtureGateway:
    """Deterministic in-process responses; this implementation has no network code."""

    def __init__(
        self,
        responses: dict[str, FixtureResponse | dict[str, Any]],
        *,
        require_input_hashes: bool = False,
        normalize_inputs: bool = False,
    ) -> None:
        self._responses = {
            key: value if isinstance(value, FixtureResponse) else FixtureResponse(value)
            for key, value in responses.items()
        }
        self.calls: list[ModelRequest] = []
        self.matched_keys: list[str] = []
        self.model_id = "fixture-model-v1"
        self.network_request_count = 0
        self._require_input_hashes = require_input_hashes
        self._normalize_inputs = normalize_inputs

    def generate(
        self, request: ModelRequest, response_type: type[ResponseT]
    ) -> ModelGeneration[ResponseT]:
        fixture_key = request.fixture_key
        resolved_key = fixture_key
        fixture = self._responses.get(fixture_key)
        if fixture is None:
            resolved_key = request.semantic_key
            fixture = self._responses.get(request.semantic_key)
        if fixture is None:
            raise WorldError(
                "FIXTURE_RESPONSE_MISSING",
                f"No Fixture response for call '{fixture_key}'",
            )
        if self._require_input_hashes and fixture.expected_input_hash is None:
            raise WorldError(
                "FIXTURE_INPUT_HASH_MISSING",
                f"Fixture input hash is required for '{resolved_key}'",
            )
        actual_input_hash = (
            sha256_text(canonical_json(normalize_fixture_input(request.input_payload)))
            if self._normalize_inputs
            else request.input_hash
        )
        if (
            fixture.expected_input_hash is not None
            and fixture.expected_input_hash != actual_input_hash
        ):
            raise WorldError(
                "FIXTURE_INPUT_HASH_MISMATCH",
                f"Fixture input hash mismatch for '{resolved_key}': "
                f"expected {fixture.expected_input_hash}, got {actual_input_hash}",
            )
        raw = canonical_json(fixture.body)
        self.calls.append(request)
        self.matched_keys.append(resolved_key)
        try:
            structured = response_type.model_validate_json(raw)
        except ValidationError as exc:
            raise ModelOutputInvalidError(request, raw, str(exc)) from exc
        return ModelGeneration(request=request, raw_response=raw, structured=structured)

    @property
    def unused_response_keys(self) -> tuple[str, ...]:
        return tuple(sorted(set(self._responses) - set(self.matched_keys)))


@dataclass(frozen=True)
class ProviderSettings:
    """Validated process configuration for the single MVP Provider."""

    base_url: str
    api_key: str = field(repr=False)
    model_id: str
    structured_output_mode: str = "json_schema"
    model_parameters: dict[str, Any] | None = None
    timeout_seconds: float = 120.0

    @classmethod
    def from_environment(
        cls,
        *,
        env_file: Path | None = None,
        environ: dict[str, str] | None = None,
    ) -> ProviderSettings:
        process = dict(os.environ if environ is None else environ)
        requested_file = env_file
        if requested_file is None and process.get("MYGO_ENV_FILE", "").strip():
            requested_file = Path(process["MYGO_ENV_FILE"].strip())
        values = _read_env_file(requested_file) if requested_file is not None else {}
        values.update(process)

        base_url = values.get("MYGO_MODEL_BASE_URL", "").strip()
        api_key = values.get("MYGO_MODEL_API_KEY", "").strip()
        model_id = values.get("MYGO_MODEL_ID", "").strip()
        missing = [
            name
            for name, value in (
                ("MYGO_MODEL_BASE_URL", base_url),
                ("MYGO_MODEL_API_KEY", api_key),
                ("MYGO_MODEL_ID", model_id),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                f"Missing required Provider configuration: {', '.join(missing)}"
            )

        parsed_url = urllib.parse.urlsplit(base_url)
        if (
            parsed_url.scheme not in {"http", "https"}
            or not parsed_url.netloc
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise ValueError(
                "MYGO_MODEL_BASE_URL must be an HTTP(S) URL without credentials, query, or fragment"
            )

        mode = values.get("MYGO_MODEL_STRUCTURED_OUTPUT_MODE", "json_schema").strip()
        if mode not in {"json_schema", "json_text"}:
            raise ValueError(
                "MYGO_MODEL_STRUCTURED_OUTPUT_MODE must be 'json_schema' or 'json_text'"
            )

        parameters_text = values.get("MYGO_MODEL_PARAMETERS_JSON", "{}").strip() or "{}"
        try:
            parameters = json.loads(
                parameters_text,
                parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(
                "MYGO_MODEL_PARAMETERS_JSON must be a finite JSON object"
            ) from exc
        if not isinstance(parameters, dict):
            raise ValueError(  # noqa: TRY004 - all configuration errors share one API
                "MYGO_MODEL_PARAMETERS_JSON must be a JSON object"
            )
        for key in parameters:
            normalized = str(key).lower()
            if not isinstance(key, str) or not key or key in _RESERVED_MODEL_PARAMETERS:
                raise ValueError(
                    f"MYGO_MODEL_PARAMETERS_JSON contains reserved or invalid field '{key}'"
                )
            if any(part in normalized for part in _SECRET_PARAMETER_PARTS):
                raise ValueError(
                    "MYGO_MODEL_PARAMETERS_JSON must not contain credential fields"
                )
        if not _contains_only_finite_json(parameters):
            raise ValueError(
                "MYGO_MODEL_PARAMETERS_JSON must contain only finite JSON values"
            )

        timeout_text = values.get("MYGO_MODEL_TIMEOUT_SECONDS", "120").strip()
        try:
            timeout_seconds = float(timeout_text)
        except ValueError as exc:
            raise ValueError(
                "MYGO_MODEL_TIMEOUT_SECONDS must be a positive number"
            ) from exc
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("MYGO_MODEL_TIMEOUT_SECONDS must be a positive number")

        return cls(
            base_url=base_url.rstrip("/"),
            api_key=api_key,
            model_id=model_id,
            structured_output_mode=mode,
            model_parameters=parameters,
            timeout_seconds=timeout_seconds,
        )


def _read_env_file(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"Cannot read MYGO_ENV_FILE '{path}'") from exc
    values: dict[str, str] = {}
    for line_number, source in enumerate(lines, start=1):
        line = source.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f"MYGO_ENV_FILE has invalid syntax at line {line_number}")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f"MYGO_ENV_FILE has invalid syntax at line {line_number}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _contains_only_finite_json(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_contains_only_finite_json(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _contains_only_finite_json(item)
            for key, item in value.items()
        )
    return False


def _single_json_value(text: str) -> str:
    match = _JSON_FENCE_PATTERN.fullmatch(text)
    candidate = match.group(1) if match is not None else text.strip()
    decoder = json.JSONDecoder()
    try:
        value, end = decoder.raw_decode(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError("Provider content is not valid JSON") from exc
    if candidate[end:].strip():
        raise ValueError("Provider content contains more than one JSON value")
    return canonical_json(value)


class OpenAICompatibleGateway:
    """Small OpenAI-compatible JSON-schema adapter sharing the Fixture contract."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model_id: str = "provider-model",
        structured_output_mode: str = "json_schema",
        model_parameters: dict[str, Any] | None = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        settings = ProviderSettings(
            base_url=base_url.rstrip("/"),
            api_key=api_key,
            model_id=model_id,
            structured_output_mode=structured_output_mode,
            model_parameters=dict(model_parameters or {}),
            timeout_seconds=timeout_seconds,
        )
        # Reuse the environment validator without ever serializing credentials.
        validated = ProviderSettings.from_environment(
            environ={
                "MYGO_MODEL_BASE_URL": settings.base_url,
                "MYGO_MODEL_API_KEY": settings.api_key,
                "MYGO_MODEL_ID": settings.model_id,
                "MYGO_MODEL_STRUCTURED_OUTPUT_MODE": settings.structured_output_mode,
                "MYGO_MODEL_PARAMETERS_JSON": canonical_json(settings.model_parameters),
                "MYGO_MODEL_TIMEOUT_SECONDS": str(settings.timeout_seconds),
            }
        )
        self._base_url = validated.base_url
        self._api_key = validated.api_key
        self.model_id = validated.model_id
        self.structured_output_mode = validated.structured_output_mode
        self.model_parameters = dict(validated.model_parameters or {})
        self._timeout_seconds = validated.timeout_seconds
        self.network_request_count = 0
        self._counter_lock = threading.Lock()

    def assert_no_credentials(self, value: str) -> None:
        if self._api_key and self._api_key in value:
            raise ValueError("A Provider credential reached an output boundary")

    def _redact_credentials(self, value: str) -> str:
        return value.replace(self._api_key, "[REDACTED]") if self._api_key else value

    @classmethod
    def from_environment(
        cls,
        *,
        env_file: Path | None = None,
        environ: dict[str, str] | None = None,
    ) -> OpenAICompatibleGateway:
        settings = ProviderSettings.from_environment(env_file=env_file, environ=environ)
        return cls(
            base_url=settings.base_url,
            api_key=settings.api_key,
            model_id=settings.model_id,
            structured_output_mode=settings.structured_output_mode,
            model_parameters=settings.model_parameters,
            timeout_seconds=settings.timeout_seconds,
        )

    def generate(
        self, request: ModelRequest, response_type: type[ResponseT]
    ) -> ModelGeneration[ResponseT]:
        return self.generate_cancellable(request, response_type, threading.Event())

    def generate_cancellable(
        self,
        request: ModelRequest,
        response_type: type[ResponseT],
        cancellation_event: threading.Event,
    ) -> ModelGeneration[ResponseT]:
        """Run one request while allowing Runtime cancellation to close its socket."""

        if cancellation_event.is_set():
            raise ModelRequestCancelledError("Provider request was cancelled")
        if request.model_id != self.model_id:
            raise ModelRequestRejectedError(
                "ModelRequest must use the globally configured Provider model"
            )
        if request.model_config != self.model_parameters:
            raise ModelRequestRejectedError(
                "ModelRequest must use the globally configured model parameters"
            )

        schema_instruction = ""
        if self.structured_output_mode == "json_text":
            schema_instruction = (
                "\n\nRequired JSON Schema:\n"
                f"{canonical_json(response_type.model_json_schema())}"
            )
        body: dict[str, Any] = {
            **request.model_config,
            "model": request.model_id,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"Runtime skill {request.skill_id}@{request.skill_version}:\n\n"
                        f"{request.skill_body}\n\n"
                        "Return exactly one JSON value matching the requested contract."
                        f"{schema_instruction}"
                    ),
                },
                {"role": "user", "content": canonical_json(request.input_payload)},
            ],
        }
        if self.structured_output_mode == "json_schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": response_type.__name__,
                    "strict": True,
                    "schema": response_type.model_json_schema(),
                },
            }

        parsed = urllib.parse.urlsplit(self._base_url)
        connection_type = (
            http.client.HTTPSConnection
            if parsed.scheme == "https"
            else http.client.HTTPConnection
        )
        connection = connection_type(
            parsed.hostname,
            parsed.port,
            timeout=self._timeout_seconds,
        )
        done = threading.Event()

        def close_on_cancel() -> None:
            while not done.wait(0.02):
                if not cancellation_event.is_set():
                    continue
                active_socket = connection.sock
                if active_socket is None:
                    continue
                try:
                    active_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                connection.close()
                return

        watcher = threading.Thread(
            target=close_on_cancel,
            name="mygo-provider-cancellation",
            daemon=True,
        )
        watcher.start()
        with self._counter_lock:
            self.network_request_count += 1
        started = time.perf_counter()
        try:
            base_path = parsed.path.rstrip("/")
            connection.request(
                "POST",
                f"{base_path}/chat/completions",
                body=canonical_json(body).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
            )
            with connection.getresponse() as response:
                if response.status >= 400:
                    error_type = (
                        ModelTransportError
                        if response.status in {408, 429} or response.status >= 500
                        else ModelRequestRejectedError
                    )
                    raise error_type(
                        f"Provider request failed with HTTP {response.status}"
                    )
                provider_body = response.read().decode("utf-8")
        except (OSError, http.client.HTTPException) as exc:
            if cancellation_event.is_set():
                raise ModelRequestCancelledError(
                    "Provider request was cancelled"
                ) from exc
            raise ModelTransportError(
                f"Provider request failed: {type(exc).__name__}"
            ) from exc
        finally:
            done.set()
            connection.close()
            watcher.join(timeout=0.1)

        if cancellation_event.is_set():
            raise ModelRequestCancelledError("Provider request was cancelled")
        latency_ms = (time.perf_counter() - started) * 1000
        try:
            decoded = json.loads(provider_body)
            content = decoded["choices"][0]["message"]["content"]
            if isinstance(content, (dict, list)):
                raw = canonical_json(content)
            elif isinstance(content, str):
                raw = (
                    content
                    if self.structured_output_mode == "json_schema"
                    else _single_json_value(content)
                )
            else:
                raise TypeError("Provider content has an unsupported type")
        except (
            KeyError,
            IndexError,
            TypeError,
            json.JSONDecodeError,
            ValueError,
        ) as exc:
            raise ModelOutputInvalidError(
                request,
                self._redact_credentials(provider_body),
                "Provider response did not contain one valid JSON value",
            ) from exc
        raw = self._redact_credentials(raw)
        try:
            structured = response_type.model_validate_json(raw)
        except ValidationError as exc:
            raise ModelOutputInvalidError(request, raw, str(exc)) from exc
        usage_source = decoded.get("usage")
        usage = {
            key: value
            for key in _USAGE_FIELDS
            if isinstance(usage_source, dict)
            and isinstance((value := usage_source.get(key)), (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        } or None
        return ModelGeneration(
            request=request,
            raw_response=raw,
            structured=structured,
            usage=usage,
            latency_ms=latency_ms,
        )


# A concise compatibility name used by callers that do not care which provider is used.
ProviderGateway = OpenAICompatibleGateway
