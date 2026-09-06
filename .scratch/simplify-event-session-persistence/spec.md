# 简化 Event Session lineage 与串行调度持久化

Status: ready-for-agent

## Problem Statement

当前 MVP 将 Event Session 的父子 lineage 和 Runnable Session Queue 分别持久化为
`event_session_parents` 与 `runnable_session_queue` 两张关系表。它们让关系型数据库中
的结构显得明确，但也重复保存了已经存在于 World Ledger、Snapshot 和 Event Session
生命周期字段中的信息：后继 Session 的 `parent_session_ids` 已进入不可变 World
Segment，Session 的创建/关闭版本已经表达入队和出队时点，而每个 Session 在当前 MVP
中只会入队一次。

这两个投影表还扩大了事务写入和迁移表面积。每次初始化、分裂、合并或关闭 Session
都必须同时维护 Event Session、父关系表、队列表和 Snapshot；测试也因而验证多份重复
状态，而不是验证唯一权威事实及用户可观察行为。

MVP 已明确同一 World 不并发推进多个 Event Session。Runtime 在整个 `advance` 期间持有
World 级写锁、每次只选择一个待推进 Session，并按全局 World Version 串行提交。因此
当前 Queue 表不是并发任务队列，也不提供 claim、lease 或 Wave 中途恢复；它只保存一次性
入队的确定性顺序。

## Solution

删除 `event_session_parents` 和 `runnable_session_queue` 两张物理表，同时保留它们背后的
必要领域语义。

Session lineage 继续作为不可变 World Ledger 中后继 Session 创建事实的一部分保存，
Snapshot 和 Canonical Export 仍能展示每个 Session 的 `parent_session_ids`，但不再维护
单独的关系型 lineage 投影。

将单调、不可变的 `queue_order` 合并到 Event Session 持久记录。逻辑上的 Runnable
Session Queue 由 `status = runnable` 的 Session 按 `queue_order` 排序得到。Session 的
`created_world_version` 表示入队版本，`closed_world_version` 表示退出可运行集合的版本，
不再重复保存 `enqueued_world_version` 和 `dequeued_world_version`。

通过新增、可升级既有 World 的数据库迁移完成数据回填和删表；初始化、Session
分裂/合并、队首选择、原子提交、Snapshot 与 Canonical Export 全部改用上述单一表示。
保持同一 World 的 Session 串行推进和同一 Session 内 Character Proposal 并行，不引入
跨 Session 并发。

## User Stories

1. As a Runtime maintainer, I want Event Session persistence to avoid redundant tables, so that lifecycle changes require fewer synchronized writes.
2. As a Runtime maintainer, I want the World Ledger to remain the authority for Session lineage, so that removing a query projection does not discard domain history.
3. As a Runtime maintainer, I want each Event Session to carry its immutable scheduling order, so that the next runnable Session can be selected without a separate Queue row.
4. As a World operator, I want a restarted process to select the same next Session, so that serial generation remains deterministic across processes.
5. As a World operator, I want an interrupted, uncommitted Wave to leave its Session runnable, so that the next `advance` starts from the last committed World Version.
6. As a World operator, I want a closed Session never to be selected again, so that resolved, partitioned and limit-reached lineages do not rerun.
7. As a World operator, I want multiple initial Sessions to retain Scenario Seed order, so that startup scheduling remains predictable.
8. As a World operator, I want successor Sessions to receive monotonically increasing queue order, so that split results are processed deterministically.
9. As a World operator, I want a Generation Batch to continue only the selected successor lineage, so that removing the Queue table does not alter current batch semantics.
10. As a World operator, I want remaining successor Sessions to stay runnable for later batches, so that a split does not lose work.
11. As an auditor, I want split and merge ancestry visible in Snapshot and Canonical Export, so that lineage remains inspectable without a dedicated relation table.
12. As an auditor, I want each lineage edge recoverable from immutable Segment history, so that Snapshot or export projections can be rebuilt.
13. As an auditor, I want scheduling order recoverable from persisted Session and Ledger data, so that deleting a cache does not change the next runnable Session.
14. As a migration operator, I want existing queue order copied before the old Queue table is dropped, so that upgraded Worlds continue in exactly the same order.
15. As a migration operator, I want existing lineage facts verified against the Ledger before their projection table is removed, so that inconsistent databases fail visibly rather than silently losing information.
16. As a migration operator, I want every existing Session to receive exactly one positive, unique queue order, so that the upgraded scheduler has no ambiguous head.
17. As a migration operator, I want upgrade and downgrade behavior to preserve observable Session scheduling and ancestry, so that schema movement does not corrupt a durable World.
18. As a database maintainer, I want Event Session members to remain immutable after Session creation, so that removal of the Queue table does not weaken an existing invariant.
19. As a database maintainer, I want Session location, scope, creation version and queue order immutable after creation, so that scheduling and interaction boundaries cannot drift.
20. As a test author, I want behavior verified through initialization, advancement, split/merge and export, so that tests assert domain outcomes rather than Repository implementation.
21. As a test author, I want the final database schema checked for absence of both removed tables, so that obsolete persistence cannot survive unnoticed.
22. As a test author, I want atomic rollback exercised after a lineage transition failure, so that Session rows, positions, pending responses and Snapshot remain unchanged together.
23. As a developer, I want existing Fixture demos to remain deterministic, so that schema simplification does not weaken reproducibility.
24. As a developer, I want default tests to remain offline, so that validating persistence changes never requires Provider credentials or WebGAL assets.
25. As a system designer, I want the logical Runnable Session Queue retained as vocabulary while its physical representation is simplified, so that scheduling semantics remain clear.
26. As a system designer, I want cross-Session concurrency explicitly excluded from the MVP, so that no one mistakes `queue_order` for a worker-claim protocol.
27. As a documentation reader, I want the MVP schema appendix to describe only the resulting tables and projections, so that implementation and architectural documentation agree.
28. As a future maintainer, I want the decision and its trade-offs recorded, so that a dedicated lineage index or leased work queue is reintroduced only when concrete query or concurrency requirements appear.

## Implementation Decisions

- Remove the `event_session_parents` and `runnable_session_queue` ORM models and physical tables. Do not introduce replacement tables for either concern.
- Add a positive, globally unique `queue_order` column to Event Session persistence. It is assigned exactly once and is immutable after the Session becomes externally visible.
- Assign initial Session queue order from Scenario Seed list order, preserving current behavior rather than sorting by Session ID or UUID.
- Assign successor Session queue order from the next global monotonic value. Preserve the existing deterministic successor ordering based on sorted participant groups.
- Define the logical Runnable Session Queue as all Event Sessions whose status is `runnable`, ordered by `queue_order`. Queue-head selection must use this definition.
- Treat `created_world_version` as the enqueue version and `closed_world_version` as the dequeue version. Do not retain duplicate enqueue/dequeue columns.
- Keep the World-level mutation lock and one-head-per-Generation-Batch behavior. The refactor must not add cross-Session workers, claims, leases, priorities, requeue operations or concurrent commits.
- Preserve Character-level parallel proposal generation inside the selected Event Session.
- Keep Session lineage as an immutable creation fact in World Segment successor data. Initial Sessions have no parents; split successors have one parent; merge successors may have multiple parents.
- Do not make a separate mutable lineage source. Snapshot and Canonical Export lineage views must be derived from committed Ledger facts, with stable sorting.
- Preserve the external Snapshot and Canonical Export information currently needed by operators: Session status, participants, parent IDs and deterministic scheduling order must remain observable. Preserve the existing separate `parents` and `queue` export arrays as derived compatibility views; they must not be backed by removed tables or treated as authoritative.
- Preserve current `show`, `advance`, deterministic Demo and export behavior. Removing tables must not change which Session is selected, when a Session closes, which successor continues in the current Batch, or which successor is selected by the next Batch.
- Add a forward migration after the current schema head. It must copy every old Queue row's `queue_order` into its Event Session before dropping the Queue table, then drop the parent projection table.
- Before destructive migration steps, validate that every Event Session has exactly one Queue row, queue order is positive and unique, enqueue version matches Session creation version, dequeue version matches Session closure state, and persisted parent edges are recoverable from committed Segment data. Abort with a clear migration failure when these invariants are violated.
- Preserve existing Worlds. Do not rewrite historical migration files or require users to recreate a World database.
- Provide a data-preserving downgrade: recreate the two projection tables from Event Session lifecycle/order and World Ledger lineage data. If the migration framework cannot guarantee a correct downgrade, fail explicitly rather than fabricating lineage.
- Replace the member-immutability trigger's use of Queue-row existence with an equivalent committed-Session boundary. Session members must remain insertable only while a new Session is being assembled inside its creation transaction, then reject late insert, update and delete operations.
- Keep `event_session_pending_responses` unchanged in this refactor. Its possible redesign into event-specific response obligations is a separate decision.
- Update the MVP schema appendix and related prose so neither deleted table appears in the current physical table list. Document `queue_order` on Event Session and state that parent lineage is held in Ledger successor facts and exposed through derived projections.
- Add a new ADR that supersedes only the physical-persistence portions of the existing Session-boundary and single-frontier decisions. Preserve their domain rules: fixed Session membership, partition/merge lineage, deterministic single-frontier scheduling and serialized World mutation.
- Keep the logical term “Runnable Session Queue” if useful, but define it as a derived ordered set rather than a standalone persistent entity.
- Refresh deterministic Fixture input hashes and expected serialized artifacts only where the changed Snapshot/export contract makes that necessary; do not relax hash verification.

## Testing Decisions

- Use one primary high-level behavior seam: initialize a World, run `advance` through production Runtime, inspect `show`/Canonical Export, reopen the same World, and advance again. This proves persistence, deterministic selection and restart behavior without testing private helper calls.
- Extend the existing Session lineage test family as prior art. Cover a split that closes the original Session, creates all successors with stable parent IDs and monotonically increasing queue order, continues the first successor in the current Batch, and selects the next runnable successor in a later Batch.
- Preserve the existing merge scenario and assert that one successor exposes all parent Session IDs even though no parent table exists.
- Preserve the existing no-work behavior: when no Event Session has `status = runnable`, `advance` returns `no_work`, performs no model call and creates no empty Batch.
- Preserve the atomic rollback scenario. Inject failure during Session transition and assert that World Version, Character position, Session rows, pending-response state, Snapshot and next-session selection are unchanged.
- Add a schema assertion at initialization or migration level that `event_session_parents` and `runnable_session_queue` do not exist and that Event Session contains a unique queue-order representation.
- Add one explicit migration test starting from the previous schema revision with multiple initial Sessions plus at least one committed split/merge. Upgrade to the new head, then verify identical next-Session selection, lineage export, lifecycle versions and queue ordering.
- Exercise downgrade in the migration test when supported and verify that reconstructed projection rows match the pre-upgrade observable relationships and ordering.
- Keep assertions focused on externally visible Session behavior and durable schema invariants. Do not assert which Repository helper or SQL query implementation is used.
- Update Canonical Export tests to prove derived compatibility views, when retained, exactly match Event Session rows and Ledger lineage facts.
- Run focused Session lineage, world initialization, schema/ledger and deterministic Fixture Demo tests during development.
- Completion requires all default offline tests plus `uv run ruff check .` and `uv run ruff format --check .` to pass. Live Provider tests, paid calls and external WebGAL integration are not required.

## Out of Scope

- Concurrent execution or commit of multiple Event Sessions in one World.
- Queue worker claims, leases, visibility timeouts, priorities, pause/resume, retries as queue entries, or re-enqueueing one Session.
- Mid-Wave checkpoint recovery. Recovery remains “restart from the last completely committed World Version.”
- Removing the World-level mutation lock or replacing the global World Version sequence.
- Changing Character Proposal concurrency within one Event Session.
- Removing Session lineage as a domain concept or deleting lineage facts from World Segment history.
- Redesigning `event_session_pending_responses` or changing the explicit utterance response semantics.
- Changing Session split, merge, resolution, limit-reached or pending-response rules.
- Changing Scenario Seed ordering semantics.
- Running a real Provider, spending Provider quota, publishing WebGAL output, committing, pushing, reviewing or deploying.

## Further Notes

- This decision intentionally separates domain facts from physical projections: lineage and runnable ordering remain part of the model even though two dedicated tables disappear.
- The current parent table is not read by Session execution; it is written during commit and read for export/tests. The Ledger already stores the same successor-parent facts.
- The current Queue table is not a concurrent work queue. It is read only to select one head while a World-wide mutation lock is held, and its enqueue/dequeue versions duplicate Session creation/closure versions under current invariants.
- Existing Snapshot data contains both Session lineage and a runnable queue projection. The implementation must preserve these external shapes for compatibility, but they remain rebuildable caches rather than new authorities.
- The repository currently has an earlier uncommitted MVP appendix change. Execution must preserve it and edit that same document carefully rather than discarding or replacing unrelated content.
- This is a bounded schema-and-runtime refactor that fits one implementation context.
