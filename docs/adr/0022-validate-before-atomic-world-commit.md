# 模型候选必须按权限组装并通过两阶段校验后原子提交

MVP 将模型输出视为不可信候选：Pydantic 只校验结构，Proposal Validator 根据对应 PerceptionFrame 检查单个 Character 提案的版本、所有权、可见性、可达性和单行动约束。Director 随后只返回 Director Resolution，不再返回它无权决定的 World Version、Session、绝对时间、Proposal Event、来源和角色 payload。

Runtime 把共同 Snapshot、当前 Session、已接受 Action Proposal、Director trace 身份和 Resolution 交给纯确定性的 Segment Assembler。Assembler 按字段所有权构造完整 Segment Draft：它注入权威上下文、复制角色动作、生成事件键与来源、解析局部因果引用，并为 `move` 生成位置变化。这是从权威输入进行构造，不是先让模型返回完整 Draft 后再静默覆盖错误字段。Segment Validator 再依据 Snapshot、原始 Proposal 和 Session 规则检查意图保真、状态迁移、资源冲突、语义时间、因果和分区。Proposal Validator、Segment Assembler 与 Segment Validator 都无副作用，不调用模型也不写数据库。

Generation Wave 只有 Segment Validator 产出的 Validated Commit Plan 可以进入 World Committer；初始化则只接受由完整校验后的 Scenario Seed 构造的 Genesis Commit Plan，不为 Genesis 虚构 Segment Validator。对于 Generation Wave，Committer 在一个 SQLite 事务中暂存 World Segment、Entity Revision 和 Session/Queue 变化，运行 Event Recognizer 与 PerceptionProjector，写入 World Event、Observation、被接受的 Belief/Commitment changes、Snapshot 和新 World Version 后整体提交；Genesis 使用同一事务写入者，但不创建普通 World Event 或 Observation。任何一步失败都会回滚。

修复同样遵守字段所有权：Proposal 诊断只允许对应 Character 修复一次；Director 自有的 Resolution schema、相对时长、结果、External Event、Entity change 或 Session intent 诊断只允许 Director 修复一次，且请求携带上一版原始/结构化输出与完整诊断。Assembler 权威上下文不变量失败属于 Runtime 缺陷，不向 Director 请求修复；再次出现可修复错误则终止 Batch。Generation Trace 保存模型实际返回的 Director Resolution 与诊断，完整 Draft 只存在于组装、校验与已提交 World Segment 链路中。

MVP 不实现通用规则引擎或可动态配置的 Validator DSL；这些窄 seam 足以让 Fixture 和真实 Provider 走同一套世界不变量，同时避免把权限与一致性规则藏进 Prompt、Director 或数据库异常中。
