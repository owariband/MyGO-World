# 04 — 跨 Batch 调度 EventSession lineage

**What to build:** 让 World 能够根据 Character 的互动与移动确定性地关闭、分裂或合并 Event Session，并让后续 `advance` 按持久 FIFO 队列跨进程继续正确 lineage。

**Blocked by:** 03 — 运行多 Character 的可靠 Lockstep Batch.

Status: resolved

**Execution goal:** `../goal.md`

- [x] Event Session 具有稳定身份、固定参与者集合、一个 Interaction Scope 和可追踪的关闭原因。
- [x] Location 作为持久 Entity 保存稳定 Scope key 与显式可达边，Character 位置由 Location 与 Scope 共同确定。
- [x] 角色仅共享 Scope 不会自动加入同一 Session；发生直接互动时可以创建带父引用的合并后继 Session。
- [x] Runtime 根据 Wave 候选结束位置计算分区，不让模型决定分区或队列顺序。
- [x] 角色位置、旧 Session 的 `partitioned` 关闭、所有后继 Session 创建和 FIFO 入队在同一个 World 提交事务中完成。
- [x] 分裂后所有后继按参与者 ID 的确定顺序入队，当前 Batch 只继续队首后继，其余留给之后的 Batch。
- [x] 移出 Scope 的 Character 在提交后的 Observation 与下一 Wave PerceptionFrame 中不能继续感知旧 Scope 内容。
- [x] Director 只能在至少一轮完成且没有未解决直接回应、进行中行动或关键 Commitment 时提议 `resolved`。
- [x] 达到默认六个 Wave 时，Runtime 以 `limit_reached` 控制 Segment 关闭当前 Session、更新队列并成功结束 Batch，同时返回警告。
- [x] 全员 `no_op` 且 Session 保持开放时只写 Batch/Wave bookkeeping 与 Trace，不创建 Segment 或推进 World Version。
- [x] 全员 `no_op` 后合法 `resolved` 时，会用不含 World Event 的控制 Segment原子提交 Session/Queue 变化并推进一个 World Version。
- [x] `advance` 在队列为空时返回成功 `no_work`，且不调用模型、不创建空记录。
- [x] 确定性测试覆盖分裂后的旧 Session、全部后继、FIFO 顺序、感知隔离及下一 Batch 续跑。

## Comments

### 2026-08-31 — Implemented and verified

- Runtime 从已验证 Wave 的结束位置和直接互动证据确定性计算 split/merge；后继 Session 使用固定成员、父引用及单调 FIFO 序号，当前 Batch 只续跑排序第一条 lineage。
- World Committer 在同一事务内提交位置、Session 关闭、后继、父引用、队列、Event、Observation 与 Snapshot；分裂注入失败测试证明整体回滚。
- `resolved` 会检查既往 Wave、待回应、进行中动作与关键 Commitment；`limit_reached` 和合法全员 `no_op` 结束使用无 Event 控制 Segment，空队列不再创建 Batch。
- 感知投影按 Event 时间推进角色位置，离开 Scope 后不再收到旧 Scope 后续 Observation，下一 Wave 的 PerceptionFrame 同样隔离。
- 验收：`uv run pytest`（80 passed）、Ruff check/format、Python compileall、Alembic 单一 head `0006_session_lineages` 且空库升级成功、Node 测试（14 passed）。
