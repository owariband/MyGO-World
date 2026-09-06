# 首个切片只推进一个 Session frontier

> Runnable Session Queue 的物理持久化部分由 ADR 0025 取代；单 frontier 与确定性 FIFO 规则保持不变。

World 持久化一个按显式单调序号排序的 Runnable Session Queue；`advance` 取队首 Session，并在当前 Generation Batch 中只推进这一条 lineage。Session 分裂后，Runtime 为所有分区创建后继 Session，按排序后的参与者 ID 确定性入队，当前 Batch 继续其中的队首后继，其余保留给之后的 Batch；所选 lineage 正常结束时当前 Batch 也结束。

MVP 不使用 `focus_character_id`、Director 选择或 UUID 排序来制造调度优先级，也不在多个 Session 之间逐 Wave 轮转。该限制保证公平、可重放的串行调度；多 Session 并行推进属于后续里程碑，不能由 Broadcast 是否展示某个 Session 来代替。

确定性分裂 Fixture 必须覆盖角色离开 Interaction Scope、旧 Session 以 `partitioned` 关闭、所有后继 Session 确定性入队、离场后的感知隔离，以及下一 Generation Batch 从队首继续；真实模型主演示不依赖随机触发分裂。
