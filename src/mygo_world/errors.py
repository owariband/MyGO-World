from __future__ import annotations

from typing import Any


class WorldError(Exception):
    """Stable operator-facing error."""

    def __init__(
        self, code: str, message: str, *, receipt: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.receipt = receipt
        self.trace_attempts: tuple[Any, ...] = ()
        self.schema_attempts: tuple[Any, ...] = ()


class WorldAlreadyExistsError(WorldError):
    def __init__(self, world_id: str) -> None:
        super().__init__("WORLD_ALREADY_EXISTS", f"World '{world_id}' already exists")


class WorldNotFoundError(WorldError):
    def __init__(self, world_id: str) -> None:
        super().__init__("WORLD_NOT_FOUND", f"World '{world_id}' does not exist")


class SchemaOutdatedError(WorldError):
    def __init__(self, current: str | None, expected: str) -> None:
        actual = current or "none"
        super().__init__(
            "SCHEMA_OUTDATED",
            f"World schema is '{actual}', expected '{expected}'; run Alembic upgrade",
        )


class SeedInvalidError(WorldError):
    def __init__(self, message: str) -> None:
        super().__init__("SEED_INVALID", message)
