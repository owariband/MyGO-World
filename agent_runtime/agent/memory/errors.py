"""Errors raised when a scoped memory stream invariant is violated."""


class MemoryInvariantError(ValueError):
    """Base error for memory ownership, identity, and time invariants."""


class MemoryScopeError(MemoryInvariantError):
    """A record belongs to a different Agent or scope."""


class DuplicateMemoryError(MemoryInvariantError):
    """A stream or touch batch repeats a memory identifier."""


class MemoryTouchError(MemoryInvariantError):
    """A touch references an unknown memory or moves its clock backwards."""
