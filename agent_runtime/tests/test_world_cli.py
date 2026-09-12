"""Offline production-assembly tests for the bounded World CLI."""

from __future__ import annotations

import json
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Never

import pytest

import agent_runtime.world_cli as world_cli
from agent_runtime.model_gateway import FixtureModelGateway
from agent_runtime.sqlite import open_project_database
from agent_runtime.world.affordances import WorldAffordanceResolver
from agent_runtime.world.contracts import ProposalKind, WorldRef
from agent_runtime.world.entry_storage import EventEntryStore
from agent_runtime.world.state import WorldStatus
from agent_runtime.world.storage import WorldStore
from agent_runtime.world_cli import (
    FailedRunCommandResult,
    FailedWorldRunManifest,
    RunCommandResult,
    WorldRunManifest,
    run_world,
)

REPOSITORY_ROOT = Path(__file__).parents[2]
PROJECT_ID = "for-the-band"

_BLOCKING_FIXTURE_CLI = r"""
import sys
import time
from pathlib import Path

import agent_runtime.world_cli as world_cli

repository_root = Path(sys.argv[1])
project_id = sys.argv[2]
world_id = sys.argv[3]
ready_path = Path(sys.argv[4])
block_on_call = int(sys.argv[5])
output_directory = Path(sys.argv[6])


class BlockingFixtureStrategy(world_cli._FixtureCognitionStrategy):
    calls = 0

    def plan_action(self, planning_input):
        type(self).calls += 1
        if type(self).calls == block_on_call:
            ready_path.write_text(str(block_on_call), encoding="utf-8")
            while True:
                time.sleep(60)
        return super().plan_action(planning_input)


world_cli._FixtureCognitionStrategy = BlockingFixtureStrategy
raise SystemExit(
    world_cli.main(
        [
            "--repository-root",
            str(repository_root),
            "run",
            project_id,
            world_id,
            "--additional-decisions",
            str(block_on_call),
            "--fixture",
            "--output-directory",
            str(output_directory),
        ]
    )
)
"""


@pytest.fixture
def runtime_repository(tmp_path: Path) -> Path:
    project = tmp_path / "projects" / PROJECT_ID
    project.mkdir(parents=True)
    for filename in ("project.json", "agents.json", "scenario.yaml"):
        shutil.copy2(REPOSITORY_ROOT / "projects" / PROJECT_ID / filename, project)
    shutil.copytree(REPOSITORY_ROOT / "content", tmp_path / "content")
    return tmp_path


def test_fixture_run_creates_world_appends_quota_and_writes_artifacts(
    runtime_repository: Path,
) -> None:
    output = runtime_repository / "review" / "first-run"

    result = run_world(
        runtime_repository,
        PROJECT_ID,
        "fixture-world",
        additional_decisions=2,
        mode="fixture",
        output_directory=output,
    )

    assert result.manifest.created_world is True
    assert result.manifest.mode == "fixture"
    assert result.manifest.provider == "fixture"
    assert result.manifest.model_id == "fixture-cognition-v1"
    assert result.manifest.dispatch_count_before == 0
    assert result.manifest.dispatch_limit_before == 0
    assert result.manifest.additional_decisions == 2
    assert result.manifest.dispatch_count_after == 2
    assert result.manifest.dispatch_limit_after == 2
    assert result.manifest.successful_step_count == 2
    assert result.manifest.outcome == "completed"
    assert result.manifest.provider_call_limit is None
    assert result.manifest.provider_generate_count == 0
    assert result.manifest.final_status == "paused"
    assert result.manifest.stop_reason == "budget_exhausted"
    assert len(result.manifest.agents) == 5
    canonical_manifest = runtime_repository / result.manifest_path
    attempt_manifest = canonical_manifest.with_name(
        f"run_attempt-{result.manifest.attempt_id}.json"
    )
    assert canonical_manifest.is_file()
    assert attempt_manifest.is_file()
    assert canonical_manifest.read_bytes() == attempt_manifest.read_bytes()
    assert canonical_manifest.stat().st_mode & 0o777 == 0o600
    assert attempt_manifest.stat().st_mode & 0o777 == 0o600
    assert not tuple(output.glob(".*.tmp"))
    assert (runtime_repository / result.storyline_path).is_file()
    assert (runtime_repository / result.trace_path).is_file()

    persisted = WorldRunManifest.model_validate_json(
        canonical_manifest.read_text(encoding="utf-8"),
        strict=True,
    )
    assert persisted == result.manifest
    assert json.loads((runtime_repository / result.storyline_path).read_text())["worldRef"] == {
        "projectId": PROJECT_ID,
        "worldId": "fixture-world",
    }


def test_second_fixture_run_loads_world_and_appends_persisted_absolute_limit(
    runtime_repository: Path,
) -> None:
    first = run_world(
        runtime_repository,
        PROJECT_ID,
        "continued-world",
        additional_decisions=1,
        mode="fixture",
    )
    second = run_world(
        runtime_repository,
        PROJECT_ID,
        "continued-world",
        additional_decisions=2,
        mode="fixture",
    )

    assert first.manifest.dispatch_limit_after == 1
    first_attempt = (runtime_repository / first.manifest_path).with_name(
        f"run_attempt-{first.manifest.attempt_id}.json"
    )
    second_attempt = (runtime_repository / second.manifest_path).with_name(
        f"run_attempt-{second.manifest.attempt_id}.json"
    )
    assert first_attempt != second_attempt
    assert first_attempt.is_file()
    assert second_attempt.is_file()
    assert second.manifest.created_world is False
    assert second.manifest.dispatch_count_before == 1
    assert second.manifest.dispatch_limit_before == 1
    assert second.manifest.dispatch_count_after == 3
    assert second.manifest.dispatch_limit_after == 3
    assert second.manifest.final_decision_seq == 3

    database = open_project_database(runtime_repository / ".runtime", PROJECT_ID, create=False)
    try:
        with database.session_factory() as session:
            state = WorldStore(second.manifest.world_ref).load(session).world
    finally:
        database.dispose()
    assert state.status is WorldStatus.PAUSED
    assert state.dispatch_count == state.dispatch_limit_at == 3


def test_sigint_during_dispatch_durably_pauses_the_world(
    runtime_repository: Path,
) -> None:
    world_id = "sigint-world"
    ready_path = runtime_repository / "sigint-ready"
    process = _launch_blocking_fixture_run(
        runtime_repository,
        world_id=world_id,
        ready_path=ready_path,
        block_on_call=1,
    )
    try:
        _wait_for_ready(process, ready_path)
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=30)
    finally:
        _kill_if_running(process)

    assert process.returncode == 0
    assert stderr == ""
    result = RunCommandResult.model_validate_json(stdout, strict=True)
    assert result.manifest.final_status is WorldStatus.PAUSED
    assert result.manifest.stop_reason == "interrupted"
    assert result.manifest.dispatch_count_after == 1
    assert result.manifest.dispatch_limit_after == 1
    assert result.manifest.successful_step_count == 0
    assert result.manifest.final_world_version == 1
    assert result.manifest.final_decision_seq == 0

    database = open_project_database(runtime_repository / ".runtime", PROJECT_ID, create=False)
    try:
        world_ref = WorldRef(project_id=PROJECT_ID, world_id=world_id)
        with database.session_factory() as session:
            state = WorldStore(world_ref).load(session).world
            entries = EventEntryStore(world_ref).list_all(session)
    finally:
        database.dispose()
    assert state.status is WorldStatus.PAUSED
    assert state.stop_reason == "interrupted"
    assert state.stopped_at_world_version == 1
    assert state.dispatch_count == state.dispatch_limit_at == 1
    assert state.active_dispatch_count is None
    assert state.active_dispatch_agent_id is None
    assert state.run_owner_id is None
    assert state.current_version == 1
    assert state.decision_seq == 0
    assert entries == ()


def test_sigkill_releases_world_lock_and_takeover_does_not_duplicate_commit(
    runtime_repository: Path,
) -> None:
    world_id = "sigkill-world"
    world_ref = WorldRef(project_id=PROJECT_ID, world_id=world_id)
    ready_path = runtime_repository / "sigkill-ready"
    process = _launch_blocking_fixture_run(
        runtime_repository,
        world_id=world_id,
        ready_path=ready_path,
        block_on_call=2,
    )
    try:
        _wait_for_ready(process, ready_path)
        database = open_project_database(
            runtime_repository / ".runtime",
            PROJECT_ID,
            create=False,
        )
        try:
            with database.session_factory() as session:
                running = WorldStore(world_ref).load(session).world
                entries_before_kill = EventEntryStore(world_ref).list_all(session)
        finally:
            database.dispose()

        assert running.status is WorldStatus.RUNNING
        assert running.dispatch_count == running.dispatch_limit_at == 2
        assert running.active_dispatch_count == 2
        assert running.active_dispatch_agent_id is not None
        assert running.current_version == 2
        assert running.decision_seq == 1
        assert len(entries_before_kill) == 1
        first_entry = entries_before_kill[0]
        assert first_entry.commit_position.world_version == 2

        process.kill()
        process.communicate(timeout=30)
        assert process.returncode == -signal.SIGKILL

        resumed = subprocess.run(
            [
                sys.executable,
                "-m",
                "agent_runtime.world_cli",
                "--repository-root",
                str(runtime_repository),
                "run",
                PROJECT_ID,
                world_id,
                "--additional-decisions",
                "1",
                "--fixture",
                "--output-directory",
                str(runtime_repository / "review" / "sigkill-takeover"),
            ],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    finally:
        _kill_if_running(process)

    assert resumed.returncode == 0, resumed.stderr
    assert resumed.stderr == ""
    result = RunCommandResult.model_validate_json(resumed.stdout, strict=True)
    assert result.manifest.created_world is False
    assert result.manifest.dispatch_count_before == 2
    assert result.manifest.dispatch_limit_before == 2
    assert result.manifest.dispatch_count_after == 3
    assert result.manifest.dispatch_limit_after == 3
    assert result.manifest.successful_step_count == 1
    assert result.manifest.final_status is WorldStatus.PAUSED
    assert result.manifest.stop_reason == "budget_exhausted"
    assert result.manifest.final_world_version == 3
    assert result.manifest.final_decision_seq == 2

    database = open_project_database(runtime_repository / ".runtime", PROJECT_ID, create=False)
    try:
        with database.session_factory() as session:
            final_state = WorldStore(world_ref).load(session).world
            entries_after_takeover = EventEntryStore(world_ref).list_all(session)
    finally:
        database.dispose()
    assert final_state.status is WorldStatus.PAUSED
    assert final_state.dispatch_count == final_state.dispatch_limit_at == 3
    assert final_state.active_dispatch_count is None
    assert final_state.active_dispatch_agent_id is None
    assert final_state.run_owner_id is None
    assert tuple(entry.commit_position.world_version for entry in entries_after_takeover) == (2, 3)
    assert sum(entry.entry_id == first_entry.entry_id for entry in entries_after_takeover) == 1
    assert len({entry.entry_id for entry in entries_after_takeover}) == 2
    assert len({entry.source_id for entry in entries_after_takeover}) == 2


def test_model_cli_uses_ark_by_default_and_assembles_gateway_without_network(
    runtime_repository: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    world_ref = WorldRef(project_id=PROJECT_ID, world_id="model-world")
    affordance = next(
        item
        for item in WorldAffordanceResolver(
            world_ref=world_ref,
            world_version=1,
            agent_id="anon",
        ).behavior_affordances()
        if item.kind is ProposalKind.ACT
    )
    gateway = FixtureModelGateway(
        (
            '{"score":1.0}',
            '{"items":[]}',
            (
                '{"action":{"kind":"act","affordanceId":"'
                f"{affordance.affordance_id}"
                '","description":"观察房间。"},"evidenceIds":[]}'
            ),
        ),
        model_id="offline-model",
    )
    factory_calls: list[str] = []

    def gateway_factory(*, model_id: str) -> FixtureModelGateway:
        factory_calls.append(model_id)
        return gateway

    monkeypatch.setenv("ARK_ENDPOINT_ID", "offline-model")
    monkeypatch.setattr(world_cli, "create_ark_gateway", gateway_factory)

    exit_code = world_cli.main(
        [
            "--repository-root",
            str(runtime_repository),
            "run",
            PROJECT_ID,
            world_ref.world_id,
            "--additional-decisions",
            "1",
            "--model",
            "--provider-call-limit",
            "3",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    result = RunCommandResult.model_validate_json(captured.out, strict=True)
    assert result.manifest.mode == "model"
    assert result.manifest.provider == "ark"
    assert result.manifest.model_id == "offline-model"
    assert result.manifest.provider_call_limit == 3
    assert result.manifest.provider_generate_count == 3
    assert result.manifest.successful_agent_ids == ("anon",)
    assert tuple(request.call_kind for request in gateway.requests) == (
        "score_poignancy",
        "plan",
        "plan_action",
    )
    assert factory_calls == ["offline-model"]


def test_budget_failure_writes_redacted_evidence_without_overwriting_success(
    runtime_repository: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    world_id = "budget-evidence"
    world_ref = WorldRef(project_id=PROJECT_ID, world_id=world_id)
    affordance = next(
        item
        for item in WorldAffordanceResolver(
            world_ref=world_ref,
            world_version=1,
            agent_id="anon",
        ).behavior_affordances()
        if item.kind is ProposalKind.ACT
    )
    successful_gateway = FixtureModelGateway(
        (
            '{"score":1.0}',
            '{"items":[]}',
            (
                '{"action":{"kind":"act","affordanceId":"'
                f"{affordance.affordance_id}"
                '","description":"观察房间。"},"evidenceIds":[]}'
            ),
        ),
        model_id="offline-model",
    )
    exhausted_gateway = FixtureModelGateway(
        ('{"score":1.0}', '{"items":[]}'),
        model_id="offline-model",
    )
    gateways = iter((successful_gateway, exhausted_gateway))

    def gateway_factory(*, model_id: str) -> FixtureModelGateway:
        assert model_id == "offline-model"
        return next(gateways)

    monkeypatch.setattr(world_cli, "create_ark_gateway", gateway_factory)
    command = [
        "--repository-root",
        str(runtime_repository),
        "run",
        PROJECT_ID,
        world_id,
        "--additional-decisions",
        "1",
        "--model",
        "offline-model",
    ]

    assert world_cli.main([*command, "--provider-call-limit", "3"]) == 0
    completed = RunCommandResult.model_validate_json(capsys.readouterr().out, strict=True)
    completed_manifest = runtime_repository / completed.manifest_path
    completed_bytes = completed_manifest.read_bytes()
    completed_attempt = completed_manifest.with_name(
        f"run_attempt-{completed.manifest.attempt_id}.json"
    )
    completed_storyline = runtime_repository / completed.storyline_path
    completed_storyline_bytes = completed_storyline.read_bytes()
    assert completed_attempt.read_bytes() == completed_bytes

    assert world_cli.main([*command, "--provider-call-limit", "2"]) == 2
    captured = capsys.readouterr()
    failed = FailedRunCommandResult.model_validate_json(captured.out, strict=True)
    assert captured.err == "World command failed (ModelCallBudgetExceededError).\n"
    assert failed.manifest.outcome == "failed"
    assert failed.manifest.error_type == "ModelCallBudgetExceededError"
    assert failed.manifest.provider_call_limit == 2
    assert failed.manifest.provider_generate_count == 2
    assert failed.manifest.world_state is not None
    assert failed.manifest.world_state.status is WorldStatus.PAUSED
    assert failed.manifest.world_state.stop_reason == "interrupted"
    assert failed.storyline_path is not None
    assert (runtime_repository / failed.storyline_path).is_file()
    assert failed.manifest_path != completed.manifest_path
    assert completed_manifest.read_bytes() == completed_bytes
    assert completed_attempt.read_bytes() == completed_bytes
    assert completed_storyline.read_bytes() == completed_storyline_bytes
    assert len(exhausted_gateway.requests) == 2

    persisted = FailedWorldRunManifest.model_validate_json(
        (runtime_repository / failed.manifest_path).read_text(encoding="utf-8"),
        strict=True,
    )
    assert persisted == failed.manifest


def test_fixture_status_and_story_commands_never_construct_provider(
    runtime_repository: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def unexpected_provider(*, model_id: str) -> Never:
        raise AssertionError(f"Provider must not be constructed for {model_id}")

    monkeypatch.setattr(world_cli, "create_ark_gateway", unexpected_provider)
    monkeypatch.setattr(world_cli, "create_deepseek_gateway", unexpected_provider)
    common = ["--repository-root", str(runtime_repository)]

    assert world_cli.main([*common, "create", PROJECT_ID, "created-world"]) == 0
    created = json.loads(capsys.readouterr().out)
    assert created["command"] == "create"
    assert created["state"]["status"] == "paused"

    assert world_cli.main([*common, "pause", PROJECT_ID, "created-world"]) == 0
    paused = json.loads(capsys.readouterr().out)
    assert paused["command"] == "pause"
    assert paused["state"]["status"] == "paused"

    assert (
        world_cli.main(
            [
                *common,
                "run",
                PROJECT_ID,
                "offline-world",
                "--additional-decisions",
                "1",
                "--fixture",
            ]
        )
        == 0
    )
    run_output = RunCommandResult.model_validate_json(capsys.readouterr().out, strict=True)
    assert run_output.manifest.mode == "fixture"

    assert world_cli.main([*common, "status", PROJECT_ID, "offline-world"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["command"] == "status"
    assert status["state"]["status"] == "paused"

    story_output = runtime_repository / "review" / "story-only"
    assert (
        world_cli.main(
            [
                *common,
                "story",
                PROJECT_ID,
                "offline-world",
                "--output-directory",
                str(story_output),
            ]
        )
        == 0
    )
    story = json.loads(capsys.readouterr().out)
    assert story["command"] == "story"
    assert (runtime_repository / story["storylinePath"]).is_file()


def test_cli_rejects_non_positive_quota_before_provider_creation(
    runtime_repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_provider(*, model_id: str) -> Never:
        raise AssertionError(f"Provider must not be constructed for {model_id}")

    monkeypatch.setattr(world_cli, "create_ark_gateway", unexpected_provider)
    monkeypatch.setattr(world_cli, "create_deepseek_gateway", unexpected_provider)
    with pytest.raises(SystemExit) as raised:
        world_cli.main(
            [
                "--repository-root",
                str(runtime_repository),
                "run",
                PROJECT_ID,
                "bad-budget",
                "--additional-decisions",
                "0",
                "--model",
                "offline-model",
            ]
        )
    assert raised.value.code == 2
    assert not (runtime_repository / ".runtime").exists()


@pytest.mark.parametrize(
    "mode_arguments",
    [
        ("--model", "offline-model"),
        ("--fixture", "--provider-call-limit", "1"),
    ],
)
def test_cli_rejects_missing_or_fixture_provider_budget_before_provider_creation(
    runtime_repository: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode_arguments: tuple[str, ...],
) -> None:
    def unexpected_provider(*, model_id: str) -> Never:
        raise AssertionError(f"Provider must not be constructed for {model_id}")

    monkeypatch.setattr(world_cli, "create_ark_gateway", unexpected_provider)
    monkeypatch.setattr(world_cli, "create_deepseek_gateway", unexpected_provider)
    exit_code = world_cli.main(
        [
            "--repository-root",
            str(runtime_repository),
            "run",
            PROJECT_ID,
            "bad-provider-budget",
            "--additional-decisions",
            "1",
            *mode_arguments,
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert captured.err == "World command failed (ValueError).\n"
    assert not (runtime_repository / ".runtime").exists()


def test_artifact_writes_reject_symlinks_and_parent_traversal(
    runtime_repository: Path,
) -> None:
    target = runtime_repository / "outside.json"
    target.write_text("do-not-overwrite", encoding="utf-8")
    output = runtime_repository / "review" / "symlink-manifest"
    output.mkdir(parents=True)
    manifest_link = output / "run_manifest.json"
    manifest_link.symlink_to(target)

    with pytest.raises(ValueError, match="manifest output cannot be a symlink"):
        run_world(
            runtime_repository,
            PROJECT_ID,
            "symlink-manifest",
            additional_decisions=1,
            mode="fixture",
            output_directory=output,
        )
    assert target.read_text(encoding="utf-8") == "do-not-overwrite"

    real_directory = runtime_repository / "real"
    real_directory.mkdir()
    linked_directory = runtime_repository / "linked"
    linked_directory.symlink_to(real_directory, target_is_directory=True)
    with pytest.raises(ValueError, match="cannot traverse symlinks"):
        run_world(
            runtime_repository,
            PROJECT_ID,
            "symlink-directory",
            additional_decisions=1,
            mode="fixture",
            output_directory=linked_directory / "review",
        )


def _launch_blocking_fixture_run(
    repository_root: Path,
    *,
    world_id: str,
    ready_path: Path,
    block_on_call: int,
) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            _BLOCKING_FIXTURE_CLI,
            str(repository_root),
            PROJECT_ID,
            world_id,
            str(ready_path),
            str(block_on_call),
            str(repository_root / "review" / f"{world_id}-blocked"),
        ],
        cwd=REPOSITORY_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _wait_for_ready(
    process: subprocess.Popen[str],
    ready_path: Path,
    *,
    timeout: float = 30,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ready_path.is_file():
            return
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            pytest.fail(
                "blocking World CLI exited before its dispatch checkpoint: "
                f"returncode={process.returncode}, stdout={stdout!r}, stderr={stderr!r}"
            )
        time.sleep(0.02)
    pytest.fail("blocking World CLI did not reach its dispatch checkpoint")


def _kill_if_running(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.kill()
        process.communicate(timeout=30)
