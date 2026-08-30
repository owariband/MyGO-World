# Scenario Seed 只负责初始化 World

Scenario Seed 可以自包含首批角色、地点、对象、记忆、事实和 Event Session，但它只是版本控制中的初始化输入，不是运行期实体仓库。`init` 在单个事务中校验并物化 Seed；此后 SQLite 中的 World Ledger、Entity Revision、Event Session 和 Agent Memory 是唯一运行权威，修改 Seed 只影响之后创建的新 World。

该边界避免运行时反复读取 YAML 导致已存在世界被仓库改动隐式改写。World 保存 Seed 的稳定 ID、声明版本和内容哈希用于来源追踪，但状态重建始终以已物化的 Ledger 为准。

`init` 先由 WorldInitializer 校验 Seed、迁移临时数据库并构造 GenesisCommitPlan，再由与 Generation Wave 共用的 World Committer 在单个事务中创建 Genesis Segment、World Version 1、初始 Entity Revision、Event Session 和原子 Agent Memory；Genesis Segment 不生成虚构的普通 World Event。World Committer 使用可注入 Clock 与 ID Generator 生成领域时间和标识，临时数据库命名与原子文件发布仍属于 WorldInitializer。目标 `world_id` 已存在时命令直接失败，既不覆盖、合并，也不把旧世界误报为本次初始化成功。
