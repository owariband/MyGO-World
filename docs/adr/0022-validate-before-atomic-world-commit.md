# 模型候选必须通过两阶段校验后原子提交

MVP 将模型输出视为不可信候选：Pydantic 只校验结构，Proposal Validator 根据对应 PerceptionFrame 检查单个 Character 提案的版本、所有权、可见性、可达性和单行动约束，Segment Validator 再根据共同 Snapshot、原始提案与 Session 规则检查 Director 补全的意图保真、状态迁移、资源冲突、语义时间、因果和分区。两者都是无副作用的确定性门禁，只返回有效结果或带稳定代码与字段路径的诊断；它们不调用模型、不修补事实，也不写数据库。

Generation Wave 只有 Segment Validator 产出的 Validated Commit Plan 可以进入 World Committer；初始化则只接受由完整校验后的 Scenario Seed 构造的 Genesis Commit Plan，不为 Genesis 虚构 Segment Validator。对于 Generation Wave，Committer 在一个 SQLite 事务中暂存 World Segment、Entity Revision 和 Session/Queue 变化，运行 Event Recognizer 与 PerceptionProjector，写入 World Event、Observation、被接受的 Belief/Commitment changes、Snapshot 和新 World Version 后整体提交；Genesis 使用同一事务写入者，但不创建普通 World Event 或 Observation。任何一步失败都会回滚。Proposal 诊断只允许对应 Character 修复一次，SegmentDraft 诊断只允许 Director 修复一次，再次失败则终止 Batch，Runtime 不得静默改写模型意图。

MVP 不实现通用规则引擎或可动态配置的 Validator DSL；两个窄校验 seam 足以让 Fixture 和真实 Provider 走同一套世界不变量，同时避免把权限与一致性规则藏进 Prompt、Director 或数据库异常中。
