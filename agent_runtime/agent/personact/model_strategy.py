"""Model-backed CognitionStrategy adapted from the remote MVP model seam."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from threading import Lock
from typing import Annotated

from pydantic import Field

from agent_runtime.agent.personact.compiler import CompiledPersonActSpec
from agent_runtime.agent.personact.errors import PlannerOutputError, ProposalValidationError
from agent_runtime.agent.personact.loop import (
    ActionPlanningInput,
    DailyPlanDraft,
    DailyPlanningInput,
)
from agent_runtime.agent.personact.manifest import PersonaDefinition
from agent_runtime.agent.personact.proposal import ProposalDraft, build_action_proposal
from agent_runtime.agent.skill import RuntimeSkill
from agent_runtime.model import StrictModel
from agent_runtime.model_gateway import (
    ModelCallTrace,
    ModelGateway,
    ModelOutputInvalidError,
    ModelRequest,
    ModelTransportError,
)
from agent_runtime.world.contracts import PerceptCandidate

Poignancy = Annotated[float, Field(ge=0.0, allow_inf_nan=False)]


class PoignancyScore(StrictModel):
    """One subjective importance score produced by the model strategy."""

    score: Poignancy


class _PoignancyInput(StrictModel):
    spec_digest: str
    agent_id: str
    persona: PersonaDefinition
    candidate: PerceptCandidate


class ModelCognitionStrategy:
    """Implement the three PersonAct judgments through one typed model gateway."""

    def __init__(
        self,
        *,
        gateway: ModelGateway,
        skill: RuntimeSkill,
        model_id: str,
        call_id_generator: Callable[[], str],
        trace_retention: int = 256,
    ) -> None:
        if skill.agent_kind != "character":
            raise ValueError("PersonAct strategy requires a character Runtime Skill")
        if not model_id.strip():
            raise ValueError("model_id cannot be empty")
        if trace_retention < 1:
            raise ValueError("trace_retention must be positive")
        self._gateway = gateway
        self._skill = skill
        self._model_id = model_id
        self._call_id_generator = call_id_generator
        self._trace_lock = Lock()
        self._traces: deque[ModelCallTrace] = deque(maxlen=trace_retention)

    @property
    def traces(self) -> tuple[ModelCallTrace, ...]:
        """Return non-secret model provenance accumulated by this strategy."""

        with self._trace_lock:
            return tuple(self._traces)

    def score_poignancy(
        self,
        spec: CompiledPersonActSpec,
        candidate: PerceptCandidate,
    ) -> float:
        """Ask the model for subjective importance without changing visibility."""

        request = self._request(
            spec=spec,
            call_kind="score_poignancy",
            task=(
                "Score how important this already-visible candidate is to the character. "
                "Return only a non-negative finite score."
            ),
            input_json=_PoignancyInput(
                spec_digest=spec.digest,
                agent_id=spec.agent_id,
                persona=spec.persona,
                candidate=candidate,
            ).model_dump_json(by_alias=True, exclude_none=False),
        )
        return self._generate(request, PoignancyScore).score

    def plan_day(self, planning_input: DailyPlanningInput) -> DailyPlanDraft:
        """Generate private intentions and a coarse schedule for the current day."""

        request = self._request(
            spec=planning_input.spec,
            call_kind="plan_day",
            task=(
                "Create private daily intentions and a coarse schedule. Treat the compiled "
                "Persona, current observations, and retrieved private Memory as authoritative. "
                "Do not claim that planned actions already happened."
            ),
            input_json=planning_input.model_dump_json(by_alias=True, exclude_none=False),
        )
        return self._generate(request, DailyPlanDraft)

    def plan_action(self, planning_input: ActionPlanningInput) -> ProposalDraft:
        """Generate one draft, repairing schema or authority failure at most once."""

        request = self._request(
            spec=planning_input.spec,
            call_kind="plan_action",
            task=(
                "Choose exactly one Action draft for this character. Use only the supplied "
                "observations, private Memory, and current affordances. Return action and "
                "visible evidence IDs only; actor, proposal, session, and World Version are "
                "injected later by trusted Runtime code."
            ),
            input_json=planning_input.model_dump_json(by_alias=True, exclude_none=False),
        )

        def validate(draft: ProposalDraft) -> None:
            build_action_proposal(
                spec=planning_input.spec,
                frame=planning_input.frame,
                proposal_id="model-strategy-validation",
                draft=draft,
            )

        return self._generate(request, ProposalDraft, semantic_validator=validate)

    def _request(
        self,
        *,
        spec: CompiledPersonActSpec,
        call_kind: str,
        task: str,
        input_json: str,
    ) -> ModelRequest:
        if spec.character_skill.skill_id != self._skill.skill_id:
            raise ValueError("compiled Character Skill ID does not match the resolved Skill")
        if spec.character_skill.version != self._skill.version:
            raise ValueError("compiled Character Skill version does not match the resolved Skill")
        if spec.character_skill.content_hash != self._skill.content_hash:
            raise ValueError("bound Character Skill content hash does not match the resolved Skill")
        return ModelRequest(
            call_id=self._call_id_generator(),
            agent_kind="character",
            agent_id=spec.agent_id,
            call_kind=call_kind,
            model_id=self._model_id,
            prompt_id=spec.prompt.id,
            prompt_version=spec.prompt.version,
            prompt_digest=spec.prompt.digest,
            skill_id=self._skill.skill_id,
            skill_version=self._skill.version,
            skill_content_hash=self._skill.content_hash,
            system_prompt=_system_prompt(self._skill.body, task),
            input_json=input_json,
        )

    def _generate[ResponseT: StrictModel](
        self,
        request: ModelRequest,
        response_type: type[ResponseT],
        *,
        semantic_validator: Callable[[ResponseT], None] | None = None,
    ) -> ResponseT:
        current = request
        for semantic_attempt in (1, 2):
            try:
                generation = self._gateway.generate(current, response_type)
            except ModelOutputInvalidError as error:
                diagnostic = error.diagnostic
                self._append_trace(_invalid_output(error.trace, diagnostic))
            except ModelTransportError as error:
                self._append_trace(error.trace)
                raise
            else:
                try:
                    if semantic_validator is not None:
                        semantic_validator(generation.structured)
                except ProposalValidationError as error:
                    diagnostic = str(error) or type(error).__name__
                    self._append_trace(_semantic_rejection(generation.trace, diagnostic))
                else:
                    self._append_trace(generation.trace)
                    return generation.structured

            if semantic_attempt == 2:
                raise PlannerOutputError(
                    f"model strategy output remained invalid after one repair: {diagnostic}"
                )
            current = ModelRequest(
                call_id=self._call_id_generator(),
                agent_kind=current.agent_kind,
                agent_id=current.agent_id,
                call_kind=f"{request.call_kind}_repair",
                model_id=current.model_id,
                prompt_id=current.prompt_id,
                prompt_version=current.prompt_version,
                prompt_digest=current.prompt_digest,
                skill_id=current.skill_id,
                skill_version=current.skill_version,
                skill_content_hash=current.skill_content_hash,
                system_prompt=current.system_prompt,
                input_json=current.input_json,
                repair_diagnostic=diagnostic,
            )
        raise AssertionError("unreachable model strategy state")

    def _append_trace(self, trace: ModelCallTrace) -> None:
        with self._trace_lock:
            self._traces.append(trace)


def _system_prompt(skill_body: str, task: str) -> str:
    return (
        f"{skill_body}\n\n"
        "The compiled PersonAct input is authoritative for current identity, goals, "
        "relationships, permissions, and visible facts. The Character Skill supplies stable "
        "creative tendencies and must not override those hard constraints.\n\n"
        f"Task: {task}"
    )


def _semantic_rejection(trace: ModelCallTrace, diagnostic: str) -> ModelCallTrace:
    return ModelCallTrace(
        call_id=trace.call_id,
        call_kind=trace.call_kind,
        model_id=trace.model_id,
        prompt_id=trace.prompt_id,
        prompt_version=trace.prompt_version,
        prompt_digest=trace.prompt_digest,
        skill_id=trace.skill_id,
        skill_version=trace.skill_version,
        skill_content_hash=trace.skill_content_hash,
        input_hash=trace.input_hash,
        output_hash=trace.output_hash,
        status="semantic_rejected",
        diagnostic=diagnostic,
        latency_ms=trace.latency_ms,
        transport_attempts=trace.transport_attempts,
    )


def _invalid_output(trace: ModelCallTrace, diagnostic: str) -> ModelCallTrace:
    return ModelCallTrace(
        call_id=trace.call_id,
        call_kind=trace.call_kind,
        model_id=trace.model_id,
        prompt_id=trace.prompt_id,
        prompt_version=trace.prompt_version,
        prompt_digest=trace.prompt_digest,
        skill_id=trace.skill_id,
        skill_version=trace.skill_version,
        skill_content_hash=trace.skill_content_hash,
        input_hash=trace.input_hash,
        output_hash=trace.output_hash,
        status="invalid_output",
        diagnostic=diagnostic,
        latency_ms=trace.latency_ms,
        transport_attempts=trace.transport_attempts,
    )
