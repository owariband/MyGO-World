"""Create and load one paused World without invoking any Agent."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agent_runtime.agent.memory.contracts import MemoryKind, MemoryRecord
from agent_runtime.agent.memory.storage import MemoryStore
from agent_runtime.agent.memory.stream import MemoryStream
from agent_runtime.agent.personact.compiler import (
    Catalog,
    CompiledMemorySeed,
    CompiledPersonActSpec,
    compile_manifest,
)
from agent_runtime.agent.personact.state import CognitiveConfig, PersonaState
from agent_runtime.agent.personact.storage import PersonaStateStore, StoredPersonaState
from agent_runtime.common.union_part import UnionPart
from agent_runtime.scenario import (
    AllAgents,
    FactSubject,
    KnowledgeSeed,
    LoadedScenario,
    ScenarioSeed,
    WorldFactSeed,
    load_project_scenario,
)
from agent_runtime.sqlite import (
    ProjectDatabase,
    ProjectDatabaseNotFoundError,
    open_project_database,
)
from agent_runtime.world.contracts import WorldRef
from agent_runtime.world.initializer import initialize_public_world
from agent_runtime.world.state import (
    AgentWorldState,
    EventSessionNode,
    LocationState,
    ObjectState,
    PublicWorldState,
    WorldFact,
    WorldState,
    WorldStatus,
)
from agent_runtime.world.storage import WorldAlreadyExistsError as StoredWorldAlreadyExistsError
from agent_runtime.world.storage import WorldStore


class WorldBootstrapError(RuntimeError):
    """Base error for deterministic World creation and loading."""


class WorldAlreadyExistsError(WorldBootstrapError):
    """The requested World already exists and cannot be overwritten."""


class WorldNotFoundError(WorldBootstrapError):
    """The requested World does not exist in its Project database."""


class WorldConfigurationMismatchError(WorldBootstrapError):
    """Current Project sources do not match the saved World identity."""


class WorldStatusError(WorldBootstrapError):
    """M2 cannot safely load a World that is not paused."""


# These values are persisted inside every PersonaState. Changing the default only
# affects newly created Worlds; loading never reapplies it to an existing save.
MVP_COGNITIVE_CONFIG_V1 = CognitiveConfig(
    attention_budget=1,
    retention=20,
    recency_weight=1.0,
    relevance_weight=1.0,
    importance_weight=1.0,
    recency_decay=0.99,
    reflection_threshold=10.0,
    reflection_count=5,
)


@dataclass(frozen=True, slots=True)
class LoadedWorld:
    """Trusted all-role assembly of a paused save, never an Agent/Director view."""

    public_state: PublicWorldState
    specs: tuple[CompiledPersonActSpec, ...]
    persona_states: tuple[StoredPersonaState, ...]
    memory_streams: tuple[MemoryStream, ...]
    session_partition: UnionPart[str]

    @property
    def world_ref(self) -> WorldRef:
        return self.public_state.world.world_ref


@dataclass(frozen=True, slots=True)
class _PreparedWorld:
    public_state: PublicWorldState
    specs: tuple[CompiledPersonActSpec, ...]
    persona_states: tuple[PersonaState, ...]
    memory_streams: tuple[MemoryStream, ...]


def create_world(
    repository_root: Path,
    project_id: str,
    world_id: str,
    *,
    catalog: Catalog,
    cognitive_config: CognitiveConfig = MVP_COGNITIVE_CONFIG_V1,
    clock: Callable[[], datetime] | None = None,
) -> LoadedWorld:
    """Create one new paused save in a single cross-domain transaction."""

    source = load_project_scenario(repository_root / "projects", project_id)
    world_ref = WorldRef(project_id=project_id, world_id=world_id)
    created_at = (clock or _utc_now)()
    prepared = _prepare_world(
        world_ref=world_ref,
        source=source,
        catalog=catalog,
        cognitive_config=cognitive_config,
        created_at=created_at,
    )
    loaded = _loaded_from_prepared(prepared)
    database = open_project_database(repository_root / ".runtime", project_id, create=True)
    try:
        try:
            with database.session_factory() as session, session.begin():
                initialize_public_world(session, prepared.public_state)
                state_store = PersonaStateStore(world_ref)
                memory_store = MemoryStore(world_ref)
                specs = {spec.agent_id: spec for spec in prepared.specs}
                for state in prepared.persona_states:
                    state_store.insert(
                        session,
                        state,
                        specs[state.agent_id].digest,
                        revision=1,
                    )
                for stream in prepared.memory_streams:
                    memory_store.insert_stream(session, stream)
                session.flush()
        except StoredWorldAlreadyExistsError as error:
            raise WorldAlreadyExistsError(
                f'World "{world_ref.world_id}" already exists in Project "{world_ref.project_id}"'
            ) from error
        return loaded
    finally:
        database.dispose()


def load_world(
    repository_root: Path,
    project_id: str,
    world_id: str,
    *,
    catalog: Catalog,
) -> LoadedWorld:
    """Load one existing save; never create, resume, or call an Agent."""

    source = load_project_scenario(repository_root / "projects", project_id)
    specs = _compile_specs(source, catalog)
    world_ref = WorldRef(project_id=project_id, world_id=world_id)
    try:
        database = open_project_database(
            repository_root / ".runtime",
            project_id,
            create=False,
        )
    except ProjectDatabaseNotFoundError as error:
        raise WorldNotFoundError(
            f'World "{world_ref.world_id}" does not exist in Project "{world_ref.project_id}"'
        ) from error
    try:
        return _load_prepared_world(database, source, specs, world_ref=world_ref)
    finally:
        database.dispose()


def _prepare_world(
    *,
    world_ref: WorldRef,
    source: LoadedScenario,
    catalog: Catalog,
    cognitive_config: CognitiveConfig,
    created_at: datetime,
) -> _PreparedWorld:
    config = CognitiveConfig.model_validate(cognitive_config, strict=True)
    specs = _compile_specs(source, catalog)
    public_state = _build_public_state(world_ref, source, created_at)
    persona_states = _build_persona_states(world_ref, source.seed, specs, config)
    memory_streams = _build_memory_streams(world_ref, source, specs)
    return _PreparedWorld(
        public_state=public_state,
        specs=specs,
        persona_states=persona_states,
        memory_streams=memory_streams,
    )


def _compile_specs(
    source: LoadedScenario,
    catalog: Catalog,
) -> tuple[CompiledPersonActSpec, ...]:
    specs = tuple(
        sorted(compile_manifest(source.manifest, catalog), key=lambda item: item.agent_id)
    )
    if tuple(spec.agent_id for spec in specs) != tuple(
        agent.agent_id for agent in source.seed.agents
    ):
        raise WorldConfigurationMismatchError(
            "compiled Agent identities do not match the canonical Scenario"
        )
    return specs


def _build_public_state(
    world_ref: WorldRef,
    source: LoadedScenario,
    created_at: datetime,
) -> PublicWorldState:
    seed = source.seed
    root_by_session = {
        member: partition.root_session_id
        for partition in seed.initial_partitions
        for member in partition.member_session_ids
    }
    locations = tuple(
        LocationState(
            world_ref=world_ref,
            location_id=item.id,
            name=item.name,
            description=item.description,
        )
        for item in seed.locations
    )
    agents = tuple(
        AgentWorldState(
            world_ref=world_ref,
            agent_id=item.agent_id,
            location_id=item.location_id,
            public_status=item.public_status,
        )
        for item in seed.agents
    )
    objects = tuple(
        ObjectState(
            world_ref=world_ref,
            object_id=item.id,
            name=item.name,
            kind=item.kind,
            description=item.description,
            location_id=item.location_id,
            owner_agent_id=item.owner_agent_id,
            state=item.state,
        )
        for item in seed.objects
    )
    facts = tuple(_world_fact(world_ref, item.subject, item) for item in seed.public_facts)
    sessions = tuple(
        EventSessionNode(
            world_ref=world_ref,
            session_id=item.session_id,
            agent_id=item.agent_id,
            root_session_id=root_by_session[item.session_id],
            topology_version=1,
            updated_world_version=1,
        )
        for item in sorted(seed.agents, key=lambda value: value.session_id)
    )
    return PublicWorldState(
        world=WorldState(
            world_ref=world_ref,
            seed_id=seed.seed_id,
            seed_version=seed.version,
            seed_hash=source.seed_hash,
            current_version=1,
            world_time=seed.world_time,
            status=WorldStatus.PAUSED,
            created_at=created_at,
        ),
        locations=locations,
        agents=agents,
        objects=objects,
        facts=facts,
        sessions=sessions,
    )


def _world_fact(
    world_ref: WorldRef,
    subject: FactSubject,
    fact: WorldFactSeed,
) -> WorldFact:
    owner: dict[str, str | None] = {
        "location_id": None,
        "agent_id": None,
        "object_id": None,
    }
    if subject.kind == "location":
        owner["location_id"] = subject.id
    elif subject.kind == "agent":
        owner["agent_id"] = subject.id
    elif subject.kind == "object":
        owner["object_id"] = subject.id
    return WorldFact(
        world_ref=world_ref,
        fact_id=fact.id,
        predicate=fact.predicate,
        object=fact.object,
        content=fact.content,
        **owner,
    )


def _build_persona_states(
    world_ref: WorldRef,
    seed: ScenarioSeed,
    specs: tuple[CompiledPersonActSpec, ...],
    config: CognitiveConfig,
) -> tuple[PersonaState, ...]:
    location_by_agent = {item.agent_id: item.location_id for item in seed.agents}
    return tuple(
        PersonaState(
            world_ref=world_ref,
            agent_id=spec.agent_id,
            cognitive_config=config,
            reflection_remaining=config.reflection_threshold,
            known_place_ids=(location_by_agent[spec.agent_id],),
        )
        for spec in specs
    )


def _build_memory_streams(
    world_ref: WorldRef,
    source: LoadedScenario,
    specs: tuple[CompiledPersonActSpec, ...],
) -> tuple[MemoryStream, ...]:
    all_agent_ids = frozenset(spec.agent_id for spec in specs)
    streams: list[MemoryStream] = []
    for spec in specs:
        manifest_records = tuple(
            _manifest_memory(world_ref, spec, item, source.seed.world_time) for item in spec.seeds
        )
        scenario_records = tuple(
            _scenario_memory(world_ref, spec, item, source)
            for item in source.seed.knowledge
            if spec.agent_id in _knowledge_recipients(item, all_agent_ids)
        )
        streams.append(
            MemoryStream(
                world_ref=world_ref,
                agent_id=spec.agent_id,
                scope=spec.memory_scope,
                records=(*manifest_records, *scenario_records),
            )
        )
    return tuple(streams)


def _manifest_memory(
    world_ref: WorldRef,
    spec: CompiledPersonActSpec,
    seed: CompiledMemorySeed,
    world_time: datetime,
) -> MemoryRecord:
    return MemoryRecord(
        id=seed.id,
        world_ref=world_ref,
        agent_id=spec.agent_id,
        scope=spec.memory_scope,
        kind=MemoryKind.THOUGHT,
        created_at=world_time,
        last_accessed_at=world_time,
        subject=spec.agent_id,
        predicate=seed.type,
        content=seed.content,
        poignancy=1.0,
        tags=seed.tags,
        source=seed.provenance,
        novelty_key=seed.provenance,
    )


def _scenario_memory(
    world_ref: WorldRef,
    spec: CompiledPersonActSpec,
    seed: KnowledgeSeed,
    source: LoadedScenario,
) -> MemoryRecord:
    provenance = f"scenario:{source.seed_hash}#{seed.id}"
    return MemoryRecord(
        id=seed.id,
        world_ref=world_ref,
        agent_id=spec.agent_id,
        scope=spec.memory_scope,
        kind=seed.kind,
        created_at=source.seed.world_time,
        last_accessed_at=source.seed.world_time,
        subject=seed.subject,
        predicate=seed.predicate,
        object=seed.object,
        content=seed.content,
        poignancy=seed.poignancy,
        tags=seed.tags,
        source=provenance,
        novelty_key=provenance,
    )


def _knowledge_recipients(
    seed: KnowledgeSeed,
    all_agent_ids: frozenset[str],
) -> frozenset[str]:
    if isinstance(seed.recipients, AllAgents):
        return all_agent_ids
    return frozenset(seed.recipients.agent_ids)


def _load_prepared_world(
    database: ProjectDatabase,
    source: LoadedScenario,
    specs: tuple[CompiledPersonActSpec, ...],
    world_ref: WorldRef,
) -> LoadedWorld:
    store = WorldStore(world_ref)
    with database.session_factory() as session, session.begin():
        if not store.exists(session):
            raise WorldNotFoundError(
                f'World "{world_ref.world_id}" does not exist in Project "{world_ref.project_id}"'
            )
        public_state = store.load(session)
        persona_states = PersonaStateStore(world_ref).load_all_for_bootstrap(session)
        stored_streams = MemoryStore(world_ref).load_all_for_bootstrap(session)

    _require_configuration_match(public_state, persona_states, source, specs)
    streams = _complete_memory_streams(world_ref, specs, stored_streams)
    _require_initial_memories_present(world_ref, source, specs, streams)
    return LoadedWorld(
        public_state=public_state,
        specs=specs,
        persona_states=persona_states,
        memory_streams=streams,
        session_partition=_restore_partition(public_state),
    )


def _loaded_from_prepared(prepared: _PreparedWorld) -> LoadedWorld:
    digests = {spec.agent_id: spec.digest for spec in prepared.specs}
    return LoadedWorld(
        public_state=prepared.public_state,
        specs=prepared.specs,
        persona_states=tuple(
            StoredPersonaState(
                state_revision=1,
                spec_digest=digests[state.agent_id],
                state=state,
            )
            for state in prepared.persona_states
        ),
        memory_streams=prepared.memory_streams,
        session_partition=_restore_partition(prepared.public_state),
    )


def _require_configuration_match(
    public_state: PublicWorldState,
    persona_states: tuple[StoredPersonaState, ...],
    source: LoadedScenario,
    specs: tuple[CompiledPersonActSpec, ...],
) -> None:
    world = public_state.world
    expected_seed = (source.seed.seed_id, source.seed.version, source.seed_hash)
    actual_seed = (world.seed_id, world.seed_version, world.seed_hash)
    if actual_seed != expected_seed:
        raise WorldConfigurationMismatchError("saved Scenario identity does not match sources")
    if world.status is not WorldStatus.PAUSED:
        raise WorldStatusError(
            f'M2 can only load paused Worlds; "{world.world_ref.world_id}" is '
            f'"{world.status.value}"'
        )

    expected_digests = {spec.agent_id: spec.digest for spec in specs}
    stored_digests = {item.state.agent_id: item.spec_digest for item in persona_states}
    if stored_digests != expected_digests:
        raise WorldConfigurationMismatchError("saved Agent specs do not match sources")
    public_agent_ids = {item.agent_id for item in public_state.agents}
    if public_agent_ids != set(expected_digests):
        raise WorldConfigurationMismatchError("saved public Agent set does not match sources")
    expected_sessions = {item.agent_id: item.session_id for item in source.seed.agents}
    stored_sessions = {item.agent_id: item.session_id for item in public_state.sessions}
    if stored_sessions != expected_sessions:
        raise WorldConfigurationMismatchError(
            "saved stable EventSession identities do not match the Scenario"
        )


def _complete_memory_streams(
    world_ref: WorldRef,
    specs: tuple[CompiledPersonActSpec, ...],
    stored: tuple[MemoryStream, ...],
) -> tuple[MemoryStream, ...]:
    by_agent = {stream.agent_id: stream for stream in stored}
    expected_agents = {spec.agent_id for spec in specs}
    if not set(by_agent).issubset(expected_agents):
        raise WorldConfigurationMismatchError("saved Memory contains an unknown Agent")
    scope_by_agent = {spec.agent_id: spec.memory_scope for spec in specs}
    if any(
        stream.scope != scope_by_agent[stream.agent_id]
        for stream in stored
        if stream.agent_id in scope_by_agent
    ):
        raise WorldConfigurationMismatchError("saved Memory scope does not match Agent specs")
    return tuple(
        by_agent.get(
            spec.agent_id,
            MemoryStream(
                world_ref=world_ref,
                agent_id=spec.agent_id,
                scope=spec.memory_scope,
            ),
        )
        for spec in specs
    )


def _require_initial_memories_present(
    world_ref: WorldRef,
    source: LoadedScenario,
    specs: tuple[CompiledPersonActSpec, ...],
    stored: tuple[MemoryStream, ...],
) -> None:
    expected = _build_memory_streams(world_ref, source, specs)
    expected_ids = {
        stream.agent_id: frozenset(record.id for record in stream.records) for stream in expected
    }
    actual_ids = {
        stream.agent_id: frozenset(record.id for record in stream.records) for stream in stored
    }
    if any(not ids.issubset(actual_ids[agent_id]) for agent_id, ids in expected_ids.items()):
        raise WorldConfigurationMismatchError("saved World is missing initial Agent Memory")


def _restore_partition(state: PublicWorldState) -> UnionPart[str]:
    partition = UnionPart(node.session_id for node in state.sessions)
    members_by_root: dict[str, list[str]] = {}
    for node in state.sessions:
        members_by_root.setdefault(node.root_session_id, []).append(node.session_id)
    for root, members in sorted(members_by_root.items()):
        partition.merge(
            keep_root=root,
            merged_roots=tuple(sorted(member for member in members if member != root)),
        )
    return partition


def _utc_now() -> datetime:
    return datetime.now(UTC)
