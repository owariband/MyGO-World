from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from pydantic import ValidationError

from mygo_world.canonical import canonical_json, sha256_bytes, sha256_text
from mygo_world.contracts import (
    AssetEntry,
    AssetManifest,
    BroadcastEvent,
    BroadcastPlan,
    CompiledRender,
    DialogueBeat,
    Live2DModelAsset,
    RenderJob,
    ValidationDiagnostic,
)
from mygo_world.errors import WorldError


def load_asset_manifest(path: Path) -> AssetManifest:
    try:
        raw = yaml.safe_load(path.read_bytes())
    except (OSError, yaml.YAMLError) as exc:
        raise WorldError(
            "ASSET_MANIFEST_INVALID", f"Cannot read Asset Manifest '{path}': {exc}"
        ) from exc
    try:
        return AssetManifest.model_validate(raw)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in exc.errors()
        )
        raise WorldError("ASSET_MANIFEST_INVALID", details) from exc


def validate_asset_manifest_files(manifest: AssetManifest, webgal_root: Path) -> None:
    """Validate every whitelisted Live asset before a paid Provider call."""

    planner = RenderPlanner()
    diagnostics: list[ValidationDiagnostic] = []
    for index, entry in enumerate(manifest.backgrounds):
        planner._asset_path(
            entry,
            asset_id=entry.asset_id,
            category="background",
            webgal_root=webgal_root,
            path=f"backgrounds.{index}",
            diagnostics=diagnostics,
        )
    for index, entry in enumerate(manifest.bgms):
        planner._asset_path(
            entry,
            asset_id=entry.asset_id,
            category="bgm",
            webgal_root=webgal_root,
            path=f"bgms.{index}",
            diagnostics=diagnostics,
        )
    for index, model in enumerate(manifest.live2d_models):
        planner._validate_model_capabilities(
            model,
            motion=None,
            expression=None,
            entrance_effect=None,
            webgal_root=webgal_root,
            path=f"live2d_models.{index}",
            diagnostics=diagnostics,
        )
        for motion in model.motions:
            planner._validate_model_capabilities(
                model,
                motion=motion,
                expression=None,
                entrance_effect=None,
                webgal_root=webgal_root,
                path=f"live2d_models.{index}",
                diagnostics=diagnostics,
            )
        for expression in model.expressions:
            planner._validate_model_capabilities(
                model,
                motion=None,
                expression=expression,
                entrance_effect=None,
                webgal_root=webgal_root,
                path=f"live2d_models.{index}",
                diagnostics=diagnostics,
            )
    if diagnostics:
        raise RenderPlanInvalid(diagnostics)


class RenderPlanInvalid(WorldError):
    def __init__(self, diagnostics: list[ValidationDiagnostic]) -> None:
        self.diagnostics = tuple(diagnostics)
        summary = "; ".join(f"{item.code}@{item.path}" for item in diagnostics)
        super().__init__("RENDER_PLAN_INVALID", summary)


def _diagnostic(code: str, path: str, message: str) -> ValidationDiagnostic:
    return ValidationDiagnostic(code=code, path=path, message=message)


class RenderPlanner:
    def __init__(
        self, *, max_beats: int = 40, max_estimated_play_ms: int = 8 * 60 * 1000
    ) -> None:
        self._max_beats = max_beats
        self._max_estimated_play_ms = max_estimated_play_ms

    def plan(
        self,
        plan: BroadcastPlan,
        *,
        world_id: str,
        target_world_version: int,
        events: list[BroadcastEvent],
        frontier_event_ids: set[str],
        manifest: AssetManifest,
        webgal_root: Path,
    ) -> list[RenderJob]:
        diagnostics: list[ValidationDiagnostic] = []
        if plan.world_id != world_id:
            diagnostics.append(
                _diagnostic(
                    "BROADCAST_WORLD_MISMATCH",
                    "world_id",
                    "Broadcast Plan does not target the requested World",
                )
            )
        if plan.target_world_version != target_world_version:
            diagnostics.append(
                _diagnostic(
                    "BROADCAST_VERSION_MISMATCH",
                    "target_world_version",
                    "Broadcast Plan does not target the fixed World Version",
                )
            )

        event_by_id = {event.event_id: event for event in events}
        disposition_ids = [item.event_id for item in plan.dispositions]
        if len(disposition_ids) != len(set(disposition_ids)):
            diagnostics.append(
                _diagnostic(
                    "BROADCAST_DISPOSITION_DUPLICATE",
                    "dispositions",
                    "Each frontier Event must have exactly one disposition",
                )
            )
        if set(disposition_ids) != frontier_event_ids:
            diagnostics.append(
                _diagnostic(
                    "BROADCAST_DISPOSITION_INCOMPLETE",
                    "dispositions",
                    "Dispositions must cover the complete unprocessed Event frontier",
                )
            )

        included = {
            item.event_id for item in plan.dispositions if item.status == "included"
        }
        omitted = {
            item.event_id for item in plan.dispositions if item.status == "omitted"
        }
        referenced: set[str] = set()
        render_jobs: list[RenderJob] = []
        render_ids = [render.render_id for render in plan.renders]
        if len(render_ids) != len(set(render_ids)):
            diagnostics.append(
                _diagnostic(
                    "RENDER_ID_DUPLICATE",
                    "renders",
                    "Render IDs must be unique within a Broadcast Plan",
                )
            )

        backgrounds = {item.asset_id: item for item in manifest.backgrounds}
        bgms = {item.asset_id: item for item in manifest.bgms}
        models = {item.asset_id: item for item in manifest.live2d_models}

        for render_index, render in enumerate(plan.renders):
            render_path = f"renders.{render_index}"
            if len(render.beats) > self._max_beats:
                diagnostics.append(
                    _diagnostic(
                        "RENDER_BEAT_LIMIT_EXCEEDED",
                        f"{render_path}.beats",
                        f"Render exceeds the {self._max_beats} Beat limit",
                    )
                )
            if render.estimated_play_ms > self._max_estimated_play_ms:
                diagnostics.append(
                    _diagnostic(
                        "RENDER_DURATION_LIMIT_EXCEEDED",
                        f"{render_path}.estimated_play_ms",
                        "Render exceeds the estimated playback duration limit",
                    )
                )

            beat_ids = [beat.beat_id for beat in render.beats]
            if len(beat_ids) != len(set(beat_ids)):
                diagnostics.append(
                    _diagnostic(
                        "BEAT_ID_DUPLICATE",
                        f"{render_path}.beats",
                        "Beat IDs must be unique within a Render",
                    )
                )

            has_background = False
            has_audio_state = False
            visible: dict[str, tuple[str, Live2DModelAsset]] = {}
            occupied: dict[str, str] = {}
            resolved_beats: list[dict[str, Any]] = []
            render_references: set[str] = set()

            for beat_index, beat in enumerate(render.beats):
                beat_path = f"{render_path}.beats.{beat_index}"
                sources = set(beat.source_event_ids)
                render_references.update(sources)
                referenced.update(sources)
                for source_id in sources:
                    source = event_by_id.get(source_id)
                    if source is None or source.world_version > target_world_version:
                        diagnostics.append(
                            _diagnostic(
                                "BEAT_SOURCE_INVALID",
                                f"{beat_path}.source_event_ids",
                                f"Beat references unavailable Event '{source_id}'",
                            )
                        )

                resolved = beat.model_dump(mode="json")
                if beat.type == "background":
                    entry = backgrounds.get(beat.asset_id)
                    asset_path = self._asset_path(
                        entry,
                        asset_id=beat.asset_id,
                        category="background",
                        webgal_root=webgal_root,
                        path=f"{beat_path}.asset_id",
                        diagnostics=diagnostics,
                    )
                    if asset_path is not None:
                        resolved["asset_path"] = asset_path
                    has_background = True
                elif beat.type == "bgm":
                    entry = bgms.get(beat.asset_id)
                    asset_path = self._asset_path(
                        entry,
                        asset_id=beat.asset_id,
                        category="bgm",
                        webgal_root=webgal_root,
                        path=f"{beat_path}.asset_id",
                        diagnostics=diagnostics,
                    )
                    if asset_path is not None:
                        resolved["asset_path"] = asset_path
                    has_audio_state = True
                elif beat.type == "stop_bgm":
                    has_audio_state = True
                elif beat.type == "show":
                    model = models.get(beat.model_asset_id)
                    model_path = self._model_path(
                        model,
                        asset_id=beat.model_asset_id,
                        character_id=beat.character_id,
                        motion=beat.motion,
                        expression=beat.expression,
                        entrance_effect=beat.entrance_effect,
                        webgal_root=webgal_root,
                        path=beat_path,
                        diagnostics=diagnostics,
                    )
                    previous = occupied.get(beat.position)
                    if previous is not None:
                        visible.pop(previous, None)
                    if model is not None:
                        visible[beat.character_id] = (beat.position, model)
                        occupied[beat.position] = beat.character_id
                        resolved["display_name"] = model.display_name
                    if model_path is not None:
                        resolved["model_path"] = model_path
                elif beat.type == "hide":
                    character_id = occupied.pop(beat.position, None)
                    if character_id is not None:
                        visible.pop(character_id, None)
                elif beat.type == "dialogue":
                    self._validate_content_stage(
                        has_background,
                        has_audio_state,
                        path=beat_path,
                        diagnostics=diagnostics,
                    )
                    state = visible.get(beat.character_id)
                    if state is None:
                        diagnostics.append(
                            _diagnostic(
                                "DIALOGUE_CHARACTER_NOT_VISIBLE",
                                f"{beat_path}.character_id",
                                "Dialogue Character must be shown earlier in this Render",
                            )
                        )
                    else:
                        position, model = state
                        if (
                            beat.model_asset_id is not None
                            and beat.model_asset_id != model.asset_id
                        ):
                            diagnostics.append(
                                _diagnostic(
                                    "DIALOGUE_MODEL_MISMATCH",
                                    f"{beat_path}.model_asset_id",
                                    "Dialogue model must match the visible Character model",
                                )
                            )
                        self._validate_model_capabilities(
                            model,
                            motion=beat.motion,
                            expression=beat.expression,
                            entrance_effect=beat.entrance_effect,
                            webgal_root=webgal_root,
                            path=beat_path,
                            diagnostics=diagnostics,
                        )
                        resolved.update(
                            {
                                "position": position,
                                "model_path": model.path,
                                "display_name": model.display_name,
                            }
                        )
                    self._validate_dialogue(
                        beat,
                        event_by_id=event_by_id,
                        path=beat_path,
                        diagnostics=diagnostics,
                    )
                elif beat.type == "narration":
                    self._validate_content_stage(
                        has_background,
                        has_audio_state,
                        path=beat_path,
                        diagnostics=diagnostics,
                    )
                resolved_beats.append(resolved)

            if not has_background:
                diagnostics.append(
                    _diagnostic(
                        "RENDER_BACKGROUND_MISSING",
                        f"{render_path}.beats",
                        "A self-contained Render must establish a background",
                    )
                )
            if not has_audio_state:
                diagnostics.append(
                    _diagnostic(
                        "RENDER_AUDIO_STATE_MISSING",
                        f"{render_path}.beats",
                        "A self-contained Render must start or explicitly stop BGM",
                    )
                )
            if not (render_references & included):
                diagnostics.append(
                    _diagnostic(
                        "RENDER_HAS_NO_NEW_EVENT",
                        f"{render_path}.beats",
                        "Each Render must cite at least one newly included Event",
                    )
                )

            render_jobs.append(
                RenderJob(
                    world_id=world_id,
                    target_world_version=target_world_version,
                    render_id=render.render_id,
                    title=render.title,
                    estimated_play_ms=render.estimated_play_ms,
                    beats=resolved_beats,
                )
            )

        for event_id in included - referenced:
            diagnostics.append(
                _diagnostic(
                    "INCLUDED_EVENT_NOT_REFERENCED",
                    "dispositions",
                    f"Included Event '{event_id}' is not cited by a Beat",
                )
            )
        for event_id in omitted & referenced:
            diagnostics.append(
                _diagnostic(
                    "OMITTED_EVENT_REFERENCED",
                    "dispositions",
                    f"Omitted Event '{event_id}' is cited by a Beat",
                )
            )
        if included and not plan.renders:
            diagnostics.append(
                _diagnostic(
                    "INCLUDED_EVENT_HAS_NO_RENDER",
                    "renders",
                    "Included Events require at least one Render",
                )
            )

        if diagnostics:
            raise RenderPlanInvalid(diagnostics)
        return render_jobs

    def _validate_content_stage(
        self,
        has_background: bool,
        has_audio_state: bool,
        *,
        path: str,
        diagnostics: list[ValidationDiagnostic],
    ) -> None:
        if not has_background:
            diagnostics.append(
                _diagnostic(
                    "CONTENT_BEFORE_BACKGROUND",
                    path,
                    "Dialogue and narration require an established background",
                )
            )
        if not has_audio_state:
            diagnostics.append(
                _diagnostic(
                    "CONTENT_BEFORE_AUDIO_STATE",
                    path,
                    "Dialogue and narration require an explicit BGM state",
                )
            )

    def _validate_dialogue(
        self,
        beat: DialogueBeat,
        *,
        event_by_id: dict[str, BroadcastEvent],
        path: str,
        diagnostics: list[ValidationDiagnostic],
    ) -> None:
        if len(beat.source_event_ids) != 1:
            diagnostics.append(
                _diagnostic(
                    "DIALOGUE_SOURCE_COUNT_INVALID",
                    f"{path}.source_event_ids",
                    "Dialogue must cite exactly one committed utterance Event",
                )
            )
            return
        source = event_by_id.get(beat.source_event_ids[0])
        if source is None:
            return
        if source.event_type != "utterance" or source.actor_id != beat.character_id:
            diagnostics.append(
                _diagnostic(
                    "DIALOGUE_SOURCE_INVALID",
                    f"{path}.source_event_ids",
                    "Dialogue source must be the Character's committed utterance Event",
                )
            )
        if source.fact.get("text") != beat.text:
            diagnostics.append(
                _diagnostic(
                    "DIALOGUE_TEXT_CHANGED",
                    f"{path}.text",
                    "Dialogue must preserve the committed utterance text exactly",
                )
            )

    def _asset_path(
        self,
        entry: AssetEntry | None,
        *,
        asset_id: str,
        category: str,
        webgal_root: Path,
        path: str,
        diagnostics: list[ValidationDiagnostic],
    ) -> str | None:
        if entry is None:
            diagnostics.append(
                _diagnostic(
                    "ASSET_NOT_WHITELISTED",
                    path,
                    f"Asset '{asset_id}' is not in the Asset Manifest",
                )
            )
            return None
        relative = self._safe_relative(entry.path, path=path, diagnostics=diagnostics)
        if relative is None:
            return None
        category_root = (webgal_root / "game" / category).resolve()
        full_path = (category_root / relative).resolve()
        if not full_path.is_relative_to(category_root):
            diagnostics.append(
                _diagnostic(
                    "ASSET_PATH_INVALID",
                    path,
                    "Manifest asset resolves outside its WebGAL category root",
                )
            )
            return None
        if not full_path.is_file():
            diagnostics.append(
                _diagnostic(
                    "ASSET_FILE_MISSING",
                    path,
                    f"Manifest asset file is missing: game/{category}/{entry.path}",
                )
            )
        return entry.path

    def _model_path(
        self,
        model: Live2DModelAsset | None,
        *,
        asset_id: str,
        character_id: str,
        motion: str | None,
        expression: str | None,
        entrance_effect: str | None,
        webgal_root: Path,
        path: str,
        diagnostics: list[ValidationDiagnostic],
    ) -> str | None:
        if model is None:
            diagnostics.append(
                _diagnostic(
                    "ASSET_NOT_WHITELISTED",
                    f"{path}.model_asset_id",
                    f"Live2D model '{asset_id}' is not in the Asset Manifest",
                )
            )
            return None
        if model.character_id != character_id:
            diagnostics.append(
                _diagnostic(
                    "MODEL_CHARACTER_MISMATCH",
                    f"{path}.model_asset_id",
                    "Live2D model is not approved for this Character",
                )
            )
        self._validate_model_capabilities(
            model,
            motion=motion,
            expression=expression,
            entrance_effect=entrance_effect,
            webgal_root=webgal_root,
            path=path,
            diagnostics=diagnostics,
        )
        return model.path

    def _validate_model_capabilities(
        self,
        model: Live2DModelAsset,
        *,
        motion: str | None,
        expression: str | None,
        entrance_effect: str | None,
        webgal_root: Path,
        path: str,
        diagnostics: list[ValidationDiagnostic],
    ) -> None:
        relative = self._safe_relative(
            model.path, path=f"{path}.model_asset_id", diagnostics=diagnostics
        )
        if relative is None:
            return
        figure_root = (webgal_root / "game" / "figure").resolve()
        model_path = (figure_root / relative).resolve()
        if not model_path.is_relative_to(figure_root):
            diagnostics.append(
                _diagnostic(
                    "ASSET_PATH_INVALID",
                    f"{path}.model_asset_id",
                    "Live2D model resolves outside the WebGAL figure root",
                )
            )
            return
        try:
            metadata = json.loads(model_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            diagnostics.append(
                _diagnostic(
                    "LIVE2D_METADATA_INVALID",
                    f"{path}.model_asset_id",
                    f"Cannot read Live2D model metadata: {exc}",
                )
            )
            return
        if not isinstance(metadata, dict):
            diagnostics.append(
                _diagnostic(
                    "LIVE2D_METADATA_INVALID",
                    f"{path}.model_asset_id",
                    "Live2D model metadata root must be an object",
                )
            )
            return
        actual_motions, actual_expressions = self._metadata_capabilities(metadata)
        if motion and (motion not in model.motions or motion not in actual_motions):
            diagnostics.append(
                _diagnostic(
                    "LIVE2D_MOTION_INVALID",
                    f"{path}.motion",
                    f"Motion '{motion}' is not allowed by the Manifest and model metadata",
                )
            )
        if expression and (
            expression not in model.expressions or expression not in actual_expressions
        ):
            diagnostics.append(
                _diagnostic(
                    "LIVE2D_EXPRESSION_INVALID",
                    f"{path}.expression",
                    f"Expression '{expression}' is not allowed by the Manifest and model metadata",
                )
            )
        if entrance_effect and entrance_effect not in model.entrance_effects:
            diagnostics.append(
                _diagnostic(
                    "ENTRANCE_EFFECT_INVALID",
                    f"{path}.entrance_effect",
                    f"Entrance effect '{entrance_effect}' is not allowed by the Manifest",
                )
            )

    def _safe_relative(
        self,
        value: str,
        *,
        path: str,
        diagnostics: list[ValidationDiagnostic],
    ) -> Path | None:
        candidate = PurePosixPath(value)
        if candidate.is_absolute() or ".." in candidate.parts:
            diagnostics.append(
                _diagnostic(
                    "ASSET_PATH_INVALID",
                    path,
                    "Manifest paths must be relative and cannot contain '..'",
                )
            )
            return None
        return Path(*candidate.parts)

    def _metadata_capabilities(
        self, metadata: dict[str, Any]
    ) -> tuple[set[str], set[str]]:
        references = metadata.get("FileReferences", metadata.get("fileReferences", {}))
        if not isinstance(references, dict):
            references = {}
        motions_value = metadata.get(
            "motions", references.get("Motions", references.get("motions", {}))
        )
        expressions_value = metadata.get(
            "expressions",
            references.get("Expressions", references.get("expressions", [])),
        )
        motions = set(motions_value) if isinstance(motions_value, dict) else set()
        expressions = {
            str(item.get("Name", item.get("name")))
            for item in expressions_value
            if isinstance(item, dict) and item.get("Name", item.get("name"))
        }
        return motions, expressions


class RenderCompiler:
    """Closed, deterministic Python templates for the eight supported Beat kinds."""

    def compile(self, job: RenderJob) -> CompiledRender:
        lines = [
            f"; Render {job.world_id}/{job.render_id} at World Version {job.target_world_version}.",
            "; Generated by mygo-world; do not hand-edit this immutable scene.",
        ]
        for beat in job.beats:
            beat_type = beat["type"]
            if beat_type == "chapter":
                subtitle = (
                    f"|{self._escape_content(beat['subtitle'])}"
                    if beat.get("subtitle")
                    else ""
                )
                lines.append(f"intro:{self._escape_content(beat['title'])}{subtitle};")
            elif beat_type == "bgm":
                lines.append(
                    f"bgm:{self._escape_content(beat['asset_path'])} "
                    f"-volume={beat['volume']} -enter={beat['fade_ms']};"
                )
            elif beat_type == "stop_bgm":
                lines.append(f"bgm:none -enter={beat['fade_ms']};")
            elif beat_type == "background":
                lines.append(
                    f"changeBg:{self._escape_content(beat['asset_path'])} -next;"
                )
            elif beat_type == "show":
                lines.append(self._figure_line(beat))
            elif beat_type == "hide":
                position = self._position_arg(beat["position"])
                lines.append(f"changeFigure:none{position} -next;")
            elif beat_type == "dialogue":
                if (
                    beat.get("motion")
                    or beat.get("expression")
                    or beat.get("entrance_effect")
                ):
                    lines.append(self._figure_line(beat))
                lines.append(
                    f"{self._escape_speaker(beat['display_name'])}:"
                    f"{self._escape_content(beat['text'])};"
                )
            elif beat_type == "narration":
                lines.append(f":{self._escape_content(beat['text'])};")
            else:
                raise WorldError(
                    "RENDER_BEAT_UNSUPPORTED", f"Unsupported Beat type '{beat_type}'"
                )
        script = "\n".join(lines).strip() + "\n"
        return CompiledRender(job=job, script=script, content_hash=sha256_text(script))

    def _figure_line(self, beat: dict[str, Any]) -> str:
        arguments: list[str] = []
        if beat.get("entrance_effect"):
            arguments.append(f"-enter={self._escape_arg(beat['entrance_effect'])}")
        if beat.get("expression"):
            arguments.append(f"-expression={self._escape_arg(beat['expression'])}")
        if beat.get("motion"):
            arguments.append(f"-motion={self._escape_arg(beat['motion'])}")
        position = self._position_arg(beat["position"])
        if position:
            arguments.append(position.strip())
        arguments.append("-next")
        return (
            f"changeFigure:{self._escape_content(beat['model_path'])} "
            f"{' '.join(arguments)};"
        )

    def _position_arg(self, position: str) -> str:
        if position == "left":
            return " -left"
        if position == "right":
            return " -right"
        return ""

    def _escape_content(self, value: Any) -> str:
        return str(value).replace("\\", "\\\\").replace(";", "\\;")

    def _escape_speaker(self, value: Any) -> str:
        return str(value).replace(":", "：").replace(";", "；")

    def _escape_arg(self, value: Any) -> str:
        return str(value).replace(" ", "_").replace(";", "")


class RenderGateway:
    """Publishes validated artifacts without replacing an immutable destination."""

    def publish(
        self,
        compiled: CompiledRender,
        *,
        artifact_root: Path,
        webgal_root: Path,
    ) -> tuple[Path, bool]:
        job = compiled.job
        artifact_dir = (
            artifact_root.resolve()
            / job.world_id
            / job.render_id
            / compiled.content_hash
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)
        self._publish_bytes(
            artifact_dir / "render-job.json",
            (canonical_json(job.model_dump(mode="json")) + "\n").encode("utf-8"),
        )
        self._publish_bytes(artifact_dir / "scene.txt", compiled.script.encode("utf-8"))

        destination = (
            webgal_root.resolve()
            / "game"
            / "scene"
            / "generated"
            / job.world_id
            / f"{job.render_id}-{compiled.content_hash}.txt"
        )
        reused = destination.exists()
        self._publish_bytes(destination, compiled.script.encode("utf-8"))
        return destination, reused

    def _publish_bytes(self, destination: Path, content: bytes) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            try:
                existing = destination.read_bytes()
            except OSError as exc:
                raise WorldError(
                    "RENDER_PUBLICATION_FAILED",
                    f"Cannot inspect existing immutable output '{destination}': {exc}",
                ) from exc
            if sha256_bytes(existing) != sha256_bytes(content):
                raise WorldError(
                    "RENDER_IMMUTABLE_CONFLICT",
                    f"Existing immutable output differs: '{destination}'",
                )
            return

        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=destination.parent, prefix=".render-", delete=False
            ) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
                temporary = Path(handle.name)
            try:
                # A hard-link is an atomic, no-replace publication on the same
                # filesystem. It closes the race that POSIX rename-overwrite leaves.
                os.link(temporary, destination)
            except FileExistsError:
                if sha256_bytes(destination.read_bytes()) != sha256_bytes(content):
                    raise WorldError(
                        "RENDER_IMMUTABLE_CONFLICT",
                        f"Existing immutable output differs: '{destination}'",
                    )
        except OSError as exc:
            raise WorldError(
                "RENDER_PUBLICATION_FAILED",
                f"Cannot publish immutable Render '{destination}': {exc}",
            ) from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
