# 同一 Generation Wave 使用共同 Snapshot 并行提案

> 状态：已由 ADR 0027 取代。

每个 Event Session 的一轮 Generation Wave 中，所有参与的 Character Agent 读取同一个已提交 World Snapshot 并行产生 Action Proposal，Director 在全部结果返回后统一补全时间与因果关系，最终由 Runtime 原子提交。该设计避免角色调用顺序暗中改变世界结果，并为确定性 Fixture、并行模型调用和重放提供稳定边界。

本决策取代旧 Wiki 中 D-030 的“同一 Event 内角色串行并逐步提交”规则；旧文档只保留为历史设计背景，不再作为 MVP 实施依据。
