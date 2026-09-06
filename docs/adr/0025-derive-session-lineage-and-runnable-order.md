# 从 Ledger 与 Event Session 派生 lineage 和可运行顺序

MVP 不再用 `event_session_parents` 和 `runnable_session_queue` 保存重复投影：后继 Session 的父关系以不可变 World Segment 为权威，Event Session 自身保存一次分配且不可变的 `queue_order`，Runnable Session Queue 则由 `status=runnable` 的 Session 按该序号排序得到。这一决策减少事务与迁移表面积，同时保留 Snapshot 和 Canonical Export 的兼容视图、跨进程确定性，以及 ADR 0005 的固定成员/lineage 规则和 ADR 0007 的串行单 frontier 规则；只有出现独立 lineage 查询或多 worker claim/lease 需求时才重新引入专用投影。
