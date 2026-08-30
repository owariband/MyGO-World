# 先形成类型化 Segment，再识别 World Event

MVP 使用 append-only World Ledger 保存原子 World Segment、Entity Revision 和 World Event，Snapshot 仅作为可由 Ledger 重建的缓存。Character Action Proposal 与 World Event 不一一对应：整轮提案和 Director 补全先形成通过校验的待提交 World Segment 与 Entity Revision，随后由确定性 Event Recognizer 从这些候选结果中识别零到多个 World Event；Recognizer 不得反向修改候选事实。

这里的“随后”是同一数据库事务中的逻辑顺序：Segment、Entity Revision 和 Session/Queue 变化先进入待提交状态，Recognizer 基于它们生成 World Event，PerceptionProjector 再形成 Observation；被接受的 Belief/Commitment changes、Snapshot 与新 World Version 一并成功后才提交事务。任何一步失败都会整体回滚，数据库中不得出现已有 Segment 却缺少其 Event、Observation、Memory 或 Snapshot 的半完成版本。

World Segment、Entity Revision 和 World Event 的 Repository 只暴露追加与读取操作，并由 SQLite Trigger 拒绝 UPDATE 和 DELETE。可重建 Snapshot 与运行中的 Batch 状态不属于不可变 Ledger，可以由受控流程更新或重建。
