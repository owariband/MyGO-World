from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, ValidationError

from mygo_world.canonical import canonical_json, sha256_text

ResponseT = TypeVar("ResponseT", bound=BaseModel)


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

    @property
    def semantic_key(self) -> str:
        return f"{self.agent_type}:{self.agent_id}:{self.call_kind}"

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

    def __init__(self, responses: dict[str, FixtureResponse | dict[str, Any]]) -> None:
        self._responses = {
            key: value if isinstance(value, FixtureResponse) else FixtureResponse(value)
            for key, value in responses.items()
        }
        self.calls: list[ModelRequest] = []
        self.model_id = "fixture-model-v1"
        self.network_request_count = 0

    def generate(
        self, request: ModelRequest, response_type: type[ResponseT]
    ) -> ModelGeneration[ResponseT]:
        try:
            fixture = self._responses[request.semantic_key]
        except KeyError as exc:
            raise LookupError(
                f"No Fixture response for semantic call '{request.semantic_key}'"
            ) from exc
        if (
            fixture.expected_input_hash is not None
            and fixture.expected_input_hash != request.input_hash
        ):
            raise ValueError(
                f"Fixture input hash mismatch for '{request.semantic_key}': "
                f"expected {fixture.expected_input_hash}, got {request.input_hash}"
            )
        raw = canonical_json(fixture.body)
        self.calls.append(request)
        try:
            structured = response_type.model_validate_json(raw)
        except ValidationError as exc:
            raise ModelOutputInvalidError(request, raw, str(exc)) from exc
        return ModelGeneration(request=request, raw_response=raw, structured=structured)


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
                        f"Use runtime skill {request.skill_id}@{request.skill_version}. "
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
