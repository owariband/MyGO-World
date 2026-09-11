"""Typed PersonAct cognition loop ending at one validated action proposal."""

from __future__ import annotations

import math
from datetime import datetime
from hashlib import sha256
from json import dumps
from typing import Annotated, Protocol, Self

from langchain_core.runnables import Runnable, RunnableConfig, RunnableLambda, RunnableSequence
from pydantic import AwareDatetime, StringConstraints, ValidationError, model_validator

from agent_runtime.agent.memory import (
    EmbeddingProvider,
    MemoryKind,
    MemoryRecord,
    MemoryStream,
    MemoryTouch,
    retrieve_ranked,
    retrieve_related,
)
from agent_runtime.agent.personact.compiler import CompiledPersonActSpec
from agent_runtime.agent.personact.errors import (
    DecisionInputError,
    PlannerOutputError,
    PlanningError,
)
from agent_runtime.agent.personact.manifest import MemoryWritePolicy
from agent_runtime.agent.personact.proposal import ProposalDraft, build_action_proposal
from agent_runtime.agent.personact.state import PersonaState, PlanDisposition, PlanItem
from agent_runtime.model import StrictModel
from agent_runtime.trace import record_trace, trace_debug_enabled
from agent_runtime.world.contracts import (
    ActionProposal,
    AgentView,
    AttentionTier,
    Identifier,
    PerceptCandidate,
    PerceptionChannel,
    WorldRef,
)

NonEmptyText = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]


class DecisionRequest(StrictModel):
    """One committed perception snapshot presented for a Persona decision."""

    proposal_id: Identifier
    view: AgentView


class PersonActLoopInput(StrictModel):
    """One immutable private snapshot consumed by the cognition loop."""

    request: DecisionRequest
    state: PersonaState
    memory: MemoryStream


class PlanDraft(StrictModel):
    """Ordered private intentions, generated only when the queue is empty."""

    items: tuple[PlanItem, ...]

    @model_validator(mode="after")
    def _validate_ids(self) -> Self:
        ids = tuple(item.plan_id for item in self.items)
        if len(ids) != len(set(ids)):
            raise ValueError("plan ids must be unique")
        return self


class Observation(StrictModel):
    """One candidate selected by attention, annotated with novelty outcome."""

    candidate: PerceptCandidate
    novelty_key: NonEmptyText
    is_novel: bool
    memory_id: Identifier | None = None


class RetrievedContext(StrictModel):
    """Literal and three-factor memories recalled for one new observation."""

    observation: Observation
    related: tuple[MemoryRecord, ...]
    ranked: tuple[MemoryRecord, ...]


class PlanningInput(StrictModel):
    spec: CompiledPersonActSpec
    state: PersonaState
    memory: MemoryStream
    world_time: AwareDatetime
    observations: tuple[Observation, ...]
    retrieved: tuple[RetrievedContext, ...]


class ActionPlanningInput(StrictModel):
    spec: CompiledPersonActSpec
    view: AgentView
    state: PersonaState
    observations: tuple[Observation, ...]
    retrieved: tuple[RetrievedContext, ...]
    active_plan: PlanItem | None = None
    focused_observation: Observation | None = None


class DecisionTrace(StrictModel):
    """Replayable private cognition trace; it is not a world action."""

    world_ref: WorldRef
    stages: tuple[str, ...]
    observations: tuple[Observation, ...]
    retrieved: tuple[RetrievedContext, ...]
    memory_writes: tuple[MemoryRecord, ...]
    touched_memory_ids: tuple[str, ...]
    active_plan: PlanItem | None = None
    focused_observation: Observation | None = None


class CognitionStrategy(Protocol):
    """Replaceable fixture/model seam for subjective cognitive judgments."""

    def score_poignancy(
        self,
        spec: CompiledPersonActSpec,
        candidate: PerceptCandidate,
        *,
        world_ref: WorldRef,
    ) -> float:
        """Score one novel observation without writing memory."""
        ...

    def plan(self, planning_input: PlanningInput) -> PlanDraft:
        """Fill an empty private queue without imposing a daily schedule."""
        ...

    def plan_action(self, planning_input: ActionPlanningInput) -> ProposalDraft:
        """Choose only this Persona's next action proposal."""
        ...


class _PreparedDecision(StrictModel):
    request: DecisionRequest
    state: PersonaState
    memory: MemoryStream
    memory_writes: tuple[MemoryRecord, ...]


class _PerceivedDecision(StrictModel):
    request: DecisionRequest
    state: PersonaState
    memory: MemoryStream
    memory_writes: tuple[MemoryRecord, ...]
    observations: tuple[Observation, ...]
    new_observations: tuple[Observation, ...]


class _RetrievedDecision(StrictModel):
    request: DecisionRequest
    state: PersonaState
    memory: MemoryStream
    memory_writes: tuple[MemoryRecord, ...]
    observations: tuple[Observation, ...]
    retrieved: tuple[RetrievedContext, ...]
    memory_touches: tuple[MemoryTouch, ...]
    touched_memory_ids: tuple[str, ...]


class _PlannedDecision(StrictModel):
    request: DecisionRequest
    state: PersonaState
    memory: MemoryStream
    memory_writes: tuple[MemoryRecord, ...]
    observations: tuple[Observation, ...]
    retrieved: tuple[RetrievedContext, ...]
    memory_touches: tuple[MemoryTouch, ...]
    touched_memory_ids: tuple[str, ...]
    draft: ProposalDraft
    active_plan: PlanItem | None = None
    focused_observation: Observation | None = None


class PersonActLoopResult(StrictModel):
    """Atomic private result; only proposal crosses into the World layer."""

    proposal: ActionProposal
    state: PersonaState
    memory: MemoryStream
    trace: DecisionTrace
    memory_writes: tuple[MemoryRecord, ...]
    memory_touches: tuple[MemoryTouch, ...]
    plan_disposition: PlanDisposition


class PersonActLoop:
    """Run one bounded prepare/perceive/retrieve/plan/propose sequence."""

    def __init__(
        self,
        *,
        spec: CompiledPersonActSpec,
        strategy: CognitionStrategy,
        embedding_provider: EmbeddingProvider,
        world_ref: WorldRef,
    ) -> None:
        self._spec = CompiledPersonActSpec.model_validate(spec, strict=True)
        self._world_ref = WorldRef.model_validate(world_ref, strict=True)
        if self._world_ref.project_id != self._spec.project_id:
            raise DecisionInputError("WorldRef project does not match compiled spec")
        self._strategy = strategy
        self._embedding_provider = embedding_provider

        prepare_node: Runnable[PersonActLoopInput, _PreparedDecision] = RunnableLambda(
            self._prepare,
            name="prepare",
        )
        perceive_node: Runnable[_PreparedDecision, _PerceivedDecision] = RunnableLambda(
            self._perceive,
            name="perceive",
        )
        retrieve_node: Runnable[_PerceivedDecision, _RetrievedDecision] = RunnableLambda(
            self._retrieve,
            name="retrieve",
        )
        plan_node: Runnable[_RetrievedDecision, _PlannedDecision] = RunnableLambda(
            self._plan,
            name="plan",
        )
        propose_node: Runnable[_PlannedDecision, PersonActLoopResult] = RunnableLambda(
            self._propose,
            name="propose",
        )
        self._runnable: Runnable[PersonActLoopInput, PersonActLoopResult] = RunnableSequence[
            PersonActLoopInput,
            PersonActLoopResult,
        ](
            first=prepare_node,
            middle=[perceive_node, retrieve_node, plan_node],
            last=propose_node,
            name="personact_decide",
        )

    def invoke(
        self,
        loop_input: PersonActLoopInput,
        config: RunnableConfig | None = None,
    ) -> PersonActLoopResult:
        """Run cognition against one immutable private snapshot."""

        try:
            validated_input = PersonActLoopInput.model_validate(loop_input, strict=True)
        except ValidationError as error:
            raise DecisionInputError("PersonAct loop input failed strict validation") from error

        result = self._runnable.invoke(validated_input, config=config)
        try:
            return PersonActLoopResult.model_validate(result, strict=True)
        except ValidationError as error:
            raise PlannerOutputError("decision returned an invalid private result") from error

    def _prepare(self, loop_input: PersonActLoopInput) -> _PreparedDecision:
        record_trace("prepare.start", world_ref=self._world_ref, agent_id=self._spec.agent_id)
        request = loop_input.request
        state = loop_input.state
        memory = loop_input.memory
        view = request.view
        if any(
            ref != self._world_ref for ref in (view.world_ref, state.world_ref, memory.world_ref)
        ):
            raise DecisionInputError("view, state or memory belongs to a different WorldRef")
        if state.agent_id != self._spec.agent_id:
            raise DecisionInputError(
                f'state belongs to agent "{state.agent_id}", not "{self._spec.agent_id}"'
            )
        if memory.agent_id != self._spec.agent_id:
            raise DecisionInputError(
                f'memory belongs to agent "{memory.agent_id}", not "{self._spec.agent_id}"'
            )
        if not _scope_belongs_to(memory.scope, self._spec.memory_scope):
            raise DecisionInputError(
                f'memory scope "{memory.scope}" is outside "{self._spec.memory_scope}"'
            )
        if view.agent_id != self._spec.agent_id:
            raise DecisionInputError(
                f'view belongs to agent "{view.agent_id}", not "{self._spec.agent_id}"'
            )
        if view.world_time is None:
            raise DecisionInputError("PersonActAgent.decide requires view.worldTime")
        if state.last_world_time is not None and view.world_time < state.last_world_time:
            raise DecisionInputError("view.worldTime cannot move Persona state backwards")
        if (
            state.last_world_version is not None
            and view.based_on_world_version < state.last_world_version
        ):
            raise DecisionInputError("view world version cannot move Persona state backwards")
        if any(record.last_accessed_at > view.world_time for record in memory.records):
            raise DecisionInputError("view.worldTime cannot precede memory access time")
        candidate_ids = tuple(candidate.candidate_id for candidate in view.candidates)
        if len(candidate_ids) != len(set(candidate_ids)):
            raise DecisionInputError("perception candidate ids must be unique")

        record_trace(
            "prepare.end",
            {"candidateIds": candidate_ids},
            world_ref=self._world_ref,
            agent_id=self._spec.agent_id,
        )
        return _PreparedDecision(
            request=request,
            state=state,
            memory=memory,
            memory_writes=(),
        )

    def _perceive(self, decision: _PreparedDecision) -> _PerceivedDecision:
        record_trace("perceive.start", world_ref=self._world_ref, agent_id=self._spec.agent_id)
        view = decision.request.view
        world_time = _world_time(view)
        mandatory = sorted(
            (
                candidate
                for candidate in view.candidates
                if candidate.attention_tier is AttentionTier.MANDATORY
            ),
            key=lambda candidate: (-candidate.salience, candidate.candidate_id),
        )
        optional = sorted(
            (
                candidate
                for candidate in view.candidates
                if candidate.attention_tier is not AttentionTier.MANDATORY
            ),
            key=lambda candidate: (
                0 if candidate.attention_tier is AttentionTier.RELEVANT else 1,
                -candidate.salience,
                candidate.candidate_id,
            ),
        )
        remaining_budget = max(
            decision.state.cognitive_config.attention_budget - len(mandatory),
            0,
        )
        attended = (*mandatory, *optional[:remaining_budget])

        state = decision.state
        memory = decision.memory
        observations: list[Observation] = []
        new_events: list[MemoryRecord] = []
        can_write_observations = MemoryWritePolicy.OBSERVATION in self._spec.write_policy
        for candidate in attended:
            novelty_key = _novelty_key(candidate)
            is_novel = memory.is_novel(
                novelty_key,
                recent_limit=None,
                kinds=(MemoryKind.EVENT,),
            )
            if not is_novel or not can_write_observations:
                observations.append(
                    Observation(
                        candidate=candidate,
                        novelty_key=novelty_key,
                        is_novel=is_novel,
                    )
                )
                continue

            try:
                poignancy = self._strategy.score_poignancy(
                    self._spec, candidate, world_ref=self._world_ref
                )
            except Exception as error:
                raise PlanningError("poignancy strategy failed") from error
            if not isinstance(poignancy, float) or not math.isfinite(poignancy) or poignancy < 0.0:
                raise PlannerOutputError("strategy returned an invalid poignancy score")

            memory_id = _stable_memory_id(
                "event",
                (decision.request.proposal_id, candidate.candidate_id, novelty_key),
            )
            record = MemoryRecord(
                world_ref=self._world_ref,
                id=memory_id,
                agent_id=self._spec.agent_id,
                scope=memory.scope,
                kind=MemoryKind.EVENT,
                created_at=world_time,
                last_accessed_at=world_time,
                expires_at=None,
                subject=candidate.subject,
                predicate=candidate.predicate,
                object=candidate.object,
                content=candidate.content,
                poignancy=poignancy,
                tags=candidate.tags,
                source_entry_id=candidate.source_entry_id,
                source=(
                    f"event-entry:{candidate.source_entry_id}"
                    if candidate.source_entry_id is not None
                    else f"percept-candidate:{candidate.candidate_id}"
                ),
                evidence_ids=_candidate_evidence(candidate),
                embedding=self._embedding_provider.embed(candidate.content),
                novelty_key=novelty_key,
            )
            memory = memory.append(record)
            new_events.append(record)
            observations.append(
                Observation(
                    candidate=candidate,
                    novelty_key=novelty_key,
                    is_novel=True,
                    memory_id=memory_id,
                )
            )
            state = _replace_state(
                state,
                plan_queue=state.plan_queue,
                active_plan_id=state.active_plan_id,
                reflection_remaining=max(0.0, state.reflection_remaining - poignancy),
                reflection_new_memory_count=state.reflection_new_memory_count + 1,
                last_world_time=state.last_world_time,
                last_world_version=state.last_world_version,
            )

        record_trace(
            "perceive.end",
            {
                "selectedCandidateIds": [item.candidate.candidate_id for item in observations],
                "novelCandidateIds": [
                    item.candidate.candidate_id for item in observations if item.is_novel
                ],
                "memoryWriteIds": [item.id for item in new_events],
            },
            content=[item.model_dump(mode="json") for item in observations]
            if trace_debug_enabled()
            else None,
            world_ref=self._world_ref,
            agent_id=self._spec.agent_id,
        )
        return _PerceivedDecision(
            request=decision.request,
            state=state,
            memory=memory,
            memory_writes=(*decision.memory_writes, *new_events),
            observations=tuple(observations),
            new_observations=tuple(
                observation for observation in observations if observation.is_novel
            ),
        )

    def _retrieve(self, decision: _PerceivedDecision) -> _RetrievedDecision:
        record_trace("retrieve.start", world_ref=self._world_ref, agent_id=self._spec.agent_id)
        world_time = _world_time(decision.request.view)
        contexts: list[RetrievedContext] = []
        touches: list[MemoryTouch] = []
        current_memory_ids = frozenset(
            observation.memory_id
            for observation in decision.new_observations
            if observation.memory_id is not None
        )
        history = MemoryStream(
            world_ref=self._world_ref,
            agent_id=decision.memory.agent_id,
            scope=decision.memory.scope,
            records=tuple(
                record for record in decision.memory.records if record.id not in current_memory_ids
            ),
        )
        for observation in decision.new_observations:
            candidate = observation.candidate
            related = retrieve_related(
                history,
                keywords=_candidate_keywords(candidate),
                accessed_at=world_time,
                limit=self._spec.retrieval.tag_limit,
                kinds=(MemoryKind.EVENT, MemoryKind.THOUGHT),
            )
            ranked = retrieve_ranked(
                history,
                focal_point=candidate.content,
                embedding_provider=self._embedding_provider,
                accessed_at=world_time,
                limit=self._spec.retrieval.recent_limit,
                recency_decay=decision.state.cognitive_config.recency_decay,
                recency_weight=decision.state.cognitive_config.recency_weight,
                relevance_weight=decision.state.cognitive_config.relevance_weight,
                importance_weight=decision.state.cognitive_config.importance_weight,
            )
            contexts.append(
                RetrievedContext(
                    observation=observation,
                    related=related.records,
                    ranked=ranked.records,
                )
            )
            touches.extend((*related.touches, *ranked.touches))

        deduplicated_touches = _deduplicate_touches(tuple(touches))
        memory = decision.memory.touch(deduplicated_touches)
        record_trace(
            "retrieve.end",
            {
                "relatedMemoryIds": [[r.id for r in item.related] for item in contexts],
                "rankedMemoryIds": [[r.id for r in item.ranked] for item in contexts],
                "touchedMemoryIds": [item.memory_id for item in deduplicated_touches],
            },
            world_ref=self._world_ref,
            agent_id=self._spec.agent_id,
        )
        return _RetrievedDecision(
            request=decision.request,
            state=decision.state,
            memory=memory,
            memory_writes=decision.memory_writes,
            observations=decision.observations,
            retrieved=tuple(contexts),
            memory_touches=deduplicated_touches,
            touched_memory_ids=tuple(touch.memory_id for touch in deduplicated_touches),
        )

    def _plan(self, decision: _RetrievedDecision) -> _PlannedDecision:
        record_trace("plan.start", world_ref=self._world_ref, agent_id=self._spec.agent_id)
        world_time = _world_time(decision.request.view)
        state = decision.state
        if not state.plan_queue:
            queue_input = PlanningInput(
                spec=self._spec,
                state=state,
                memory=decision.memory,
                world_time=world_time,
                observations=decision.observations,
                retrieved=decision.retrieved,
            )
            try:
                raw_plan = self._strategy.plan(queue_input)
            except Exception as error:
                raise PlanningError("planning strategy failed") from error
            try:
                plan = PlanDraft.model_validate(raw_plan, strict=True)
                state = _replace_state(
                    state,
                    plan_queue=plan.items,
                    active_plan_id=plan.items[0].plan_id if plan.items else None,
                    reflection_remaining=state.reflection_remaining,
                    reflection_new_memory_count=state.reflection_new_memory_count,
                    last_world_time=state.last_world_time,
                    last_world_version=state.last_world_version,
                )
            except ValidationError as error:
                raise PlannerOutputError("strategy returned an invalid PlanDraft") from error

        # A proposal does not complete or dequeue a plan. World outcome owns that step.
        active_plan_id = state.active_plan_id
        if active_plan_id is None and state.plan_queue:
            active_plan_id = state.plan_queue[0].plan_id
        state = _replace_state(
            state,
            plan_queue=state.plan_queue,
            active_plan_id=active_plan_id,
            reflection_remaining=state.reflection_remaining,
            reflection_new_memory_count=state.reflection_new_memory_count,
            last_world_time=world_time,
            last_world_version=decision.request.view.based_on_world_version,
        )
        focus = _choose_focus(decision.observations)
        planning_input = ActionPlanningInput(
            spec=self._spec,
            view=decision.request.view,
            state=state,
            observations=decision.observations,
            retrieved=decision.retrieved,
            active_plan=state.active_plan,
            focused_observation=focus,
        )
        try:
            raw_draft = self._strategy.plan_action(planning_input)
        except Exception as error:
            raise PlanningError("action planning strategy failed") from error
        try:
            draft = ProposalDraft.model_validate(raw_draft, strict=True)
        except ValidationError as error:
            raise PlannerOutputError("strategy returned an invalid ProposalDraft") from error

        record_trace(
            "plan.end",
            {
                "planQueueIds": [item.plan_id for item in state.plan_queue],
                "activePlanId": state.active_plan_id,
                "kind": draft.action.kind,
                "evidenceIds": draft.evidence_ids,
            },
            content=[item.model_dump(mode="json") for item in state.plan_queue]
            if trace_debug_enabled()
            else None,
            world_ref=self._world_ref,
            agent_id=self._spec.agent_id,
        )
        return _PlannedDecision(
            request=decision.request,
            state=state,
            memory=decision.memory,
            memory_writes=decision.memory_writes,
            observations=decision.observations,
            retrieved=decision.retrieved,
            memory_touches=decision.memory_touches,
            touched_memory_ids=decision.touched_memory_ids,
            draft=draft,
            active_plan=state.active_plan,
            focused_observation=focus,
        )

    def _propose(self, decision: _PlannedDecision) -> PersonActLoopResult:
        record_trace("propose.start", world_ref=self._world_ref, agent_id=self._spec.agent_id)
        proposal = build_action_proposal(
            world_ref=self._world_ref,
            spec=self._spec,
            view=decision.request.view,
            proposal_id=decision.request.proposal_id,
            draft=decision.draft,
        )
        record_trace(
            "propose.end",
            {"proposalId": proposal.proposal_id, "kind": proposal.action.kind},
            world_ref=self._world_ref,
            agent_id=self._spec.agent_id,
        )
        return PersonActLoopResult(
            proposal=proposal,
            state=decision.state,
            memory=decision.memory,
            memory_writes=decision.memory_writes,
            memory_touches=decision.memory_touches,
            plan_disposition=decision.draft.plan_disposition,
            trace=DecisionTrace(
                world_ref=self._world_ref,
                stages=("prepare", "perceive", "retrieve", "plan", "propose"),
                observations=decision.observations,
                retrieved=decision.retrieved,
                memory_writes=decision.memory_writes,
                touched_memory_ids=decision.touched_memory_ids,
                active_plan=decision.active_plan,
                focused_observation=decision.focused_observation,
            ),
        )


def _world_time(view: AgentView) -> datetime:
    if view.world_time is None:
        raise DecisionInputError("PersonActAgent.decide requires view.worldTime")
    return view.world_time


def _scope_belongs_to(scope: str, base_scope: str) -> bool:
    return scope == base_scope or scope.startswith(f"{base_scope}/")


def _replace_state(
    state: PersonaState,
    *,
    plan_queue: tuple[PlanItem, ...],
    active_plan_id: str | None,
    reflection_remaining: float,
    reflection_new_memory_count: int,
    last_world_time: datetime | None,
    last_world_version: int | None,
) -> PersonaState:
    return PersonaState(
        world_ref=state.world_ref,
        agent_id=state.agent_id,
        cognitive_config=state.cognitive_config,
        reflection_remaining=reflection_remaining,
        last_world_time=last_world_time,
        last_world_version=last_world_version,
        plan_queue=plan_queue,
        active_plan_id=active_plan_id,
        reflection_new_memory_count=reflection_new_memory_count,
        known_place_ids=state.known_place_ids,
    )


def _novelty_key(candidate: PerceptCandidate) -> str:
    identity = (
        candidate.source_entry_id or candidate.candidate_id,
        tuple(sorted(set(candidate.visible_fields))),
    )
    return dumps(identity, ensure_ascii=False, separators=(",", ":"))


def _candidate_evidence(candidate: PerceptCandidate) -> tuple[str, ...]:
    values = (
        *((candidate.source_entry_id,) if candidate.source_entry_id is not None else ()),
        *candidate.source_fact_refs,
        *candidate.source_info_refs,
    )
    return tuple(dict.fromkeys(values))


def _candidate_keywords(candidate: PerceptCandidate) -> tuple[str, ...]:
    values = (
        candidate.subject,
        candidate.predicate,
        *((candidate.object,) if candidate.object is not None else ()),
        *candidate.tags,
    )
    normalized: set[str] = set()
    keywords: list[str] = []
    for value in values:
        folded = value.casefold()
        if folded not in normalized:
            normalized.add(folded)
            keywords.append(value)
    return tuple(keywords)


def _deduplicate_touches(touches: tuple[MemoryTouch, ...]) -> tuple[MemoryTouch, ...]:
    memory_ids: set[str] = set()
    deduplicated: list[MemoryTouch] = []
    for touch in touches:
        if touch.memory_id not in memory_ids:
            memory_ids.add(touch.memory_id)
            deduplicated.append(touch)
    return tuple(deduplicated)


def _choose_focus(observations: tuple[Observation, ...]) -> Observation | None:
    novel = tuple(observation for observation in observations if observation.is_novel)
    if not novel:
        return None
    return min(novel, key=_focus_key)


def _focus_key(observation: Observation) -> tuple[int, int, int, float, str]:
    candidate = observation.candidate
    is_self = candidate.channel is PerceptionChannel.SELF
    is_direct = candidate.channel in {
        PerceptionChannel.DIRECT_INTERACTION,
        PerceptionChannel.TARGETED_MESSAGE,
    }
    is_mandatory = candidate.attention_tier is AttentionTier.MANDATORY
    return (
        int(is_self),
        int(not is_direct),
        int(not is_mandatory),
        -candidate.salience,
        candidate.candidate_id,
    )


def _stable_memory_id(kind: str, identity: tuple[str, ...]) -> str:
    canonical = dumps((kind, identity), ensure_ascii=False, separators=(",", ":"))
    return f"{kind}-{sha256(canonical.encode()).hexdigest()}"
