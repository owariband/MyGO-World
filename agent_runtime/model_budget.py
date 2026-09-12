"""Process-local hard budget for logical ModelGateway calls."""

from __future__ import annotations

from collections.abc import Callable
from threading import Lock

from langchain_core.runnables import RunnableConfig

from agent_runtime.model import StrictModel
from agent_runtime.model_gateway import (
    ModelGateway,
    ModelGeneration,
    ModelRequest,
    ModelRequestRejectedError,
)


class ModelCallBudgetExceededError(ModelRequestRejectedError):
    """The process-local logical generate budget has been exhausted."""


class BudgetedModelGateway:
    """Pre-charge one shared budget slot before each delegated generate call."""

    def __init__(
        self,
        gateway: ModelGateway,
        *,
        call_limit: int,
        on_exhausted: Callable[[], None] | None = None,
    ) -> None:
        if isinstance(call_limit, bool) or call_limit < 1:
            raise ValueError("call_limit must be positive")
        self._gateway = gateway
        self._call_limit = call_limit
        self._generate_count = 0
        self._exhausted = False
        self._on_exhausted = on_exhausted
        self._lock = Lock()

    @property
    def call_limit(self) -> int:
        return self._call_limit

    @property
    def generate_count(self) -> int:
        with self._lock:
            return self._generate_count

    @property
    def exhausted(self) -> bool:
        with self._lock:
            return self._exhausted

    def generate[ResponseT: StrictModel](
        self,
        request: ModelRequest,
        response_type: type[ResponseT],
        config: RunnableConfig | None = None,
    ) -> ModelGeneration[ResponseT]:
        first_exhaustion = False
        rejected = False
        with self._lock:
            if self._generate_count >= self._call_limit:
                first_exhaustion = not self._exhausted
                self._exhausted = True
                rejected = True
            else:
                self._generate_count += 1

        if not rejected:
            return self._gateway.generate(request, response_type, config)

        if first_exhaustion and self._on_exhausted is not None:
            try:
                self._on_exhausted()
            except Exception as error:
                raise ModelCallBudgetExceededError("Provider generate budget exhausted") from error
        raise ModelCallBudgetExceededError("Provider generate budget exhausted")
