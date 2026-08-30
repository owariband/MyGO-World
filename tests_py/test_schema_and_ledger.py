from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import MINIMAL_SEED, REPOSITORY_ROOT, json_output, run_cli
from sqlalchemy.orm import Session

from mygo_world.db.engine import create_world_engine
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
        ).fetchone() == ("0004_generation_batches",)
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
        }


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
