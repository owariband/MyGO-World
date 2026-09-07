# 限制模型修复和 Session 生成

> 关于“全员 `no_op`”的 lockstep 表述已由 ADR 0027 修订为单个被选中角色的
> Decision Turn；本 ADR 的重试上限与 Session 收束规则继续有效。

Director 可以在角色提案之间补充环境反应、对象结果、时间衔接和无主体桥接事件，但不能替角色作出重要选择、发言或改变动机。每次模型输出的结构或语义校验失败时，只允许一次携带明确诊断的修复；再次失败则终止当前 Generation Batch、保留完整 Generation Trace，并且不提交候选事实。

Director 可以在满足 Session 关闭前置条件时提议正常结束；Runtime 以最大 Generation Wave 数和最大模型调用数提供确定性上限，首个切片默认最多 6 个 Wave，并允许 CLI 显式覆盖。

正常达到 `max_waves` 时，Runtime 以 `limit_reached` 关闭当前 Session，Generation Batch 成功结束并返回警告；这与 Provider 请求预算耗尽或模型最终失败不同，后两者使 Batch 失败并以非零状态退出。

任一 Character Agent 在传输层重试后仍然超时或失败时，整个 Wave 和 Batch 失败且不提交，技术失败不能转换为角色的 `no_op`。当所有角色都主动返回 `no_op` 时，Director 仍可判断 Session 是否自然结束，但不能凭空创造 World Event；若未结束，本轮不提交 World Event，并继续运行到确定性上限。

全员 `no_op` 且 Session 保持开放时只持久化 Batch/Wave bookkeeping 与 Generation Trace，不创建 World Segment 或推进 World Version。若 Director 合法提议 `resolved`，或 Runtime 应用 `limit_reached`，Session/Queue 生命周期变化通过一个不含 World Event 的控制 Segment 提交并推进 World Version。
