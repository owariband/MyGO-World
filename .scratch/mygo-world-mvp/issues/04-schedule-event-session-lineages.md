# 04 — 跨 Batch 调度 EventSession lineage

**What to build:** 让 World 能够根据 Character 的互动与移动确定性地关闭、分裂或合并 Event Session，并让后续 `advance` 按持久 FIFO 队列跨进程继续正确 lineage。

**Blocked by:** 03 — 运行多 Character 的可靠 Lockstep Batch.

Status: ready-for-agent

- [ ] Event Session 具有稳定身份、固定参与者集合、一个 Interaction Scope 和可追踪的关闭原因。
- [ ] Location 作为持久 Entity 保存稳定 Scope key 与显式可达边，Character 位置由 Location 与 Scope 共同确定。
- [ ] 角色仅共享 Scope 不会自动加入同一 Session；发生直接互动时可以创建带父引用的合并后继 Session。
- [ ] Runtime 根据 Wave 候选结束位置计算分区，不让模型决定分区或队列顺序。
- [ ] 角色位置、旧 Session 的 `partitioned` 关闭、所有后继 Session 创建和 FIFO 入队在同一个 World 提交事务中完成。
- [ ] 分裂后所有后继按参与者 ID 的确定顺序入队，当前 Batch 只继续队首后继，其余留给之后的 Batch。
- [ ] 移出 Scope 的 Character 在提交后的 Observation 与下一 Wave PerceptionFrame 中不能继续感知旧 Scope 内容。
- [ ] Director 只能在至少一轮完成且没有未解决直接回应、进行中行动或关键 Commitment 时提议 `resolved`。
- [ ] 达到默认六个 Wave 时，Runtime 以 `limit_reached` 控制 Segment 关闭当前 Session、更新队列并成功结束 Batch，同时返回警告。
- [ ] 全员 `no_op` 且 Session 保持开放时只写 Batch/Wave bookkeeping 与 Trace，不创建 Segment 或推进 World Version。
- [ ] 全员 `no_op` 后合法 `resolved` 时，会用不含 World Event 的控制 Segment原子提交 Session/Queue 变化并推进一个 World Version。
- [ ] `advance` 在队列为空时返回成功 `no_work`，且不调用模型、不创建空记录。
- [ ] 确定性测试覆盖分裂后的旧 Session、全部后继、FIFO 顺序、感知隔离及下一 Batch 续跑。
