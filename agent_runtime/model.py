"""Shared model policy for all runtime contracts."""

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class StrictModel(BaseModel):
    """Validated immutable model with explicit fields and no implicit coercion.

    Nested collections use immutable types as well. Runtime code must not use
    the unvalidated model-copy update shortcut; construct a new model or call
    model_validate instead.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
        serialize_by_alias=True,
        strict=True,
        validate_by_alias=True,
        validate_by_name=True,
        validate_default=True,
    )
