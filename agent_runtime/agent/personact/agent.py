"""Transactional PersonActAgent facade around the typed cognition loop."""

from dataclasses import dataclass
from hashlib import sha256
from threading import Lock

from langchain_core.runnables import RunnableConfig
from pydantic import ValidationError

from agent_runtime.agent.memory import EmbeddingProvider, MemoryStream
from agent_runtime.agent.personact.compiler import CompiledPersonActSpec
from agent_runtime.agent.personact.errors import DecisionInputError
from agent_runtime.agent.personact.loop import (
    ActionPlanningInput,
    CognitionStrategy,
    DecisionRequest,
    DecisionTrace,
    Observation,
    PersonActLoop,
    PersonActLoopInput,
    PlanDraft,
    PlanningInput,
    RetrievedContext,
)
from agent_runtime.agent.personact.state import PersonaState
from agent_runtime.trace import LocalTrace, record_trace, trace_debug_enabled, trace_scope
from agent_runtime.world.contracts import ActionProposal, WorldRef

__all__ = [
    "ActionPlanningInput",
    "CognitionStrategy",
    "DecisionRequest",
    "DecisionTrace",
    "Observation",
    "PersonActAgent",
    "PlanDraft",
    "PlanningInput",
    "RetrievedContext",
]


@dataclass(frozen=True, slots=True)
class _ReplayRecord:
    proposal_id: str
    request_fingerprint: str
    proposal: ActionProposal
    trace: DecisionTrace


@dataclass(frozen=True, slots=True)
class _PrivateSnapshot:
    state: PersonaState
    memory: MemoryStream
    trace: DecisionTrace | None
    replay: _ReplayRecord | None
    used_proposal_ids: frozenset[str]


class PersonActAgent:
    """Own one Persona's private snapshot and invoke its cognition loop.

    Calls for one Persona are serialized. The private snapshot is replaced
    once, after proposal validation. No movement or world execution occurs.
    """

    def __init__(
        self,
        spec: CompiledPersonActSpec,
        state: PersonaState,
        memory: MemoryStream,
        strategy: CognitionStrategy,
        embedding_provider: EmbeddingProvider,
        *,
        world_ref: WorldRef,
        trace_log: LocalTrace | None = None,
    ) -> None:
        self._world_ref = WorldRef.model_validate(world_ref, strict=True)
        if trace_log is not None and trace_log.world_ref != self._world_ref:
            raise DecisionInputError("trace log belongs to a different WorldRef")
        self._trace_log = trace_log
        self._spec = CompiledPersonActSpec.model_validate(spec, strict=True)
        validated_state = PersonaState.model_validate(state, strict=True)
        validated_memory = MemoryStream.model_validate(memory, strict=True)
        self._lock = Lock()
        self._snapshot = _PrivateSnapshot(
            state=validated_state,
            memory=validated_memory,
            trace=None,
            replay=None,
            used_proposal_ids=frozenset(),
        )
        self._validate_ownership()
        self._loop = PersonActLoop(
            world_ref=self._world_ref,
            spec=self._spec,
            strategy=strategy,
            embedding_provider=embedding_provider,
        )

    @property
    def state(self) -> PersonaState:
        """Return private state after the last successful decision."""

        with self._lock:
            return self._snapshot.state

    @property
    def memory(self) -> MemoryStream:
        """Return scoped memory after the last successful decision."""

        with self._lock:
            return self._snapshot.memory

    @property
    def last_trace(self) -> DecisionTrace | None:
        """Return the replay trace for the last successful decision."""

        with self._lock:
            return self._snapshot.trace

    def decide(
        self,
        request: DecisionRequest,
        config: RunnableConfig | None = None,
    ) -> ActionProposal:
        """Return a proposal, never a movement or execution result."""

        with (
            self._lock,
            trace_scope(
                self._trace_log,
                world_ref=self._world_ref,
                agent_kind="character",
                agent_id=self._spec.agent_id,
            ),
        ):
            try:
                validated_request = DecisionRequest.model_validate(request, strict=True)
                PersonaState.model_validate(self._snapshot.state, strict=True)
                MemoryStream.model_validate(self._snapshot.memory, strict=True)
            except ValidationError as error:
                raise DecisionInputError("decision request failed strict validation") from error

            self._validate_ownership()
            if validated_request.view.world_ref != self._world_ref:
                raise DecisionInputError("view belongs to a different WorldRef")
            if validated_request.view.agent_id != self._spec.agent_id:
                raise DecisionInputError("view belongs to a different agent")
            record_trace(
                "decision.input",
                {
                    "proposalId": validated_request.proposal_id,
                    "eventSessionId": validated_request.view.event_session_id,
                    "worldVersion": validated_request.view.based_on_world_version,
                    "candidateIds": [c.candidate_id for c in validated_request.view.candidates],
                },
            )
            request_fingerprint = _fingerprint(validated_request)
            replay = self._snapshot.replay
            if replay is not None and replay.proposal_id == validated_request.proposal_id:
                if replay.request_fingerprint != request_fingerprint:
                    raise DecisionInputError(
                        f'proposal id "{validated_request.proposal_id}" was reused '
                        "with a different view"
                    )
                self._snapshot = _PrivateSnapshot(
                    state=self._snapshot.state,
                    memory=self._snapshot.memory,
                    trace=replay.trace,
                    replay=replay,
                    used_proposal_ids=self._snapshot.used_proposal_ids,
                )
                record_trace("decision.replay", {"proposalId": replay.proposal_id})
                record_trace(
                    "decision.result",
                    {
                        "kind": replay.proposal.action.kind,
                        "evidenceIds": replay.proposal.evidence_ids,
                    },
                    content={
                        "proposal": replay.proposal.model_dump(mode="json"),
                        "decisionTrace": replay.trace.model_dump(mode="json"),
                    }
                    if trace_debug_enabled()
                    else None,
                )
                return replay.proposal
            if validated_request.proposal_id in self._snapshot.used_proposal_ids:
                raise DecisionInputError(
                    f'proposal id "{validated_request.proposal_id}" was already consumed'
                )

            result = self._loop.invoke(
                PersonActLoopInput(
                    request=validated_request,
                    state=self._snapshot.state,
                    memory=self._snapshot.memory,
                ),
                config=config,
            )
            self._snapshot = _PrivateSnapshot(
                state=result.state,
                memory=result.memory,
                trace=result.trace,
                replay=_ReplayRecord(
                    proposal_id=validated_request.proposal_id,
                    request_fingerprint=request_fingerprint,
                    proposal=result.proposal,
                    trace=result.trace,
                ),
                used_proposal_ids=(
                    self._snapshot.used_proposal_ids | {validated_request.proposal_id}
                ),
            )
            record_trace(
                "decision.result",
                {"kind": result.proposal.action.kind, "evidenceIds": result.proposal.evidence_ids},
                content={
                    "proposal": result.proposal.model_dump(mode="json"),
                    "decisionTrace": result.trace.model_dump(mode="json"),
                }
                if trace_debug_enabled()
                else None,
            )
            return result.proposal

    def _validate_ownership(self) -> None:
        if self._world_ref.project_id != self._spec.project_id:
            raise DecisionInputError("WorldRef project does not match compiled spec")
        if (
            self._snapshot.state.world_ref != self._world_ref
            or self._snapshot.memory.world_ref != self._world_ref
        ):
            raise DecisionInputError("private state or memory belongs to a different WorldRef")
        if self._snapshot.state.agent_id != self._spec.agent_id:
            raise DecisionInputError(
                f'state belongs to agent "{self._snapshot.state.agent_id}", '
                f'not "{self._spec.agent_id}"'
            )
        if self._snapshot.memory.agent_id != self._spec.agent_id:
            raise DecisionInputError(
                f'memory belongs to agent "{self._snapshot.memory.agent_id}", '
                f'not "{self._spec.agent_id}"'
            )
        if not _scope_belongs_to(self._snapshot.memory.scope, self._spec.memory_scope):
            raise DecisionInputError(
                f'memory scope "{self._snapshot.memory.scope}" '
                f'is outside "{self._spec.memory_scope}"'
            )


def _scope_belongs_to(scope: str, base_scope: str) -> bool:
    return scope == base_scope or scope.startswith(f"{base_scope}/")


def _fingerprint(request: DecisionRequest) -> str:
    canonical = request.model_dump_json(by_alias=True, exclude_none=False)
    return sha256(canonical.encode()).hexdigest()
