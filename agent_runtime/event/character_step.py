"""One disposable Character decision and one atomic World/Persona commit."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Annotated

from langchain_core.runnables import RunnableConfig
from pydantic import Field
from sqlalchemy.orm import Session

from agent_runtime.agent.memory import EmbeddingProvider, MemoryStream
from agent_runtime.agent.memory.storage import MemoryStore
from agent_runtime.agent.personact.agent import DecisionRequest, PersonActAgent
from agent_runtime.agent.personact.compiler import CompiledPersonActSpec
from agent_runtime.agent.personact.loop import CognitionStrategy
from agent_runtime.agent.personact.state import DecisionOutcome
from agent_runtime.agent.personact.storage import PersonaStateStore, StoredPersonaState
from agent_runtime.model import StrictModel
from agent_runtime.scenario import ObjectSeed
from agent_runtime.sqlite import ProjectDatabase
from agent_runtime.trace import LocalTrace
from agent_runtime.world.contracts import (
    ActionProposal,
    AgentView,
    ControlEpoch,
    DecisionSequence,
    Identifier,
    RespondAction,
    WorldRef,
    WorldVersion,
)
from agent_runtime.world.entries import DialogueEntry, EventEntry, InteractionRequest
from agent_runtime.world.entry_storage import EventEntryStore
from agent_runtime.world.state import PublicWorldState, WorldStatus
from agent_runtime.world.storage import WorldStore
from agent_runtime.world.updater import (
    WorldChangeValidator,
    WorldUpdater,
    WorldUpdateResult,
    WorldUpdateStatus,
)
from agent_runtime.world.view_builder import AgentViewBuilder


class CharacterStepError(RuntimeError):
    """The configured Runtime cannot execute the requested Character step."""


class CharacterStepResult(StrictModel):
    """Durable result of one live decision or an idempotent replay lookup."""

    world_ref: WorldRef
    agent_id: str
    decision_id: str
    proposal: ActionProposal | None = None
    world_update: WorldUpdateResult
    persona_state_revision: int


class CharacterDispatch(StrictModel):
    """One pre-charged model attempt issued only by the World Runner."""

    world_ref: WorldRef
    owner_id: Identifier
    agent_id: Identifier
    dispatch_count: Annotated[int, Field(ge=1)]
    decision_id: Identifier
    control_epoch: ControlEpoch
    based_on_world_version: WorldVersion
    based_on_decision_seq: DecisionSequence
    priority_request_entry_id: Identifier | None = None


@dataclass(frozen=True, slots=True)
class _ReadSnapshot:
    public_state: PublicWorldState
    persona: StoredPersonaState
    memory: MemoryStream
    previous_entries: tuple[EventEntry, ...]
    pending_requests: tuple[InteractionRequest, ...]
    pending_sources: tuple[DialogueEntry, ...]


class CharacterStep:
    """Coordinate exactly one Character decision; scheduling remains an outer concern."""

    def __init__(
        self,
        *,
        database: ProjectDatabase,
        world_ref: WorldRef,
        specs: tuple[CompiledPersonActSpec, ...],
        object_seeds: tuple[ObjectSeed, ...],
        strategy_factory: Callable[[CompiledPersonActSpec], CognitionStrategy],
        embedding_provider: EmbeddingProvider,
        clock: Callable[[], datetime] | None = None,
        trace_log: LocalTrace | None = None,
    ) -> None:
        self._world_ref = WorldRef.model_validate(world_ref, strict=True)
        if database.project_id != self._world_ref.project_id:
            raise CharacterStepError("ProjectDatabase belongs to another Project")
        validated_specs = tuple(
            CompiledPersonActSpec.model_validate(spec, strict=True) for spec in specs
        )
        spec_ids = tuple(spec.agent_id for spec in validated_specs)
        if len(spec_ids) != len(set(spec_ids)):
            raise CharacterStepError("compiled Agent spec IDs must be unique")
        if any(spec.project_id != self._world_ref.project_id for spec in validated_specs):
            raise CharacterStepError("compiled Agent spec belongs to another Project")
        if trace_log is not None and trace_log.world_ref != self._world_ref:
            raise CharacterStepError("trace log belongs to another WorldRef")

        self._database = database
        self._specs = {spec.agent_id: spec for spec in validated_specs}
        self._strategy_factory = strategy_factory
        self._embedding_provider = embedding_provider
        self._clock = clock or _utc_now
        self._trace_log = trace_log
        self._world_store = WorldStore(self._world_ref)
        self._entry_store = EventEntryStore(self._world_ref)
        self._persona_store = PersonaStateStore(self._world_ref)
        self._memory_store = MemoryStore(self._world_ref)
        self._view_builder = AgentViewBuilder(self._world_ref, object_seeds)
        self._validator = WorldChangeValidator(self._world_ref, object_seeds)
        self._updater = WorldUpdater(self._world_ref, object_seeds)

    @property
    def world_ref(self) -> WorldRef:
        return self._world_ref

    def next_decision_id(self, agent_id: str) -> str:
        """Return the only decision ID valid for this Agent's current World fence."""

        agent_id = _identifier(agent_id, "agent_id")
        if agent_id not in self._specs:
            raise CharacterStepError(f'Agent "{agent_id}" has no compiled PersonAct spec')
        with self._database.session_factory() as session:
            public_state = self._world_store.load(session)
        world = public_state.world
        if world.status is not WorldStatus.RUNNING:
            raise CharacterStepError("World must be running before issuing a decision")
        if world.run_owner_id is not None or world.active_dispatch_count is not None:
            raise CharacterStepError("a Runner-owned World requires a CharacterDispatch")
        return _decision_id(
            self._world_ref,
            agent_id=agent_id,
            world_version=world.current_version,
            control_epoch=world.control_epoch,
            decision_seq=world.decision_seq,
        )

    def run(
        self,
        agent_id: str,
        decision_id: str,
        *,
        config: RunnableConfig | None = None,
        checkpoint: Callable[[str], None] | None = None,
    ) -> CharacterStepResult:
        """Run model work outside SQL and atomically publish the accepted outcome."""

        agent_id = _identifier(agent_id, "agent_id")
        decision_id = _identifier(decision_id, "decision_id")
        spec = self._specs.get(agent_id)
        if spec is None:
            raise CharacterStepError(f'Agent "{agent_id}" has no compiled PersonAct spec')

        return self._run(
            agent_id,
            decision_id,
            spec,
            dispatch=None,
            config=config,
            checkpoint=checkpoint,
        )

    def run_dispatch(
        self,
        dispatch: CharacterDispatch,
        *,
        config: RunnableConfig | None = None,
        checkpoint: Callable[[str], None] | None = None,
    ) -> CharacterStepResult:
        """Run exactly one pre-charged dispatch from the durable Runner."""

        dispatch = CharacterDispatch.model_validate(dispatch, strict=True)
        if dispatch.world_ref != self._world_ref:
            raise CharacterStepError("CharacterDispatch belongs to another WorldRef")
        spec = self._specs.get(dispatch.agent_id)
        if spec is None:
            raise CharacterStepError(f'Agent "{dispatch.agent_id}" has no compiled PersonAct spec')
        return self._run(
            dispatch.agent_id,
            dispatch.decision_id,
            spec,
            dispatch=dispatch,
            config=config,
            checkpoint=checkpoint,
        )

    def _run(
        self,
        agent_id: str,
        decision_id: str,
        spec: CompiledPersonActSpec,
        *,
        dispatch: CharacterDispatch | None,
        config: RunnableConfig | None,
        checkpoint: Callable[[str], None] | None,
    ) -> CharacterStepResult:
        snapshot, view, replay = self._read(agent_id, decision_id, spec, dispatch=dispatch)
        if replay is not None:
            return replay
        if snapshot is None or view is None:
            raise AssertionError("live Character read did not return a snapshot")

        strategy = self._strategy_factory(spec)
        agent = PersonActAgent(
            spec,
            snapshot.persona.state,
            snapshot.memory,
            strategy,
            self._embedding_provider,
            world_ref=self._world_ref,
            trace_log=self._trace_log,
        )
        proposal = agent.decide(
            DecisionRequest(proposal_id=decision_id, view=view),
            config=config,
        )
        response_request, response_source = _response_context(proposal, snapshot)
        plan = self._validator.plan(
            proposal,
            view,
            snapshot.public_state,
            created_at=self._clock(),
            previous_entries=snapshot.previous_entries,
            response_request=response_request,
            response_source=response_source,
            dispatch_count=(dispatch.dispatch_count if dispatch is not None else None),
            run_owner_id=(dispatch.owner_id if dispatch is not None else None),
            priority_request_entry_id=(
                dispatch.priority_request_entry_id if dispatch is not None else None
            ),
        )
        committed_version = plan.expected_world_version + (
            1 if plan.status is WorldUpdateStatus.APPLIED else 0
        )
        private_update = agent.observe_outcome(
            decision_id=decision_id,
            outcome=DecisionOutcome(plan.status.value),
            committed_world_version=committed_version,
        )

        with self._database.session_factory.begin() as session:
            world_update = self._updater.apply(
                session,
                plan,
                checkpoint=checkpoint,
            )
            stored = self._persona_store.compare_and_set(
                session,
                private_update,
                expected_revision=snapshot.persona.state_revision,
            )
            _checkpoint(checkpoint, "persona")
            self._memory_store.append(
                session,
                agent_id=agent_id,
                scope=spec.memory_scope,
                records=private_update.memory_writes,
            )
            self._memory_store.touch(session, private_update.memory_touches)
            session.flush()
            _checkpoint(checkpoint, "memory")

        return CharacterStepResult(
            world_ref=self._world_ref,
            agent_id=agent_id,
            decision_id=decision_id,
            proposal=proposal,
            world_update=world_update,
            persona_state_revision=stored.state_revision,
        )

    def _read(
        self,
        agent_id: str,
        decision_id: str,
        spec: CompiledPersonActSpec,
        *,
        dispatch: CharacterDispatch | None,
    ) -> tuple[_ReadSnapshot | None, AgentView | None, CharacterStepResult | None]:
        with self._database.session_factory() as session, session.begin():
            public_state = self._world_store.load(session)
            persona = self._persona_store.load(session, agent_id)
            if persona.spec_digest != spec.digest:
                raise CharacterStepError("saved Persona spec does not match Runtime configuration")
            replay = self._replay_result(
                session,
                public_state=public_state,
                persona=persona,
                agent_id=agent_id,
                decision_id=decision_id,
            )
            if replay is not None:
                return None, None, replay
            if public_state.world.status is not WorldStatus.RUNNING:
                raise CharacterStepError("World must be running before a Character step")
            if dispatch is None:
                if (
                    public_state.world.run_owner_id is not None
                    or public_state.world.active_dispatch_count is not None
                ):
                    raise CharacterStepError("a Runner-owned World requires a CharacterDispatch")
                expected_decision_id = _decision_id(
                    self._world_ref,
                    agent_id=agent_id,
                    world_version=public_state.world.current_version,
                    control_epoch=public_state.world.control_epoch,
                    decision_seq=public_state.world.decision_seq,
                )
            else:
                self._require_dispatch(public_state, dispatch)
                expected_decision_id = dispatch_decision_id(
                    self._world_ref,
                    agent_id=agent_id,
                    control_epoch=dispatch.control_epoch,
                    dispatch_count=dispatch.dispatch_count,
                )
            if decision_id != expected_decision_id:
                raise CharacterStepError(
                    "decision_id does not match the current World decision fence"
                )
            memory = self._memory_store.load(session, agent_id, spec.memory_scope)
            view = self._view_builder.build(
                session,
                agent_id=agent_id,
                observed_through=persona.observation_cursor,
                priority_request_entry_id=(
                    dispatch.priority_request_entry_id if dispatch is not None else None
                ),
                restrict_pending_priority=dispatch is not None,
            )
            line_keys = sorted(
                {(item.root_session_id, item.topology_version) for item in public_state.sessions}
            )
            previous_entries = tuple(
                entry
                for root_session_id, topology_version in line_keys
                if (
                    entry := self._entry_store.latest_for_line(
                        session,
                        root_session_id,
                        topology_version,
                    )
                )
                is not None
            )
            pending_requests = self._entry_store.pending_requests_for(session, agent_id)
            pending_sources: list[DialogueEntry] = []
            for request in pending_requests:
                source = self._entry_store.get(session, request.request_entry_id)
                if not isinstance(source, DialogueEntry):
                    raise CharacterStepError("pending response source is not a DialogueEntry")
                pending_sources.append(source)
        return (
            _ReadSnapshot(
                public_state=public_state,
                persona=persona,
                memory=memory,
                previous_entries=previous_entries,
                pending_requests=pending_requests,
                pending_sources=tuple(pending_sources),
            ),
            view,
            None,
        )

    def _require_dispatch(
        self,
        public_state: PublicWorldState,
        dispatch: CharacterDispatch,
    ) -> None:
        world = public_state.world
        expected = (
            self._world_ref,
            world.run_owner_id,
            world.active_dispatch_agent_id,
            world.active_dispatch_count,
            world.control_epoch,
            world.current_version,
            world.decision_seq,
        )
        actual = (
            dispatch.world_ref,
            dispatch.owner_id,
            dispatch.agent_id,
            dispatch.dispatch_count,
            dispatch.control_epoch,
            dispatch.based_on_world_version,
            dispatch.based_on_decision_seq,
        )
        if actual != expected:
            raise CharacterStepError("CharacterDispatch does not match the active World token")

    def _replay_result(
        self,
        session: Session,
        *,
        public_state: PublicWorldState,
        persona: StoredPersonaState,
        agent_id: str,
        decision_id: str,
    ) -> CharacterStepResult | None:
        entry = self._entry_store.get_by_source(session, source_id=decision_id)
        if entry is not None:
            if entry.actor_agent_id != agent_id:
                raise CharacterStepError("decision_id already belongs to another Agent")
            status = WorldUpdateStatus.APPLIED
            entry_id = entry.entry_id
        elif persona.last_decision_id == decision_id:
            if persona.last_decision_outcome is None:
                raise CharacterStepError("saved decision outcome is incomplete")
            status = WorldUpdateStatus(persona.last_decision_outcome.value)
            if status is WorldUpdateStatus.APPLIED:
                raise CharacterStepError("applied decision is missing its committed Entry")
            entry_id = None
        else:
            return None
        return CharacterStepResult(
            world_ref=self._world_ref,
            agent_id=agent_id,
            decision_id=decision_id,
            world_update=WorldUpdateResult(
                world_ref=self._world_ref,
                decision_id=decision_id,
                status=status,
                current_world_version=public_state.world.current_version,
                current_decision_seq=public_state.world.decision_seq,
                entry_id=entry_id,
                entry_position=(entry.commit_position if entry is not None else None),
                replayed=True,
            ),
            persona_state_revision=persona.state_revision,
        )


def _response_context(
    proposal: ActionProposal,
    snapshot: _ReadSnapshot,
) -> tuple[InteractionRequest | None, DialogueEntry | None]:
    if not isinstance(proposal.action, RespondAction):
        return None, None
    source_id = proposal.action.in_reply_to_entry_id
    request = next(
        (item for item in snapshot.pending_requests if item.request_entry_id == source_id),
        None,
    )
    source = next(
        (item for item in snapshot.pending_sources if item.entry_id == source_id),
        None,
    )
    return request, source


def _checkpoint(callback: Callable[[str], None] | None, name: str) -> None:
    if callback is not None:
        callback(name)


def _decision_id(
    world_ref: WorldRef,
    *,
    agent_id: str,
    world_version: int,
    control_epoch: int,
    decision_seq: int,
) -> str:
    identity = (
        "character-decision/v1",
        world_ref.project_id,
        world_ref.world_id,
        agent_id,
        world_version,
        control_epoch,
        decision_seq,
    )
    canonical = json.dumps(
        identity,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    return f"decision-{sha256(canonical.encode('utf-8')).hexdigest()}"


def dispatch_decision_id(
    world_ref: WorldRef,
    *,
    agent_id: str,
    control_epoch: int,
    dispatch_count: int,
) -> str:
    """Derive the non-reusable source identity for one charged model attempt."""

    identity = (
        "character-dispatch/v1",
        world_ref.project_id,
        world_ref.world_id,
        control_epoch,
        dispatch_count,
        agent_id,
    )
    canonical = json.dumps(
        identity,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    return f"decision-{sha256(canonical.encode('utf-8')).hexdigest()}"


def _identifier(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise CharacterStepError(f"{name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise CharacterStepError(f"{name} cannot be empty")
    return normalized


def _utc_now() -> datetime:
    return datetime.now(UTC)
