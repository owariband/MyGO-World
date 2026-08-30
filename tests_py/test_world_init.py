from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from conftest import MINIMAL_SEED, json_output, run_cli

import mygo_world.committer as committer_module
from mygo_world.db.models import EntityRevisionRow
from mygo_world.repositories import LedgerRepository
from mygo_world.worlds import initialize_world, show_world


def database_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_init_atomically_materializes_genesis_world(worlds_dir: Path) -> None:
    receipt = initialize_world(MINIMAL_SEED, "world-one", worlds_dir)
    database = worlds_dir / "world-one" / "world.sqlite3"

    assert receipt["status"] == "created"
    assert receipt["world_version"] == 1
    assert len(receipt["snapshot_checksum"]) == 64
    assert database.is_file()
    assert not list(database.parent.glob("*.tmp"))

    with sqlite3.connect(database) as connection:
        counts = {
            table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "worlds",
                "world_versions",
                "world_segments",
                "entity_revisions",
                "world_events",
                "event_sessions",
                "event_session_members",
                "runnable_session_queue",
                "snapshots",
                "agent_memory_records",
            )
        }
        segment_type = connection.execute(
            "SELECT segment_type FROM world_segments"
        ).fetchone()[0]
        schema_revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()[0]

    assert counts == {
        "worlds": 1,
        "world_versions": 1,
        "world_segments": 1,
        "entity_revisions": 3,
        "world_events": 0,
        "event_sessions": 1,
        "event_session_members": 2,
        "runnable_session_queue": 1,
        "snapshots": 1,
        "agent_memory_records": 1,
    }
    assert segment_type == "genesis"
    assert schema_revision == "0003_broadcast_renders"


def test_init_uses_injected_clock_and_domain_ids(worlds_dir: Path) -> None:
    created_at = datetime(2026, 8, 31, 12, 34, 56, 789, tzinfo=UTC)
    domain_ids = iter(
        [
            "segment-genesis",
            "revision-location",
            "revision-anon",
            "revision-soyo",
        ]
    )

    initialize_world(
        MINIMAL_SEED,
        "deterministic",
        worlds_dir,
        clock=lambda: created_at,
        id_generator=domain_ids.__next__,
    )

    database = worlds_dir / "deterministic" / "world.sqlite3"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT created_at FROM worlds").fetchone() == (
            "2026-08-31T12:34:56.000789+00:00",
        )
        assert connection.execute(
            "SELECT segment_id, committed_at FROM world_segments"
        ).fetchone() == (
            "segment-genesis",
            "2026-08-31T12:34:56.000789+00:00",
        )
        assert connection.execute(
            "SELECT entity_id, entity_revision_id FROM entity_revisions "
            "ORDER BY entity_id"
        ).fetchall() == [
            ("character-anon", "revision-anon"),
            ("character-soyo", "revision-soyo"),
            ("location-live-house", "revision-location"),
        ]

    with pytest.raises(StopIteration):
        next(domain_ids)


def test_duplicate_init_returns_stable_error_without_changing_database(
    worlds_dir: Path,
) -> None:
    first = run_cli(
        "init",
        "--world-id",
        "duplicate",
        "--seed",
        str(MINIMAL_SEED),
        "--worlds-dir",
        str(worlds_dir),
        "--json",
    )
    database = worlds_dir / "duplicate" / "world.sqlite3"
    before = database_hash(database)

    second = run_cli(
        "init",
        "--world-id",
        "duplicate",
        "--seed",
        str(worlds_dir / "even-a-missing-seed.yaml"),
        "--worlds-dir",
        str(worlds_dir),
        "--json",
    )

    assert first.returncode == 0
    assert second.returncode == 3
    assert json_output(second) == {
        "command": "init",
        "status": "error",
        "error": {
            "code": "WORLD_ALREADY_EXISTS",
            "message": "World 'duplicate' already exists",
        },
    }
    assert database_hash(database) == before


def test_new_process_reopens_same_snapshot_after_seed_changes(
    worlds_dir: Path, tmp_path: Path
) -> None:
    changed_seed = tmp_path / "scenario.yaml"
    changed_seed.write_bytes(MINIMAL_SEED.read_bytes())
    init_result = run_cli(
        "init",
        "--world-id",
        "reopen",
        "--seed",
        str(changed_seed),
        "--worlds-dir",
        str(worlds_dir),
        "--json",
    )
    init_receipt = json_output(init_result)

    raw = yaml.safe_load(changed_seed.read_text())
    raw["name"] = "A mutated source name"
    raw["entities"][1]["state"]["mood"] = "changed outside the World"
    changed_seed.write_text(yaml.safe_dump(raw), encoding="utf-8")

    show_result = run_cli(
        "show",
        "--world-id",
        "reopen",
        "--worlds-dir",
        str(worlds_dir),
        "--json",
    )
    shown = json_output(show_result)

    assert init_result.returncode == show_result.returncode == 0
    assert shown["world_name"] == "Minimal MyGO World"
    assert shown["snapshot_checksum"] == init_receipt["snapshot_checksum"]
    assert shown["seed"] == init_receipt["seed"]
    anon = next(
        entity
        for entity in shown["snapshot"]["entities"]
        if entity["entity_id"] == "character-anon"
    )
    assert anon["payload"]["state"]["mood"] == "curious"


def test_invalid_seed_does_not_publish_a_database(
    worlds_dir: Path, tmp_path: Path
) -> None:
    invalid_seed = tmp_path / "invalid.yaml"
    invalid_seed.write_text(
        "schema_version: 1\nseed_id: broken\nversion: '1'\nname: Broken\n",
        encoding="utf-8",
    )

    result = run_cli(
        "init",
        "--world-id",
        "invalid",
        "--seed",
        str(invalid_seed),
        "--worlds-dir",
        str(worlds_dir),
        "--json",
    )

    assert result.returncode == 2
    assert json_output(result)["error"]["code"] == "SEED_INVALID"
    assert not (worlds_dir / "invalid" / "world.sqlite3").exists()


def test_materialization_failure_does_not_publish_partial_world(
    worlds_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = committer_module.LedgerRepository.append_entity_revision
    append_count = 0

    def fail_after_first_revision(
        repository: LedgerRepository, row: EntityRevisionRow
    ) -> None:
        nonlocal append_count
        append_count += 1
        if append_count == 2:
            raise RuntimeError("injected materialization failure")
        original(repository, row)

    monkeypatch.setattr(
        committer_module.LedgerRepository,
        "append_entity_revision",
        fail_after_first_revision,
    )

    with pytest.raises(RuntimeError, match="injected materialization failure"):
        initialize_world(MINIMAL_SEED, "atomic-failure", worlds_dir)

    world_dir = worlds_dir / "atomic-failure"
    assert not (world_dir / "world.sqlite3").exists()
    assert not list(world_dir.glob("*.tmp"))


def test_show_detects_snapshot_tampering(worlds_dir: Path) -> None:
    initialize_world(MINIMAL_SEED, "tampered", worlds_dir)
    database = worlds_dir / "tampered" / "world.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE snapshots SET snapshot_json = ? WHERE world_version = 1",
            (json.dumps({"tampered": True}),),
        )

    result = run_cli(
        "show",
        "--world-id",
        "tampered",
        "--worlds-dir",
        str(worlds_dir),
        "--json",
    )
    assert result.returncode == 1
    assert json_output(result)["error"]["code"] == "SNAPSHOT_CHECKSUM_MISMATCH"


def test_human_receipts_are_concise(worlds_dir: Path) -> None:
    initialized = run_cli(
        "init",
        "--world-id",
        "human",
        "--seed",
        str(MINIMAL_SEED),
        "--worlds-dir",
        str(worlds_dir),
    )
    shown = run_cli(
        "show",
        "--world-id",
        "human",
        "--worlds-dir",
        str(worlds_dir),
    )

    assert initialized.returncode == shown.returncode == 0
    assert initialized.stdout.startswith("Created World human at version 1")
    assert shown.stdout.startswith("World human is at version 1")


def test_service_reopens_without_the_seed_file(worlds_dir: Path) -> None:
    initialize_world(MINIMAL_SEED, "service-open", worlds_dir)
    shown = show_world("service-open", worlds_dir)
    assert shown["world_version"] == 1
    assert shown["world_event_count"] == 0
