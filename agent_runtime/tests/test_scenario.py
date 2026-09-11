"""Scenario seed decoding, identity, and canonical-hash contract tests."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
import yaml
from pydantic import ValidationError

from agent_runtime.scenario import (
    AllAgents,
    ExplicitAgents,
    ScenarioDecodeError,
    ScenarioReferenceError,
    load_project_scenario,
    load_scenario_seed,
    scenario_seed_hash,
)

PROJECTS_PATH = Path(__file__).parents[2] / "projects"
MANIFEST_FIXTURE = Path(__file__).parents[1] / "testdata" / "npc_diy" / "agents.json"


def test_formal_projects_load_as_isolated_strict_scenarios() -> None:
    band = load_project_scenario(PROJECTS_PATH, "for-the-band")
    rain = load_project_scenario(PROJECTS_PATH, "rain-after")

    assert band.manifest.project_id == band.seed.project_id == "for-the-band"
    assert rain.manifest.project_id == rain.seed.project_id == "rain-after"
    assert band.seed_hash == "9eada0aaf0960634de42cfda915151dc07dc23eff43b369085e2312bffa9efb6"
    assert rain.seed_hash == "c4e3f46b9f9ce70b5e8ba14875bf1ab064208ad7ce1a53191df9673fc562d33c"
    assert band.seed_hash != rain.seed_hash
    assert {agent.agent_id for agent in band.seed.agents} == {
        "anon",
        "rana",
        "soyo",
        "taki",
        "tomori",
    }
    assert {agent.agent_id for agent in rain.seed.agents} == {"anon", "taki", "tomori"}
    assert band.seed.agents[0].public_status is not None
    assert band.seed.objects[0].description
    assert band.seed.public_facts[0].content
    assert isinstance(band.seed.locations, tuple)
    assert band.seed.model_config.get("frozen") is True

    with pytest.raises(ValidationError, match="Instance is frozen"):
        band.seed.agents[0].__setattr__("public_status", "changed")


def test_scenario_uses_camel_case_on_its_external_surface(tmp_path: Path) -> None:
    path = _write_seed(tmp_path / "scenario.yaml", _scenario_data("test-project"))

    seed = load_scenario_seed(path)
    external = json.loads(seed.model_dump_json())

    assert "projectId" in external
    assert "project_id" not in external
    assert external["agents"][0]["publicStatus"] is None
    assert external["publicFacts"][0]["object"] == "ready"
    assert external["publicFacts"][0]["content"] == "The machine can be used."
    assert "operations" not in external["objects"][0]


def test_object_operations_are_strict_unique_and_canonical(tmp_path: Path) -> None:
    first = _scenario_data("test-project")
    second = deepcopy(first)
    operations = [
        {
            "operationId": "stop",
            "fromState": "running",
            "toState": "stopped",
            "resultText": "The machine stops.",
        },
        {
            "operationId": "start",
            "fromState": "idle",
            "toState": "running",
            "resultText": "The machine starts.",
        },
    ]
    _object_list(first, "objects")[0]["operations"] = operations
    _object_list(second, "objects")[0]["operations"] = list(reversed(operations))

    first_seed = load_scenario_seed(_write_seed(tmp_path / "first.yaml", first))
    second_seed = load_scenario_seed(_write_seed(tmp_path / "second.yaml", second))

    assert tuple(item.operation_id for item in first_seed.objects[0].operations) == (
        "start",
        "stop",
    )
    assert scenario_seed_hash(first_seed) == scenario_seed_hash(second_seed)

    duplicate = deepcopy(first)
    duplicate_operations = cast(
        list[dict[str, object]], _object_list(duplicate, "objects")[0]["operations"]
    )
    duplicate_operations[1]["operationId"] = duplicate_operations[0]["operationId"]
    with pytest.raises(ScenarioDecodeError) as raised:
        load_scenario_seed(_write_seed(tmp_path / "duplicate.yaml", duplicate))
    assert "operation ids" in _cause_text(raised.value)


def test_semantic_hash_ignores_comments_field_order_and_set_like_order(
    tmp_path: Path,
) -> None:
    original = _scenario_data("test-project")
    reordered = cast(dict[str, object], _reverse_mapping_order(deepcopy(original)))
    _reverse_collection(reordered, "locations")
    _reverse_collection(reordered, "objects")
    _reverse_collection(reordered, "publicFacts")
    _reverse_collection(reordered, "agents")
    _reverse_collection(reordered, "initialPartitions")
    _reverse_collection(reordered, "knowledge")
    partitions = _object_list(reordered, "initialPartitions")
    _reverse_collection(partitions[0], "memberSessionIds")
    knowledge = _object_list(reordered, "knowledge")
    for item in knowledge:
        _reverse_collection(item, "tags")
        recipients = cast(dict[str, object], item["recipients"])
        if recipients["kind"] == "explicit":
            _reverse_collection(recipients, "agentIds")

    first_path = _write_seed(tmp_path / "first.yaml", original)
    second_path = tmp_path / "second.yaml"
    second_path.write_text(
        "# Source comments are not semantic.\n" + json.dumps(reordered, ensure_ascii=False),
        encoding="utf-8",
    )
    first = load_scenario_seed(first_path)
    second = load_scenario_seed(second_path)

    assert scenario_seed_hash(first) == scenario_seed_hash(second)
    assert tuple(item.id for item in second.knowledge) == ("anon-secret", "shared-opening")
    assert isinstance(second.knowledge[0].recipients, ExplicitAgents)
    assert second.knowledge[0].recipients.agent_ids == ("anon", "soyo")

    changed = deepcopy(original)
    _object_list(changed, "knowledge")[0]["content"] = "Different semantic content."
    changed_seed = load_scenario_seed(_write_seed(tmp_path / "changed.yaml", changed))
    assert scenario_seed_hash(first) != scenario_seed_hash(changed_seed)


def test_semantic_hash_normalizes_signed_zero(tmp_path: Path) -> None:
    positive = _scenario_data("test-project")
    negative = deepcopy(positive)
    _object_list(positive, "knowledge")[0]["poignancy"] = 0.0
    _object_list(negative, "knowledge")[0]["poignancy"] = -0.0

    positive_seed = load_scenario_seed(_write_seed(tmp_path / "positive.yaml", positive))
    negative_seed = load_scenario_seed(_write_seed(tmp_path / "negative.yaml", negative))

    assert positive_seed == negative_seed
    assert scenario_seed_hash(positive_seed) == scenario_seed_hash(negative_seed)


def test_yaml_duplicate_keys_are_rejected(tmp_path: Path) -> None:
    raw = json.dumps(_scenario_data("test-project"), ensure_ascii=False)
    duplicate = raw.replace(
        '"projectId": "test-project"',
        '"projectId": "test-project", "projectId": "other-project"',
        1,
    )
    path = tmp_path / "scenario.yaml"
    path.write_text(duplicate, encoding="utf-8")

    with pytest.raises(ScenarioDecodeError) as raised:
        load_scenario_seed(path)

    assert "duplicate key" in _cause_text(raised.value)


@pytest.mark.parametrize("bad_value", ["1", 1.0, True])
def test_json_mode_strict_validation_does_not_coerce_version(
    tmp_path: Path,
    bad_value: object,
) -> None:
    data = _scenario_data("test-project")
    data["version"] = bad_value

    with pytest.raises(ScenarioDecodeError) as raised:
        load_scenario_seed(_write_seed(tmp_path / "scenario.yaml", data))

    assert "valid integer" in _cause_text(raised.value)


def test_unknown_fields_are_rejected(tmp_path: Path) -> None:
    data = _scenario_data("test-project")
    data["publicState"] = {"arbitrary": "state"}

    with pytest.raises(ScenarioDecodeError) as raised:
        load_scenario_seed(_write_seed(tmp_path / "scenario.yaml", data))

    assert "Extra inputs are not permitted" in _cause_text(raised.value)


def test_yaml_timestamp_objects_are_rejected_before_pydantic(tmp_path: Path) -> None:
    data = _scenario_data("test-project")
    data["worldTime"] = datetime(2026, 9, 8, 12, tzinfo=UTC)
    path = tmp_path / "scenario.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")

    with pytest.raises(ScenarioDecodeError) as raised:
        load_scenario_seed(path)

    assert "non-JSON value: datetime" in _cause_text(raised.value)


def test_non_finite_yaml_numbers_are_rejected(tmp_path: Path) -> None:
    data = _scenario_data("test-project")
    _object_list(data, "knowledge")[0]["poignancy"] = float("nan")
    path = tmp_path / "scenario.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")

    with pytest.raises(ScenarioDecodeError) as raised:
        load_scenario_seed(path)

    assert "must be finite" in _cause_text(raised.value)


@pytest.mark.parametrize(
    "case",
    [
        "object-location",
        "object-owner",
        "agent-location",
        "fact-location",
        "fact-object",
        "fact-agent",
    ],
)
def test_entity_references_must_resolve(tmp_path: Path, case: str) -> None:
    data = _scenario_data("test-project")
    if case == "object-location":
        _object_list(data, "objects")[0]["locationId"] = "missing"
    elif case == "object-owner":
        _object_list(data, "objects")[0]["ownerAgentId"] = "missing"
    elif case == "agent-location":
        _object_list(data, "agents")[0]["locationId"] = "missing"
    else:
        kind = case.removeprefix("fact-")
        _object_list(data, "publicFacts")[0]["subject"] = {
            "kind": kind,
            "id": "missing",
        }

    with pytest.raises(ScenarioDecodeError) as raised:
        load_scenario_seed(_write_seed(tmp_path / f"{case}.yaml", data))

    assert "references unknown" in _cause_text(raised.value)


@pytest.mark.parametrize(
    "case",
    ["overlap", "omitted", "duplicate-member", "missing-root", "unknown-member"],
)
def test_initial_partitions_are_complete_and_non_overlapping(
    tmp_path: Path,
    case: str,
) -> None:
    data = _scenario_data("test-project")
    if case == "overlap":
        data["initialPartitions"] = [
            {
                "rootSessionId": "session-anon",
                "memberSessionIds": ["session-anon", "session-soyo"],
            },
            {"rootSessionId": "session-soyo", "memberSessionIds": ["session-soyo"]},
        ]
    elif case == "omitted":
        _object_list(data, "initialPartitions")[0]["memberSessionIds"] = ["session-anon"]
    elif case == "duplicate-member":
        _object_list(data, "initialPartitions")[0]["memberSessionIds"] = [
            "session-anon",
            "session-soyo",
            "session-soyo",
        ]
    elif case == "missing-root":
        _object_list(data, "initialPartitions")[0]["memberSessionIds"] = ["session-soyo"]
    else:
        _object_list(data, "initialPartitions")[0]["memberSessionIds"] = [
            "session-anon",
            "session-soyo",
            "session-missing",
        ]

    with pytest.raises(ScenarioDecodeError):
        load_scenario_seed(_write_seed(tmp_path / f"{case}.yaml", data))


@pytest.mark.parametrize("case", ["unknown", "empty", "duplicate", "all-with-ids"])
def test_knowledge_recipients_are_explicit_and_valid(tmp_path: Path, case: str) -> None:
    data = _scenario_data("test-project")
    recipients = cast(dict[str, object], _object_list(data, "knowledge")[1]["recipients"])
    if case == "unknown":
        recipients["agentIds"] = ["anon", "missing"]
    elif case == "empty":
        recipients["agentIds"] = []
    elif case == "duplicate":
        recipients["agentIds"] = ["anon", "anon"]
    else:
        recipients["kind"] = "all"
        recipients["agentIds"] = ["anon"]

    with pytest.raises(ScenarioDecodeError):
        load_scenario_seed(_write_seed(tmp_path / f"{case}.yaml", data))


@pytest.mark.parametrize(
    "unsafe_id",
    ["", ".", "../other", "other/project", "Uppercase", "_private", "a" * 65],
)
def test_project_loader_rejects_unsafe_project_slugs(
    tmp_path: Path,
    unsafe_id: str,
) -> None:
    with pytest.raises(ScenarioReferenceError):
        load_project_scenario(tmp_path, unsafe_id)


def test_project_directory_symlinks_are_rejected(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    projects.mkdir()
    _write_project(projects, "real-project")
    (projects / "alias-project").symlink_to(projects / "real-project", target_is_directory=True)

    with pytest.raises(ScenarioDecodeError, match="unsafe"):
        load_project_scenario(projects, "alias-project")


@pytest.mark.parametrize("mismatch", ["project", "manifest", "scenario"])
def test_directory_project_manifest_and_scenario_ids_must_match(
    tmp_path: Path,
    mismatch: str,
) -> None:
    projects = tmp_path / "projects"
    project_dir = _write_project(projects, "test-project")
    if mismatch == "project":
        _write_json(project_dir / "project.json", {"id": "other-project"})
    elif mismatch == "manifest":
        manifest = _manifest_text("test-project").replace(
            '"projectId": "test-project"',
            '"projectId": "other-project"',
            1,
        )
        (project_dir / "agents.json").write_text(manifest, encoding="utf-8")
    else:
        data = _scenario_data("other-project")
        _write_seed(project_dir / "scenario.yaml", data)

    with pytest.raises(ScenarioReferenceError, match="ids must match"):
        load_project_scenario(projects, "test-project")


def test_project_json_is_used_only_for_its_string_id(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    project_dir = _write_project(projects, "test-project")
    _write_json(
        project_dir / "project.json",
        {
            "id": "test-project",
            "rendererVersionUnknownToRuntime": 999,
            "rendererNestedData": {"anything": [True, None, "is ignored"]},
        },
    )

    loaded = load_project_scenario(projects, "test-project")

    assert loaded.seed.project_id == "test-project"


def test_project_json_accepts_finite_json_numbers(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    project_dir = _write_project(projects, "test-project")
    (project_dir / "project.json").write_text(
        (
            '{"id":"test-project","integer":42,"fraction":1.25,'
            '"negativeZero":-0.0,"underflow":1e-400,'
            '"largestFinite":1.7976931348623157e308}'
        ),
        encoding="utf-8",
    )

    loaded = load_project_scenario(projects, "test-project")

    assert loaded.seed.project_id == "test-project"


@pytest.mark.parametrize(
    "case",
    [
        "project-duplicate",
        "manifest-duplicate",
        "project-non-finite",
        "manifest-non-finite",
        "project-overflow",
        "manifest-overflow",
        "manifest-invalid-utf8",
    ],
)
def test_project_json_inputs_reject_ambiguous_values(tmp_path: Path, case: str) -> None:
    projects = tmp_path / "projects"
    project_dir = _write_project(projects, "test-project")
    if case == "project-duplicate":
        project_path = project_dir / "project.json"
        project_path.write_text(
            '{"id":"test-project","id":"test-project"}',
            encoding="utf-8",
        )
    elif case == "manifest-duplicate":
        manifest_path = project_dir / "agents.json"
        manifest_path.write_text(
            manifest_path.read_text(encoding="utf-8").replace(
                '"identity": "月之森转学生，主动但会掩饰不安"',
                ('"identity": "月之森转学生，主动但会掩饰不安", "identity": "ambiguous"'),
                1,
            ),
            encoding="utf-8",
        )
    elif case == "project-non-finite":
        project_path = project_dir / "project.json"
        project_path.write_text(
            '{"id":"test-project","rendererValue":NaN}',
            encoding="utf-8",
        )
    elif case == "manifest-non-finite":
        manifest_path = project_dir / "agents.json"
        manifest_path.write_text(
            manifest_path.read_text(encoding="utf-8").replace(
                '"formatVersion": 2',
                '"formatVersion": 2, "ambiguous": Infinity',
                1,
            ),
            encoding="utf-8",
        )
    elif case == "project-overflow":
        project_path = project_dir / "project.json"
        project_path.write_text(
            '{"id":"test-project","rendererValue":1e400}',
            encoding="utf-8",
        )
    elif case == "manifest-overflow":
        manifest_path = project_dir / "agents.json"
        manifest_path.write_text(
            manifest_path.read_text(encoding="utf-8").replace(
                '"formatVersion": 2',
                '"formatVersion": 2, "ambiguous": 1e400',
                1,
            ),
            encoding="utf-8",
        )
    else:
        (project_dir / "agents.json").write_bytes(b"\xff\xfe")

    with pytest.raises(ScenarioDecodeError) as raised:
        load_project_scenario(projects, "test-project")

    if case in {"project-duplicate", "manifest-duplicate"}:
        assert "duplicate" in _cause_text(raised.value)
    elif case != "manifest-invalid-utf8":
        assert "non-finite" in _cause_text(raised.value)


def test_scenario_agents_must_exactly_match_manifest_agents(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    project_dir = _write_project(projects, "test-project")
    data = _scenario_data("test-project")
    _object_list(data, "agents").append(
        {
            "agentId": "tomori",
            "locationId": "cafe",
            "sessionId": "session-tomori",
            "publicStatus": None,
        }
    )
    _object_list(data, "initialPartitions").append(
        {"rootSessionId": "session-tomori", "memberSessionIds": ["session-tomori"]}
    )
    _write_seed(project_dir / "scenario.yaml", data)

    with pytest.raises(ScenarioReferenceError, match="exactly match"):
        load_project_scenario(projects, "test-project")


def test_manifest_relationship_targets_must_resolve_in_same_project(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    project_dir = _write_project(projects, "test-project")
    manifest = json.loads(_manifest_text("test-project"))
    agents = cast(list[dict[str, object]], manifest["agents"])
    persona = cast(dict[str, object], agents[0]["persona"])
    relationships = cast(list[dict[str, object]], persona["relationships"])
    relationships[0]["targetId"] = "missing"
    _write_json(project_dir / "agents.json", cast(dict[str, object], manifest))

    with pytest.raises(ScenarioReferenceError, match="relationship target"):
        load_project_scenario(projects, "test-project")


def test_expanded_scenario_knowledge_cannot_collide_with_agent_memory(
    tmp_path: Path,
) -> None:
    projects = tmp_path / "projects"
    project_dir = _write_project(projects, "test-project")
    data = _scenario_data("test-project")
    private = _object_list(data, "knowledge")[1]
    private["id"] = "knows-soyo"
    private["recipients"] = {"kind": "explicit", "agentIds": ["anon"]}
    _write_seed(project_dir / "scenario.yaml", data)

    with pytest.raises(ScenarioReferenceError, match='memory id "knows-soyo" conflicts'):
        load_project_scenario(projects, "test-project")


def test_same_memory_id_is_allowed_when_knowledge_does_not_target_owner(
    tmp_path: Path,
) -> None:
    projects = tmp_path / "projects"
    project_dir = _write_project(projects, "test-project")
    data = _scenario_data("test-project")
    private = _object_list(data, "knowledge")[1]
    private["id"] = "knows-soyo"
    private["recipients"] = {"kind": "explicit", "agentIds": ["soyo"]}
    _write_seed(project_dir / "scenario.yaml", data)

    loaded = load_project_scenario(projects, "test-project")

    assert loaded.seed.knowledge[0].id == "knows-soyo"
    assert isinstance(loaded.seed.knowledge[0].recipients, ExplicitAgents)


def test_all_recipients_remain_an_explicit_discriminated_value(tmp_path: Path) -> None:
    seed = load_scenario_seed(
        _write_seed(tmp_path / "scenario.yaml", _scenario_data("test-project"))
    )

    shared = next(item for item in seed.knowledge if item.id == "shared-opening")
    assert isinstance(shared.recipients, AllAgents)
    assert shared.recipients.kind == "all"


def _scenario_data(project_id: str) -> dict[str, object]:
    return {
        "formatVersion": 1,
        "projectId": project_id,
        "seedId": "opening",
        "version": 1,
        "worldTime": "2026-09-08T12:00:00+00:00",
        "locations": [
            {"id": "cafe", "name": "Cafe", "description": "A quiet cafe."},
            {"id": "street", "name": "Street", "description": "The street outside."},
        ],
        "objects": [
            {
                "id": "coffee-machine",
                "name": "Coffee machine",
                "kind": "appliance",
                "description": "A small automatic coffee machine.",
                "locationId": "cafe",
                "state": "idle",
            },
            {
                "id": "umbrella",
                "name": "Umbrella",
                "kind": "tool",
                "description": "A folded shared umbrella.",
                "locationId": "street",
                "ownerAgentId": "soyo",
                "state": "folded",
            },
        ],
        "publicFacts": [
            {
                "id": "machine-ready",
                "subject": {"kind": "object", "id": "coffee-machine"},
                "predicate": "status",
                "object": "ready",
                "content": "The machine can be used.",
            },
            {
                "id": "world-is-daytime",
                "subject": {"kind": "world"},
                "predicate": "time_of_day",
                "object": None,
                "content": "It is daytime.",
            },
        ],
        "agents": [
            {
                "agentId": "anon",
                "locationId": "cafe",
                "sessionId": "session-anon",
                "publicStatus": None,
            },
            {
                "agentId": "soyo",
                "locationId": "cafe",
                "sessionId": "session-soyo",
                "publicStatus": "waiting",
            },
        ],
        "initialPartitions": [
            {
                "rootSessionId": "session-anon",
                "memberSessionIds": ["session-anon", "session-soyo"],
            }
        ],
        "knowledge": [
            {
                "id": "shared-opening",
                "kind": "thought",
                "subject": "the group",
                "predicate": "knows",
                "object": "the rehearsal time",
                "content": "Everyone knows rehearsal starts soon.",
                "poignancy": 1.0,
                "tags": ["shared", "opening"],
                "recipients": {"kind": "all"},
            },
            {
                "id": "anon-secret",
                "kind": "plan",
                "subject": "anon",
                "predicate": "intends",
                "object": "make coffee",
                "content": "Anon intends to make coffee before rehearsal.",
                "poignancy": 3.0,
                "tags": ["private", "coffee"],
                "recipients": {"kind": "explicit", "agentIds": ["soyo", "anon"]},
            },
        ],
    }


def _write_project(projects: Path, project_id: str) -> Path:
    project_dir = projects / project_id
    project_dir.mkdir(parents=True)
    _write_json(project_dir / "project.json", {"id": project_id})
    (project_dir / "agents.json").write_text(_manifest_text(project_id), encoding="utf-8")
    _write_seed(project_dir / "scenario.yaml", _scenario_data(project_id))
    return project_dir


def _manifest_text(project_id: str) -> str:
    return MANIFEST_FIXTURE.read_text(encoding="utf-8").replace(
        '"projectId": "coffee-golden"',
        f'"projectId": "{project_id}"',
        1,
    )


def _write_seed(path: Path, data: dict[str, object]) -> Path:
    _write_json(path, data)
    return path


def _write_json(path: Path, data: dict[str, object]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _object_list(data: dict[str, object], key: str) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], data[key])


def _reverse_collection(data: dict[str, object], key: str) -> None:
    values = cast(list[object], data[key])
    data[key] = list(reversed(values))


def _reverse_mapping_order(value: object) -> object:
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        return {key: _reverse_mapping_order(item) for key, item in reversed(tuple(mapping.items()))}
    if isinstance(value, list):
        return [_reverse_mapping_order(item) for item in cast(list[object], value)]
    return value


def _cause_text(error: BaseException) -> str:
    cause = error.__cause__
    assert cause is not None
    return str(cause)
