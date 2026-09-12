"""Configure the existing model gateway with OpenAI-compatible providers."""

from __future__ import annotations

import os
from typing import cast

import httpx
from langchain_openai import ChatOpenAI
from openai import (
    APIConnectionError,
    APIStatusError,
    AsyncOpenAI,
    InternalServerError,
    OpenAI,
    RateLimitError,
)
from pydantic import SecretStr

from agent_runtime.model_gateway import LangChainModelGateway, ModelRequestRejectedError

DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"


def create_deepseek_gateway(
    *,
    model_id: str = DEFAULT_DEEPSEEK_MODEL,
    api_key: str | None = None,
    http_client: httpx.Client | None = None,
) -> LangChainModelGateway:
    """Build a client without making a request; never fall back to OPENAI_API_KEY.

    The injected HTTP client is caller-owned and permits offline wire-level tests.
    At most three transport attempts; HTTP phase timeouts are 60s, not a wall-clock deadline.
    """

    key = api_key if api_key is not None else os.environ.get("DEEPSEEK_API_KEY", "")
    if not key.strip():
        raise ModelRequestRejectedError("DEEPSEEK_API_KEY is required")
    if not model_id.strip():
        raise ModelRequestRejectedError("DeepSeek model_id cannot be empty")

    model = ChatOpenAI(
        model=model_id,
        api_key=SecretStr(key.strip()),
        base_url=DEEPSEEK_BASE_URL,
        use_responses_api=False,
        timeout=60.0,
        max_retries=0,
        http_client=http_client,
        disabled_params={"parallel_tool_calls": None},
        # Forced tool_choice needs non-thinking mode. DeepSeek expects max_tokens,
        # not the max_completion_tokens produced by ChatOpenAI's max_tokens alias.
        extra_body={"thinking": {"type": "disabled"}, "max_tokens": 2048},
    )
    # Both SDK clients inherit OpenAI-only metadata from the environment during
    # construction. Clear public configuration before any request to DeepSeek.
    model.openai_organization = None
    sync_client = cast(OpenAI, model.root_client)
    async_client = cast(AsyncOpenAI, model.root_async_client)
    sync_client.organization = async_client.organization = None
    sync_client.project = async_client.project = None
    return LangChainModelGateway(
        model,
        model_id=model_id,
        structured_output_method="function_calling",
        retryable_error_types=(APIConnectionError, RateLimitError, InternalServerError),
        rejected_error_types=(APIStatusError,),
    )


def create_ark_gateway(
    *,
    model_id: str | None = None,
    api_key: str | None = None,
    http_client: httpx.Client | None = None,
) -> LangChainModelGateway:
    """Build a Volcano Ark client without making a request.

    ``model_id`` overrides the Ark inference endpoint ID from the environment.
    The explicit environment boundary prevents another Provider key from being
    used accidentally.
    """

    endpoint_id = model_id if model_id is not None else os.environ.get("ARK_ENDPOINT_ID", "")
    if not endpoint_id.strip():
        raise ModelRequestRejectedError("ARK_ENDPOINT_ID is required")
    endpoint_id = endpoint_id.strip()
    key = api_key if api_key is not None else os.environ.get("ARK_API_KEY", "")
    if not key.strip():
        raise ModelRequestRejectedError("ARK_API_KEY is required")

    model = ChatOpenAI(
        model=endpoint_id,
        api_key=SecretStr(key.strip()),
        base_url=ARK_BASE_URL,
        use_responses_api=False,
        timeout=60.0,
        max_retries=0,
        http_client=http_client,
        disabled_params={"parallel_tool_calls": None},
    )
    model.openai_organization = None
    sync_client = cast(OpenAI, model.root_client)
    async_client = cast(AsyncOpenAI, model.root_async_client)
    sync_client.organization = async_client.organization = None
    sync_client.project = async_client.project = None
    return LangChainModelGateway(
        model,
        model_id=endpoint_id,
        structured_output_method="function_calling",
        retryable_error_types=(APIConnectionError, RateLimitError, InternalServerError),
        rejected_error_types=(APIStatusError,),
    )
