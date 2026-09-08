"""Local trace storage, isolation, bounded disclosures, and scope lifetime."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent_runtime.trace import LocalTrace, TraceRecord, record_trace, trace_scope
from agent_runtime.world.contracts import WorldRef

REF = WorldRef(project_id="coffee-golden", world_id="save-1")


def read_records(path: Path) -> list[TraceRecord]:
    return [
        TraceRecord.model_validate_json(line, strict=True) for line in path.read_text().splitlines()
    ]


def test_trace_is_flushed_before_scope_finishes_and_survives_close(tmp_path: Path) -> None:
    with LocalTrace(tmp_path, REF) as log:
        with trace_scope(log, world_ref=REF, agent_kind="character", agent_id="anon"):
            record_trace("prepare.end", {"candidateIds": ["soyo-visible"]})
            assert [r.event for r in read_records(log.path)] == ["decision.start", "prepare.end"]
        record_trace("outside.scope")
    records = read_records(log.path)
    assert [r.seq for r in records] == [1, 2, 3]
    assert len({r.trace_id for r in records}) == 1
    assert records[-1].event == "decision.end"
    assert all(r.world_ref == REF and r.agent_id == "anon" for r in records)
    assert log.path.parent == tmp_path / "coffee-golden/.runtime/traces/save-1"
    assert log.path.stat().st_mode & 0o777 == 0o600
    log.close()  # Idempotent.


@pytest.mark.parametrize("debug", [False, True])
def test_debug_content_is_opt_in_and_console_is_not_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], debug: bool
) -> None:
    with (
        LocalTrace(tmp_path, REF, debug=debug) as log,
        trace_scope(log, world_ref=REF, agent_kind="character", agent_id="anon"),
    ):
        record_trace("model.result", {"callId": "call-1"}, content={"prompt": "private-text"})
    captured = capsys.readouterr()
    assert captured.out == ""
    assert ("private-text" in log.path.read_text()) is debug
    assert ("private-text" in captured.err) is debug


def test_project_world_and_execution_files_do_not_overlap(tmp_path: Path) -> None:
    refs = (
        REF,
        WorldRef(project_id="other", world_id="save-1"),
        WorldRef(project_id="coffee-golden", world_id="save-2"),
        REF,
    )
    paths: list[Path] = []
    for ref in refs:
        with LocalTrace(tmp_path, ref) as log:
            with trace_scope(log, world_ref=ref, agent_kind="character", agent_id="anon"):
                record_trace("own-record")
            paths.append(log.path)
    assert len(set(paths)) == 4
    for ref, path in zip(refs, paths, strict=True):
        assert all(record.world_ref == ref for record in read_records(path))


@pytest.mark.parametrize("identifier", ["..", ".", "../other", "/absolute", "%2F", "角色/存档"])
def test_world_ids_cannot_escape_trace_directory(tmp_path: Path, identifier: str) -> None:
    ref = WorldRef(project_id=identifier, world_id=identifier)
    with LocalTrace(tmp_path, ref) as log:
        assert log.path.resolve().is_relative_to(tmp_path.resolve())
        assert len(log.path.relative_to(tmp_path).parts) == 5


def test_symlink_cannot_redirect_one_project_trace_into_another(tmp_path: Path) -> None:
    (tmp_path / "other").mkdir()
    (tmp_path / "coffee-golden").symlink_to(tmp_path / "other", target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        LocalTrace(tmp_path, REF)
    assert list((tmp_path / "other").iterdir()) == []


def test_case_insensitive_filesystems_still_get_distinct_world_directories(tmp_path: Path) -> None:
    ids = ("save-a", "save-A", "~736176652d41", ".", "..", "角色", "角/色")
    directories: list[str] = []
    for world_id in ids:
        ref = WorldRef(project_id="coffee-golden", world_id=world_id)
        with LocalTrace(tmp_path, ref) as log:
            directories.append(log.path.parent.name)
    assert len({name.casefold() for name in directories}) == len(ids)
    assert directories[0] == "save-a"


def test_error_and_keyboard_interrupt_keep_identity_but_not_exception_text(tmp_path: Path) -> None:
    for exception in (ValueError, KeyboardInterrupt):
        with LocalTrace(tmp_path, REF) as log:
            with (
                pytest.raises(exception),
                trace_scope(log, world_ref=REF, agent_kind="character", agent_id="anon"),
            ):
                record_trace("plan.start")
                raise exception("private-error-body")
            record_trace("outside.scope")
        records = read_records(log.path)
        assert records[-1].event == "decision.error"
        assert records[-1].data["errorType"] == exception.__name__
        assert "private-error-body" not in log.path.read_text()
        assert "outside.scope" not in log.path.read_text()


def test_nested_disabled_scope_does_not_inherit_another_agent(tmp_path: Path) -> None:
    with (
        LocalTrace(tmp_path, REF) as log,
        trace_scope(log, world_ref=REF, agent_kind="character", agent_id="anon"),
    ):
        with trace_scope(None, world_ref=REF, agent_kind="character", agent_id="soyo"):
            record_trace("must-not-be-anon")
        record_trace("anon-again")
    assert [r.event for r in read_records(log.path)] == [
        "decision.start",
        "anon-again",
        "decision.end",
    ]


def test_mismatched_scope_is_rejected_and_mismatched_model_records_are_omitted(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    other = WorldRef(project_id="other", world_id="save-1")
    with LocalTrace(tmp_path, REF, debug=True) as log:
        with (
            pytest.raises(ValueError, match="different WorldRef"),
            trace_scope(log, world_ref=other, agent_kind="character", agent_id="anon"),
        ):
            pytest.fail("wrong scope must not run")
        with trace_scope(log, world_ref=REF, agent_kind="character", agent_id="anon"):
            record_trace("wrong-world", content="foreign-private-data", world_ref=other)
            record_trace("wrong-agent", content="foreign-private-data", agent_id="soyo")
    assert len(read_records(log.path)) == 2
    assert "foreign-private-data" not in log.path.read_text() + caplog.text
    assert "identity mismatch" in caplog.text


def test_threads_keep_actor_context_and_file_sequence(tmp_path: Path) -> None:
    with LocalTrace(tmp_path, REF) as log:

        def decide(agent: str) -> None:
            with trace_scope(log, world_ref=REF, agent_kind="character", agent_id=agent):
                record_trace("actor", {"expectedActor": agent})

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(decide, ["anon", "soyo", "tomori", "taki"]))
    records = read_records(log.path)
    assert [record.seq for record in records] == list(range(1, 13))
    assert len({record.trace_id for record in records}) == 4
    assert all(r.agent_id == r.data["expectedActor"] for r in records if r.event == "actor")


def test_invalid_payload_does_not_leak_or_prevent_later_records(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with (
        LocalTrace(tmp_path, REF) as log,
        trace_scope(log, world_ref=REF, agent_kind="character", agent_id="anon"),
    ):
        record_trace("bad", {"secret-error-body": object()})
        record_trace("nan", {"value": float("nan")})
        record_trace("good")
    assert [r.event for r in read_records(log.path)] == ["decision.start", "good", "decision.end"]
    assert "secret-error-body" not in caplog.text


def test_closed_writer_warns_once_without_changing_caller_result(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with (
        LocalTrace(tmp_path, REF) as log,
        trace_scope(log, world_ref=REF, agent_kind="character", agent_id="anon"),
    ):
        log.close()
        record_trace("write-after-close")
        record_trace("another-write")
    assert [r.event for r in read_records(log.path)] == ["decision.start"]
    assert caplog.text.count("writer disabled") == 1
