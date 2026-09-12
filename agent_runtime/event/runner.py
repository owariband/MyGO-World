"""Bounded, single-World Character scheduling with durable dispatch fencing."""

from __future__ import annotations

import fcntl
import json
import os
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from sqlalchemy.orm import Session

from agent_runtime.agent.personact.errors import PersonActError
from agent_runtime.event.character_step import (
    CharacterDispatch,
    CharacterStep,
    CharacterStepResult,
    dispatch_decision_id,
)
from agent_runtime.model import StrictModel
from agent_runtime.sqlite import ProjectDatabase
from agent_runtime.trace import LocalTrace
from agent_runtime.world.contracts import WorldRef
from agent_runtime.world.entry_storage import EventEntryStore
from agent_runtime.world.state import EventSessionNode, WorldState, WorldStatus
from agent_runtime.world.storage import (
    WorldCommitConflictError,
    WorldStore,
)


class WorldRunnerError(RuntimeError):
    """The World cannot be scheduled under the requested lifecycle operation."""


class WorldAlreadyRunningError(WorldRunnerError):
    """Another local process owns the World run lock."""


class WorldStopReason(StrEnum):
    USER_PAUSE = "user_pause"
    INTERRUPTED = "interrupted"
    BUDGET_EXHAUSTED = "budget_exhausted"
    IDLE = "idle"


class DispatchFailure(StrictModel):
    dispatch_count: int
    agent_id: str
    error_type: str


class WorldRunResult(StrictModel):
    world_ref: WorldRef
    owner_id: str
    successful_steps: tuple[CharacterStepResult, ...]
    failed_dispatches: tuple[DispatchFailure, ...]
    final_state: WorldState


@dataclass(slots=True)
class _WorldFileLock:
    path: Path
    descriptor: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            os.close(descriptor)
            raise WorldAlreadyRunningError("another process owns this World run") from error
        self.descriptor = descriptor

    def release(self) -> None:
        descriptor = self.descriptor
        if descriptor is None:
            return
        self.descriptor = None
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


class WorldRunner:
    """Issue one model call at a time and stop only on a durable boundary."""

    def __init__(
        self,
        *,
        database: ProjectDatabase,
        world_ref: WorldRef,
        character_step: CharacterStep,
        trace_log: LocalTrace | None = None,
        owner_id: str | None = None,
    ) -> None:
        self._world_ref = WorldRef.model_validate(world_ref, strict=True)
        if database.project_id != self._world_ref.project_id:
            raise WorldRunnerError("ProjectDatabase belongs to another Project")
        if character_step.world_ref != self._world_ref:
            raise WorldRunnerError("CharacterStep belongs to another WorldRef")
        if trace_log is not None and trace_log.world_ref != self._world_ref:
            raise WorldRunnerError("LocalTrace belongs to another WorldRef")
        self._database = database
        self._step = character_step
        self._trace = trace_log
        self._store = WorldStore(self._world_ref)
        self._entries = EventEntryStore(self._world_ref)
        self._owner_id = (owner_id or f"runner-{uuid4().hex}").strip()
        if not self._owner_id:
            raise WorldRunnerError("owner_id cannot be empty")
        lock_name = sha256(self._world_ref.world_id.encode("utf-8")).hexdigest()[:24]
        self._lock = _WorldFileLock(database.path.parent / f".{lock_name}.run.lock")
        self._owns_lock = False

    @property
    def owner_id(self) -> str:
        return self._owner_id

    def resume(self, *, additional_decisions: int) -> WorldState:
        """Acquire local ownership, fence stale work, and append a bounded quota."""

        if self._owns_lock:
            raise WorldRunnerError("WorldRunner already owns the run lock")
        self._lock.acquire()
        self._owns_lock = True
        try:
            with self._database.session_factory.begin() as session:
                state = self._store.resume(
                    session,
                    owner_id=self._owner_id,
                    additional_decisions=additional_decisions,
                )
        except BaseException:
            self.close()
            raise
        self._trace_event(
            "runtime.resume",
            {
                "controlEpoch": state.control_epoch,
                "dispatchCount": state.dispatch_count,
                "dispatchLimitAt": state.dispatch_limit_at,
            },
        )
        return state

    def run(self, *, config: RunnableConfig | None = None) -> WorldRunResult:
        """Run until quota exhaustion, idle convergence, or an explicit interrupt."""

        if not self._owns_lock:
            raise WorldRunnerError("resume must acquire the World before run")
        successful: list[CharacterStepResult] = []
        failed: list[DispatchFailure] = []
        try:
            while (dispatch := self.next_dispatch()) is not None:
                self._trace_event(
                    "runtime.dispatch",
                    {
                        "agentId": dispatch.agent_id,
                        "dispatchCount": dispatch.dispatch_count,
                        "decisionId": dispatch.decision_id,
                        "controlEpoch": dispatch.control_epoch,
                        "priorityRequestEntryId": dispatch.priority_request_entry_id,
                    },
                    agent_id=dispatch.agent_id,
                )
                try:
                    result = self._step.run_dispatch(dispatch, config=config)
                except PersonActError as error:
                    self._clear_failed_dispatch(dispatch)
                    failure = DispatchFailure(
                        dispatch_count=dispatch.dispatch_count,
                        agent_id=dispatch.agent_id,
                        error_type=type(error).__name__,
                    )
                    failed.append(failure)
                    self._trace_event(
                        "runtime.dispatch_failed",
                        failure.model_dump(mode="json"),
                        agent_id=dispatch.agent_id,
                    )
                    continue
                successful.append(result)
                self._trace_event(
                    "runtime.outcome",
                    {
                        "agentId": dispatch.agent_id,
                        "dispatchCount": dispatch.dispatch_count,
                        "decisionId": dispatch.decision_id,
                        "status": result.world_update.status.value,
                        "entryId": result.world_update.entry_id,
                    },
                    agent_id=dispatch.agent_id,
                )
        except KeyboardInterrupt:
            self.pause_and_save(reason=WorldStopReason.INTERRUPTED)
        final_state = self.state()
        return WorldRunResult(
            world_ref=self._world_ref,
            owner_id=self._owner_id,
            successful_steps=tuple(successful),
            failed_dispatches=tuple(failed),
            final_state=final_state,
        )

    def next_dispatch(self) -> CharacterDispatch | None:
        """Choose and durably charge the next fair, runnable Character."""

        if not self._owns_lock:
            raise WorldRunnerError("resume must acquire the World before dispatch")
        with self._database.session_factory.begin() as session:
            public = self._store.load(session)
            world = public.world
            if world.status is not WorldStatus.RUNNING:
                return None
            if world.run_owner_id != self._owner_id:
                raise WorldRunnerError("World database owner does not match this Runner")
            if world.active_dispatch_count is not None:
                raise WorldRunnerError("World already has an active dispatch")
            if world.dispatch_count >= world.dispatch_limit_at:
                self._store.pause(
                    session,
                    reason=WorldStopReason.BUDGET_EXHAUSTED.value,
                    expected_owner_id=self._owner_id,
                )
                self._trace_event("runtime.pause", {"reason": "budget_exhausted"})
                return None

            runnable = tuple(node for node in public.sessions if self._is_runnable(session, node))
            if not runnable:
                self._store.pause(
                    session,
                    reason=WorldStopReason.IDLE.value,
                    expected_owner_id=self._owner_id,
                )
                self._trace_event("runtime.pause", {"reason": "idle"})
                return None
            agent_id, priority_request_entry_id = self._select(session, public.sessions, runnable)
            dispatch_count = self._store.claim_dispatch(
                session,
                owner_id=self._owner_id,
                expected_control_epoch=world.control_epoch,
                expected_dispatch_count=world.dispatch_count,
                agent_id=agent_id,
            )
            return CharacterDispatch(
                world_ref=self._world_ref,
                owner_id=self._owner_id,
                agent_id=agent_id,
                dispatch_count=dispatch_count,
                decision_id=dispatch_decision_id(
                    self._world_ref,
                    agent_id=agent_id,
                    control_epoch=world.control_epoch,
                    dispatch_count=dispatch_count,
                ),
                control_epoch=world.control_epoch,
                based_on_world_version=world.current_version,
                based_on_decision_seq=world.decision_seq,
                priority_request_entry_id=priority_request_entry_id,
            )

    def pause_and_save(self, *, reason: WorldStopReason = WorldStopReason.USER_PAUSE) -> WorldState:
        if not self._owns_lock:
            raise WorldRunnerError("this Runner does not own the World")
        with self._database.session_factory.begin() as session:
            state = self._store.pause(
                session,
                reason=reason.value,
                expected_owner_id=self._owner_id,
            )
        self._trace_event("runtime.pause", {"reason": reason.value})
        return state

    def state(self) -> WorldState:
        with self._database.session_factory() as session:
            return self._store.load(session).world

    def close(self) -> None:
        self._owns_lock = False
        self._lock.release()

    def __enter__(self) -> WorldRunner:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _select(
        self,
        session: Session,
        all_nodes: tuple[EventSessionNode, ...],
        runnable: tuple[EventSessionNode, ...],
    ) -> tuple[str, str | None]:
        pending = self._entries.unconsumed_pending_requests(session)
        runnable_by_agent = {node.agent_id: node for node in runnable}
        indexed = tuple(enumerate(pending))
        priority = min(
            (
                (runnable_by_agent[request.recipient_agent_id].last_dispatch_count, order, request)
                for order, request in indexed
                if request.recipient_agent_id in runnable_by_agent
            ),
            default=None,
            key=lambda item: (item[0], item[1], item[2].request_entry_id),
        )
        if priority is not None:
            return priority[2].recipient_agent_id, priority[2].request_entry_id

        by_root: dict[str, list[EventSessionNode]] = {}
        for node in all_nodes:
            by_root.setdefault(node.root_session_id, []).append(node)
        runnable_roots = {node.root_session_id for node in runnable}
        root = min(
            runnable_roots,
            key=lambda root_id: (
                max(node.last_dispatch_count for node in by_root[root_id]),
                root_id,
            ),
        )
        selected = min(
            (node for node in runnable if node.root_session_id == root),
            key=lambda node: (node.last_dispatch_count, node.agent_id),
        )
        return selected.agent_id, None

    def _is_runnable(self, session: Session, node: EventSessionNode) -> bool:
        threshold = node.wait_for_visible_entry_after_version
        if threshold is None:
            return True
        return self._entries.has_visible_entry_after(
            session,
            agent_id=node.agent_id,
            world_version=threshold,
        )

    def _clear_failed_dispatch(self, dispatch: CharacterDispatch) -> None:
        try:
            with self._database.session_factory.begin() as session:
                self._store.fail_dispatch(
                    session,
                    owner_id=dispatch.owner_id,
                    control_epoch=dispatch.control_epoch,
                    dispatch_count=dispatch.dispatch_count,
                    agent_id=dispatch.agent_id,
                )
        except WorldCommitConflictError:
            # An external pause may already have invalidated and cleared it.
            if self.state().status is WorldStatus.RUNNING:
                raise

    def _trace_event(
        self,
        event: str,
        data: dict[str, object],
        *,
        agent_id: str = "world-runner",
    ) -> None:
        if self._trace is None:
            return
        self._trace.write(
            event,
            json.loads(json.dumps(data, allow_nan=False)),
            trace_id=self._owner_id,
            agent_kind="runtime",
            agent_id=agent_id,
        )


def pause_world(
    database: ProjectDatabase,
    world_ref: WorldRef,
    *,
    reason: WorldStopReason = WorldStopReason.USER_PAUSE,
) -> WorldState:
    """External fencing pause; safe even while a Provider call is still in flight."""

    with database.session_factory.begin() as session:
        return WorldStore(world_ref).pause(session, reason=reason.value)
