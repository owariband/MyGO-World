"""A fixed set of items partitioned by explicit merge and split operations."""

from __future__ import annotations

from collections.abc import Collection, Hashable, Iterable, Mapping


class UnionPart[T: Hashable]:
    """Maintain direct roots and member sets; items need stable hash/equality."""

    def __init__(self, items: Iterable[T]) -> None:
        self._root_by_item: dict[T, T] = {}
        self._members_by_root: dict[T, set[T]] = {}
        for item in items:
            if item in self._root_by_item:
                raise ValueError("items must be unique")
            self._root_by_item[item] = item
            self._members_by_root[item] = {item}

    def root_of(self, item: T) -> T:
        return self._root_by_item[item]

    def members_of(self, root: T) -> frozenset[T]:
        """Return an immutable snapshot, unaffected by later operations."""
        return frozenset(self._require_root(root))

    def connected(self, left: T, right: T) -> bool:
        return self.root_of(left) == self.root_of(right)

    def merge(self, *, keep_root: T, merged_roots: Collection[T]) -> None:
        """Merge other complete partitions into an explicitly retained root."""
        combined = self._require_root(keep_root).copy()
        roots = tuple(merged_roots)
        seen: set[T] = {keep_root}
        for root in roots:
            if root in seen:
                raise ValueError("merged_roots must be unique and exclude keep_root")
            combined.update(self._require_root(root))
            seen.add(root)

        # All caller input is validated before either index changes.
        for item in combined:
            self._root_by_item[item] = keep_root
        self._members_by_root[keep_root] = combined
        for root in roots:
            del self._members_by_root[root]

    def split(self, root: T, parts: Mapping[T, Collection[T]]) -> None:
        """Replace one partition with a complete, explicitly rooted partitioning."""
        original = self._require_root(root)
        groups: dict[T, set[T]] = {}
        covered: set[T] = set()
        for new_root, items in parts.items():
            members = set(items)
            if not members or len(members) != len(items):
                raise ValueError("each part must contain nonempty, unique members")
            if new_root not in members:
                raise ValueError("each part must contain its root")
            if not covered.isdisjoint(members):
                raise ValueError("parts must not overlap")
            groups[new_root] = members
            covered.update(members)
        if covered != original:
            raise ValueError("parts must cover exactly the original members")

        del self._members_by_root[root]
        for new_root, members in groups.items():
            self._members_by_root[new_root] = members
            for item in members:
                self._root_by_item[item] = new_root

    def _require_root(self, root: T) -> set[T]:
        if self.root_of(root) != root:
            raise ValueError("expected a current root, not a non-root member")
        return self._members_by_root[root]
