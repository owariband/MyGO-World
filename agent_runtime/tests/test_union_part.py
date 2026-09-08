"""M1.3: partition invariants, explicit roots, and failure-safe updates."""

from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass
from random import Random
from typing import cast

import pytest

from agent_runtime.common.union_part import UnionPart


def test_empty_singleton_and_unique_initial_items() -> None:
    empty = UnionPart[int](())
    with pytest.raises(KeyError):
        empty.root_of(0)
    with pytest.raises(KeyError):
        empty.members_of(0)

    singleton = UnionPart((0,))
    assert singleton.root_of(0) == 0
    assert singleton.members_of(0) == frozenset({0})
    assert singleton.connected(0, 0)

    parts = UnionPart(iter(range(5)))
    assert _snapshot(parts, tuple(range(5))) == {item: frozenset({item}) for item in range(5)}
    with pytest.raises(ValueError, match="unique"):
        UnionPart((0, 1, 0))
    with pytest.raises(TypeError, match="unhashable"):
        UnionPart((cast(Hashable, [0]),))


def test_repeated_merges_and_splits_leave_other_partitions_unchanged() -> None:
    parts = UnionPart(range(7))
    parts.merge(keep_root=0, merged_roots=[1, 2])
    parts.merge(keep_root=3, merged_roots={4})
    parts.merge(keep_root=0, merged_roots=(3,))
    parts.merge(keep_root=5, merged_roots=(6,))
    assert _snapshot(parts, tuple(range(7))) == {
        0: frozenset(range(5)),
        5: frozenset({5, 6}),
    }

    parts.split(0, {1: (1, 2), 3: {0, 3, 4}})
    assert parts.root_of(0) == 3
    assert parts.connected(1, 2)
    assert not parts.connected(0, 1)
    assert _snapshot(parts, tuple(range(7))) == {
        1: frozenset({1, 2}),
        3: frozenset({0, 3, 4}),
        5: frozenset({5, 6}),
    }

    parts.split(3, {item: (item,) for item in (0, 3, 4)})
    assert _snapshot(parts, tuple(range(7))) == {
        0: frozenset({0}),
        1: frozenset({1, 2}),
        3: frozenset({3}),
        4: frozenset({4}),
        5: frozenset({5, 6}),
    }


def test_no_op_and_single_group_reroot_are_explicit() -> None:
    parts = UnionPart((0, 1, 2))
    parts.merge(keep_root=0, merged_roots=())
    parts.merge(keep_root=0, merged_roots=(1,))
    before = _snapshot(parts, (0, 1, 2))
    parts.split(0, {0: (0, 1)})
    assert _snapshot(parts, (0, 1, 2)) == before

    parts.split(0, {1: [0, 1]})
    assert parts.root_of(0) == 1
    assert parts.members_of(1) == frozenset({0, 1})
    with pytest.raises(ValueError, match="non-root"):
        parts.members_of(0)


@pytest.mark.parametrize(
    ("keep_root", "merged_roots", "error"),
    (
        (99, (), KeyError),
        (1, (), ValueError),
        (0, (99,), KeyError),
        (0, (1,), ValueError),
        (0, (0,), ValueError),
        (0, (2, 2), ValueError),
        (0, (2, 99), KeyError),
        (0, (2, 1), ValueError),
    ),
)
def test_invalid_merge_never_changes_either_index(
    keep_root: int,
    merged_roots: tuple[int, ...],
    error: type[Exception],
) -> None:
    parts = UnionPart(range(4))
    parts.merge(keep_root=0, merged_roots=(1,))
    before = _snapshot(parts, tuple(range(4)))
    with pytest.raises(error):
        parts.merge(keep_root=keep_root, merged_roots=merged_roots)
    assert _snapshot(parts, tuple(range(4))) == before


@pytest.mark.parametrize(
    "groups",
    (
        {},
        {0: (), 1: (0, 1, 2, 3)},
        {0: (0, 0, 1, 2, 3)},
        {0: (0, 1), 1: (1, 2, 3)},
        {0: (0, 1, 2)},
        {0: (0, 1, 2, 3, 4)},
        {0: (0, 1, 2, 3, 99)},
        {0: (1, 2), 3: (0, 3)},
        {0: (0, 1, 2, 3), 4: (4, 5)},
        {99: (0, 1, 2, 3)},
    ),
)
def test_invalid_split_never_changes_either_index(
    groups: dict[int, tuple[int, ...]],
) -> None:
    parts = UnionPart(range(6))
    parts.merge(keep_root=0, merged_roots=(1, 2, 3))
    parts.merge(keep_root=4, merged_roots=(5,))
    before = _snapshot(parts, tuple(range(6)))
    with pytest.raises(ValueError):
        parts.split(0, groups)
    assert _snapshot(parts, tuple(range(6))) == before


def test_unknown_and_non_root_queries_and_split_targets() -> None:
    parts = UnionPart[int]((0, 1, 2))
    parts.merge(keep_root=0, merged_roots=(1,))
    before = _snapshot(parts, (0, 1, 2))
    with pytest.raises(KeyError):
        parts.root_of(99)
    with pytest.raises(KeyError):
        parts.members_of(99)
    with pytest.raises(ValueError, match="non-root"):
        parts.members_of(1)
    with pytest.raises(KeyError):
        parts.connected(0, 99)
    with pytest.raises(KeyError):
        parts.connected(99, 0)
    with pytest.raises(KeyError):
        parts.split(99, {0: (0, 1)})
    with pytest.raises(ValueError, match="non-root"):
        parts.split(1, {0: (0, 1)})
    assert _snapshot(parts, (0, 1, 2)) == before


def test_returned_members_are_detached_immutable_snapshots() -> None:
    parts = UnionPart((0, 1, 2))
    initial = parts.members_of(0)
    assert isinstance(initial, frozenset)
    assert not hasattr(initial, "add")
    parts.merge(keep_root=0, merged_roots=(1,))
    merged = parts.members_of(0)
    parts.split(0, {0: (0,), 1: (1,)})
    assert initial == frozenset({0})
    assert merged == frozenset({0, 1})
    copied_members = set(parts.members_of(0))
    copied_members.add(2)
    assert parts.members_of(0) == frozenset({0})
    assert not parts.connected(0, 2)


@pytest.mark.parametrize("seed", range(8))
def test_deterministic_operation_sequences_preserve_partition_invariants(seed: int) -> None:
    random = Random(seed)
    items = tuple(range(9))
    parts = UnionPart(items)
    expected = {item: frozenset({item}) for item in items}
    for _ in range(60):
        roots = tuple(expected)
        if len(roots) > 1 and random.choice((True, False)):
            keep_root, merged_root = random.sample(roots, 2)
            parts.merge(keep_root=keep_root, merged_roots=(merged_root,))
            expected[keep_root] |= expected.pop(merged_root)
        else:
            old_root = random.choice(roots)
            members = [item for item in items if item in expected[old_root]]
            random.shuffle(members)
            pivot = random.randint(1, len(members))
            groups = {members[0]: tuple(members[:pivot])}
            if pivot < len(members):
                groups[members[pivot]] = tuple(members[pivot:])
            parts.split(old_root, groups)
            del expected[old_root]
            expected.update({root: frozenset(group) for root, group in groups.items()})
        assert _snapshot(parts, items) == expected


def test_hashable_items_do_not_need_ordering_or_string_identity() -> None:
    @dataclass(frozen=True)
    class Item:
        number: int

    first, second, third = (Item(number) for number in range(3))
    parts = UnionPart((first, second, third))
    parts.merge(keep_root=second, merged_roots=(first, third))
    assert parts.members_of(second) == frozenset({first, second, third})
    parts.split(second, {third: (first, third), second: (second,)})
    assert _snapshot(parts, (first, second, third)) == {
        third: frozenset({first, third}),
        second: frozenset({second}),
    }


def _snapshot[T: Hashable](parts: UnionPart[T], items: tuple[T, ...]) -> dict[T, frozenset[T]]:
    groups = {parts.root_of(item): parts.members_of(parts.root_of(item)) for item in items}
    covered: set[T] = set()
    for root, members in groups.items():
        assert parts.root_of(root) == root
        assert root in members
        assert covered.isdisjoint(members)
        assert all(parts.root_of(item) == root for item in members)
        covered.update(members)
    assert covered == set(items)
    return groups
