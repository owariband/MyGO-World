# Runtime 派生 Generation Wave 的事件顺序、因果与位置

Director Resolution 只保留相对 Wave 时长、结果摘要、External Event 的内容与 Scope、
无位置字段的 Entity State Patch 和 Session intent。Segment Assembler 将 External Event
确定性放在 Wave 末端，并自动把本轮 Proposal Event 记录为直接原因和来源证据；角色
位置只从该角色已接受的 `move` Proposal 派生。这样排序、引用与位置所有权成为
Assembler 深模块的实现细节，不再要求模型同时满足 offset 跨字段约束、严格时间因果和
“字段存在但无权使用”的隐含规则。

## Considered Options

保留 Director 的 offset、局部引用与通用 Entity 位置字段，再依靠 Skill、Pydantic
跨字段校验和一次 repair，能表达更细的 Wave 内并发时间线；真实 Provider Trace 却反复
产生越界时间、与覆盖整个 Wave 的 Proposal Event 重叠的原因，以及角色越权移动。
MVP 选择 Wave 末端点事件这一较窄语义；需要 Wave 内多阶段或真正独立环境时间线时，
应新增显式领域模型，而不是把底层 Segment 字段重新暴露给 Director。

## Consequences

同一 Resolution 的多个 External Event 具有相同的 Wave 末端时间，数组顺序只用于稳定
存储，不声称彼此存在因果。`no_op` 继续表示本次 Decision Turn 没有行动且不产生世界
变化；“角色不行动、环境仍推进”若成为需求，应建模为 `wait` 或独立环境输入，而不是由
Director 在严格 `no_op` 上补写结果。

Runtime 因此在严格 `no_op` 上完全绕过 Director Resolution：未到批次上限时只完成
Decision Turn、Wave 与 Trace 记录；到 `max_waves` 时才由 Runtime 确定性构造一个无事件、
零时长的 `limit_reached` 控制 Segment 来关闭 Session。
