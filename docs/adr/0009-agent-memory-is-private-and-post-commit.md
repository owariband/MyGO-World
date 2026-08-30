# Agent Memory 按身份隔离并在世界提交点写入

所有 Agent Memory 使用同一个 SQLite 数据库和统一表结构，但每次读写必须由 `agent_id + namespace` 限定，角色之间不能直接读取彼此的私有记忆。Character Agent 只能提出 Memory 候选；PerceptionProjector 根据事务中已经通过校验、即将提交的 World Event 形成对应 Observation，Observation 与被接受的 Belief/Commitment changes 只有在同一个 World 提交点成功后才持久化，未提交或失败的提案只进入 Generation Trace。

MVP 不额外调用模型决定角色是否“注意到”事件。PerceptionProjector 根据事件发生时间、角色位置、可见范围、感知方式和字段权限，将所有符合条件的已提交事件确定性投影为 Observation；角色即使提前完成本 Wave 的行动，也能观察之后仍在其感知范围内发生的事件，但只能在下一 Wave 响应。

实现上，Recognizer 在 World 提交事务中形成 Event 后，PerceptionProjector 立即根据这些已通过校验、即将提交的 Event 生成 Observation；被接受的 Belief/Commitment changes、Observation、Ledger 和 Snapshot 在同一提交点对外可见。投影或 Memory 写入失败会回滚当前 Wave，保证不存在 Agent 看到未提交 Event、已提交 Event 缺少应有 Observation，或世界与角色主观状态断裂的状态。

首期启用 `observation`、`belief` 和 `commitment`，保留 `reflection` Schema 但不自动生成。Scenario Seed 提供多条带类型、实体/地点标签、相对时间、重要度和来源的原子初始记忆；Character Skill 只保存高度概括的稳定背景，摘要不能替换原始记录。

Character 的正常结构化输出可以在唯一 ActionProposal 之外附带属于自己的 `belief_changes` 和 `commitment_changes`；它们不是世界行动，只有当前 Wave 成功提交后才与 Observation 一起写入。Agent Memory 本身 append-only，新 Belief 通过 `supersedes_memory_id` 取代旧认知，Commitment 通过新状态记录完成或取消，不原地改写历史内容。

检索时始终注入活跃 commitment，再按当前角色、namespace、相关实体、标签、时间和重要度从 SQLite 选择记录；首期不引入向量数据库，也不把全部记忆塞入 Prompt。
