from __future__ import annotations

from dataclasses import dataclass

from mygo_world.contracts import TurnSelection
from mygo_world.scheduling import (
    DeterministicDirectorFixture,
    TurnContext,
    TurnScheduler,
)


def _context(**overrides: object) -> TurnContext:
    values: dict[str, object] = {
        "run_id": "run-1",
        "wave_id": "wave-1",
        "wave_number": 1,
        "world_version": 3,
        "session_id": "session-1",
        "participant_ids": ("character-a", "character-b", "character-c"),
    }
    values.update(overrides)
    return TurnContext(**values)  # type: ignore[arg-type]


@dataclass
class RecordingPolicy:
    actor_id: str
    calls: int = 0

    def select(
        self, context: TurnContext, candidate_ids: tuple[str, ...]
    ) -> TurnSelection:
        self.calls += 1
        return TurnSelection(
            world_version=context.world_version,
            session_id=context.session_id,
            actor_id=self.actor_id,
            reason=f"Choose from {candidate_ids}",
        )


def test_nomination_skips_director_and_rotates_among_pending_responders() -> None:
    policy = RecordingPolicy("character-a")
    scheduler = TurnScheduler(id_generator=lambda: "decision-1")

    decision = scheduler.select(
        _context(
            pending_response_ids=("character-a", "character-c"),
            last_selected_actor_id="character-a",
        ),
        policy,
    )

    assert decision.selected_actor_id == "character-c"
    assert decision.selection_source == "nominated"
    assert decision.candidate_ids == ("character-a", "character-c")
    assert policy.calls == 0


def test_valid_director_selection_wins() -> None:
    policy = RecordingPolicy("character-c")

    decision = TurnScheduler(id_generator=lambda: "decision-1").select(
        _context(), policy
    )

    assert decision.selected_actor_id == "character-c"
    assert decision.selection_source == "director"
    assert policy.calls == 1


def test_invalid_director_selection_uses_persistent_round_robin_cursor() -> None:
    policy = RecordingPolicy("not-a-participant")

    decision = TurnScheduler(id_generator=lambda: "decision-1").select(
        _context(last_selected_actor_id="character-b"), policy
    )

    assert decision.selected_actor_id == "character-c"
    assert decision.selection_source == "round_robin"


def test_missing_director_uses_persistent_round_robin_cursor() -> None:
    decision = TurnScheduler(id_generator=lambda: "decision-1").select(
        _context(last_selected_actor_id="character-c"), None
    )

    assert decision.selected_actor_id == "character-a"
    assert decision.selection_source == "round_robin"


def test_deterministic_director_fixture_selects_first_stable_candidate() -> None:
    decision = TurnScheduler(id_generator=lambda: "decision-1").select(
        _context(participant_ids=("character-c", "character-a", "character-b")),
        DeterministicDirectorFixture(),
    )

    assert decision.selected_actor_id == "character-a"
    assert decision.selection_source == "director"
