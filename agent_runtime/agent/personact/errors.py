"""Typed error boundaries for creator configuration and decision execution."""


class PersonActError(Exception):
    """Base error for PersonAct configuration and execution."""


class ManifestDecodeError(PersonActError):
    """The creator manifest is not valid against the public schema."""


class ManifestCompileError(PersonActError):
    """The manifest is shaped correctly but requests invalid capabilities."""


class DecisionInputError(PersonActError):
    """The runtime supplied input outside this agent's authority."""


class ProposalValidationError(PersonActError):
    """A planner returned a proposal outside the compiled or world grant."""


class PlanningError(PersonActError):
    """The planning strategy failed before producing a proposal draft."""


class PlannerOutputError(PersonActError):
    """The planning strategy returned a value outside its typed contract."""
