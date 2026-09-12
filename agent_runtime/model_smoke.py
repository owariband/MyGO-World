"""One model-backed Anon decision over a fixed view, without a World commit.

Run with ``uv run python -m agent_runtime.model_smoke`` and DEEPSEEK_API_KEY.
The fixture view and empty embedding are a wiring check, not a Scenario loader
or a production semantic-retrieval implementation.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from itertools import count
from pathlib import Path
from typing import cast

from agent_runtime.agent.memory import MemoryStream
from agent_runtime.agent.personact.agent import DecisionRequest, PersonActAgent
from agent_runtime.agent.personact.compiler import (
    Catalog,
    PromptDefinition,
    ToolDefinition,
    ToolMode,
    compile_manifest,
)
from agent_runtime.agent.personact.manifest import load_manifest
from agent_runtime.agent.personact.model_strategy import ModelCognitionStrategy
from agent_runtime.agent.personact.state import CognitiveConfig, PersonaState
from agent_runtime.agent.skill import RuntimeSkillCatalog
from agent_runtime.model import StrictModel
from agent_runtime.model_gateway import ModelCallTrace, ModelGateway
from agent_runtime.model_provider import DEFAULT_DEEPSEEK_MODEL, create_deepseek_gateway
from agent_runtime.trace import LocalTrace
from agent_runtime.world.contracts import (
    ActionProposal,
    Affordance,
    AgentView,
    AttentionTier,
    CharacterTarget,
    DeliveryChannel,
    PerceptCandidate,
    PerceptionChannel,
    ProposalKind,
    WorldRef,
)

SMOKE_WORLD_REF = WorldRef(project_id="coffee-golden", world_id="model-smoke")


class SmokeResult(StrictModel):
    """One uncommitted proposal and non-secret model-call provenance."""

    proposal: ActionProposal
    traces: tuple[ModelCallTrace, ...]


class _EmptyEmbeddingProvider:
    """Explicit smoke placeholder: no embedding model or semantic similarity."""

    def embed(self, text: str) -> tuple[float, ...]:
        del text
        return ()


def run_smoke(
    gateway: ModelGateway, *, model_id: str, trace_log: LocalTrace | None = None
) -> SmokeResult:
    """Decide once with optional diagnostic logs; never commit or save World state."""

    runtime_root = Path(__file__).resolve().parent
    skills = RuntimeSkillCatalog.load(runtime_root.parent / "content" / "skills")
    catalog = Catalog(
        tools=(ToolDefinition(id="visible_location.query", version="1", mode=ToolMode.QUERY),),
        prompts=(PromptDefinition(id="personact.v1", version="1", digest="prompt-v1"),),
        skills=skills.skills,
    )
    manifest = load_manifest(runtime_root / "testdata" / "npc_diy" / "agents.json")
    spec = next(item for item in compile_manifest(manifest, catalog) if item.agent_id == "anon")
    skill = skills.resolve(
        spec.character_skill.skill_id,
        spec.character_skill.version,
        agent_kind="character",
    )
    world_ref = SMOKE_WORLD_REF
    call_ids = count(1)
    strategy = ModelCognitionStrategy(
        gateway=gateway,
        skill=skill,
        model_id=model_id,
        call_id_generator=lambda: f"smoke-call-{next(call_ids)}",
    )
    state = PersonaState(
        world_ref=world_ref,
        agent_id=spec.agent_id,
        cognitive_config=CognitiveConfig(
            attention_budget=1,
            retention=20,
            recency_weight=1.0,
            relevance_weight=1.0,
            importance_weight=1.0,
            recency_decay=0.99,
            reflection_threshold=10.0,
            reflection_count=5,
        ),
        reflection_remaining=10.0,
    )
    agent = PersonActAgent(
        spec=spec,
        state=state,
        memory=MemoryStream(world_ref=world_ref, agent_id=spec.agent_id, scope=spec.memory_scope),
        strategy=strategy,
        embedding_provider=_EmptyEmbeddingProvider(),
        world_ref=world_ref,
        trace_log=trace_log,
    )
    view = AgentView(
        world_ref=world_ref,
        agent_id=spec.agent_id,
        event_session_id="smoke-cafe",
        based_on_world_version=1,
        based_on_control_epoch=1,
        based_on_decision_seq=0,
        current_location_id="cafe",
        world_time=datetime(2026, 9, 1, 9, tzinfo=UTC),
        candidates=(
            PerceptCandidate(
                candidate_id="soyo-greeting-for-anon",
                source_entry_id="soyo-greeting",
                channel=PerceptionChannel.DIRECT_INTERACTION,
                attention_tier=AttentionTier.MANDATORY,
                subject="soyo",
                predicate="greets",
                object="anon",
                content="爽世向爱音打招呼并询问她想喝什么",
                salience=0.8,
            ),
        ),
        visible_evidence_ids=("soyo-greeting",),
        affordances=(
            Affordance(
                affordance_id="smoke-utter-soyo-direct",
                kind=ProposalKind.UTTER,
                target=CharacterTarget(id="soyo"),
                delivery_channel=DeliveryChannel.DIRECT,
            ),
            Affordance(
                affordance_id="smoke-interact-soyo",
                kind=ProposalKind.INTERACT,
                target=CharacterTarget(id="soyo"),
                operation_id="join_target_session",
            ),
        ),
    )
    proposal = agent.decide(DecisionRequest(proposal_id="smoke-proposal-1", view=view))
    return SmokeResult(proposal=proposal, traces=strategy.traces)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one Anon model decision; output an uncommitted proposal and traces.",
        epilog="Requires DEEPSEEK_API_KEY. Local traces only; no World save or real embeddings.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL),
        help="Provider model ID (default: DEEPSEEK_MODEL or the configured DeepSeek default).",
    )
    parser.add_argument(
        "--trace-root",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "projects",
        help="Root containing per-project local trace directories (default: repository projects/).",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Include private prompt/output content in local traces and echo records to stderr.",
    )
    arguments = parser.parse_args(argv)
    model_id = cast(str, arguments.model)
    try:
        gateway = create_deepseek_gateway(model_id=model_id)
        with LocalTrace(
            cast(Path, arguments.trace_root), SMOKE_WORLD_REF, debug=cast(bool, arguments.debug)
        ) as trace_log:
            sys.stderr.write(f"Local trace: {trace_log.path}\n")
            result = run_smoke(gateway, model_id=model_id, trace_log=trace_log)
    except Exception as error:
        # SDK errors and chained planning failures can contain keys or private prompt text.
        sys.stderr.write(
            f"Model smoke failed ({type(error).__name__}). "
            "Check DEEPSEEK_API_KEY and provider/model configuration.\n"
        )
        return 1
    sys.stdout.write(result.model_dump_json(by_alias=True, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
