from __future__ import annotations

from collections.abc import Callable

from mygo_world.contracts import ValidatedCommitPlan
from mygo_world.perception import RecognizedEvent


class EventRecognizer:
    """Turns validated candidate facts into queryable events without changing them."""

    def recognize(
        self,
        plan: ValidatedCommitPlan,
        *,
        first_event_order: int,
        id_generator: Callable[[], str],
    ) -> list[RecognizedEvent]:
        return [
            RecognizedEvent(
                event_id=id_generator(),
                event_order=first_event_order + index,
                candidate=candidate.model_copy(deep=True),
            )
            for index, candidate in enumerate(plan.events)
        ]
