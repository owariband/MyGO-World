"""Single Event steps and EventSession scheduling boundaries."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_runtime.event.character_step import (
        CharacterStep,
        CharacterStepError,
        CharacterStepResult,
    )
    from agent_runtime.event.runner import (
        WorldRunner,
        WorldRunResult,
        WorldStopReason,
        pause_world,
    )
    from agent_runtime.event.session import SessionTransitionPlan, plan_join, plan_leave
    from agent_runtime.event.story_line import StageView, StoryLineBuilder, export_storyline

__all__ = [
    "CharacterStep",
    "CharacterStepError",
    "CharacterStepResult",
    "SessionTransitionPlan",
    "StageView",
    "StoryLineBuilder",
    "WorldRunResult",
    "WorldRunner",
    "WorldStopReason",
    "export_storyline",
    "pause_world",
    "plan_join",
    "plan_leave",
]

_EXPORT_MODULES = {
    "CharacterStep": "character_step",
    "CharacterStepError": "character_step",
    "CharacterStepResult": "character_step",
    "SessionTransitionPlan": "session",
    "StageView": "story_line",
    "StoryLineBuilder": "story_line",
    "WorldRunResult": "runner",
    "WorldRunner": "runner",
    "WorldStopReason": "runner",
    "export_storyline": "story_line",
    "pause_world": "runner",
    "plan_join": "session",
    "plan_leave": "session",
}


def __getattr__(name: str) -> object:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(name)
    module = import_module(f"agent_runtime.event.{module_name}")
    return getattr(module, name)
