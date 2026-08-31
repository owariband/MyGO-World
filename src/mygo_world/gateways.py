from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, ValidationError

from mygo_world.canonical import canonical_json, sha256_text
from mygo_world.errors import WorldError

ResponseT = TypeVar("ResponseT", bound=BaseModel)

_LINEAGE_SESSION_PATTERN = re.compile(r"^(session-lineage-\d+-\d+)-[0-9a-f]{16}$")


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


@runtime_checkable
class ModelGateway(Protocol):
    """The only model-call seam used by Character, Director and Broadcast."""

    def generate(
        self, request: ModelRequest, response_type: type[ResponseT]
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


class OpenAICompatibleGateway:
    """Small OpenAI-compatible JSON-schema adapter sharing the Fixture contract."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model_id: str = "provider-model",
        timeout_seconds: float = 120.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.model_id = model_id
        self._timeout_seconds = timeout_seconds
        self.network_request_count = 0

    @classmethod
    def from_environment(cls) -> OpenAICompatibleGateway:
        base_url = os.environ.get("MYGO_MODEL_BASE_URL", "").strip()
        api_key = os.environ.get("MYGO_MODEL_API_KEY", "").strip()
        model_id = os.environ.get("MYGO_MODEL_ID", "").strip()
        if not base_url or not api_key or not model_id:
            raise ValueError(
                "MYGO_MODEL_BASE_URL, MYGO_MODEL_API_KEY and MYGO_MODEL_ID are required"
            )
        return cls(base_url=base_url, api_key=api_key, model_id=model_id)

    def generate(
        self, request: ModelRequest, response_type: type[ResponseT]
    ) -> ModelGeneration[ResponseT]:
        body = {
            "model": request.model_id,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"Runtime skill {request.skill_id}@{request.skill_version}:\n\n"
                        f"{request.skill_body}\n\n"
                        "Return only a value matching the supplied JSON schema."
                    ),
                },
                {
                    "role": "user",
                    "content": canonical_json(request.input_payload),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": response_type.__name__,
                    "strict": True,
                    "schema": response_type.model_json_schema(),
                },
            },
            **request.model_config,
        }
        http_request = urllib.request.Request(
            f"{self._base_url}/chat/completions",
            data=canonical_json(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        self.network_request_count += 1
        try:
            with urllib.request.urlopen(
                http_request, timeout=self._timeout_seconds
            ) as response:
                provider_body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            # Never include request headers or the API key in diagnostics.
            error_type = (
                ModelTransportError
                if exc.code in {408, 429} or 500 <= exc.code < 600
                else ModelRequestRejectedError
            )
            raise error_type(f"Provider request failed with HTTP {exc.code}") from exc
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            # Never include request headers or the API key in diagnostics.
            raise ModelTransportError(
                f"Provider request failed: {type(exc).__name__}"
            ) from exc

        decoded = json.loads(provider_body)
        content = decoded["choices"][0]["message"]["content"]
        if isinstance(content, dict):
            raw = canonical_json(content)
        else:
            raw = str(content)
        try:
            structured = response_type.model_validate_json(raw)
        except ValidationError as exc:
            raise ModelOutputInvalidError(request, raw, str(exc)) from exc
        return ModelGeneration(request=request, raw_response=raw, structured=structured)


# A concise compatibility name used by callers that do not care which provider is used.
ProviderGateway = OpenAICompatibleGateway
