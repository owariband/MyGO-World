"""Deterministic hard-visibility projection for one Character decision."""

from __future__ import annotations

import json
from collections.abc import Iterable
from hashlib import sha256

from sqlalchemy.orm import Session

from agent_runtime.scenario import ObjectSeed
from agent_runtime.world.affordances import WorldAffordanceResolver
from agent_runtime.world.contracts import (
    Affordance,
    AgentView,
    AttentionTier,
    CharacterTarget,
    CommitPosition,
    DeliveryChannel,
    PerceptCandidate,
    PerceptionChannel,
    ProposalKind,
    WorldRef,
)
from agent_runtime.world.entries import (
    ActionEntry,
    BehaviorEntry,
    DialogueEntry,
    EventEntry,
    InteractionRequest,
)
from agent_runtime.world.entry_storage import EventEntryStore
from agent_runtime.world.state import (
    AgentWorldState,
    EventSessionNode,
    ObjectState,
    PublicWorldState,
    WorldFact,
)
from agent_runtime.world.storage import WorldStore


class AgentViewBuildError(RuntimeError):
    """Current committed state cannot produce a safe Agent view."""


class AgentViewStaleError(AgentViewBuildError):
    """The requested observation cursor is not valid for the current snapshot."""


class AgentViewCatalogError(AgentViewBuildError):
    """The trusted Scenario object catalog does not match the loaded World."""


class AgentViewBuilder:
    """Build one read-only view from public state and materialized recipients."""

    def __init__(self, world_ref: WorldRef, object_seeds: tuple[ObjectSeed, ...]) -> None:
        self._world_ref = WorldRef.model_validate(world_ref, strict=True)
        seeds = tuple(ObjectSeed.model_validate(item, strict=True) for item in object_seeds)
        seed_ids = tuple(item.id for item in seeds)
        if len(seed_ids) != len(set(seed_ids)):
            raise AgentViewCatalogError("Scenario object catalog IDs must be unique")
        self._object_seeds = {item.id: item for item in seeds}

    @property
    def world_ref(self) -> WorldRef:
        return self._world_ref

    def build(
        self,
        session: Session,
        *,
        agent_id: str,
        observed_through: CommitPosition | None = None,
        priority_request_entry_id: str | None = None,
        restrict_pending_priority: bool = False,
    ) -> AgentView:
        """Project current committed facts into one Character's hard-visible input."""

        cursor = (
            None
            if observed_through is None
            else CommitPosition.model_validate(observed_through, strict=True)
        )
        public = WorldStore(self._world_ref).load(session)
        actor, actor_session = self._actor(public, agent_id)
        self._validate_catalog(public.objects)

        current_version = public.world.current_version
        if cursor is not None and (
            cursor.world_version > current_version or cursor.entry_index != 0
        ):
            raise AgentViewStaleError(
                "observation cursor is not valid at the current World version"
            )

        entry_store = EventEntryStore(self._world_ref)
        visible_entries = entry_store.list_visible_after(session, agent_id)
        self._validate_visible_history(visible_entries, current_version)
        visible_by_id = {entry.entry_id: entry for entry in visible_entries}
        if cursor is not None and _position_key(cursor) not in {
            _position_key(entry.commit_position) for entry in visible_entries
        }:
            raise AgentViewStaleError(
                "observation cursor does not identify a visible committed Entry"
            )

        pending = entry_store.pending_requests_for(session, agent_id)
        pending_sources = self._validate_pending_requests(
            pending,
            visible_by_id=visible_by_id,
            current_version=current_version,
        )
        if (
            priority_request_entry_id is not None
            and priority_request_entry_id not in pending_sources
        ):
            raise AgentViewBuildError("priority request is not pending for this Agent")
        mandatory_entry_ids = (
            frozenset((priority_request_entry_id,) if priority_request_entry_id is not None else ())
            if restrict_pending_priority
            else frozenset(pending_sources)
        )
        new_entries = tuple(
            entry
            for entry in visible_entries
            if cursor is None or _position_key(entry.commit_position) > _position_key(cursor)
        )
        candidate_entries = _merge_candidate_entries(new_entries, pending_sources)

        candidates = (
            *(
                self._entry_candidate(
                    entry,
                    agent_id=agent_id,
                    pending_entry_ids=mandatory_entry_ids,
                )
                for entry in candidate_entries
            ),
            *self._ambient_candidates(public, actor),
        )
        resolver = WorldAffordanceResolver(
            world_ref=self._world_ref,
            world_version=current_version,
            agent_id=agent_id,
        )
        affordances = self._affordances(
            public,
            actor,
            actor_session,
            pending,
            pending_sources,
            resolver,
        )
        next_cursor = (
            max((entry.commit_position for entry in new_entries), key=_position_key)
            if new_entries
            else cursor
        )
        return AgentView(
            world_ref=self._world_ref,
            agent_id=agent_id,
            event_session_id=actor_session.session_id,
            based_on_world_version=current_version,
            based_on_control_epoch=public.world.control_epoch,
            based_on_decision_seq=public.world.decision_seq,
            current_location_id=actor.location_id,
            world_time=public.world.world_time,
            observed_through=next_cursor,
            candidates=candidates,
            visible_evidence_ids=_visible_evidence_ids(candidates),
            affordances=affordances,
        )

    def _actor(
        self,
        public: PublicWorldState,
        agent_id: str,
    ) -> tuple[AgentWorldState, EventSessionNode]:
        actor = next((item for item in public.agents if item.agent_id == agent_id), None)
        actor_session = next(
            (item for item in public.sessions if item.agent_id == agent_id),
            None,
        )
        if actor is None or actor_session is None:
            raise AgentViewBuildError(f'Agent "{agent_id}" does not exist in the current World')
        return actor, actor_session

    def _validate_catalog(self, objects: tuple[ObjectState, ...]) -> None:
        states = {item.object_id: item for item in objects}
        if states.keys() != self._object_seeds.keys():
            raise AgentViewCatalogError(
                "Scenario object catalog IDs do not match current World object IDs"
            )
        for object_id, state in states.items():
            seed = self._object_seeds[object_id]
            if (
                state.name,
                state.kind,
                state.description,
                state.owner_agent_id,
            ) != (
                seed.name,
                seed.kind,
                seed.description,
                seed.owner_agent_id,
            ):
                raise AgentViewCatalogError(
                    f'Scenario object "{object_id}" does not match current World identity'
                )

    @staticmethod
    def _validate_visible_history(
        entries: tuple[EventEntry, ...],
        current_version: int,
    ) -> None:
        entry_ids = tuple(entry.entry_id for entry in entries)
        if len(entry_ids) != len(set(entry_ids)):
            raise AgentViewBuildError("visible committed Entry IDs must be unique")
        if any(entry.commit_position.world_version > current_version for entry in entries):
            raise AgentViewBuildError("visible Entry is newer than the current World version")

    @staticmethod
    def _validate_pending_requests(
        requests: tuple[InteractionRequest, ...],
        *,
        visible_by_id: dict[str, EventEntry],
        current_version: int,
    ) -> dict[str, DialogueEntry]:
        sources: dict[str, DialogueEntry] = {}
        for request in requests:
            source = visible_by_id.get(request.request_entry_id)
            if not isinstance(source, DialogueEntry):
                raise AgentViewBuildError(
                    f'pending request "{request.request_entry_id}" lacks a visible DialogueEntry'
                )
            if (
                source.actor_agent_id != request.requester_agent_id
                or source.target_agent_id != request.recipient_agent_id
            ):
                raise AgentViewBuildError(
                    f'pending request "{request.request_entry_id}" disagrees with its source'
                )
            if (
                request.updated_world_version < source.commit_position.world_version
                or request.updated_world_version > current_version
            ):
                raise AgentViewBuildError(
                    f'pending request "{request.request_entry_id}" has an invalid World version'
                )
            sources[source.entry_id] = source
        return sources

    @staticmethod
    def _entry_candidate(
        entry: EventEntry,
        *,
        agent_id: str,
        pending_entry_ids: frozenset[str],
    ) -> PerceptCandidate:
        if entry.actor_agent_id == agent_id:
            channel = PerceptionChannel.SELF
        elif isinstance(entry, DialogueEntry):
            channel = (
                PerceptionChannel.TARGETED_MESSAGE
                if entry.delivery_channel is DeliveryChannel.WHISPER
                else (
                    PerceptionChannel.DIRECT_INTERACTION
                    if entry.target_agent_id == agent_id
                    else PerceptionChannel.SAME_SCENE
                )
            )
        else:
            channel = PerceptionChannel.SAME_SCENE

        mandatory = entry.entry_id in pending_entry_ids and entry.actor_agent_id != agent_id
        if isinstance(entry, DialogueEntry):
            object_id = entry.target_agent_id
            predicate = "said"
            visible_fields = (
                "actor_agent_id",
                "target_agent_id",
                "delivery_channel",
                "occurred_at",
                "text",
            )
            tags = ("dialogue", entry.delivery_channel.value)
        elif isinstance(entry, ActionEntry):
            object_id = entry.target_object_id
            predicate = entry.operation_id
            visible_fields = (
                "actor_agent_id",
                "target_object_id",
                "operation_id",
                "occurred_at",
                "text",
            )
            tags = ("action", entry.operation_id)
        elif isinstance(entry, BehaviorEntry):
            object_id = None
            predicate = entry.operation_id
            visible_fields = (
                "actor_agent_id",
                "operation_id",
                "occurred_at",
                "text",
            )
            tags = ("behavior", entry.operation_id)
        else:
            object_id = entry.target_agent_id
            predicate = entry.transition_reason.value
            visible_fields = (
                "actor_agent_id",
                "target_agent_id",
                "transition_reason",
                "occurred_at",
                "text",
            )
            tags = ("session_transition", entry.transition_reason.value)
        return PerceptCandidate(
            candidate_id=f"entry-{entry.entry_id}",
            channel=channel,
            attention_tier=(AttentionTier.MANDATORY if mandatory else AttentionTier.RELEVANT),
            subject=entry.actor_agent_id,
            predicate=predicate,
            object=object_id,
            content=entry.text,
            salience=1.0 if mandatory else 0.75,
            source_entry_id=entry.entry_id,
            visible_fields=visible_fields,
            tags=tags,
        )

    @staticmethod
    def _ambient_candidates(
        public: PublicWorldState,
        actor: AgentWorldState,
    ) -> tuple[PerceptCandidate, ...]:
        locations = {item.location_id: item for item in public.locations}
        location = locations[actor.location_id]
        candidates: list[PerceptCandidate] = [
            PerceptCandidate(
                candidate_id=f"location-{location.location_id}",
                channel=PerceptionChannel.SAME_SCENE,
                attention_tier=AttentionTier.AMBIENT,
                subject=location.location_id,
                predicate="location",
                content=f"{location.name}: {location.description}",
                salience=0.25,
                visible_fields=("name", "description"),
                tags=("location",),
            )
        ]
        local_agent_ids = {
            item.agent_id for item in public.agents if item.location_id == actor.location_id
        }
        local_object_ids = {
            item.object_id for item in public.objects if item.location_id == actor.location_id
        }
        for item in public.agents:
            if item.agent_id not in local_agent_ids:
                continue
            identity = (item.agent_id, item.location_id, item.public_status)
            candidates.append(
                PerceptCandidate(
                    candidate_id=_ambient_id("agent", identity),
                    channel=(
                        PerceptionChannel.SELF
                        if item.agent_id == actor.agent_id
                        else PerceptionChannel.SAME_SCENE
                    ),
                    attention_tier=(
                        AttentionTier.RELEVANT
                        if item.agent_id == actor.agent_id
                        else AttentionTier.AMBIENT
                    ),
                    subject=item.agent_id,
                    predicate="located_at",
                    object=item.location_id,
                    content=item.public_status or f"Present at {location.name}",
                    salience=0.5 if item.agent_id == actor.agent_id else 0.25,
                    visible_fields=(
                        ("location_id", "public_status")
                        if item.public_status is not None
                        else ("location_id",)
                    ),
                    tags=("agent", "presence"),
                )
            )
        for item in public.objects:
            if item.object_id not in local_object_ids:
                continue
            identity = (item.object_id, item.location_id, item.state)
            candidates.append(
                PerceptCandidate(
                    candidate_id=_ambient_id("object", identity),
                    channel=PerceptionChannel.SAME_SCENE,
                    attention_tier=AttentionTier.AMBIENT,
                    subject=item.object_id,
                    predicate="state",
                    object=item.state,
                    content=f"{item.name}: {item.description}",
                    salience=0.3,
                    visible_fields=("name", "kind", "description", "location_id", "state"),
                    tags=("object", item.kind),
                )
            )
        candidates.extend(
            _fact_candidate(fact, actor.agent_id)
            for fact in public.facts
            if _fact_is_local(
                fact,
                location_id=actor.location_id,
                local_agent_ids=local_agent_ids,
                local_object_ids=local_object_ids,
            )
        )
        return tuple(sorted(candidates, key=lambda item: item.candidate_id))

    def _affordances(
        self,
        public: PublicWorldState,
        actor: AgentWorldState,
        actor_session: EventSessionNode,
        pending: tuple[InteractionRequest, ...],
        pending_sources: dict[str, DialogueEntry],
        resolver: WorldAffordanceResolver,
    ) -> tuple[Affordance, ...]:
        affordances = [
            *resolver.behavior_affordances(),
            resolver.simple_affordance(ProposalKind.WAIT),
        ]
        same_root_agent_ids = sorted(
            node.agent_id
            for node in public.sessions
            if node.root_session_id == actor_session.root_session_id
            and node.agent_id != actor.agent_id
        )
        dialogue_allowed = actor_session.consecutive_dialogue_turns < 50
        if dialogue_allowed:
            for target_id in same_root_agent_ids:
                target = CharacterTarget(id=target_id)
                affordances.extend(
                    resolver.utter_affordance(target=target, delivery_channel=channel)
                    for channel in (DeliveryChannel.DIRECT, DeliveryChannel.WHISPER)
                )

        if same_root_agent_ids:
            affordances.append(resolver.leave_session_affordance())

        local_sessions = {
            item.agent_id: next(node for node in public.sessions if node.agent_id == item.agent_id)
            for item in public.agents
            if item.location_id == actor.location_id and item.agent_id != actor.agent_id
        }
        for target_id, target_session in sorted(local_sessions.items()):
            if target_session.root_session_id != actor_session.root_session_id:
                affordances.append(
                    resolver.join_session_affordance(target=CharacterTarget(id=target_id))
                )

        for item in public.objects:
            if item.location_id == actor.location_id:
                affordances.extend(
                    resolver.object_affordances(
                        object_state=item,
                        object_seed=self._object_seeds[item.object_id],
                    )
                )

        same_root = frozenset(same_root_agent_ids)
        if dialogue_allowed:
            for request in pending:
                if request.requester_agent_id not in same_root:
                    continue
                source = pending_sources[request.request_entry_id]
                affordances.append(
                    resolver.response_affordance(
                        target=CharacterTarget(id=request.requester_agent_id),
                        delivery_channel=source.delivery_channel,
                        request_entry_id=request.request_entry_id,
                    )
                )
        return tuple(sorted(affordances, key=_affordance_key))


def _merge_candidate_entries(
    new_entries: tuple[EventEntry, ...],
    pending_sources: dict[str, DialogueEntry],
) -> tuple[EventEntry, ...]:
    entries = {entry.entry_id: entry for entry in new_entries}
    entries.update(pending_sources)
    return tuple(sorted(entries.values(), key=lambda item: _position_key(item.commit_position)))


def _position_key(position: CommitPosition) -> tuple[int, int]:
    return position.world_version, position.entry_index


def _visible_evidence_ids(candidates: Iterable[PerceptCandidate]) -> tuple[str, ...]:
    evidence: list[str] = []
    for candidate in candidates:
        if candidate.source_entry_id is not None:
            evidence.append(candidate.source_entry_id)
        evidence.extend(candidate.source_fact_refs)
        evidence.extend(candidate.source_info_refs)
    return tuple(dict.fromkeys(evidence))


def _ambient_id(kind: str, identity: tuple[str | None, ...]) -> str:
    canonical = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
    return f"{kind}-{sha256(canonical.encode()).hexdigest()}"


def _fact_is_local(
    fact: WorldFact,
    *,
    location_id: str,
    local_agent_ids: set[str],
    local_object_ids: set[str],
) -> bool:
    if fact.location_id is not None:
        return fact.location_id == location_id
    if fact.agent_id is not None:
        return fact.agent_id in local_agent_ids
    if fact.object_id is not None:
        return fact.object_id in local_object_ids
    return True


def _fact_candidate(fact: WorldFact, agent_id: str) -> PerceptCandidate:
    subject = fact.location_id or fact.agent_id or fact.object_id or "world"
    owner_field = (
        "location_id"
        if fact.location_id is not None
        else (
            "agent_id"
            if fact.agent_id is not None
            else "object_id"
            if fact.object_id is not None
            else None
        )
    )
    return PerceptCandidate(
        candidate_id=_ambient_id(
            "fact",
            (fact.fact_id, fact.predicate, fact.object, fact.content, subject),
        ),
        channel=(
            PerceptionChannel.SELF if fact.agent_id == agent_id else PerceptionChannel.SAME_SCENE
        ),
        attention_tier=AttentionTier.AMBIENT,
        subject=subject,
        predicate=fact.predicate,
        object=fact.object,
        content=fact.content,
        salience=0.3,
        visible_fields=(
            *((owner_field,) if owner_field is not None else ()),
            "predicate",
            "object",
            "content",
        ),
        tags=("fact",),
        source_fact_refs=(fact.fact_id,),
    )


def _affordance_key(item: Affordance) -> tuple[str, str, str, str, str, str]:
    return (
        item.kind.value,
        item.target.kind if item.target is not None else "",
        item.target.id if item.target is not None else "",
        item.operation_id or "",
        item.delivery_channel.value if item.delivery_channel is not None else "",
        item.request_entry_id or "",
    )
