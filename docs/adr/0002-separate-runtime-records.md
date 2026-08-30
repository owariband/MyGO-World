# 分离世界事实、Agent 记忆、生成轨迹与演出产物

MVP 分开持久化作为唯一世界权威的 World Ledger、可由其重建的 Snapshot、Agent 私有 Memory、用于诊断的 Generation Trace，以及下游 Broadcast/Render 产物。它们不合并成通用状态记录，避免模型记忆、调试数据或演出结果反向成为世界事实，并支持独立重放和故障定位。

每个 World Version 持久化一份经过 Pydantic 校验的 Snapshot JSON 和校验和，以便并行 Agent 精确绑定同一数据切片；Snapshot 可以删除并从 Ledger 重建，不能覆盖或修正 Ledger。SQLite 使用混合 Schema：参与身份、外键、查询、排序、唯一性或世界不变量的字段使用关系型列，可扩展的叙事状态和类型化 payload 使用带 Schema 版本的 JSON；角色位置和 Session 成员关系不得隐藏在 JSON 中。

每个 World 只使用一个 `world.sqlite3`，Ledger、Snapshot、Agent Memory、Generation Trace、Batch 和 Render 通过独立表组保持逻辑边界，不拆成多个数据库文件，也不与其他 World 共用数据库。
