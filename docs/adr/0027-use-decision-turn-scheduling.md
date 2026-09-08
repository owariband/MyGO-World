# MVP 使用单角色 Decision Turn 调度

> 日期：2026-09-08  
> 状态：已接受；取代 ADR 0003 与 ADR 0019 的全员 lockstep 提案语义，并修订
> ADR 0007 的角色选择表述、ADR 0014 的全员提案语义与 ADR 0015 的全员 `no_op` 语义

同一 Event Session 中的多个 Character 不再从同一个 Snapshot 同时提案。每个
Generation Wave 先由 `TurnScheduler` 授予一名 Character 一个 Decision Turn，该角色
基于当前已提交 World Version 产生至多一个 Action Proposal；非 `no_op` Proposal 经
Director 结算后原子提交，严格 `no_op` 则绕过 Director。
因此下一名角色能通过新的 PerceptionFrame 观察上一小步已提交的结果。

选择优先级固定为：`pending_response_ids` 中的被点名角色优先；没有点名时由
Director 从 Runtime 提供的其余合法参与者中选择（多人 Session 默认排除上一位已完成
Decision Turn 的角色）；Director 输出缺失、版本或 Session
不匹配、或选择集合外角色时，Scheduler 使用稳定 participant ID 环和持久游标进行
round-robin 回退。存在多个被点名者时，同样在这组角色中沿该环选择下一位。首期不
实现 continuation，也不收集 Character `TurnBid`。

`TurnScheduler` 是确定性 Runtime 模块，不是第四类 Agent。Director 只给出
`TurnSelection` 建议，不能绕过候选集合或直接触发 Character 行动。离线测试使用
`DeterministicDirectorFixture`；真实 Provider 使用独立的 `turn_selection` 调用。被选中
仍只代表获得决策机会，Character 可以返回 `no_op`。

每次选择先写入 `DecisionTurnRecord`，记录 Session、起始 World Version、候选集合、
选中角色和 `nominated | director | round_robin` 来源。成功提交时，记录状态与新 World
Version 在 World Committer 的同一事务中更新；`no_op` 和失败则分别记为 `no_op` 与
`failed`。该记录属于可审计运行状态，不是 World Ledger 事实；round-robin 从同一
Session 最新已完成（`no_op` 或 `committed`）记录的选中角色恢复游标；失败的机会不
推进游标，也不能依赖进程内变量、UUID 或数据库 `rowid`。

Generation Wave 继续作为 Proposal、非 `no_op` Proposal 的 Director 结算和 World
Commit 的原子边界，但每个 Wave 最多只有一个 Character Proposal。严格 `no_op` 绕过
Director，不产生 World Event、实体变化或 World Time 推进；未到上限时只完成运行记录，
到 `max_waves` 时由 Runtime 提交无事件的 `limit_reached` 控制 Segment。该方案牺牲同一时刻的群体联合提案，以换取
逐步可观察性，避免多人同时对同一发言作出彼此不可见的重复回应。异步长行动、显式
continuation、Turn Bid 和跨 Session 并发留待后续决策。
