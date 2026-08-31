from __future__ import annotations

import http.client
import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from mygo_world.contracts import ActionProposal
from mygo_world.errors import WorldError
from mygo_world.gateways import (
    ModelOutputInvalidError,
    ModelRequest,
    ModelRequestCancelledError,
    ModelRequestRejectedError,
    ModelTransportError,
    OpenAICompatibleGateway,
    ProviderSettings,
)
from mygo_world.live_demo import run_live_demo


def _proposal() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "proposal_id": "proposal-1",
        "world_version": 1,
        "session_id": "session-1",
        "actor_id": "character-anon",
        "intent_summary": "Checks the set list.",
        "action": {
            "kind": "interact",
            "target_id": "object-set-list",
            "description": "Checks the first song.",
        },
        "memory_changes": [],
    }


def _request(*, model_config: dict[str, Any] | None = None) -> ModelRequest:
    return ModelRequest(
        agent_type="character",
        agent_id="character-anon",
        call_kind="action_proposal",
        model_id="test-model",
        skill_id="test.skill",
        skill_version="1",
        skill_content_hash="a" * 64,
        input_payload={"world_version": 1},
        model_config=model_config or {"temperature": 0.2},
        skill_body="Return a valid proposal.",
    )


@contextmanager
def _server(
    *, status: int = 200, body: dict[str, Any] | bytes | None = None
) -> Iterator[tuple[str, list[dict[str, Any]], list[str]]]:
    requests: list[dict[str, Any]] = []
    authorizations: list[str] = []
    payload = body or {
        "choices": [{"message": {"content": json.dumps(_proposal())}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["Content-Length"])
            requests.append(json.loads(self.rfile.read(length)))
            authorizations.append(self.headers.get("Authorization", ""))
            encoded = (
                payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            )
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, _format: str, *_args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests, authorizations
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize("mode", ["json_schema", "json_text"])
def test_provider_modes_validate_response_and_request_shape(mode: str) -> None:
    content: str = json.dumps(_proposal())
    if mode == "json_text":
        content = f"```json\n{content}\n```"
    with _server(body={"choices": [{"message": {"content": content}}]}) as (
        base_url,
        requests,
        authorizations,
    ):
        gateway = OpenAICompatibleGateway(
            base_url=base_url,
            api_key="super-secret",
            model_id="test-model",
            structured_output_mode=mode,
            model_parameters={"temperature": 0.2},
        )
        generation = gateway.generate(_request(), ActionProposal)

    assert generation.structured.proposal_id == "proposal-1"
    assert authorizations == ["Bearer super-secret"]
    assert requests[0]["model"] == "test-model"
    assert requests[0]["temperature"] == 0.2
    if mode == "json_schema":
        assert requests[0]["response_format"]["json_schema"]["schema"] == (
            ActionProposal.model_json_schema()
        )
    else:
        assert "response_format" not in requests[0]
        assert "Required JSON Schema" in requests[0]["messages"][0]["content"]


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (400, ModelRequestRejectedError),
        (401, ModelRequestRejectedError),
        (429, ModelTransportError),
        (500, ModelTransportError),
        (503, ModelTransportError),
    ],
)
def test_provider_classifies_http_errors_without_disclosing_secret(
    status: int, error_type: type[Exception]
) -> None:
    with _server(status=status, body=b'{"error":"server body secret"}') as (
        base_url,
        _requests,
        _authorizations,
    ):
        gateway = OpenAICompatibleGateway(
            base_url=base_url,
            api_key="super-secret",
            model_id="test-model",
            model_parameters={"temperature": 0.2},
        )
        with pytest.raises(error_type) as caught:
            gateway.generate(_request(), ActionProposal)
    assert "super-secret" not in str(caught.value)
    assert "server body secret" not in str(caught.value)


@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        b'{"choices":[]}',
        json.dumps({"choices": [{"message": {"content": "{} {}"}}]}).encode(),
        json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode(),
    ],
)
def test_provider_rejects_malformed_responses(body: bytes) -> None:
    with _server(body=body) as (base_url, _requests, _authorizations):
        gateway = OpenAICompatibleGateway(
            base_url=base_url,
            api_key="super-secret",
            model_id="test-model",
            structured_output_mode="json_text",
            model_parameters={"temperature": 0.2},
        )
        with pytest.raises(ModelOutputInvalidError) as caught:
            gateway.generate(_request(), ActionProposal)
    assert "super-secret" not in str(caught.value)


def test_provider_timeout_is_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    def timeout(*_args: Any, **_kwargs: Any) -> Any:
        raise TimeoutError("super-secret")

    monkeypatch.setattr(http.client.HTTPSConnection, "request", timeout)
    gateway = OpenAICompatibleGateway(
        base_url="https://example.invalid/v1",
        api_key="super-secret",
        model_id="test-model",
        model_parameters={"temperature": 0.2},
    )
    with pytest.raises(ModelTransportError) as caught:
        gateway.generate(_request(), ActionProposal)
    assert str(caught.value) == "Provider request failed: TimeoutError"


def test_provider_cancels_an_in_flight_request() -> None:
    request_started = threading.Event()
    release_response = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["Content-Length"])
            self.rfile.read(length)
            request_started.set()
            release_response.wait(timeout=5)

        def log_message(self, _format: str, *_args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    cancel = threading.Event()
    gateway = OpenAICompatibleGateway(
        base_url=f"http://127.0.0.1:{server.server_port}/v1",
        api_key="super-secret",
        model_id="test-model",
        model_parameters={"temperature": 0.2},
        timeout_seconds=120,
    )
    outcome: list[BaseException] = []

    def call() -> None:
        try:
            gateway.generate_cancellable(_request(), ActionProposal, cancel)
        except ModelRequestCancelledError as exc:
            outcome.append(exc)

    worker = threading.Thread(target=call)
    worker.start()
    try:
        assert request_started.wait(timeout=2)
        started = time.monotonic()
        cancel.set()
        worker.join(timeout=1)
        elapsed = time.monotonic() - started
        assert not worker.is_alive(), "cancelled Provider request remained blocked"
        assert elapsed < 1
        assert isinstance(outcome[0], ModelRequestCancelledError)
    finally:
        release_response.set()
        worker.join(timeout=5)
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_provider_rejects_per_request_model_override_before_network() -> None:
    gateway = OpenAICompatibleGateway(
        base_url="https://example.invalid/v1",
        api_key="super-secret",
        model_id="global-model",
        model_parameters={"temperature": 0.2},
    )
    with pytest.raises(ModelRequestRejectedError):
        gateway.generate(_request(), ActionProposal)
    assert gateway.network_request_count == 0


def test_environment_file_is_explicit_and_process_values_override(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env.local"
    env_file.write_text(
        """MYGO_MODEL_BASE_URL=https://file.example/v1
MYGO_MODEL_API_KEY=file-secret
MYGO_MODEL_ID=file-model
MYGO_MODEL_STRUCTURED_OUTPUT_MODE=json_text
MYGO_MODEL_PARAMETERS_JSON={"temperature":0.7}""",
        encoding="utf-8",
    )
    settings = ProviderSettings.from_environment(
        env_file=env_file,
        environ={"MYGO_MODEL_ID": "process-model"},
    )
    assert settings.model_id == "process-model"
    assert settings.api_key == "file-secret"
    assert settings.structured_output_mode == "json_text"
    assert settings.model_parameters == {"temperature": 0.7}


@pytest.mark.parametrize(
    "parameters",
    [
        "[]",
        '{"model":"other"}',
        '{"messages":[]}',
        '{"api_key":"secret"}',
        '{"temperature":NaN}',
    ],
)
def test_provider_rejects_invalid_or_reserved_model_parameters(
    parameters: str,
) -> None:
    with pytest.raises(ValueError):
        ProviderSettings.from_environment(
            environ={
                "MYGO_MODEL_BASE_URL": "https://example.invalid/v1",
                "MYGO_MODEL_API_KEY": "secret",
                "MYGO_MODEL_ID": "model",
                "MYGO_MODEL_PARAMETERS_JSON": parameters,
            }
        )


def test_live_preflight_missing_config_creates_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "MYGO_MODEL_BASE_URL",
        "MYGO_MODEL_API_KEY",
        "MYGO_MODEL_ID",
        "MYGO_ENV_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    output_dir = tmp_path / "live-output"
    webgal_root = tmp_path / "webgal"
    webgal_root.mkdir()

    with pytest.raises(WorldError) as caught:
        run_live_demo(
            "preflight-world",
            output_dir=output_dir,
            webgal_root=webgal_root,
        )

    assert caught.value.code == "LIVE_CONFIGURATION_INVALID"
    assert not output_dir.exists()
