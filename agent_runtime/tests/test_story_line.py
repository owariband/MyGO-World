"""M4 latest-only StoryLine projection and deterministic export tests."""

from __future__ import annotations

import copy
import json
import os
import stat
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from agent_runtime.event.session import plan_join, plan_leave
from agent_runtime.event.story_line import (
    StageView,
    StoryLine,
    StoryLineBuilder,
    StoryLineKey,
    export_storyline,
)
from agent_runtime.sqlite import ProjectDatabase, open_project_database
from agent_runtime.world.contracts import CommitPosition, DeliveryChannel, WorldRef
from agent_runtime.world.entries import (
    AudienceMode,
    DialogueEntry,
    EntryRelationKind,
    EventEntryLink,
    EventEntryRecipient,
    SessionTransitionEntry,
)
from agent_runtime.world.entry_storage import EventEntryStore
from agent_runtime.world.state import (
    AgentWorldState,
    EventSessionNode,
    LocationState,
    PublicWorldState,
    WorldState,
    WorldStatus,
)
from agent_runtime.world.storage import WorldStore

PROJECT_ID = "story-line-tests"
WORLD_REF = WorldRef(project_id=PROJECT_ID, world_id="save-001")
NOW = datetime(2026, 9, 11, 12, tzinfo=UTC)


def test_builder_keeps_reused_roots_lineage_and_empty_child_lines(tmp_path: Path) -> None:
    database = _database_with_merge_and_split(tmp_path)
    try:
        with database.session_factory() as session:
            stage = StoryLineBuilder(WORLD_REF).build(session)

        assert stage.status is WorldStatus.PAUSED
        assert stage.built_through == CommitPosition(world_version=4)
        assert tuple(
            (line.key.root_session_id, line.key.topology_version) for line in stage.story_lines
        ) == (
            ("session-anon", 1),
            ("session-soyo", 1),
            ("session-anon", 3),
            ("session-anon", 4),
            ("session-tomori", 4),
        )

        lines = {_key(line.key): line for line in stage.story_lines}
        assert lines[("session-anon", 1)].member_agent_ids == ("anon", "tomori")
        assert lines[("session-soyo", 1)].member_agent_ids == ("soyo",)
        assert lines[("session-anon", 3)].member_agent_ids == (
            "anon",
            "soyo",
            "tomori",
        )
        assert lines[("session-anon", 4)].member_agent_ids == ("anon", "soyo")
        assert lines[("session-tomori", 4)].member_agent_ids == ("tomori",)

        assert _entry_ids(lines[("session-anon", 1)]) == ("entry-dialogue",)
        assert _entry_ids(lines[("session-soyo", 1)]) == ()
        assert _entry_ids(lines[("session-anon", 3)]) == ("entry-merge",)
        assert _entry_ids(lines[("session-anon", 4)]) == ()
        assert _entry_ids(lines[("session-tomori", 4)]) == ("entry-split",)

        assert _keys(lines[("session-anon", 1)].child_line_keys) == {("session-anon", 3)}
        assert _keys(lines[("session-soyo", 1)].child_line_keys) == {("session-anon", 3)}
        assert _keys(lines[("session-anon", 3)].parent_line_keys) == {
            ("session-anon", 1),
            ("session-soyo", 1),
        }
        assert _keys(lines[("session-anon", 3)].child_line_keys) == {
            ("session-anon", 4),
            ("session-tomori", 4),
        }
        assert _keys(lines[("session-anon", 4)].parent_line_keys) == {("session-anon", 3)}
        assert _keys(lines[("session-tomori", 4)].parent_line_keys) == {("session-anon", 3)}

        merge = lines[("session-anon", 3)].entries[0]
        assert merge.transition is not None
        assert merge.transition.reason.value == "merge"
        assert tuple(_key(part.line_key) for part in merge.transition.before_parts) == (
            ("session-anon", 1),
            ("session-soyo", 1),
        )
        assert tuple(_key(part.line_key) for part in merge.transition.after_parts) == (
            ("session-anon", 3),
        )

        split = lines[("session-tomori", 4)].entries[0]
        assert split.transition is not None
        assert split.transition.reason.value == "split"
        assert tuple(_key(part.line_key) for part in split.transition.before_parts) == (
            ("session-anon", 3),
        )
        assert tuple(_key(part.line_key) for part in split.transition.after_parts) == (
            ("session-anon", 4),
            ("session-tomori", 4),
        )
    finally:
        database.dispose()


def test_builder_preserves_historical_recipients_and_links(tmp_path: Path) -> None:
    database = _database_with_merge_and_split(tmp_path)
    try:
        with database.session_factory() as session:
            stage = StoryLineBuilder(WORLD_REF).build(session)

        entries = {entry.entry_id: entry for line in stage.story_lines for entry in line.entries}
        assert tuple(entries) == ("entry-dialogue", "entry-merge", "entry-split")
        assert entries["entry-dialogue"].recipient_agent_ids == ("anon", "tomori")
        assert "soyo" not in entries["entry-dialogue"].recipient_agent_ids
        assert entries["entry-merge"].recipient_agent_ids == ("anon", "soyo", "tomori")
        assert entries["entry-split"].recipient_agent_ids == ("anon", "soyo", "tomori")
        assert tuple(
            (link.relation_kind.value, link.related_entry_id)
            for link in entries["entry-merge"].links
        ) == (("previous", "entry-dialogue"),)
        assert tuple(
            (link.relation_kind.value, link.related_entry_id)
            for link in entries["entry-split"].links
        ) == (("previous", "entry-merge"),)
    finally:
        database.dispose()


def test_canonical_export_is_stable_and_consumed_by_the_node_viewer(
    tmp_path: Path,
) -> None:
    database = _database_with_merge_and_split(tmp_path)
    story_path = tmp_path / "storyline.json"
    html_path = tmp_path / "storyline.html"
    repository_root = Path(__file__).parents[2]
    try:
        with database.session_factory() as session:
            first = StoryLineBuilder(WORLD_REF).build(session).to_canonical_json()
        with database.session_factory() as session:
            second = StoryLineBuilder(WORLD_REF).build(session).to_canonical_json()
        assert first == second

        export_storyline(database, WORLD_REF, story_path)
        assert story_path.read_bytes() == first.encode()
        assert stat.S_IMODE(story_path.stat().st_mode) == 0o600
        payload = json.loads(first)
        assert tuple(payload) == (
            "builtThrough",
            "formatVersion",
            "status",
            "storyLines",
            "worldRef",
        )
        assert "privateMemory" not in first

        converted = subprocess.run(
            (
                "node",
                str(repository_root / "tools" / "storyline-to-html.mjs"),
                str(story_path),
                "--out",
                str(html_path),
            ),
            cwd=repository_root,
            check=False,
            capture_output=True,
            text=True,
        )
        assert converted.returncode == 0, converted.stderr
        html = html_path.read_text(encoding="utf-8")
        assert "session-anon" in html
        assert "session-tomori" in html
        assert "<b>变化前</b>" in html
        assert "<b>变化后</b>" in html
    finally:
        database.dispose()


def test_empty_world_exports_each_current_line_without_a_frontier(tmp_path: Path) -> None:
    database = open_project_database(tmp_path / ".runtime", PROJECT_ID, create=True)
    try:
        with database.session_factory.begin() as session:
            WorldStore(WORLD_REF).insert_initial(
                session,
                _state((("anon",), ("tomori",))),
            )
        with database.session_factory() as session:
            stage = StoryLineBuilder(WORLD_REF).build(
                session,
                as_of=CommitPosition(world_version=1),
            )

        assert stage.built_through is None
        assert tuple(_key(line.key) for line in stage.story_lines) == (
            ("session-anon", 1),
            ("session-tomori", 1),
        )
        assert all(not line.entries for line in stage.story_lines)
        assert json.loads(stage.to_canonical_json())["builtThrough"] is None
    finally:
        database.dispose()


def test_builder_as_of_reconstructs_merge_split_and_excludes_future_entries(
    tmp_path: Path,
) -> None:
    database = _database_with_merge_and_split(tmp_path)
    _append_post_split_dialogue(database)
    builder = StoryLineBuilder(WORLD_REF)
    try:
        with database.session_factory() as session:
            initial = builder.build(session, as_of=CommitPosition(world_version=1))
            after_dialogue = builder.build(
                session,
                as_of=CommitPosition(world_version=2),
            )
            after_merge = builder.build(
                session,
                as_of=CommitPosition(world_version=3),
            )
            between_entries = builder.build(
                session,
                as_of=CommitPosition(world_version=3, entry_index=99),
            )
            after_split = builder.build(
                session,
                as_of=CommitPosition(world_version=4),
            )
            latest = builder.build(session)

        assert initial.built_through is None
        assert initial.status is WorldStatus.PAUSED
        assert tuple(_key(line.key) for line in initial.story_lines) == (
            ("session-anon", 1),
            ("session-soyo", 1),
        )
        assert all(not line.entries for line in initial.story_lines)
        assert _stage_entry_ids(after_dialogue) == ("entry-dialogue",)
        assert after_dialogue.built_through == CommitPosition(world_version=2)

        assert _stage_entry_ids(after_merge) == ("entry-dialogue", "entry-merge")
        assert after_merge.built_through == CommitPosition(world_version=3)
        assert between_entries == after_merge
        assert tuple(_key(line.key) for line in after_merge.story_lines) == (
            ("session-anon", 1),
            ("session-soyo", 1),
            ("session-anon", 3),
        )
        assert _keys(after_merge.story_lines[-1].parent_line_keys) == {
            ("session-anon", 1),
            ("session-soyo", 1),
        }
        merge_entry = after_merge.story_lines[-1].entries[0]
        assert merge_entry.transition is not None
        assert merge_entry.transition.reason.value == "merge"
        assert tuple(link.related_entry_id for link in merge_entry.links) == ("entry-dialogue",)

        assert _stage_entry_ids(after_split) == (
            "entry-dialogue",
            "entry-merge",
            "entry-split",
        )
        assert after_split.built_through == CommitPosition(world_version=4)
        assert {line.key.topology_version for line in after_split.story_lines} == {1, 3, 4}
        split_lines = {_key(line.key): line for line in after_split.story_lines}
        assert split_lines[("session-anon", 4)].entries == ()
        assert _entry_ids(split_lines[("session-tomori", 4)]) == ("entry-split",)
        assert "entry-after-split" not in after_split.to_canonical_json()

        assert _stage_entry_ids(latest) == (
            "entry-dialogue",
            "entry-merge",
            "entry-split",
            "entry-after-split",
        )
        assert latest.built_through == CommitPosition(world_version=5)
    finally:
        database.dispose()


def test_builder_rejects_as_of_beyond_the_current_world_position(tmp_path: Path) -> None:
    database = _database_with_merge_and_split(tmp_path)
    builder = StoryLineBuilder(WORLD_REF)
    try:
        with database.session_factory() as session:
            with pytest.raises(ValueError, match="newer than the current World position"):
                builder.build(session, as_of=CommitPosition(world_version=5))
            with pytest.raises(ValueError, match="newer than the current World position"):
                builder.build(
                    session,
                    as_of=CommitPosition(world_version=4, entry_index=1),
                )
    finally:
        database.dispose()


def test_builder_uses_but_never_commits_the_callers_transaction(tmp_path: Path) -> None:
    database = open_project_database(tmp_path / ".runtime", PROJECT_ID, create=True)
    store = WorldStore(WORLD_REF)
    try:
        with database.session_factory.begin() as session:
            store.insert_initial(session, _state((("anon",),)))

        with database.session_factory() as session:
            session.execute(
                text("UPDATE worlds SET status = 'running' WHERE world_id = :world_id"),
                {"world_id": WORLD_REF.world_id},
            )
            stage = StoryLineBuilder(WORLD_REF).build(session)
            assert session.in_transaction()
            assert stage.status is WorldStatus.RUNNING

        with database.session_factory() as session:
            assert StoryLineBuilder(WORLD_REF).build(session).status is WorldStatus.PAUSED
    finally:
        database.dispose()


def test_stage_view_rejects_links_outside_the_exported_graph(tmp_path: Path) -> None:
    database = _database_with_merge_and_split(tmp_path)
    try:
        with database.session_factory() as session:
            payload = (
                StoryLineBuilder(WORLD_REF)
                .build(session)
                .model_dump(
                    mode="json",
                    by_alias=True,
                    exclude_none=True,
                )
            )
        split_entry = next(
            entry
            for line in payload["storyLines"]
            for entry in line["entries"]
            if entry["entryId"] == "entry-split"
        )
        split_entry["links"][0]["relatedEntryId"] = "missing-entry"

        with pytest.raises(ValidationError, match="outside the StageView"):
            StageView.model_validate_json(json.dumps(payload), strict=True)
    finally:
        database.dispose()


def test_stage_view_rejects_entry_visibility_outside_its_storyline(
    tmp_path: Path,
) -> None:
    database = _database_with_merge_and_split(tmp_path)
    try:
        with database.session_factory() as session:
            payload = (
                StoryLineBuilder(WORLD_REF)
                .build(session)
                .model_dump(mode="json", by_alias=True, exclude_none=True)
            )
        dialogue = next(
            entry
            for line in payload["storyLines"]
            for entry in line["entries"]
            if entry["entryId"] == "entry-dialogue"
        )

        foreign_actor = copy.deepcopy(payload)
        foreign_dialogue = next(
            entry
            for line in foreign_actor["storyLines"]
            for entry in line["entries"]
            if entry["entryId"] == "entry-dialogue"
        )
        foreign_dialogue["actorAgentId"] = "foreign-agent"
        foreign_dialogue["recipientAgentIds"].append("foreign-agent")
        with pytest.raises(ValidationError, match="actor must belong"):
            StageView.model_validate_json(json.dumps(foreign_actor), strict=True)

        dialogue["recipientAgentIds"] = ["anon"]
        with pytest.raises(ValidationError, match="recipients do not match"):
            StageView.model_validate_json(json.dumps(payload), strict=True)

        foreign_target = copy.deepcopy(foreign_actor)
        foreign_dialogue = _payload_entry(foreign_target, "entry-dialogue")
        foreign_dialogue["actorAgentId"] = "anon"
        foreign_dialogue["recipientAgentIds"] = ["anon", "tomori"]
        foreign_dialogue["targetAgentId"] = "foreign-agent"
        with pytest.raises(ValidationError, match="dialogue target must belong"):
            StageView.model_validate_json(json.dumps(foreign_target), strict=True)
    finally:
        database.dispose()


def test_stage_view_rejects_transition_partition_visibility_and_topology_drift(
    tmp_path: Path,
) -> None:
    database = _database_with_merge_and_split(tmp_path)
    try:
        with database.session_factory() as session:
            payload = (
                StoryLineBuilder(WORLD_REF)
                .build(session)
                .model_dump(mode="json", by_alias=True, exclude_none=True)
            )

        mismatched_after = copy.deepcopy(payload)
        split = _payload_entry(mismatched_after, "entry-split")
        split["transition"]["afterParts"][1]["lineKey"]["topologyVersion"] = 99
        with pytest.raises(ValidationError, match="after parts must share"):
            StageView.model_validate_json(json.dumps(mismatched_after), strict=True)

        future_after = copy.deepcopy(payload)
        split = _payload_entry(future_after, "entry-split")
        split["commitPosition"]["worldVersion"] = 5
        future_after["builtThrough"]["worldVersion"] = 5
        with pytest.raises(ValidationError, match="must equal its commit World version"):
            StageView.model_validate_json(json.dumps(future_after), strict=True)

        missing_recipient = copy.deepcopy(payload)
        split = _payload_entry(missing_recipient, "entry-split")
        split["recipientAgentIds"] = ["tomori"]
        with pytest.raises(ValidationError, match="recipients must equal all affected"):
            StageView.model_validate_json(json.dumps(missing_recipient), strict=True)

        foreign_target = copy.deepcopy(payload)
        merge = _payload_entry(foreign_target, "entry-merge")
        merge["targetAgentId"] = "foreign-agent"
        with pytest.raises(ValidationError, match="target must belong"):
            StageView.model_validate_json(json.dumps(foreign_target), strict=True)

        phantom_lineage = copy.deepcopy(payload)
        split_line = next(
            line
            for line in phantom_lineage["storyLines"]
            if any(entry["entryId"] == "entry-split" for entry in line["entries"])
        )
        split_line["entries"] = []
        phantom_lineage["builtThrough"] = {"worldVersion": 3, "entryIndex": 0}
        with pytest.raises(ValidationError, match="lineage must exactly match"):
            StageView.model_validate_json(json.dumps(phantom_lineage), strict=True)
    finally:
        database.dispose()


def test_export_rejects_database_aliases_and_replaces_symlink_atomically(
    tmp_path: Path,
) -> None:
    database = _database_with_merge_and_split(tmp_path)
    hard_link = tmp_path / "database-hard-link.json"
    symbolic_link = tmp_path / "database-symbolic-link.json"
    sentinel = tmp_path / "sentinel.txt"
    output = tmp_path / "storyline.json"
    try:
        os.link(database.path, hard_link)
        symbolic_link.symlink_to(database.path)
        with pytest.raises(ValueError, match="cannot replace its Project database"):
            export_storyline(database, WORLD_REF, hard_link)
        with pytest.raises(ValueError, match="cannot replace its Project database"):
            export_storyline(database, WORLD_REF, symbolic_link)

        sentinel.write_text("do not overwrite", encoding="utf-8")
        output.symlink_to(sentinel)
        export_storyline(database, WORLD_REF, output)
        assert sentinel.read_text(encoding="utf-8") == "do not overwrite"
        assert not output.is_symlink()
        assert stat.S_IMODE(output.stat().st_mode) == 0o600
        with database.session_factory() as session:
            assert StoryLineBuilder(WORLD_REF).build(session).world_ref == WORLD_REF
    finally:
        database.dispose()


def _database_with_merge_and_split(tmp_path: Path) -> ProjectDatabase:
    database = open_project_database(tmp_path / ".runtime", PROJECT_ID, create=True)
    world_store = WorldStore(WORLD_REF)
    entry_store = EventEntryStore(WORLD_REF)
    with database.session_factory.begin() as session:
        world_store.insert_initial(
            session,
            _state((("anon", "tomori"), ("soyo",))),
        )
        session.execute(
            text("UPDATE worlds SET status = 'running' WHERE world_id = :world_id"),
            {"world_id": WORLD_REF.world_id},
        )

    dialogue = DialogueEntry(
        world_ref=WORLD_REF,
        entry_id="entry-dialogue",
        source_id="proposal-dialogue",
        commit_position=CommitPosition(world_version=2),
        root_session_id_at_commit="session-anon",
        topology_version=1,
        actor_agent_id="anon",
        target_agent_id="tomori",
        audience_mode=AudienceMode.SESSION,
        delivery_channel=DeliveryChannel.DIRECT,
        occurred_at=NOW,
        text="灯，魔咒课后要一起练习吗？",
        created_at=NOW,
    )
    with database.session_factory.begin() as session:
        world_store.compare_and_advance(
            session,
            expected_version=1,
            expected_control_epoch=1,
            expected_decision_seq=0,
            advances_world=True,
        )
        entry_store.append(
            session,
            dialogue,
            recipients=_recipients(dialogue.entry_id, "anon", "tomori"),
        )

    with database.session_factory.begin() as session:
        transition = plan_join(
            world_store.load(session),
            actor_agent_id="soyo",
            target_agent_id="anon",
        )
        world_store.compare_and_advance(
            session,
            expected_version=2,
            expected_control_epoch=1,
            expected_decision_seq=1,
            advances_world=True,
        )
        world_store.apply_session_transition(session, transition)
        entry = SessionTransitionEntry(
            world_ref=WORLD_REF,
            entry_id="entry-merge",
            source_id="proposal-merge",
            commit_position=CommitPosition(world_version=3),
            root_session_id_at_commit="session-anon",
            topology_version=3,
            actor_agent_id="soyo",
            target_agent_id="anon",
            occurred_at=NOW + timedelta(minutes=1),
            text="素世走近爱音与灯，加入了她们的谈话。",
            created_at=NOW + timedelta(minutes=1),
            transition_reason=transition.reason,
        )
        entry_store.append(
            session,
            entry,
            links=(_previous(entry.entry_id, dialogue.entry_id),),
            recipients=_recipients(entry.entry_id, "anon", "soyo", "tomori"),
            transition=transition,
        )

    with database.session_factory.begin() as session:
        transition = plan_leave(
            world_store.load(session),
            actor_agent_id="tomori",
        )
        world_store.compare_and_advance(
            session,
            expected_version=3,
            expected_control_epoch=1,
            expected_decision_seq=2,
            advances_world=True,
        )
        world_store.apply_session_transition(session, transition)
        entry = SessionTransitionEntry(
            world_ref=WORLD_REF,
            entry_id="entry-split",
            source_id="proposal-split",
            commit_position=CommitPosition(world_version=4),
            root_session_id_at_commit="session-tomori",
            topology_version=4,
            actor_agent_id="tomori",
            occurred_at=NOW + timedelta(minutes=2),
            text="灯想起约定，暂时离开去猫头鹰棚。",
            created_at=NOW + timedelta(minutes=2),
            transition_reason=transition.reason,
        )
        entry_store.append(
            session,
            entry,
            links=(_previous(entry.entry_id, "entry-merge"),),
            recipients=_recipients(entry.entry_id, "anon", "soyo", "tomori"),
            transition=transition,
        )
        world_store.pause(session, reason="story review")
    return database


def _append_post_split_dialogue(database: ProjectDatabase) -> None:
    world_store = WorldStore(WORLD_REF)
    entry_store = EventEntryStore(WORLD_REF)
    entry = DialogueEntry(
        world_ref=WORLD_REF,
        entry_id="entry-after-split",
        source_id="proposal-after-split",
        commit_position=CommitPosition(world_version=5),
        root_session_id_at_commit="session-anon",
        topology_version=4,
        actor_agent_id="anon",
        target_agent_id="soyo",
        audience_mode=AudienceMode.SESSION,
        delivery_channel=DeliveryChannel.DIRECT,
        occurred_at=NOW + timedelta(minutes=3),
        text="素世，我们先把这一段练完吧。",
        created_at=NOW + timedelta(minutes=3),
    )
    with database.session_factory.begin() as session:
        session.execute(
            text("UPDATE worlds SET status = 'running' WHERE world_id = :world_id"),
            {"world_id": WORLD_REF.world_id},
        )
        world_store.compare_and_advance(
            session,
            expected_version=4,
            expected_control_epoch=2,
            expected_decision_seq=3,
            advances_world=True,
        )
        entry_store.append(
            session,
            entry,
            links=(_previous(entry.entry_id, "entry-split"),),
            recipients=_recipients(entry.entry_id, "anon", "soyo"),
        )
        world_store.pause(session, reason="as-of review")


def _state(partitions: tuple[tuple[str, ...], ...]) -> PublicWorldState:
    agent_ids = tuple(agent_id for partition in partitions for agent_id in partition)
    return PublicWorldState(
        world=WorldState(
            world_ref=WORLD_REF,
            seed_id="story-line-seed",
            seed_version=1,
            seed_hash="a" * 64,
            current_version=1,
            world_time=NOW,
            status=WorldStatus.PAUSED,
            created_at=NOW,
        ),
        locations=(
            LocationState(
                world_ref=WORLD_REF,
                location_id="charms-room",
                name="Charms classroom",
                description="A bright Hogwarts classroom",
            ),
        ),
        agents=tuple(
            AgentWorldState(
                world_ref=WORLD_REF,
                agent_id=agent_id,
                location_id="charms-room",
            )
            for agent_id in agent_ids
        ),
        sessions=tuple(
            EventSessionNode(
                world_ref=WORLD_REF,
                session_id=f"session-{agent_id}",
                agent_id=agent_id,
                root_session_id=f"session-{partition[0]}",
                topology_version=1,
                updated_world_version=1,
            )
            for partition in partitions
            for agent_id in partition
        ),
    )


def _previous(entry_id: str, related_entry_id: str) -> EventEntryLink:
    return EventEntryLink(
        world_ref=WORLD_REF,
        entry_id=entry_id,
        relation_kind=EntryRelationKind.PREVIOUS,
        related_entry_id=related_entry_id,
    )


def _recipients(entry_id: str, *agent_ids: str) -> tuple[EventEntryRecipient, ...]:
    return tuple(
        EventEntryRecipient(world_ref=WORLD_REF, entry_id=entry_id, agent_id=agent_id)
        for agent_id in agent_ids
    )


def _key(key: StoryLineKey) -> tuple[str, int]:
    return key.root_session_id, key.topology_version


def _keys(keys: tuple[StoryLineKey, ...]) -> set[tuple[str, int]]:
    return {_key(key) for key in keys}


def _payload_entry(payload: dict[str, Any], entry_id: str) -> dict[str, Any]:
    story_lines = cast(list[dict[str, Any]], payload["storyLines"])
    for line in story_lines:
        entries = cast(list[dict[str, Any]], line["entries"])
        for entry in entries:
            if entry["entryId"] == entry_id:
                return entry
    raise AssertionError(f'Entry "{entry_id}" not found')


def _entry_ids(line: StoryLine) -> tuple[str, ...]:
    return tuple(entry.entry_id for entry in line.entries)


def _stage_entry_ids(stage: StageView) -> tuple[str, ...]:
    entries = sorted(
        (entry for line in stage.story_lines for entry in line.entries),
        key=lambda entry: (
            entry.commit_position.world_version,
            entry.commit_position.entry_index,
        ),
    )
    return tuple(entry.entry_id for entry in entries)
