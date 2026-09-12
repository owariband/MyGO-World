"""Thin production assembly and CLI for one bounded Agent World run."""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Callable, Sequence
from importlib import import_module
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

from agent_runtime.agent.memory import EmbeddingProvider
from agent_runtime.agent.personact.compiler import (
    Catalog,
    CompiledPersonActSpec,
    PromptDefinition,
    ToolDefinition,
    ToolMode,
)
from agent_runtime.agent.personact.loop import (
    ActionPlanningInput,
    CognitionStrategy,
    PlanDraft,
    PlanningInput,
)
from agent_runtime.agent.personact.model_strategy import ModelCognitionStrategy
from agent_runtime.agent.personact.proposal import ProposalDraft
from agent_runtime.agent.skill import RuntimeSkillCatalog
from agent_runtime.bootstrap import (
    LoadedWorld,
    WorldAlreadyExistsError,
    WorldNotFoundError,
    WorldStatusError,
    create_world,
    load_world_for_resume,
)
from agent_runtime.event.character_step import CharacterStep
from agent_runtime.event.runner import (
    DispatchFailure,
    WorldAlreadyRunningError,
    WorldRunner,
    WorldRunResult,
    WorldStopReason,
    pause_world,
)
from agent_runtime.model import StrictModel
from agent_runtime.model_budget import BudgetedModelGateway, ModelCallBudgetExceededError
from agent_runtime.model_gateway import ModelGateway, ModelRequestRejectedError
from agent_runtime.model_provider import create_ark_gateway, create_deepseek_gateway
from agent_runtime.sqlite import (
    ProjectDatabase,
    ProjectDatabaseNotFoundError,
    open_project_database,
)
from agent_runtime.trace import LocalTrace
from agent_runtime.world.affordances import LEAVE_CURRENT_SESSION
from agent_runtime.world.contracts import ActAction, ProposalKind, WorldRef
from agent_runtime.world.state import WorldState, WorldStatus
from agent_runtime.world.storage import WorldCommitConflictError, WorldStore
from agent_runtime.world.storage import WorldNotFoundError as StoredWorldNotFoundError

RunMode = Literal["fixture", "model"]
ProviderName = Literal["fixture", "ark", "deepseek", "injected"]
StorylineExporter = Callable[[ProjectDatabase, WorldRef, Path], None]
_FIXTURE_MODEL_ID = "fixture-cognition-v1"


class StorylineUnavailableError(RuntimeError):
    """The committed StoryLine exporter has not been installed or produced output."""


class RunAgentIdentity(StrictModel):
    agent_id: str
    spec_digest: str
    skill_id: str
    skill_version: str
    skill_content_hash: str


class WorldRunAttemptMetadata(StrictModel):
    """Shared, non-secret identity for one bounded Runner invocation."""

    format_version: Literal[1] = 1
    attempt_id: str
    world_ref: WorldRef
    seed_hash: str
    mode: RunMode
    provider: ProviderName
    model_id: str
    agents: tuple[RunAgentIdentity, ...]
    created_world: bool
    dispatch_count_before: int
    dispatch_limit_before: int
    additional_decisions: int
    provider_call_limit: int | None
    provider_generate_count: int


class WorldRunManifest(WorldRunAttemptMetadata):
    """Evidence for one completed bounded Runner invocation."""

    outcome: Literal["completed"] = "completed"
    dispatch_count_after: int
    dispatch_limit_after: int
    successful_step_count: int
    successful_agent_ids: tuple[str, ...]
    failed_dispatches: tuple[DispatchFailure, ...]
    final_world_version: int
    final_decision_seq: int
    final_status: WorldStatus
    stop_reason: str | None
    trace_path: str
    storyline_path: str


class FailedWorldRunManifest(WorldRunAttemptMetadata):
    """Recoverable, redacted evidence for one failed Runner invocation."""

    outcome: Literal["failed"] = "failed"
    error_type: str
    world_state: WorldState | None
    trace_path: str
    storyline_path: str | None


class RunCommandResult(StrictModel):
    command: Literal["run"] = "run"
    manifest_path: str
    storyline_path: str
    trace_path: str
    manifest: WorldRunManifest


class FailedRunCommandResult(StrictModel):
    command: Literal["run"] = "run"
    manifest_path: str
    storyline_path: str | None
    trace_path: str
    manifest: FailedWorldRunManifest


class WorldRunAttemptFailedError(RuntimeError):
    """One failed invocation whose redacted evidence was persisted."""

    def __init__(self, result: FailedRunCommandResult, *, exit_code: int) -> None:
        super().__init__("World run failed; redacted attempt evidence was persisted")
        self.result = result
        self.exit_code = exit_code


class CreateCommandResult(StrictModel):
    command: Literal["create"] = "create"
    state: WorldState


class StateCommandResult(StrictModel):
    command: Literal["pause", "status"]
    state: WorldState


class StoryCommandResult(StrictModel):
    command: Literal["story"] = "story"
    world_ref: WorldRef
    storyline_path: str


class _EmptyEmbeddingProvider:
    """MVP placeholder until a project configures semantic embeddings."""

    def embed(self, text: str) -> tuple[float, ...]:
        del text
        return ()


class _FixtureCognitionStrategy:
    """Deterministic production-side fixture; it never constructs a Provider."""

    def score_poignancy(
        self,
        spec: CompiledPersonActSpec,
        candidate: object,
        *,
        world_ref: WorldRef,
    ) -> float:
        del spec, candidate, world_ref
        return 1.0

    def plan(self, planning_input: PlanningInput) -> PlanDraft:
        del planning_input
        return PlanDraft(items=())

    def plan_action(self, planning_input: ActionPlanningInput) -> ProposalDraft:
        affordance = next(
            item
            for item in planning_input.view.affordances
            if item.kind is ProposalKind.ACT and item.operation_id != LEAVE_CURRENT_SESSION
        )
        return ProposalDraft(
            action=ActAction(
                affordance_id=affordance.affordance_id,
                description="观察当前环境并整理眼前的线索。",
            )
        )


def run_world(
    repository_root: Path,
    project_id: str,
    world_id: str,
    *,
    additional_decisions: int,
    mode: RunMode,
    provider: ProviderName | None = None,
    model_id: str | None = None,
    gateway: ModelGateway | None = None,
    provider_call_limit: int | None = None,
    output_directory: Path | None = None,
    storyline_exporter: StorylineExporter | None = None,
    debug_trace: bool = False,
) -> RunCommandResult:
    """Create or load one World, run an appended quota, and export review artifacts."""

    if isinstance(additional_decisions, bool) or additional_decisions < 1:
        raise ValueError("additional_decisions must be positive")
    if mode == "model" and (
        gateway is None or model_id is None or not model_id.strip() or provider_call_limit is None
    ):
        raise ValueError(
            "model mode requires one configured gateway, model_id, and provider_call_limit"
        )
    if provider_call_limit is not None and (
        isinstance(provider_call_limit, bool) or provider_call_limit < 1
    ):
        raise ValueError("provider_call_limit must be positive")
    if mode == "fixture" and (
        gateway is not None
        or model_id is not None
        or provider_call_limit is not None
        or provider not in {None, "fixture"}
    ):
        raise ValueError("fixture mode cannot carry Provider configuration")
    if mode == "model" and provider == "fixture":
        raise ValueError("model mode cannot use the fixture Provider")

    root = repository_root.resolve()
    catalog, skills = _catalog(root)
    loaded, created_world = _load_or_create_world(root, project_id, world_id, catalog)
    world_ref = loaded.world_ref
    database = open_project_database(root / ".runtime", project_id, create=False)
    artifact_directory = _artifact_directory(
        root,
        world_ref,
        output_directory=output_directory,
    )
    attempt_id = uuid4().hex
    story_path = artifact_directory / "storyline.json"
    story_candidate_path = artifact_directory / f".storyline-{attempt_id}.json"
    failed_story_path = artifact_directory / f"storyline-failed-{attempt_id}.json"
    attempt_manifest_path = artifact_directory / f"run_attempt-{attempt_id}.json"
    canonical_manifest_path = artifact_directory / "run_manifest.json"
    trace = LocalTrace(root / "projects", world_ref, debug=debug_trace)
    budgeted_gateway = (
        BudgetedModelGateway(
            gateway,
            call_limit=provider_call_limit,
            on_exhausted=lambda: _fence_exhausted_budget(
                database,
                world_ref,
            ),
        )
        if gateway is not None and provider_call_limit is not None
        else None
    )
    strategy_factory = _strategy_factory(
        mode=mode,
        gateway=budgeted_gateway,
        model_id=model_id,
        skills=skills,
    )
    before = loaded.public_state.world
    metadata = WorldRunAttemptMetadata(
        attempt_id=attempt_id,
        world_ref=world_ref,
        seed_hash=before.seed_hash,
        mode=mode,
        provider=provider or ("fixture" if mode == "fixture" else "injected"),
        model_id=model_id or _FIXTURE_MODEL_ID,
        agents=_agent_identities(loaded),
        created_world=created_world,
        dispatch_count_before=before.dispatch_count,
        dispatch_limit_before=before.dispatch_limit_at,
        additional_decisions=additional_decisions,
        provider_call_limit=provider_call_limit,
        provider_generate_count=0,
    )
    runner: WorldRunner | None = None
    try:
        try:
            step = CharacterStep(
                database=database,
                world_ref=world_ref,
                specs=loaded.specs,
                object_seeds=loaded.object_seeds,
                strategy_factory=strategy_factory,
                embedding_provider=cast(EmbeddingProvider, _EmptyEmbeddingProvider()),
                trace_log=trace,
            )
            runner = WorldRunner(
                database=database,
                world_ref=world_ref,
                character_step=step,
                trace_log=trace,
            )
            with trace, runner:
                runner.resume(additional_decisions=additional_decisions)
                try:
                    run_result = runner.run()
                    _raise_if_provider_budget_exhausted(budgeted_gateway)
                except BaseException:
                    _pause_after_failure(runner)
                    raise
            _export_storyline(storyline_exporter, database, world_ref, story_candidate_path)
            _publish_output(story_candidate_path, story_path)
        except Exception as error:
            story_candidate_path.unlink(missing_ok=True)
            provider_generate_count = (
                budgeted_gateway.generate_count if budgeted_gateway is not None else 0
            )
            failure_storyline = _try_export_storyline(
                storyline_exporter,
                database,
                world_ref,
                failed_story_path,
            )
            failed_manifest = FailedWorldRunManifest.model_validate(
                {
                    **metadata.model_dump(mode="python", by_alias=False),
                    "provider_generate_count": provider_generate_count,
                    "outcome": "failed",
                    "error_type": type(error).__name__,
                    "world_state": _read_world_state(database, world_ref),
                    "trace_path": _portable_path(root, trace.path),
                    "storyline_path": (
                        _portable_path(root, failure_storyline)
                        if failure_storyline is not None
                        else None
                    ),
                },
                strict=True,
            )
            _write_json(attempt_manifest_path, failed_manifest)
            failed_result = FailedRunCommandResult(
                manifest_path=_portable_path(root, attempt_manifest_path),
                storyline_path=failed_manifest.storyline_path,
                trace_path=failed_manifest.trace_path,
                manifest=failed_manifest,
            )
            raise WorldRunAttemptFailedError(
                failed_result,
                exit_code=_error_exit_code(error),
            ) from error

        provider_generate_count = (
            budgeted_gateway.generate_count if budgeted_gateway is not None else 0
        )
        trace_path = _portable_path(root, trace.path)
        portable_story_path = _portable_path(root, story_path)
        manifest = _completed_manifest(
            metadata,
            run_result,
            provider_generate_count=provider_generate_count,
            trace_path=trace_path,
            storyline_path=portable_story_path,
        )
        _write_json(attempt_manifest_path, manifest)
        _write_json(canonical_manifest_path, manifest, replace=True)
        return RunCommandResult(
            manifest_path=_portable_path(root, canonical_manifest_path),
            storyline_path=portable_story_path,
            trace_path=trace_path,
            manifest=manifest,
        )
    finally:
        trace.close()
        if runner is not None:
            runner.close()
        database.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    repository_root = cast(Path, arguments.repository_root).resolve()
    try:
        if arguments.command == "create":
            result: StrictModel = _create_command(
                repository_root,
                cast(str, arguments.project),
                cast(str, arguments.world),
            )
        elif arguments.command == "run":
            fixture = cast(bool, arguments.fixture)
            selected_model = cast(str | None, arguments.model)
            selected_provider = cast(Literal["ark", "deepseek"], arguments.provider)
            provider_call_limit = cast(int | None, arguments.provider_call_limit)
            gateway, provider_call_limit, configured_model_id = _provider_configuration(
                fixture=fixture,
                provider=selected_provider,
                model_id=selected_model,
                call_limit=provider_call_limit,
            )
            result = run_world(
                repository_root,
                cast(str, arguments.project),
                cast(str, arguments.world),
                additional_decisions=cast(int, arguments.additional_decisions),
                mode="fixture" if fixture else "model",
                provider=None if fixture else selected_provider,
                model_id=configured_model_id,
                gateway=gateway,
                provider_call_limit=provider_call_limit,
                output_directory=cast(Path | None, arguments.output_directory),
                debug_trace=cast(bool, arguments.debug),
            )
        elif arguments.command == "pause":
            result = _pause_command(
                repository_root,
                cast(str, arguments.project),
                cast(str, arguments.world),
            )
        elif arguments.command == "status":
            result = _status_command(
                repository_root,
                cast(str, arguments.project),
                cast(str, arguments.world),
            )
        else:
            result = _story_command(
                repository_root,
                cast(str, arguments.project),
                cast(str, arguments.world),
                output_directory=cast(Path | None, arguments.output_directory),
            )
    except WorldRunAttemptFailedError as error:
        sys.stdout.write(error.result.model_dump_json(by_alias=True, indent=2) + "\n")
        _safe_error_type(error.result.manifest.error_type)
        return error.exit_code
    except Exception as error:
        _safe_error(error)
        return _error_exit_code(error)
    sys.stdout.write(result.model_dump_json(by_alias=True, indent=2) + "\n")
    return 0


def _create_command(repository_root: Path, project_id: str, world_id: str) -> CreateCommandResult:
    catalog, _ = _catalog(repository_root)
    loaded = create_world(repository_root, project_id, world_id, catalog=catalog)
    return CreateCommandResult(state=loaded.public_state.world)


def _pause_command(repository_root: Path, project_id: str, world_id: str) -> StateCommandResult:
    world_ref = WorldRef(project_id=project_id, world_id=world_id)
    database = open_project_database(repository_root / ".runtime", project_id, create=False)
    try:
        state = pause_world(database, world_ref)
    finally:
        database.dispose()
    return StateCommandResult(command="pause", state=state)


def _status_command(repository_root: Path, project_id: str, world_id: str) -> StateCommandResult:
    world_ref = WorldRef(project_id=project_id, world_id=world_id)
    database = open_project_database(repository_root / ".runtime", project_id, create=False)
    try:
        with database.session_factory() as session:
            state = WorldStore(world_ref).load(session).world
    finally:
        database.dispose()
    return StateCommandResult(command="status", state=state)


def _story_command(
    repository_root: Path,
    project_id: str,
    world_id: str,
    *,
    output_directory: Path | None,
) -> StoryCommandResult:
    world_ref = WorldRef(project_id=project_id, world_id=world_id)
    database = open_project_database(repository_root / ".runtime", project_id, create=False)
    story_path = (
        _artifact_directory(
            repository_root,
            world_ref,
            output_directory=output_directory,
        )
        / "storyline.json"
    )
    try:
        _load_storyline_exporter()(database, world_ref, story_path)
    finally:
        database.dispose()
    if not story_path.is_file():
        raise StorylineUnavailableError("StoryLine exporter did not create its output")
    return StoryCommandResult(
        world_ref=world_ref,
        storyline_path=_portable_path(repository_root, story_path),
    )


def _catalog(repository_root: Path) -> tuple[Catalog, RuntimeSkillCatalog]:
    skills = RuntimeSkillCatalog.load(repository_root / "content" / "skills")
    return (
        Catalog(
            tools=(
                ToolDefinition(
                    id="visible_location.query",
                    version="1",
                    mode=ToolMode.QUERY,
                ),
            ),
            prompts=(PromptDefinition(id="personact.v1", version="1", digest="prompt-v1"),),
            skills=skills.skills,
        ),
        skills,
    )


def _load_or_create_world(
    repository_root: Path,
    project_id: str,
    world_id: str,
    catalog: Catalog,
) -> tuple[LoadedWorld, bool]:
    try:
        return (
            load_world_for_resume(
                repository_root,
                project_id,
                world_id,
                catalog=catalog,
            ),
            False,
        )
    except WorldNotFoundError:
        return (
            create_world(
                repository_root,
                project_id,
                world_id,
                catalog=catalog,
            ),
            True,
        )


def _strategy_factory(
    *,
    mode: RunMode,
    gateway: ModelGateway | None,
    model_id: str | None,
    skills: RuntimeSkillCatalog,
) -> Callable[[CompiledPersonActSpec], CognitionStrategy]:
    if mode == "fixture":
        return lambda _spec: _FixtureCognitionStrategy()
    if gateway is None or model_id is None:
        raise ValueError("model strategy requires Provider configuration")

    def build(spec: CompiledPersonActSpec) -> CognitionStrategy:
        skill = skills.resolve(
            spec.character_skill.skill_id,
            spec.character_skill.version,
            agent_kind="character",
        )
        strategy = ModelCognitionStrategy(
            gateway=gateway,
            skill=skill,
            model_id=model_id,
            call_id_generator=lambda: f"model-call-{uuid4().hex}",
        )
        return strategy

    return build


def _provider_configuration(
    *,
    fixture: bool,
    provider: Literal["ark", "deepseek"],
    model_id: str | None,
    call_limit: int | None,
) -> tuple[ModelGateway | None, int | None, str | None]:
    if fixture:
        if call_limit is not None:
            raise ValueError("fixture mode cannot set --provider-call-limit")
        return None, None, None
    if model_id is None:
        raise ValueError("model mode requires --model")
    if call_limit is None:
        raise ValueError("model mode requires --provider-call-limit")
    if provider == "ark":
        configured_model_id = model_id.strip() or os.environ.get("ARK_ENDPOINT_ID", "").strip()
        if not configured_model_id:
            raise ModelRequestRejectedError("ARK_ENDPOINT_ID is required")
        return (
            create_ark_gateway(model_id=configured_model_id),
            call_limit,
            configured_model_id,
        )
    configured_model_id = model_id.strip()
    if not configured_model_id:
        raise ValueError("DeepSeek model mode requires --model MODEL_ID")
    return create_deepseek_gateway(model_id=configured_model_id), call_limit, configured_model_id


def _raise_if_provider_budget_exhausted(gateway: BudgetedModelGateway | None) -> None:
    if gateway is not None and gateway.exhausted:
        raise ModelCallBudgetExceededError("Provider generate budget exhausted")


def _agent_identities(loaded: LoadedWorld) -> tuple[RunAgentIdentity, ...]:
    return tuple(
        RunAgentIdentity(
            agent_id=spec.agent_id,
            spec_digest=spec.digest,
            skill_id=spec.character_skill.skill_id,
            skill_version=spec.character_skill.version,
            skill_content_hash=spec.character_skill.content_hash,
        )
        for spec in loaded.specs
    )


def _completed_manifest(
    metadata: WorldRunAttemptMetadata,
    run_result: WorldRunResult,
    *,
    provider_generate_count: int,
    trace_path: str,
    storyline_path: str,
) -> WorldRunManifest:
    return WorldRunManifest.model_validate(
        {
            **metadata.model_dump(mode="python", by_alias=False),
            "provider_generate_count": provider_generate_count,
            "outcome": "completed",
            "dispatch_count_after": run_result.final_state.dispatch_count,
            "dispatch_limit_after": run_result.final_state.dispatch_limit_at,
            "successful_step_count": len(run_result.successful_steps),
            "successful_agent_ids": tuple(
                sorted({result.agent_id for result in run_result.successful_steps})
            ),
            "failed_dispatches": run_result.failed_dispatches,
            "final_world_version": run_result.final_state.current_version,
            "final_decision_seq": run_result.final_state.decision_seq,
            "final_status": run_result.final_state.status,
            "stop_reason": run_result.final_state.stop_reason,
            "trace_path": trace_path,
            "storyline_path": storyline_path,
        },
        strict=True,
    )


def _pause_after_failure(runner: WorldRunner) -> None:
    try:
        if runner.state().status is WorldStatus.RUNNING:
            runner.pause_and_save(reason=WorldStopReason.INTERRUPTED)
    except Exception:
        # The original runtime failure remains authoritative. The failure
        # manifest reads whatever durable state is still available below.
        pass


def _fence_exhausted_budget(database: ProjectDatabase, world_ref: WorldRef) -> None:
    pause_world(database, world_ref, reason=WorldStopReason.INTERRUPTED)


def _read_world_state(database: ProjectDatabase, world_ref: WorldRef) -> WorldState | None:
    try:
        with database.session_factory() as session:
            return WorldStore(world_ref).load(session).world
    except Exception:
        return None


def _try_export_storyline(
    exporter: StorylineExporter | None,
    database: ProjectDatabase,
    world_ref: WorldRef,
    path: Path,
) -> Path | None:
    try:
        _export_storyline(exporter, database, world_ref, path)
    except Exception:
        path.unlink(missing_ok=True)
        return None
    return path


def _export_storyline(
    exporter: StorylineExporter | None,
    database: ProjectDatabase,
    world_ref: WorldRef,
    path: Path,
) -> None:
    if path.is_symlink():
        raise ValueError("StoryLine output cannot be a symlink")
    (exporter or _load_storyline_exporter())(database, world_ref, path)
    if not path.is_file():
        raise StorylineUnavailableError("StoryLine exporter did not create its output")


def _load_storyline_exporter() -> StorylineExporter:
    try:
        module = import_module("agent_runtime.event.story_line")
        exporter = module.export_storyline
    except (ImportError, AttributeError) as error:
        raise StorylineUnavailableError(
            "agent_runtime.event.story_line.export_storyline is unavailable"
        ) from error
    if not callable(exporter):
        raise StorylineUnavailableError("StoryLine exporter is not callable")
    return cast(StorylineExporter, exporter)


def _artifact_directory(
    repository_root: Path,
    world_ref: WorldRef,
    *,
    output_directory: Path | None,
) -> Path:
    path = (
        repository_root / "artifacts" / world_ref.project_id / _safe_component(world_ref.world_id)
        if output_directory is None
        else output_directory
    )
    if path.is_symlink():
        raise ValueError("artifact output directory cannot be a symlink")
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise ValueError("artifact output path is not a directory")
    absolute = path.absolute()
    resolved = path.resolve()
    if absolute != resolved:
        raise ValueError("artifact output directory cannot traverse symlinks or parent segments")
    return resolved


def _safe_component(value: str) -> str:
    return value if re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value) else f"~{value.encode().hex()}"


def _portable_path(repository_root: Path, path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(repository_root.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def _publish_output(candidate: Path, target: Path) -> None:
    if target.is_symlink():
        raise ValueError("canonical output cannot be a symlink")
    os.replace(candidate, target)


def _write_json(path: Path, value: StrictModel, *, replace: bool = False) -> None:
    try:
        path.lstat()
    except FileNotFoundError:
        pass
    else:
        if path.is_symlink():
            raise ValueError("manifest output cannot be a symlink")
        if not replace:
            raise FileExistsError("attempt manifest already exists")

    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(value.model_dump_json(by_alias=True, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _safe_error(error: BaseException) -> None:
    _safe_error_type(type(error).__name__)


def _safe_error_type(error_type: str) -> None:
    sys.stderr.write(f"World command failed ({error_type}).\n")


def _error_exit_code(error: Exception) -> int:
    if isinstance(
        error,
        (
            WorldAlreadyExistsError,
            WorldAlreadyRunningError,
            WorldCommitConflictError,
            WorldStatusError,
        ),
    ):
        return 3
    if isinstance(
        error,
        (
            ValueError,
            ModelRequestRejectedError,
            ProjectDatabaseNotFoundError,
            StoredWorldNotFoundError,
            StorylineUnavailableError,
            WorldNotFoundError,
        ),
    ):
        return 2
    return 1


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create, run, pause, and inspect an Agent World.")
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root containing projects/, content/, .runtime/, and artifacts/.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="Create one paused World without a Provider.")
    _world_arguments(create)

    run = commands.add_parser("run", help="Create or load and run one bounded World quota.")
    _world_arguments(run)
    run.add_argument("--additional-decisions", type=_positive_int, required=True)
    modes = run.add_mutually_exclusive_group(required=True)
    modes.add_argument("--fixture", action="store_true", help="Use deterministic local cognition.")
    modes.add_argument(
        "--model",
        nargs="?",
        const="",
        metavar="MODEL_ID",
        help="Use a Provider; Ark defaults to ARK_ENDPOINT_ID when MODEL_ID is omitted.",
    )
    run.add_argument(
        "--provider",
        choices=("ark", "deepseek"),
        default="ark",
        help="Model Provider used with --model (default: ark).",
    )
    run.add_argument(
        "--provider-call-limit",
        type=_positive_int,
        help="Required in model mode; hard cap for shared logical Gateway generate calls.",
    )
    run.add_argument("--output-directory", type=Path)
    run.add_argument("--debug", action="store_true", help="Include private content in local trace.")

    pause = commands.add_parser("pause", help="Fence a running World at a commit boundary.")
    _world_arguments(pause)

    status = commands.add_parser("status", help="Read public World status without a Provider.")
    _world_arguments(status)

    story = commands.add_parser("story", help="Export committed public StoryLine JSON.")
    _world_arguments(story)
    story.add_argument("--output-directory", type=Path)
    return parser


def _world_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("project")
    parser.add_argument("world")


if __name__ == "__main__":
    raise SystemExit(main())
