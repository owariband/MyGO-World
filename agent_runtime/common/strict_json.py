"""Strict JSON decoding shared by untrusted Project configuration."""

from __future__ import annotations

import json
import math
from typing import Never, cast


def loads_strict_json(value: str) -> object:
    """Decode one JSON value without duplicate members or non-finite constants."""

    return cast(
        object,
        json.loads(
            value,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_non_finite_constant,
            parse_float=_parse_finite_float,
        ),
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON member {key!r}")
        result[key] = value
    return result


def _reject_non_finite_constant(value: str) -> Never:
    raise ValueError(f"non-finite JSON constant {value!r} is not allowed")


def _parse_finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"non-finite JSON number {value!r} is not allowed")
    return parsed
