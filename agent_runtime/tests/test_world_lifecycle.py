"""M4 World ownership, pause/resume, and local run-lock tests."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import text

from agent_runtime.agent.personact.compiler import (
    Catalog,
    PromptDefinition,
    ToolDefinition,
    ToolMode,
)
from agent_runtime.agent.skill import RuntimeSkillCatalog
from agent_runtime.bootstrap import (
    WorldStatusError,
    create_world,
    load_world,
    load_world_for_resume,
)
from agent_runtime.event.character_step import CharacterStep
from agent_runtime.event.runner import (
    WorldAlreadyRunningError,
    WorldRunner,
    WorldStopReason,
    pause_world,
)
from agent_runtime.sqlite import ProjectDatabase, open_project_database
from agent_runtime.world.contracts import WorldRef
from agent_runtime.world.state import (
    AgentWorldState,
    EventSessionNode,
    LocationState,
    PublicWorldState,
    WorldState,
    WorldStatus,
)
from agent_runtime.world.storage import WorldCommitConflictError, WorldStore

REPOSITORY_ROOT = Path(__file__).parents[2]
PROJECT_ID = "lifecycle-tests"
WORLD_REF = WorldRef(project_id=PROJECT_ID, world_id="save-001")
NOW = datetime(2026, 9, 11, 16, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class _NoCallStep:
    world_ref: WorldRef


def test_file_lock_blocks_a_second_runner_then_stale_takeover_fences_old_work(
    tmp_path: Path,
) -> None:
    database = _database_with_world(tmp_path, WORLD_REF)
    first = _runner(database, WORLD_REF, owner_id="owner-one")
    second = _runner(database, WORLD_REF, owner_id="owner-two")
    try:
        first_state = first.resume(additional_decisions=2)
        stale_dispatch = first.next_dispatch()
        assert stale_dispatch is not None

        with pytest.raises(WorldAlreadyRunningError):
            second.resume(additional_decisions=3)

        first.close()
        taken_over = second.resume(additional_decisions=3)
        assert taken_over.status is WorldStatus.RUNNING
        assert taken_over.run_owner_id == "owner-two"
        assert taken_over.control_epoch == first_state.control_epoch + 1
        assert taken_over.dispatch_count == stale_dispatch.dispatch_count == 1
        assert taken_over.dispatch_limit_at == 5
        assert taken_over.active_dispatch_count is None
        assert taken_over.active_dispatch_agent_id is None

        with (
            pytest.raises(WorldCommitConflictError),
            database.session_factory.begin() as session,
        ):
            WorldStore(WORLD_REF).compare_and_advance(
                session,
                expected_version=stale_dispatch.based_on_world_version,
                expected_control_epoch=stale_dispatch.control_epoch,
                expected_decision_seq=stale_dispatch.based_on_decision_seq,
                advances_world=True,
                dispatch_count=stale_dispatch.dispatch_count,
                run_owner_id=stale_dispatch.owner_id,
                dispatch_agent_id=stale_dispatch.agent_id,
            )
    finally:
        first.close()
        second.close()
        database.dispose()


def test_external_pause_is_durable_idempotent_and_rejects_a_late_outcome(
    tmp_path: Path,
) -> None:
    database = _database_with_world(tmp_path, WORLD_REF)
    runner = _runner(database, WORLD_REF, owner_id="owner-pause")
    try:
        runner.resume(additional_decisions=2)
        dispatch = runner.next_dispatch()
        assert dispatch is not None

        paused = pause_world(database, WORLD_REF, reason=WorldStopReason.USER_PAUSE)
        paused_again = pause_world(database, WORLD_REF, reason=WorldStopReason.INTERRUPTED)
        assert paused.status is WorldStatus.PAUSED
        assert paused.stop_reason == WorldStopReason.USER_PAUSE.value
        assert paused.stopped_at_world_version == paused.current_version
        assert paused_again == paused
        assert paused.control_epoch == dispatch.control_epoch + 1
        assert paused.active_dispatch_count is None

        with (
            pytest.raises(WorldCommitConflictError),
            database.session_factory.begin() as session,
        ):
            WorldStore(WORLD_REF).compare_and_advance(
                session,
                expected_version=dispatch.based_on_world_version,
                expected_control_epoch=dispatch.control_epoch,
                expected_decision_seq=dispatch.based_on_decision_seq,
                advances_world=True,
                dispatch_count=dispatch.dispatch_count,
                run_owner_id=dispatch.owner_id,
                dispatch_agent_id=dispatch.agent_id,
            )

        with database.session_factory() as session:
            assert WorldStore(WORLD_REF).load(session).world == paused
    finally:
        runner.close()
        database.dispose()


def test_project_databases_do_not_share_world_run_locks(tmp_path: Path) -> None:
    first_ref = WorldRef(project_id="project-one", world_id="same-save")
    second_ref = WorldRef(project_id="project-two", world_id="same-save")
    first_database = _database_with_world(tmp_path / "one", first_ref)
    second_database = _database_with_world(tmp_path / "two", second_ref)
    first = _runner(first_database, first_ref, owner_id="owner-one")
    second = _runner(second_database, second_ref, owner_id="owner-two")
    try:
        assert first.resume(additional_decisions=1).status is WorldStatus.RUNNING
        assert second.resume(additional_decisions=1).status is WorldStatus.RUNNING
    finally:
        first.close()
        second.close()
        first_database.dispose()
        second_database.dispose()


def test_resume_loader_accepts_stale_running_but_neither_loader_accepts_ended(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    project_dir = repository / "projects" / "for-the-band"
    project_dir.mkdir(parents=True)
    for filename in ("project.json", "agents.json", "scenario.yaml"):
        shutil.copy2(REPOSITORY_ROOT / "projects" / "for-the-band" / filename, project_dir)
    catalog = _catalog()
    create_world(repository, "for-the-band", "stale-running", catalog=catalog)
    create_world(repository, "for-the-band", "ended-save", catalog=catalog)
    database = open_project_database(repository / ".runtime", "for-the-band", create=False)
    try:
        with database.session_factory.begin() as session:
            session.execute(
                text(
                    "UPDATE worlds SET status = 'running', run_owner_id = 'dead-owner' "
                    "WHERE world_id = 'stale-running'"
                )
            )
            session.execute(
                text("UPDATE worlds SET status = 'ended' WHERE world_id = 'ended-save'")
            )
    finally:
        database.dispose()

    with pytest.raises(WorldStatusError, match="only load paused"):
        load_world(repository, "for-the-band", "stale-running", catalog=catalog)
    resumed_input = load_world_for_resume(
        repository,
        "for-the-band",
        "stale-running",
        catalog=catalog,
    )
    assert resumed_input.public_state.world.status is WorldStatus.RUNNING
    assert resumed_input.public_state.world.run_owner_id == "dead-owner"

    with pytest.raises(WorldStatusError, match="ended"):
        load_world(repository, "for-the-band", "ended-save", catalog=catalog)
    with pytest.raises(WorldStatusError, match="ended"):
        load_world_for_resume(repository, "for-the-band", "ended-save", catalog=catalog)


def _runner(
    database: ProjectDatabase,
    world_ref: WorldRef,
    *,
    owner_id: str,
) -> WorldRunner:
    return WorldRunner(
        database=database,
        world_ref=world_ref,
        character_step=cast(CharacterStep, _NoCallStep(world_ref)),
        owner_id=owner_id,
    )


def _database_with_world(tmp_path: Path, world_ref: WorldRef) -> ProjectDatabase:
    database = open_project_database(tmp_path / ".runtime", world_ref.project_id, create=True)
    with database.session_factory.begin() as session:
        WorldStore(world_ref).insert_initial(session, _state(world_ref))
    return database


def _state(world_ref: WorldRef) -> PublicWorldState:
    return PublicWorldState(
        world=WorldState(
            world_ref=world_ref,
            seed_id="lifecycle-seed",
            seed_version=1,
            seed_hash="c" * 64,
            current_version=1,
            world_time=NOW,
            status=WorldStatus.PAUSED,
            created_at=NOW,
        ),
        locations=(
            LocationState(
                world_ref=world_ref,
                location_id="room",
                name="Room",
                description="One lifecycle test room",
            ),
        ),
        agents=(AgentWorldState(world_ref=world_ref, agent_id="anon", location_id="room"),),
        sessions=(
            EventSessionNode(
                world_ref=world_ref,
                session_id="session-anon",
                agent_id="anon",
                root_session_id="session-anon",
                topology_version=1,
                updated_world_version=1,
            ),
        ),
    )


def _catalog() -> Catalog:
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
