from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from alembic import command
from conftest import MINIMAL_SEED, REPOSITORY_ROOT, json_output, run_cli
from sqlalchemy.orm import Session

from mygo_world.canonical import canonical_json, sha256_text
from mygo_world.canonical_export import export_world
from mygo_world.db.engine import alembic_config, create_world_engine, upgrade_to_head
from mygo_world.repositories import LedgerRepository
from mygo_world.worlds import initialize_world


def test_alembic_cli_creates_latest_schema(tmp_path: Path) -> None:
    database = tmp_path / "alembic.sqlite3"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(REPOSITORY_ROOT / "alembic.ini"),
            "-x",
            f"db_path={database}",
            "upgrade",
            "head",
        ],
        cwd=REPOSITORY_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("0009_decision_turns",)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='worlds'"
        ).fetchone() == ("worlds",)
        assert {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        } >= {
            "broadcast_runs",
            "broadcast_dispositions",
            "renders",
            "generation_batches",
            "generation_waves",
            "decision_turn_records",
        }
        assert "event_session_parents" not in {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "runnable_session_queue" not in {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        queue_order_column = next(
            row
            for row in connection.execute("PRAGMA table_info(event_sessions)")
            if row[1] == "queue_order"
        )
        assert queue_order_column[3] == 1
        assert queue_order_column[4] == "'0'"
        assert (
            "NEW.queue_order <= 0"
            in connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' "
                "AND name='event_sessions_validate_insert'"
            ).fetchone()[0]
        )


def test_non_init_command_rejects_outdated_schema(worlds_dir: Path) -> None:
    world_dir = worlds_dir / "old"
    world_dir.mkdir(parents=True)
    with sqlite3.connect(world_dir / "world.sqlite3") as connection:
        connection.execute(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
        )
        connection.execute("INSERT INTO alembic_version VALUES ('obsolete')")

    result = run_cli(
        "show",
        "--world-id",
        "old",
        "--worlds-dir",
        str(worlds_dir),
        "--json",
    )

    assert result.returncode == 5
    assert json_output(result)["error"]["code"] == "SCHEMA_OUTDATED"


def test_session_persistence_migration_preserves_order_and_ledger_lineage(
    worlds_dir: Path,
) -> None:
    world_dir = worlds_dir / "legacy"
    world_dir.mkdir(parents=True)
    database = world_dir / "world.sqlite3"
    config = alembic_config(database)
    command.upgrade(config, "0007_recoverable_rendering")

    segment_payloads = [
        {
            "schema_version": 1,
            "kind": "genesis",
            "session_initializations": [
                {"session_id": "session-1"},
                {"session_id": "session-2"},
            ],
            "queue_initializations": [
                {"queue_order": 1, "session_id": "session-1"},
                {"queue_order": 2, "session_id": "session-2"},
            ],
        },
        {
            "schema_version": 1,
            "kind": "generation_wave",
            "successor_sessions": [
                {
                    "session_id": "session-3",
                    "parent_session_ids": ["session-1"],
                },
                {
                    "session_id": "session-4",
                    "parent_session_ids": ["session-1"],
                },
            ],
        },
        {
            "schema_version": 1,
            "kind": "generation_wave",
            "successor_sessions": [
                {
                    "session_id": "session-5",
                    "parent_session_ids": ["session-2", "session-4"],
                }
            ],
        },
    ]
    snapshot = {
        "schema_version": 1,
        "world_id": "legacy",
        "world_version": 3,
        "world_time_ms": 200,
        "entities": [],
        "sessions": [],
        "runnable_session_queue": [
            {"queue_order": 3, "session_id": "session-3"},
            {"queue_order": 5, "session_id": "session-5"},
        ],
    }
    snapshot_json = canonical_json(snapshot)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO worlds VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("legacy", "Legacy", "seed", "1", "0" * 64, 3, None, "now"),
        )
        for version, payload in enumerate(segment_payloads, start=1):
            segment_id = f"segment-{version}"
            connection.execute(
                "INSERT INTO world_segments VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    segment_id,
                    version,
                    version,
                    "genesis" if version == 1 else "generation_wave",
                    None,
                    1,
                    canonical_json(payload),
                    "now",
                ),
            )
            connection.execute(
                "INSERT INTO world_versions VALUES (?, ?, ?, ?)",
                (version, segment_id, (version - 1) * 100, "now"),
            )
        connection.execute(
            "INSERT INTO world_segments VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "segment-uncommitted",
                4,
                4,
                "generation_wave",
                None,
                1,
                canonical_json(
                    {
                        "kind": "generation_wave",
                        "successor_sessions": [
                            {
                                "session_id": "phantom-session",
                                "parent_session_ids": ["session-5"],
                            }
                        ],
                    }
                ),
                "now",
            ),
        )
        for session_id, created_version in (
            ("session-1", 1),
            ("session-2", 1),
            ("session-3", 2),
            ("session-4", 2),
            ("session-5", 3),
        ):
            connection.execute(
                "INSERT INTO event_sessions VALUES (?, 'runnable', ?, ?, ?, NULL, NULL)",
                (session_id, "location", "scope", created_version),
            )
        for child, parent in (
            ("session-3", "session-1"),
            ("session-4", "session-1"),
            ("session-5", "session-2"),
            ("session-5", "session-4"),
        ):
            connection.execute(
                "INSERT INTO event_session_parents VALUES (?, ?)", (child, parent)
            )
        for queue_order, session_id, enqueued, dequeued in (
            (1, "session-1", 1, 2),
            (2, "session-2", 1, 3),
            (3, "session-3", 2, None),
            (4, "session-4", 2, 3),
            (5, "session-5", 3, None),
        ):
            connection.execute(
                "INSERT INTO runnable_session_queue VALUES (?, ?, ?, ?)",
                (queue_order, session_id, enqueued, dequeued),
            )
        for session_id, closed_version in (
            ("session-1", 2),
            ("session-2", 3),
            ("session-4", 3),
        ):
            connection.execute(
                "UPDATE event_sessions SET status='closed', "
                "closed_world_version=?, closure_reason='partitioned' "
                "WHERE session_id=?",
                (closed_version, session_id),
            )
        connection.execute(
            "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?)",
            (3, 1, snapshot_json, sha256_text(snapshot_json), "now"),
        )

    with sqlite3.connect(database) as connection:
        before_queue = connection.execute(
            "SELECT queue_order, session_id, enqueued_world_version, "
            "dequeued_world_version FROM runnable_session_queue ORDER BY queue_order"
        ).fetchall()
        before_parents = connection.execute(
            "SELECT session_id, parent_session_id FROM event_session_parents "
            "ORDER BY session_id, parent_session_id"
        ).fetchall()

    upgrade_to_head(database)
    exported = export_world("legacy", worlds_dir)["sessions"]
    assert [
        (
            item["queue_order"],
            item["session_id"],
            item["enqueued_world_version"],
            item["dequeued_world_version"],
        )
        for item in exported["queue"]
    ] == before_queue
    assert [
        (item["session_id"], item["parent_session_id"]) for item in exported["parents"]
    ] == before_parents
    assert (
        next(
            item["session_id"]
            for item in exported["queue"]
            if item["dequeued_world_version"] is None
        )
        == "session-3"
    )
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        lifecycle = connection.execute(
            "SELECT session_id, queue_order, created_world_version, "
            "closed_world_version FROM event_sessions ORDER BY queue_order"
        ).fetchall()
    assert "event_session_parents" not in tables
    assert "runnable_session_queue" not in tables
    assert lifecycle == [
        ("session-1", 1, 1, 2),
        ("session-2", 2, 1, 3),
        ("session-3", 3, 2, None),
        ("session-4", 4, 2, 3),
        ("session-5", 5, 3, None),
    ]

    command.downgrade(alembic_config(database), "0007_recoverable_rendering")
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute(
                "SELECT queue_order, session_id, enqueued_world_version, "
                "dequeued_world_version FROM runnable_session_queue ORDER BY queue_order"
            ).fetchall()
            == before_queue
        )
        assert (
            connection.execute(
                "SELECT session_id, parent_session_id FROM event_session_parents "
                "ORDER BY session_id, parent_session_id"
            ).fetchall()
            == before_parents
        )

    upgrade_to_head(database)


def test_session_persistence_migration_rejects_invalid_legacy_queue(
    worlds_dir: Path,
) -> None:
    initialize_world(MINIMAL_SEED, "invalid-legacy", worlds_dir)
    database = worlds_dir / "invalid-legacy" / "world.sqlite3"
    command.downgrade(alembic_config(database), "0007_recoverable_rendering")
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE runnable_session_queue SET queue_order=0")

    with pytest.raises(
        RuntimeError,
        match="SESSION_PERSISTENCE_MIGRATION_FAILED: queue_order must be positive",
    ):
        upgrade_to_head(database)


def test_ledger_repository_has_no_mutating_existing_record_api(tmp_path: Path) -> None:
    engine = create_world_engine(tmp_path / "unused.sqlite3")
    try:
        with Session(engine) as session:
            repository = LedgerRepository(session)
            assert not hasattr(repository, "update")
            assert not hasattr(repository, "delete")
            assert not hasattr(repository, "update_segment")
            assert not hasattr(repository, "delete_segment")
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("table", "id_column"),
    [
        ("world_segments", "segment_id"),
        ("entity_revisions", "entity_revision_id"),
        ("world_events", "event_id"),
    ],
)
def test_sqlite_triggers_reject_ledger_update_and_delete(
    worlds_dir: Path, table: str, id_column: str
) -> None:
    initialize_world(MINIMAL_SEED, f"immutable-{table}", worlds_dir)
    database = worlds_dir / f"immutable-{table}" / "world.sqlite3"
    with sqlite3.connect(database) as connection:
        if table == "world_events":
            segment_id = connection.execute(
                "SELECT segment_id FROM world_segments"
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO world_events (
                    event_id, event_order, world_version, segment_id, session_id,
                    start_time_ms, end_time_ms, event_type, schema_version, payload_json
                ) VALUES ('event-test', 1, 1, ?, NULL, 0, 0, 'test', 1, '{}')
                """,
                (segment_id,),
            )
            connection.commit()
        record_id = connection.execute(
            f"SELECT {id_column} FROM {table} LIMIT 1"
        ).fetchone()[0]

        with pytest.raises(sqlite3.IntegrityError, match="LEDGER_IMMUTABLE"):
            connection.execute(
                f"UPDATE {table} SET {id_column} = {id_column} WHERE {id_column} = ?",
                (record_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="LEDGER_IMMUTABLE"):
            connection.execute(
                f"DELETE FROM {table} WHERE {id_column} = ?", (record_id,)
            )
