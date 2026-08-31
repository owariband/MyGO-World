from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic_evals import Case, Dataset

from mygo_world.canonical import canonical_json, sha256_text
from mygo_world.contracts import ActionProposal, PerceptionFrame
from mygo_world.errors import WorldError
from mygo_world.gateways import ModelRequest, OpenAICompatibleGateway
from mygo_world.validators import ProposalValidator

DEFAULT_EVAL_DATASET = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "evals"
    / "provider-quality-v1.yaml"
)


def run_provider_evals(
    *,
    dataset_path: Path = DEFAULT_EVAL_DATASET,
    env_file: Path | None = None,
) -> dict[str, Any]:
    """Run non-authoritative quality observations over fixed redacted cases."""

    try:
        raw = yaml.safe_load(dataset_path.read_bytes())
        samples = raw["samples"]
    except (OSError, KeyError, TypeError, yaml.YAMLError) as exc:
        raise WorldError(
            "EVAL_DATASET_INVALID", "Cannot load the Eval dataset"
        ) from exc
    if not isinstance(samples, list) or not samples:
        raise WorldError("EVAL_DATASET_INVALID", "Eval dataset has no samples")
    try:
        gateway = OpenAICompatibleGateway.from_environment(env_file=env_file)
    except ValueError as exc:
        raise WorldError("GATEWAY_CONFIGURATION_INVALID", str(exc)) from exc

    cases = [
        Case(name=item["sample_id"], inputs=item)
        for item in samples
        if isinstance(item, dict) and isinstance(item.get("sample_id"), str)
    ]
    if len(cases) != len(samples):
        raise WorldError("EVAL_DATASET_INVALID", "Eval samples require stable IDs")
    dataset: Dataset[dict[str, Any], dict[str, Any], None] = Dataset(
        name="mygo-provider-quality-v1", cases=cases
    )

    def evaluate_sample(sample: dict[str, Any]) -> dict[str, Any]:
        frame = PerceptionFrame.model_validate(sample["perception_frame"])
        request = ModelRequest(
            agent_type="character",
            agent_id=sample["character_id"],
            call_kind="eval_action_proposal",
            model_id=gateway.model_id,
            skill_id="mygo.eval.character",
            skill_version="1.0.0",
            skill_content_hash=sha256_text(sample["skill_body"]),
            input_payload={"perception_frame": frame.model_dump(mode="json")},
            model_config=dict(gateway.model_parameters),
            skill_body=sample["skill_body"],
        )
        generation = gateway.generate(request, ActionProposal)
        quality = ProposalValidator().validate(frame, generation.structured)
        return {
            "quality": "pass" if quality.ok else "fail",
            "diagnostics": [
                item.model_dump(mode="json") for item in quality.diagnostics
            ],
            "latency_ms": generation.latency_ms,
            "usage": generation.usage,
        }

    report = dataset.evaluate_sync(evaluate_sample, progress=False)
    results = [
        {
            "sample_id": item.name,
            "quality": item.output["quality"] if item.output else "error",
            "latency_ms": (
                item.output.get("latency_ms")
                if item.output
                else item.task_duration * 1000
            ),
            "usage": item.output.get("usage") if item.output else None,
            "diagnostics": item.output.get("diagnostics", []) if item.output else [],
        }
        for item in report.cases
    ]
    gateway.assert_no_credentials(canonical_json(results))
    return {
        "command": "eval-provider",
        "status": "completed" if not report.failures else "failed",
        "dataset_id": raw.get("dataset_id"),
        "dataset_schema_version": raw.get("schema_version"),
        "sample_count": len(results),
        "results": results,
    }
