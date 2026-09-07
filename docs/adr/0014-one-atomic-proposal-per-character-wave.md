# 每名角色每轮只提出一个原子行动

> “每名角色都在同一 Wave 提案”的语义已由 ADR 0027 修订为：每个 Wave 只向一名
> 获得 Decision Turn 的 Character 征集提案。本 ADR 的单个提案原子性与行动边界继续有效。

每个 Generation Wave 中，每名 Character Agent 必须返回一个且仅一个 `utterance`、`move`、`interact`、`wait` 或 `no_op` Action Proposal。台词可携带不改变世界状态的表情和表演提示，但不能借此隐藏第二个世界行动；这个限制使并行提案的冲突检测和原子提交保持可判定。

Proposal 只携带一句简短 `intent_summary`，不请求或保存模型思维链。`wait` 表示角色主动等待并由 Director 分配语义时长，可以推进 World Time；`no_op` 表示本轮没有角色行动，不单独产生 World Event。`utterance` 显式携带零到多个 `addressee_ids`，为空时表示对当前 Session 公开发言。

`move` 只能选择 PerceptionFrame 中的可达 `destination_location_id`。`interact` 若引用既有对象或角色，其 `target_entity_id` 必须来自当前可见实体，但具体交互意图使用自然语言表达；Director 解析客观结果，Proposal Validator 先校验单个提案的输入权限，Segment Validator 再只允许合法的类型化状态变更，不能用自然语言绕过可见范围或实体所有权。

MVP 允许 ActionProposal 在自然语言中引用普通、非持久的 Incidental Prop。Director 可以把它作为带局部描述的值对象写入当前 World Event payload，但不得为它创建 Entity Revision、跨 Wave 暴露为可交互目标，或借此引入人物、地点、重要剧情物品和未登记美术资源。

每个 Incidental Prop 保存 Event 内唯一 `prop_key`、受限类型和自然语言描述。MVP 不实现升格；未来若将其变为持久 Entity，必须创建新的 Entity 与首个 Entity Revision，并通过 `origin_event_id + origin_prop_key` 引用原始出现位置，不能改写旧 Event。

全部角色提案必须基于相同 World Version。提案冲突时保留每个角色的意图，由 Director 补全各自的客观结果，Segment Validator 再检查资源占用和世界不变量；一次修复后仍不合法则整轮不提交，不能按固定角色优先级静默删除提案。
