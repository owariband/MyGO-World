"""M4 WorldRunner dispatch fencing, fairness, failure, and wakeup tests."""

from __future__ import annotations

import shutil
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy.orm import Session

from agent_runtime.agent.memory.storage import MemoryStore
from agent_runtime.agent.personact.agent import ActionPlanningInput, PlanDraft, PlanningInput
from agent_runtime.agent.personact.compiler import (
    Catalog,
    CompiledPersonActSpec,
    PromptDefinition,
    ToolDefinition,
    ToolMode,
)
from agent_runtime.agent.personact.errors import PlanningError
from agent_runtime.agent.personact.proposal import ProposalDraft
from agent_runtime.agent.personact.state import PlanItem
from agent_runtime.agent.personact.storage import PersonaStateStore
from agent_runtime.agent.skill import RuntimeSkillCatalog
from agent_runtime.bootstrap import create_world, load_world_for_resume
from agent_runtime.event.character_step import CharacterDispatch, CharacterStep, CharacterStepResult
from agent_runtime.event.runner import WorldRunner, WorldStopReason
from agent_runtime.sqlite import ProjectDatabase, open_project_database
from agent_runtime.trace import LocalTrace, TraceRecord
from agent_runtime.world.affordances import JOIN_TARGET_SESSION, LEAVE_CURRENT_SESSION
from agent_runtime.world.contracts import (
    ActAction,
    Affordance,
    CharacterTarget,
    CommitPosition,
    DeliveryChannel,
    InteractAction,
    PerceptCandidate,
    ProposalKind,
    RespondAction,
    UtterAction,
    WorldRef,
)
from agent_runtime.world.entries import (
    AudienceMode,
    BehaviorEntry,
    DialogueEntry,
    EntryRelationKind,
    EventEntryRecipient,
    InteractionRequest,
    InteractionRequestStatus,
    SessionTransitionEntry,
    SessionTransitionReason,
)
from agent_runtime.world.entry_storage import EventEntryStore
from agent_runtime.world.state import (
    AgentWorldState,
    EventSessionNode,
    LocationState,
    PublicWorldState,
    WorldState,
    WorldStatus,
)
from agent_runtime.world.storage import WorldCommitConflictError, WorldStore

PROJECT_ID = "runner-tests"
WORLD_REF = WorldRef(project_id=PROJECT_ID, world_id="save-001")
NOW = datetime(2026, 9, 11, 14, tzinfo=UTC)
REPOSITORY_ROOT = Path(__file__).parents[2]

type _DraftBuilder = Callable[[ActionPlanningInput], ProposalDraft]


@dataclass(frozen=True, slots=True)
class _UnusedStep:
    world_ref: WorldRef

    def run_dispatch(
        self,
        _dispatch: CharacterDispatch,
        *,
        config: object | None = None,
    ) -> CharacterStepResult:
        del config
        raise AssertionError("this test must not execute a CharacterStep")


@dataclass(frozen=True, slots=True)
class _FailingStep:
    world_ref: WorldRef

    def run_dispatch(
        self,
        dispatch: CharacterDispatch,
        *,
        config: object | None = None,
    ) -> CharacterStepResult:
        del config
        raise PlanningError(f"injected failure for {dispatch.agent_id}")


@dataclass(slots=True)
class _RecordingFailingStep:
    world_ref: WorldRef
    dispatches: list[CharacterDispatch] = field(default_factory=lambda: list[CharacterDispatch]())

    def run_dispatch(
        self,
        dispatch: CharacterDispatch,
        *,
        config: object | None = None,
    ) -> CharacterStepResult:
        del config
        self.dispatches.append(dispatch)
        raise PlanningError(f"injected failure for {dispatch.agent_id}")


@dataclass(slots=True)
class _IntegrationHarness:
    scripts: dict[str, deque[_DraftBuilder]] = field(default_factory=lambda: defaultdict(deque))
    action_inputs: dict[str, list[ActionPlanningInput]] = field(
        default_factory=lambda: defaultdict(list)
    )

    def queue(self, agent_id: str, *builders: _DraftBuilder) -> None:
        self.scripts[agent_id].extend(builders)

    def factory(self, spec: CompiledPersonActSpec) -> _IntegrationStrategy:
        return _IntegrationStrategy(agent_id=spec.agent_id, harness=self)


@dataclass(slots=True)
class _IntegrationStrategy:
    agent_id: str
    harness: _IntegrationHarness

    def score_poignancy(
        self,
        spec: CompiledPersonActSpec,
        candidate: PerceptCandidate,
        *,
        world_ref: WorldRef,
    ) -> float:
        assert spec.agent_id == self.agent_id
        assert candidate.content
        assert world_ref.world_id == "m4-multi-step"
        return 1.0

    def plan(self, planning_input: PlanningInput) -> PlanDraft:
        assert planning_input.spec.agent_id == self.agent_id
        return PlanDraft(
            items=(
                PlanItem(
                    plan_id=f"plan-{self.agent_id}",
                    description=f"Continue {self.agent_id}'s current intent.",
                ),
            )
        )

    def plan_action(self, planning_input: ActionPlanningInput) -> ProposalDraft:
        assert planning_input.spec.agent_id == self.agent_id
        self.harness.action_inputs[self.agent_id].append(planning_input)
        try:
            builder = self.harness.scripts[self.agent_id].popleft()
        except IndexError as error:
            raise AssertionError(f'no scripted action for Agent "{self.agent_id}"') from error
        return builder(planning_input)


@dataclass(frozen=True, slots=True)
class _FixedEmbeddingProvider:
    def embed(self, text: str) -> tuple[float, ...]:
        assert text
        return (1.0, 0.0)


def test_scheduler_balances_story_lines_then_agents_with_deterministic_ties(
    tmp_path: Path,
) -> None:
    database = _database_with_world(
        tmp_path,
        _state((("anon", "soyo"), ("tomori",))),
    )
    runner = _runner(database, _UnusedStep(WORLD_REF), owner_id="runner-fair")
    store = WorldStore(WORLD_REF)
    selected: list[str] = []
    try:
        resumed = runner.resume(additional_decisions=6)
        for _ in range(6):
            dispatch = runner.next_dispatch()
            assert dispatch is not None
            selected.append(dispatch.agent_id)
            with database.session_factory.begin() as session:
                store.fail_dispatch(
                    session,
                    owner_id=dispatch.owner_id,
                    control_epoch=dispatch.control_epoch,
                    dispatch_count=dispatch.dispatch_count,
                    agent_id=dispatch.agent_id,
                )

        assert selected == ["anon", "tomori", "soyo", "tomori", "anon", "tomori"]
        assert runner.next_dispatch() is None
        stopped = runner.state()
        assert stopped.status is WorldStatus.PAUSED
        assert stopped.stop_reason == WorldStopReason.BUDGET_EXHAUSTED.value
        assert stopped.dispatch_count == resumed.dispatch_limit_at == 6
    finally:
        runner.close()
        database.dispose()


def test_active_dispatch_requires_the_exact_owner_epoch_count_and_agent(
    tmp_path: Path,
) -> None:
    database = _database_with_world(tmp_path, _state((("anon",),)))
    runner = _runner(database, _UnusedStep(WORLD_REF), owner_id="runner-cas")
    store = WorldStore(WORLD_REF)
    try:
        runner.resume(additional_decisions=1)
        dispatch = runner.next_dispatch()
        assert dispatch is not None

        stale_attempts = (
            ("other-owner", dispatch.dispatch_count, dispatch.agent_id),
            (dispatch.owner_id, dispatch.dispatch_count + 1, dispatch.agent_id),
            (dispatch.owner_id, dispatch.dispatch_count, "other-agent"),
        )
        for owner_id, dispatch_count, agent_id in stale_attempts:
            with (
                pytest.raises(WorldCommitConflictError),
                database.session_factory.begin() as session,
            ):
                store.compare_and_advance(
                    session,
                    expected_version=dispatch.based_on_world_version,
                    expected_control_epoch=dispatch.control_epoch,
                    expected_decision_seq=dispatch.based_on_decision_seq,
                    advances_world=True,
                    run_owner_id=owner_id,
                    dispatch_count=dispatch_count,
                    dispatch_agent_id=agent_id,
                )

        with database.session_factory.begin() as session:
            assert store.compare_and_advance(
                session,
                expected_version=dispatch.based_on_world_version,
                expected_control_epoch=dispatch.control_epoch,
                expected_decision_seq=dispatch.based_on_decision_seq,
                advances_world=True,
                run_owner_id=dispatch.owner_id,
                dispatch_count=dispatch.dispatch_count,
                dispatch_agent_id=dispatch.agent_id,
            ) == (2, 1)
            store.finish_session_step(
                session,
                agent_id=dispatch.agent_id,
                world_version=2,
                entry_kind="behavior",
                waiting=False,
                dispatch_count=dispatch.dispatch_count,
            )

        committed = runner.state()
        assert committed.active_dispatch_count is None
        assert committed.active_dispatch_agent_id is None
        assert committed.current_version == 2
        assert committed.decision_seq == 1
    finally:
        runner.close()
        database.dispose()


def test_failed_model_calls_consume_quota_release_the_token_and_remain_fair(
    tmp_path: Path,
) -> None:
    database = _database_with_world(tmp_path, _state((("anon", "soyo"),)))
    runner = _runner(database, _FailingStep(WORLD_REF), owner_id="runner-failure")
    try:
        runner.resume(additional_decisions=2)
        result = runner.run()

        assert result.successful_steps == ()
        assert tuple(
            (failure.dispatch_count, failure.agent_id) for failure in result.failed_dispatches
        ) == ((1, "anon"), (2, "soyo"))
        assert result.final_state.status is WorldStatus.PAUSED
        assert result.final_state.stop_reason == WorldStopReason.BUDGET_EXHAUSTED.value
        assert result.final_state.dispatch_count == 2
        assert result.final_state.active_dispatch_count is None
        with database.session_factory() as session:
            sessions = WorldStore(WORLD_REF).load(session).sessions
        assert {node.agent_id: node.last_dispatch_count for node in sessions} == {
            "anon": 1,
            "soyo": 2,
        }
    finally:
        runner.close()
        database.dispose()


def test_all_waiting_agents_pause_idle_without_dispatch_or_entry(tmp_path: Path) -> None:
    database = _database_with_world(tmp_path, _state((("anon", "soyo"),)))
    runner = _runner(database, _UnusedStep(WORLD_REF), owner_id="runner-idle")
    world_store = WorldStore(WORLD_REF)
    entry_store = EventEntryStore(WORLD_REF)
    try:
        with database.session_factory.begin() as session:
            for agent_id in ("anon", "soyo"):
                world_store.finish_session_step(
                    session,
                    agent_id=agent_id,
                    world_version=1,
                    entry_kind=None,
                    waiting=True,
                    dispatch_count=None,
                )

        resumed = runner.resume(additional_decisions=5)
        result = runner.run()

        assert resumed.dispatch_limit_at == 5
        assert result.successful_steps == ()
        assert result.failed_dispatches == ()
        assert result.final_state.status is WorldStatus.PAUSED
        assert result.final_state.stop_reason == WorldStopReason.IDLE.value
        assert result.final_state.dispatch_count == 0
        assert result.final_state.dispatch_limit_at == 5
        assert result.final_state.current_version == 1
        assert result.final_state.decision_seq == 0
        with database.session_factory() as session:
            assert entry_store.list_all(session) == ()
    finally:
        runner.close()
        database.dispose()


def test_failed_priority_dispatch_keeps_the_request_priority_unconsumed(
    tmp_path: Path,
) -> None:
    database = _database_with_world(tmp_path, _state((("anon", "soyo"),)))
    step = _RecordingFailingStep(WORLD_REF)
    runner = _runner(database, step, owner_id="runner-priority-failure")
    entry_store = EventEntryStore(WORLD_REF)
    request_entry = DialogueEntry(
        world_ref=WORLD_REF,
        entry_id="entry-request-soyo",
        source_id="decision-request-soyo",
        commit_position=CommitPosition(world_version=1),
        root_session_id_at_commit="session-anon",
        topology_version=1,
        actor_agent_id="anon",
        target_agent_id="soyo",
        audience_mode=AudienceMode.SESSION,
        delivery_channel=DeliveryChannel.DIRECT,
        occurred_at=NOW,
        text="Will you answer me?",
        created_at=NOW,
    )
    try:
        with database.session_factory.begin() as session:
            entry_store.append(
                session,
                request_entry,
                recipients=tuple(
                    EventEntryRecipient(
                        world_ref=WORLD_REF,
                        entry_id=request_entry.entry_id,
                        agent_id=agent_id,
                    )
                    for agent_id in ("anon", "soyo")
                ),
            )
            entry_store.create_request(
                session,
                InteractionRequest(
                    world_ref=WORLD_REF,
                    request_entry_id=request_entry.entry_id,
                    requester_agent_id="anon",
                    recipient_agent_id="soyo",
                    status=InteractionRequestStatus.PENDING,
                    updated_world_version=1,
                ),
            )

        runner.resume(additional_decisions=2)
        result = runner.run()

        assert tuple(dispatch.agent_id for dispatch in step.dispatches) == ("soyo", "soyo")
        assert tuple(dispatch.priority_request_entry_id for dispatch in step.dispatches) == (
            request_entry.entry_id,
            request_entry.entry_id,
        )
        assert tuple(failure.agent_id for failure in result.failed_dispatches) == (
            "soyo",
            "soyo",
        )
        with database.session_factory() as session:
            request = entry_store.get_request(session, request_entry.entry_id)
        assert request.status is InteractionRequestStatus.PENDING
        assert request.priority_consumed_dispatch_count is None
    finally:
        runner.close()
        database.dispose()


def test_waiting_agent_wakes_only_for_a_new_visible_entry(tmp_path: Path) -> None:
    initial = _state((("anon",), ("soyo",)))
    database = _database_with_world(tmp_path, initial)
    runner = _runner(database, _UnusedStep(WORLD_REF), owner_id="runner-wake")
    world_store = WorldStore(WORLD_REF)
    entry_store = EventEntryStore(WORLD_REF)
    try:
        with database.session_factory.begin() as session:
            world_store.finish_session_step(
                session,
                agent_id="anon",
                world_version=1,
                entry_kind=None,
                waiting=True,
                dispatch_count=None,
            )
        resumed = runner.resume(additional_decisions=3)

        with database.session_factory.begin() as session:
            world_store.compare_and_advance(
                session,
                expected_version=1,
                expected_control_epoch=resumed.control_epoch,
                expected_decision_seq=0,
                advances_world=True,
            )
            _append_behavior(entry_store, session, agent_id="soyo", version=2)

        invisible = runner.next_dispatch()
        assert invisible is not None
        assert invisible.agent_id == "soyo"
        with database.session_factory.begin() as session:
            world_store.fail_dispatch(
                session,
                owner_id=invisible.owner_id,
                control_epoch=invisible.control_epoch,
                dispatch_count=invisible.dispatch_count,
                agent_id=invisible.agent_id,
            )
            world_store.compare_and_advance(
                session,
                expected_version=2,
                expected_control_epoch=resumed.control_epoch,
                expected_decision_seq=1,
                advances_world=True,
            )
            _append_behavior(entry_store, session, agent_id="anon", version=3)

        awakened = runner.next_dispatch()
        assert awakened is not None
        assert awakened.agent_id == "anon"
        with database.session_factory() as session:
            anon_node = next(
                node for node in world_store.load(session).sessions if node.agent_id == "anon"
            )
        assert anon_node.wait_for_visible_entry_after_version is None
    finally:
        runner.close()
        database.dispose()


def test_runner_trace_records_only_minimal_dispatch_outcome_failure_and_pause_fields(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    project_id = "for-the-band"
    world_id = "m4-multi-step"
    world_ref = WorldRef(project_id=project_id, world_id=world_id)
    project_directory = repository / "projects" / project_id
    project_directory.mkdir(parents=True)
    for filename in ("project.json", "agents.json", "scenario.yaml"):
        shutil.copy2(REPOSITORY_ROOT / "projects" / project_id / filename, project_directory)

    loaded = create_world(
        repository,
        project_id,
        world_id,
        catalog=_integration_catalog(),
        clock=lambda: NOW,
    )
    harness = _IntegrationHarness()
    harness.queue("anon", _self_behavior)
    harness.queue("rana", _planning_failure)
    database = open_project_database(repository / ".runtime", project_id, create=False)
    trace = LocalTrace(repository / "projects", world_ref)
    step = _integration_step(database, world_ref, loaded, harness, trace_log=trace)
    runner = WorldRunner(
        database=database,
        world_ref=world_ref,
        character_step=step,
        trace_log=trace,
        owner_id="runner-trace",
    )
    try:
        runner.resume(additional_decisions=2)
        result = runner.run()
        assert tuple(item.agent_id for item in result.successful_steps) == ("anon",)
        assert tuple(item.agent_id for item in result.failed_dispatches) == ("rana",)
    finally:
        runner.close()
        trace.close()
        database.dispose()

    records = tuple(
        TraceRecord.model_validate_json(line, strict=True)
        for line in trace.path.read_text(encoding="utf-8").splitlines()
    )
    runtime_records = tuple(record for record in records if record.event.startswith("runtime."))
    assert tuple(record.event for record in runtime_records) == (
        "runtime.resume",
        "runtime.dispatch",
        "runtime.outcome",
        "runtime.dispatch",
        "runtime.dispatch_failed",
        "runtime.pause",
    )
    assert all(
        record.agent_kind == "runtime" and record.trace_id == "runner-trace"
        for record in runtime_records
    )
    assert set(runtime_records[0].data) == {
        "controlEpoch",
        "dispatchCount",
        "dispatchLimitAt",
    }
    assert set(runtime_records[1].data) == {
        "agentId",
        "dispatchCount",
        "decisionId",
        "controlEpoch",
        "priorityRequestEntryId",
    }
    assert set(runtime_records[2].data) == {
        "agentId",
        "dispatchCount",
        "decisionId",
        "status",
        "entryId",
    }
    assert set(runtime_records[3].data) == set(runtime_records[1].data)
    assert set(runtime_records[4].data) == {"dispatchCount", "agentId", "errorType"}
    assert runtime_records[4].data["errorType"] == "PlanningError"
    assert runtime_records[5].data == {"reason": WorldStopReason.BUDGET_EXHAUSTED.value}
    assert "private planning failure" not in trace.path.read_text(encoding="utf-8")


def test_real_character_steps_survive_pause_reopen_and_dynamic_session_changes(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    project_id = "for-the-band"
    world_id = "m4-multi-step"
    world_ref = WorldRef(project_id=project_id, world_id=world_id)
    project_directory = repository / "projects" / project_id
    project_directory.mkdir(parents=True)
    for filename in ("project.json", "agents.json", "scenario.yaml"):
        shutil.copy2(REPOSITORY_ROOT / "projects" / project_id / filename, project_directory)

    catalog = _integration_catalog()
    created = create_world(repository, project_id, world_id, catalog=catalog, clock=lambda: NOW)
    initial_memory_sizes = {
        stream.agent_id: len(stream.records) for stream in created.memory_streams
    }
    harness = _IntegrationHarness()
    harness.queue(
        "anon",
        _utter("soyo", expects_response=True),
        _join("tomori"),
    )
    harness.queue(
        "soyo",
        _respond("anon", "Yes. Let's keep talking."),
        _self_behavior,
    )
    harness.queue("rana", _join("tomori"))
    harness.queue("taki", _self_behavior, _self_behavior)
    harness.queue("tomori", _leave)

    database = open_project_database(repository / ".runtime", project_id, create=False)
    first_step = _integration_step(database, world_ref, created, harness)
    first_runner = WorldRunner(
        database=database,
        world_ref=world_ref,
        character_step=first_step,
        owner_id="integration-first",
    )
    try:
        first_runner.resume(additional_decisions=5)
        first_results = tuple(_execute_one(first_runner, first_step) for _ in range(5))
        assert tuple(result.agent_id for result in first_results) == (
            "anon",
            "soyo",
            "rana",
            "taki",
            "anon",
        )
        assert (
            tuple(result.world_update.status.value for result in first_results) == ("applied",) * 5
        )
        first_entries = tuple(result.world_update.entry_id for result in first_results)
        assert all(entry_id is not None for entry_id in first_entries)

        paused = first_runner.pause_and_save(reason=WorldStopReason.USER_PAUSE)
        assert paused.status is WorldStatus.PAUSED
        assert paused.current_version == 6
        assert paused.dispatch_count == paused.dispatch_limit_at == 5
    finally:
        first_runner.close()
        database.dispose()

    resumable = load_world_for_resume(repository, project_id, world_id, catalog=catalog)
    assert resumable.public_state.world.status is WorldStatus.PAUSED
    assert resumable.public_state.world.dispatch_count == 5
    assert {node.agent_id: node.root_session_id for node in resumable.public_state.sessions} == {
        "anon": "session-tomori",
        "rana": "session-tomori",
        "soyo": "session-soyo",
        "taki": "session-taki",
        "tomori": "session-tomori",
    }

    reopened = open_project_database(repository / ".runtime", project_id, create=False)
    second_step = _integration_step(reopened, world_ref, resumable, harness)
    second_runner = WorldRunner(
        database=reopened,
        world_ref=world_ref,
        character_step=second_step,
        owner_id="integration-second",
    )
    try:
        second_runner.resume(additional_decisions=3)
        second_results = tuple(_execute_one(second_runner, second_step) for _ in range(3))
        assert tuple(result.agent_id for result in second_results) == (
            "soyo",
            "taki",
            "tomori",
        )
        assert second_runner.next_dispatch() is None

        with reopened.session_factory() as session:
            final_public = WorldStore(world_ref).load(session)
            entries = EventEntryStore(world_ref).list_all(session)
            requests = EventEntryStore(world_ref).pending_requests_for(session, "soyo")
            first_request = EventEntryStore(world_ref).get_request(
                session,
                _required_entry_id(first_results[0]),
            )
            personas = PersonaStateStore(world_ref).load_all_for_bootstrap(session)
            streams = MemoryStore(world_ref).load_all_for_bootstrap(session)
            transition_entries = tuple(
                entry for entry in entries if isinstance(entry, SessionTransitionEntry)
            )
            transfer_links = EventEntryStore(world_ref).links_for(
                session,
                (transition_entries[1].entry_id,),
            )
            split_links = EventEntryStore(world_ref).links_for(
                session,
                (transition_entries[2].entry_id,),
            )

        assert final_public.world.status is WorldStatus.PAUSED
        assert final_public.world.stop_reason == WorldStopReason.BUDGET_EXHAUSTED.value
        assert final_public.world.current_version == 9
        assert final_public.world.decision_seq == 8
        assert final_public.world.dispatch_count == final_public.world.dispatch_limit_at == 8
        assert final_public.world.active_dispatch_count is None
        assert tuple(entry.commit_position.world_version for entry in entries) == tuple(
            range(2, 10)
        )
        assert tuple(entry.entry_kind for entry in entries) == (
            "dialogue",
            "dialogue",
            "session_transition",
            "behavior",
            "session_transition",
            "behavior",
            "behavior",
            "session_transition",
        )
        assert tuple(entry.transition_reason for entry in transition_entries) == (
            SessionTransitionReason.MERGE,
            SessionTransitionReason.TRANSFER,
            SessionTransitionReason.SPLIT,
        )
        assert len(transfer_links) == 2
        assert {link.relation_kind for link in transfer_links} == {EntryRelationKind.PREVIOUS}
        assert {link.relation_order for link in transfer_links} == {0, 1}
        assert len(split_links) == 1
        assert split_links[0].related_entry_id == transition_entries[1].entry_id

        assert requests == ()
        assert first_request.status is InteractionRequestStatus.RESOLVED
        assert first_request.resolution_entry_id == _required_entry_id(first_results[1])
        assert first_request.priority_consumed_dispatch_count == 2

        nodes = {node.agent_id: node for node in final_public.sessions}
        assert {
            agent_id: (node.root_session_id, node.topology_version, node.last_dispatch_count)
            for agent_id, node in nodes.items()
        } == {
            "anon": ("session-anon", 9, 5),
            "rana": ("session-anon", 9, 3),
            "soyo": ("session-soyo", 6, 6),
            "taki": ("session-taki", 1, 7),
            "tomori": ("session-tomori", 9, 8),
        }
        assert {item.state.agent_id: item.state_revision for item in personas} == {
            "anon": 3,
            "rana": 2,
            "soyo": 3,
            "taki": 3,
            "tomori": 2,
        }
        final_memory_sizes = {stream.agent_id: len(stream.records) for stream in streams}
        assert all(
            final_memory_sizes[agent_id] > initial_size
            for agent_id, initial_size in initial_memory_sizes.items()
        )
        soyo_memory = next(stream for stream in streams if stream.agent_id == "soyo")
        assert any(
            record.source_entry_id == _required_entry_id(first_results[0])
            for record in soyo_memory.records
        )
    finally:
        second_runner.close()
        reopened.dispose()


def _runner(
    database: ProjectDatabase,
    step: _UnusedStep | _FailingStep | _RecordingFailingStep,
    *,
    owner_id: str,
) -> WorldRunner:
    return WorldRunner(
        database=database,
        world_ref=WORLD_REF,
        character_step=cast(CharacterStep, step),
        owner_id=owner_id,
    )


def _execute_one(runner: WorldRunner, step: CharacterStep) -> CharacterStepResult:
    dispatch = runner.next_dispatch()
    assert dispatch is not None
    return step.run_dispatch(dispatch)


def _integration_step(
    database: ProjectDatabase,
    world_ref: WorldRef,
    loaded: object,
    harness: _IntegrationHarness,
    *,
    trace_log: LocalTrace | None = None,
) -> CharacterStep:
    from agent_runtime.bootstrap import LoadedWorld

    runtime = cast(LoadedWorld, loaded)
    return CharacterStep(
        database=database,
        world_ref=world_ref,
        specs=runtime.specs,
        object_seeds=runtime.object_seeds,
        strategy_factory=harness.factory,
        embedding_provider=_FixedEmbeddingProvider(),
        clock=lambda: NOW,
        trace_log=trace_log,
    )


def _required_entry_id(result: CharacterStepResult) -> str:
    entry_id = result.world_update.entry_id
    assert entry_id is not None
    return entry_id


def _database_with_world(
    tmp_path: Path,
    state: PublicWorldState,
) -> ProjectDatabase:
    database = open_project_database(tmp_path / ".runtime", PROJECT_ID, create=True)
    with database.session_factory.begin() as session:
        WorldStore(WORLD_REF).insert_initial(session, state)
    return database


def _state(partitions: tuple[tuple[str, ...], ...]) -> PublicWorldState:
    agent_ids = tuple(agent_id for partition in partitions for agent_id in partition)
    return PublicWorldState(
        world=WorldState(
            world_ref=WORLD_REF,
            seed_id="runner-seed",
            seed_version=1,
            seed_hash="b" * 64,
            current_version=1,
            world_time=NOW,
            status=WorldStatus.PAUSED,
            created_at=NOW,
        ),
        locations=(
            LocationState(
                world_ref=WORLD_REF,
                location_id="room",
                name="Room",
                description="One shared test room",
            ),
        ),
        agents=tuple(
            AgentWorldState(world_ref=WORLD_REF, agent_id=agent_id, location_id="room")
            for agent_id in agent_ids
        ),
        sessions=tuple(
            EventSessionNode(
                world_ref=WORLD_REF,
                session_id=f"session-{agent_id}",
                agent_id=agent_id,
                root_session_id=f"session-{partition[0]}",
                topology_version=1,
                updated_world_version=1,
            )
            for partition in partitions
            for agent_id in partition
        ),
    )


def _append_behavior(
    store: EventEntryStore,
    session: Session,
    *,
    agent_id: str,
    version: int,
) -> None:
    entry = BehaviorEntry(
        world_ref=WORLD_REF,
        entry_id=f"entry-{agent_id}-{version}",
        source_id=f"decision-{agent_id}-{version}",
        commit_position=CommitPosition(world_version=version),
        root_session_id_at_commit=f"session-{agent_id}",
        topology_version=1,
        actor_agent_id=agent_id,
        occurred_at=NOW,
        text=f"{agent_id} does something visible",
        created_at=NOW,
        operation_id="look-around",
    )
    store.append(
        session,
        entry,
        recipients=(
            EventEntryRecipient(
                world_ref=WORLD_REF,
                entry_id=entry.entry_id,
                agent_id=agent_id,
            ),
        ),
    )


def _integration_catalog() -> Catalog:
    skills = RuntimeSkillCatalog.load(REPOSITORY_ROOT / "content" / "skills")
    return Catalog(
        tools=(
            ToolDefinition(
                id="visible_location.query",
                version="1",
                mode=ToolMode.QUERY,
            ),
        ),
        prompts=(PromptDefinition(id="personact.v1", version="1", digest="prompt-v1"),),
        skills=skills.skills,
    )


def _utter(target_id: str, *, expects_response: bool) -> _DraftBuilder:
    def build(planning_input: ActionPlanningInput) -> ProposalDraft:
        affordance = _integration_affordance(
            planning_input,
            ProposalKind.UTTER,
            target_id=target_id,
            channel=DeliveryChannel.DIRECT,
        )
        return ProposalDraft(
            action=UtterAction(
                affordance_id=affordance.affordance_id,
                target=CharacterTarget(id=target_id),
                content="Would you like to keep talking?",
                expects_response=expects_response,
            )
        )

    return build


def _respond(target_id: str, content: str) -> _DraftBuilder:
    def build(planning_input: ActionPlanningInput) -> ProposalDraft:
        affordance = _integration_affordance(
            planning_input,
            ProposalKind.RESPOND,
            target_id=target_id,
        )
        assert affordance.request_entry_id is not None
        return ProposalDraft(
            action=RespondAction(
                affordance_id=affordance.affordance_id,
                target=CharacterTarget(id=target_id),
                content=content,
                in_reply_to_entry_id=affordance.request_entry_id,
            ),
            evidence_ids=(affordance.request_entry_id,),
        )

    return build


def _join(target_id: str) -> _DraftBuilder:
    def build(planning_input: ActionPlanningInput) -> ProposalDraft:
        affordance = _integration_affordance(
            planning_input,
            ProposalKind.INTERACT,
            target_id=target_id,
            operation_id=JOIN_TARGET_SESSION,
        )
        return ProposalDraft(
            action=InteractAction(
                affordance_id=affordance.affordance_id,
                target=CharacterTarget(id=target_id),
                description=f"Join {target_id}'s current conversation.",
            )
        )

    return build


def _leave(planning_input: ActionPlanningInput) -> ProposalDraft:
    affordance = _integration_affordance(
        planning_input,
        ProposalKind.ACT,
        operation_id=LEAVE_CURRENT_SESSION,
    )
    return ProposalDraft(
        action=ActAction(
            affordance_id=affordance.affordance_id,
            description="Leave the current conversation.",
        )
    )


def _self_behavior(planning_input: ActionPlanningInput) -> ProposalDraft:
    affordance = _integration_affordance(
        planning_input,
        ProposalKind.ACT,
        operation_id="adjust_posture",
    )
    return ProposalDraft(
        action=ActAction(
            affordance_id=affordance.affordance_id,
            description="Adjust posture while considering the conversation.",
        )
    )


def _planning_failure(_planning_input: ActionPlanningInput) -> ProposalDraft:
    raise PlanningError("private planning failure")


def _integration_affordance(
    planning_input: ActionPlanningInput,
    kind: ProposalKind,
    *,
    target_id: str | None = None,
    operation_id: str | None = None,
    channel: DeliveryChannel | None = None,
) -> Affordance:
    matches = tuple(
        item
        for item in planning_input.view.affordances
        if item.kind is kind
        and (target_id is None or (item.target is not None and item.target.id == target_id))
        and (operation_id is None or item.operation_id == operation_id)
        and (channel is None or item.delivery_channel is channel)
    )
    assert len(matches) == 1
    return matches[0]
