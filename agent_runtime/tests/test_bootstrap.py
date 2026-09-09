"""End-to-end checks for paused World creation and restart loading."""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

import agent_runtime.bootstrap as bootstrap_runtime
from agent_runtime.agent.memory.storage import MemoryStore
from agent_runtime.agent.memory.stream import MemoryStream
from agent_runtime.agent.personact.agent import PersonActAgent
from agent_runtime.agent.personact.compiler import (
    Catalog,
    PromptDefinition,
    ToolDefinition,
    ToolMode,
)
from agent_runtime.agent.personact.state import CognitiveConfig
from agent_runtime.agent.skill import RuntimeSkillCatalog
from agent_runtime.bootstrap import (
    MVP_COGNITIVE_CONFIG_V1,
    WorldAlreadyExistsError,
    WorldConfigurationMismatchError,
    WorldNotFoundError,
    WorldStatusError,
    create_world,
    load_world,
)
from agent_runtime.sqlite import open_project_database
from agent_runtime.world.state import PublicWorldState, WorldStatus

REPOSITORY_ROOT = Path(__file__).parents[2]
PROJECT_IDS = ("for-the-band", "rain-after")


@pytest.fixture
def runtime_repository(tmp_path: Path) -> Path:
    for project_id in PROJECT_IDS:
        target = tmp_path / "projects" / project_id
        target.mkdir(parents=True)
        for filename in ("project.json", "agents.json", "scenario.yaml"):
            shutil.copy2(REPOSITORY_ROOT / "projects" / project_id / filename, target / filename)
    return tmp_path


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    skills = RuntimeSkillCatalog.load(REPOSITORY_ROOT / "content" / "skills")
    return Catalog(
        tools=(
            ToolDefinition(
                id="visible_location.query",
                version="1",
                mode=ToolMode.QUERY,
            ),
        ),
        prompts=(PromptDefinition(id="personact.v1", version="1", digest="prompt-v1"),),
        skills=skills.skills,
    )


def test_create_and_restart_load_complete_paused_world(
    runtime_repository: Path,
    catalog: Catalog,
) -> None:
    created = create_world(
        runtime_repository,
        "for-the-band",
        "save-001",
        catalog=catalog,
    )

    assert created.world_ref.project_id == "for-the-band"
    assert created.world_ref.world_id == "save-001"
    assert created.public_state.world.status is WorldStatus.PAUSED
    assert created.public_state.world.current_version == 1
    assert tuple(item.agent_id for item in created.public_state.agents) == (
        "anon",
        "rana",
        "soyo",
        "taki",
        "tomori",
    )
    assert len(created.public_state.sessions) == len(created.public_state.agents) == 5
    assert created.session_partition.connected("session-anon", "session-soyo")
    assert not created.session_partition.connected("session-anon", "session-taki")
    assert not created.session_partition.connected("session-anon", "session-tomori")
    assert not created.session_partition.connected("session-anon", "session-rana")
    assert all(
        item.state.cognitive_config == MVP_COGNITIVE_CONFIG_V1 for item in created.persona_states
    )
    assert all(item.state.last_world_time is None for item in created.persona_states)
    assert all(item.state.last_world_version is None for item in created.persona_states)

    restarted = load_world(
        runtime_repository,
        "for-the-band",
        "save-001",
        catalog=catalog,
    )

    assert restarted.public_state == created.public_state
    assert restarted.specs == created.specs
    assert restarted.persona_states == created.persona_states
    assert restarted.memory_streams == created.memory_streams
    assert restarted.session_partition.root_of("session-anon") == "session-anon"
    assert restarted.session_partition.members_of("session-anon") == {
        "session-anon",
        "session-soyo",
    }


def test_restart_preserves_session_order_when_agent_and_session_ids_sort_differently(
    runtime_repository: Path,
    catalog: Catalog,
) -> None:
    scenario_path = runtime_repository / "projects/for-the-band/scenario.yaml"
    scenario = scenario_path.read_text(encoding="utf-8")
    scenario = scenario.replace("session-anon", "session-z-anon")
    scenario = scenario.replace("session-rana", "session-a-rana")
    scenario_path.write_text(scenario, encoding="utf-8")

    created = create_world(
        runtime_repository,
        "for-the-band",
        "non-correlated-session-order",
        catalog=catalog,
    )
    restarted = load_world(
        runtime_repository,
        "for-the-band",
        "non-correlated-session-order",
        catalog=catalog,
    )

    session_ids = tuple(item.session_id for item in created.public_state.sessions)
    assert session_ids == tuple(sorted(session_ids))
    assert restarted.public_state == created.public_state


def test_new_process_loads_the_same_saved_world(
    runtime_repository: Path,
    catalog: Catalog,
) -> None:
    create_world(
        runtime_repository,
        "for-the-band",
        "process-restart",
        catalog=catalog,
    )
    script = """
import sys
from pathlib import Path
from agent_runtime.agent.personact.compiler import (
    Catalog,
    PromptDefinition,
    ToolDefinition,
    ToolMode,
)
from agent_runtime.agent.skill import RuntimeSkillCatalog
from agent_runtime.bootstrap import load_world

repository = Path(sys.argv[1])
source_repository = Path(sys.argv[2])
skills = RuntimeSkillCatalog.load(source_repository / "content" / "skills")
catalog = Catalog(
    tools=(ToolDefinition(id="visible_location.query", version="1", mode=ToolMode.QUERY),),
    prompts=(PromptDefinition(id="personact.v1", version="1", digest="prompt-v1"),),
    skills=skills.skills,
)
loaded = load_world(repository, "for-the-band", "process-restart", catalog=catalog)
print(f"{loaded.world_ref.project_id}/{loaded.world_ref.world_id}/{len(loaded.public_state.sessions)}")
"""

    completed = subprocess.run(
        [sys.executable, "-c", script, str(runtime_repository), str(REPOSITORY_ROOT)],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "for-the-band/process-restart/5"


def test_bootstrap_routes_public_and_private_knowledge(
    runtime_repository: Path,
    catalog: Catalog,
) -> None:
    loaded = create_world(
        runtime_repository,
        "for-the-band",
        "knowledge-routing",
        catalog=catalog,
    )
    records = {
        stream.agent_id: {record.id: record for record in stream.records}
        for stream in loaded.memory_streams
    }

    assert "rehearsal-finished" in {fact.fact_id for fact in loaded.public_state.facts}
    assert all("rehearsal-finished" not in memories for memories in records.values())
    assert all("band-finished-rehearsal" in memories for memories in records.values())
    assert "anon-notices-distance" in records["anon"]
    assert "anon-notices-distance" not in records["soyo"]
    assert "anon-notices-distance" not in records["taki"]
    assert "anon-notices-distance" not in records["tomori"]
    assert "anon-notices-distance" not in records["rana"]
    assert "soyo-hides-tension" in records["soyo"]
    assert "soyo-hides-tension" not in records["anon"]
    assert records["anon"]["anon-notices-distance"].poignancy == 4.0
    assert records["anon"]["anon-notices-distance"].source.startswith("scenario:")


def test_two_projects_and_two_worlds_can_reuse_internal_ids(
    runtime_repository: Path,
    catalog: Catalog,
) -> None:
    first = create_world(
        runtime_repository,
        "for-the-band",
        "shared-save",
        catalog=catalog,
    )
    second = create_world(
        runtime_repository,
        "rain-after",
        "shared-save",
        catalog=catalog,
    )
    sibling = create_world(
        runtime_repository,
        "for-the-band",
        "second-save",
        catalog=catalog,
    )

    assert (runtime_repository / ".runtime/for-the-band/world.sqlite").is_file()
    assert (runtime_repository / ".runtime/rain-after/world.sqlite").is_file()
    assert first.world_ref != second.world_ref != sibling.world_ref
    assert {item.session_id for item in first.public_state.sessions}.intersection(
        item.session_id for item in second.public_state.sessions
    )
    assert first.memory_streams[0].world_ref == first.world_ref
    assert second.memory_streams[0].world_ref == second.world_ref
    assert sibling.memory_streams[0].world_ref == sibling.world_ref

    database = open_project_database(
        runtime_repository / ".runtime",
        "for-the-band",
        create=False,
    )
    try:
        with database.engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM worlds")) == 2
    finally:
        database.dispose()


def test_duplicate_create_never_overwrites_existing_world(
    runtime_repository: Path,
    catalog: Catalog,
) -> None:
    original = create_world(
        runtime_repository,
        "for-the-band",
        "immutable-genesis",
        catalog=catalog,
    )

    with pytest.raises(WorldAlreadyExistsError):
        create_world(
            runtime_repository,
            "for-the-band",
            "immutable-genesis",
            catalog=catalog,
        )

    restored = load_world(
        runtime_repository,
        "for-the-band",
        "immutable-genesis",
        catalog=catalog,
    )
    assert restored.public_state == original.public_state
    assert restored.persona_states == original.persona_states
    assert restored.memory_streams == original.memory_streams


@pytest.mark.parametrize(
    ("world_ids", "expected_results"),
    [
        (("concurrent-a", "concurrent-b"), ("created", "created")),
        (("same-world", "same-world"), ("created", "duplicate")),
    ],
)
def test_concurrent_world_creation_is_serialized_without_raw_lock_errors(
    runtime_repository: Path,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
    world_ids: tuple[str, str],
    expected_results: tuple[str, str],
) -> None:
    database = open_project_database(
        runtime_repository / ".runtime",
        "for-the-band",
        create=True,
    )
    database.dispose()
    barrier = threading.Barrier(2)
    original_initialize = bootstrap_runtime.initialize_public_world

    def synchronized_initialize(session: Session, state: PublicWorldState) -> None:
        barrier.wait(timeout=5)
        original_initialize(session, state)

    monkeypatch.setattr(
        bootstrap_runtime,
        "initialize_public_world",
        synchronized_initialize,
    )

    def create_one(world_id: str) -> str:
        try:
            create_world(
                runtime_repository,
                "for-the-band",
                world_id,
                catalog=catalog,
            )
        except WorldAlreadyExistsError:
            return "duplicate"
        return "created"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(create_one, world_ids))

    assert tuple(sorted(results)) == tuple(sorted(expected_results))
    for world_id in set(world_ids):
        assert (
            load_world(
                runtime_repository,
                "for-the-band",
                world_id,
                catalog=catalog,
            ).world_ref.world_id
            == world_id
        )


def test_load_missing_world_never_creates_or_bootstraps(
    runtime_repository: Path,
    catalog: Catalog,
) -> None:
    with pytest.raises(WorldNotFoundError, match="does not exist"):
        load_world(
            runtime_repository,
            "for-the-band",
            "missing-save",
            catalog=catalog,
        )
    assert not (runtime_repository / ".runtime").exists()

    create_world(
        runtime_repository,
        "for-the-band",
        "existing-save",
        catalog=catalog,
    )
    with pytest.raises(WorldNotFoundError, match="missing-save"):
        load_world(
            runtime_repository,
            "for-the-band",
            "missing-save",
            catalog=catalog,
        )


def test_failure_after_public_writes_rolls_back_the_whole_world(
    runtime_repository: Path,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_insert = MemoryStore.insert_stream
    calls = 0

    def fail_on_second_stream(
        self: MemoryStore,
        session: Session,
        stream: MemoryStream,
    ) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected private-memory failure")
        original_insert(self, session, stream)

    monkeypatch.setattr(MemoryStore, "insert_stream", fail_on_second_stream)
    with pytest.raises(RuntimeError, match="injected private-memory failure"):
        create_world(
            runtime_repository,
            "for-the-band",
            "rollback-me",
            catalog=catalog,
        )

    database = open_project_database(
        runtime_repository / ".runtime",
        "for-the-band",
        create=False,
    )
    try:
        with database.engine.connect() as connection:
            for table in (
                "worlds",
                "locations",
                "agent_world_states",
                "objects",
                "world_facts",
                "event_sessions",
                "agent_runtime_states",
                "agent_memory_records",
            ):
                count = connection.scalar(
                    text(f"SELECT count(*) FROM {table} WHERE world_id = :world_id"),
                    {"world_id": "rollback-me"},
                )
                assert count == 0
    finally:
        database.dispose()


def test_non_finite_cognitive_state_is_rejected_before_database_creation(
    runtime_repository: Path,
    catalog: Catalog,
) -> None:
    invalid = CognitiveConfig.model_construct(
        attention_budget=1,
        retention=20,
        recency_weight=float("inf"),
        relevance_weight=1.0,
        importance_weight=1.0,
        recency_decay=0.99,
        reflection_threshold=10.0,
        reflection_count=5,
    )

    with pytest.raises(ValueError, match="finite"):
        create_world(
            runtime_repository,
            "for-the-band",
            "non-finite-state",
            catalog=catalog,
            cognitive_config=invalid,
        )

    assert not (runtime_repository / ".runtime").exists()


def test_changed_scenario_or_agent_spec_is_not_silently_bootstrapped(
    runtime_repository: Path,
    catalog: Catalog,
) -> None:
    create_world(
        runtime_repository,
        "for-the-band",
        "config-pinned",
        catalog=catalog,
    )
    scenario_path = runtime_repository / "projects/for-the-band/scenario.yaml"
    original_scenario = scenario_path.read_text(encoding="utf-8")
    scenario_path.write_text(
        original_scenario.replace("晚间排练刚结束", "傍晚排练刚结束"),
        encoding="utf-8",
    )
    with pytest.raises(WorldConfigurationMismatchError, match="Scenario"):
        load_world(
            runtime_repository,
            "for-the-band",
            "config-pinned",
            catalog=catalog,
        )

    scenario_path.write_text(original_scenario, encoding="utf-8")
    manifest_path = runtime_repository / "projects/for-the-band/agents.json"
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").replace(
            "让排练后的气氛重新轻松起来",
            "让排练后的气氛自然缓和下来",
        ),
        encoding="utf-8",
    )
    with pytest.raises(WorldConfigurationMismatchError, match="Agent specs"):
        load_world(
            runtime_repository,
            "for-the-band",
            "config-pinned",
            catalog=catalog,
        )


def test_load_rejects_tampered_memory_scope_and_stable_session_identity(
    runtime_repository: Path,
    catalog: Catalog,
) -> None:
    for world_id in ("tampered-scope", "tampered-session"):
        create_world(
            runtime_repository,
            "for-the-band",
            world_id,
            catalog=catalog,
        )
    database = open_project_database(
        runtime_repository / ".runtime",
        "for-the-band",
        create=False,
    )
    try:
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE agent_memory_records SET scope = 'project/wrong/persona/anon' "
                    "WHERE world_id = 'tampered-scope' AND agent_id = 'anon'"
                )
            )
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM event_sessions "
                    "WHERE world_id = 'tampered-session' AND agent_id = 'taki'"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO event_sessions("
                    "world_id, session_id, agent_id, root_session_id, "
                    "topology_version, updated_world_version"
                    ") VALUES ("
                    "'tampered-session', 'renamed-taki-session', 'taki', "
                    "'renamed-taki-session', 1, 1"
                    ")"
                )
            )
    finally:
        database.dispose()

    with pytest.raises(WorldConfigurationMismatchError, match="Memory scope"):
        load_world(
            runtime_repository,
            "for-the-band",
            "tampered-scope",
            catalog=catalog,
        )
    with pytest.raises(WorldConfigurationMismatchError, match="EventSession identities"):
        load_world(
            runtime_repository,
            "for-the-band",
            "tampered-session",
            catalog=catalog,
        )


def test_m2_refuses_to_claim_recovery_of_a_running_world(
    runtime_repository: Path,
    catalog: Catalog,
) -> None:
    create_world(
        runtime_repository,
        "for-the-band",
        "running-save",
        catalog=catalog,
    )
    database = open_project_database(
        runtime_repository / ".runtime",
        "for-the-band",
        create=False,
    )
    try:
        with database.engine.begin() as connection:
            connection.execute(
                text("UPDATE worlds SET status = 'running' WHERE world_id = 'running-save'")
            )
    finally:
        database.dispose()

    with pytest.raises(WorldStatusError, match="only load paused"):
        load_world(
            runtime_repository,
            "for-the-band",
            "running-save",
            catalog=catalog,
        )


def test_create_and_load_do_not_invoke_character_agents(
    runtime_repository: Path,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_decision(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("bootstrap invoked a Character Agent")

    monkeypatch.setattr(PersonActAgent, "decide", unexpected_decision)
    create_world(
        runtime_repository,
        "rain-after",
        "no-generation",
        catalog=catalog,
    )
    loaded = load_world(
        runtime_repository,
        "rain-after",
        "no-generation",
        catalog=catalog,
    )
    assert loaded.public_state.world.status is WorldStatus.PAUSED


def test_custom_cognitive_config_is_persisted_not_reapplied_on_load(
    runtime_repository: Path,
    catalog: Catalog,
) -> None:
    custom = CognitiveConfig(
        attention_budget=2,
        retention=7,
        recency_weight=0.7,
        relevance_weight=1.2,
        importance_weight=1.4,
        recency_decay=0.95,
        reflection_threshold=12.0,
        reflection_count=3,
    )
    created = create_world(
        runtime_repository,
        "rain-after",
        "custom-cognition",
        catalog=catalog,
        cognitive_config=custom,
    )
    restarted = load_world(
        runtime_repository,
        "rain-after",
        "custom-cognition",
        catalog=catalog,
    )

    assert all(item.state.cognitive_config == custom for item in created.persona_states)
    assert restarted.persona_states == created.persona_states
