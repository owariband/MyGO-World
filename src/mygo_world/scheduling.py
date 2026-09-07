from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from mygo_world.committer import IdGenerator, uuid4_id
from mygo_world.contracts import TurnSelection

TurnSelectionSource = Literal["nominated", "director", "round_robin"]
DecisionTurnStatus = Literal["selected", "no_op", "committed", "failed"]


@dataclass(frozen=True)
class TurnContext:
    run_id: str
    wave_id: str
    wave_number: int
    world_version: int
    session_id: str
    participant_ids: tuple[str, ...]
    pending_response_ids: tuple[str, ...] = ()
    last_selected_actor_id: str | None = None


@dataclass(frozen=True)
class DecisionTurnRecord:
    """Auditable runtime decision granting one Character a decision turn."""

    decision_id: str
    run_id: str
    wave_id: str
    wave_number: int
    session_id: str
    base_world_version: int
    selected_actor_id: str
    selection_source: TurnSelectionSource
    candidate_ids: tuple[str, ...]
    status: DecisionTurnStatus = "selected"
    resulting_world_version: int | None = None
    error_code: str | None = None


class DirectorTurnPolicy(Protocol):
    def select(
        self, context: TurnContext, candidate_ids: tuple[str, ...]
    ) -> TurnSelection | None: ...


@dataclass(frozen=True)
class DeterministicDirectorFixture:
    """Offline Director policy used by fixtures without making a model call."""

    actor_id: str | None = None

    def select(
        self, context: TurnContext, candidate_ids: tuple[str, ...]
    ) -> TurnSelection:
        selected = self.actor_id or candidate_ids[0]
        return TurnSelection(
            world_version=context.world_version,
            session_id=context.session_id,
            actor_id=selected,
            reason="Deterministic fixture selection.",
        )


class TurnScheduler:
    """Grant exactly one turn using nomination, Director, then round-robin."""

    def __init__(self, *, id_generator: IdGenerator = uuid4_id) -> None:
        self._id_generator = id_generator

    def select(
        self,
        context: TurnContext,
        director_policy: DirectorTurnPolicy | None,
    ) -> DecisionTurnRecord:
        participants = tuple(sorted(set(context.participant_ids)))
        if not participants:
            raise ValueError("TurnContext must contain at least one participant")
        if len(participants) != len(context.participant_ids):
            raise ValueError("TurnContext participant_ids must be unique")

        nominated = tuple(
            actor_id
            for actor_id in participants
            if actor_id in set(context.pending_response_ids)
        )
        if nominated:
            selected_actor_id = self._round_robin(
                participants,
                eligible=frozenset(nominated),
                previous=context.last_selected_actor_id,
            )
            source: TurnSelectionSource = "nominated"
            candidates = nominated
        else:
            candidates = tuple(
                actor_id
                for actor_id in participants
                if actor_id != context.last_selected_actor_id
            ) or participants
            recommendation = (
                director_policy.select(context, candidates)
                if director_policy is not None
                else None
            )
            if (
                recommendation is not None
                and recommendation.world_version == context.world_version
                and recommendation.session_id == context.session_id
                and recommendation.actor_id in candidates
            ):
                selected_actor_id = recommendation.actor_id
                source = "director"
            else:
                selected_actor_id = self._round_robin(
                    participants,
                    eligible=frozenset(participants),
                    previous=context.last_selected_actor_id,
                )
                source = "round_robin"

        return DecisionTurnRecord(
            decision_id=self._id_generator(),
            run_id=context.run_id,
            wave_id=context.wave_id,
            wave_number=context.wave_number,
            session_id=context.session_id,
            base_world_version=context.world_version,
            selected_actor_id=selected_actor_id,
            selection_source=source,
            candidate_ids=candidates,
        )

    @staticmethod
    def _round_robin(
        participants: tuple[str, ...],
        *,
        eligible: frozenset[str],
        previous: str | None,
    ) -> str:
        start = participants.index(previous) + 1 if previous in participants else 0
        for offset in range(len(participants)):
            candidate = participants[(start + offset) % len(participants)]
            if candidate in eligible:
                return candidate
        raise ValueError("TurnContext has no eligible participant")
