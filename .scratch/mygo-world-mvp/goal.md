Goal: 完成 ticket 04 的 Event Session lineage 调度，使 Session 的关闭、分裂、合并与跨 Batch FIFO 续跑由 Runtime 确定性计算并原子持久化

Completion criteria:
- [x] Event Session 持久化稳定 `session_id`、唯一 `location_id + scope_key` 和零到多个父 Session 引用。
- [x] 已创建 Session 的参与者集合不会被原地增删；边界变化只通过关闭旧 Session 并创建后继 Session 表达。
- [x] Session 关闭时持久化且只接受 `partitioned`、`resolved` 或 `limit_reached` 关闭原因。
- [x] Location Entity Revision 与 Snapshot 保留稳定 Scope key 和显式可达边。
- [x] Character 的提交后位置始终引用现存的 `location_id + scope_key`。
- [x] Runtime 根据已验证 Wave 的候选结束位置确定性计算参与者分区，模型输出不能指定分区。
- [x] 单人分区会形成合法的后继 Session。
- [x] 角色仅处于同一 Interaction Scope 时不会自动合并 Session。
- [x] 已提交的直接互动可以关闭所有受影响的旧 Session，并创建记录全部 `parent_session_ids` 的合并后继 Session。
- [x] 一次分裂或合并提交中的 Character 位置变化、旧 Session 关闭、全部后继 Session 创建、父引用和 FIFO 队列变更在同一个 World Committer 事务中可见；任一注入失败会全部回滚。
- [x] 多个后继 Session 按各分区排序后的参与者 ID 确定性排序，并使用显式单调 `queue_order` 入队。
- [x] 当前 Generation Batch 在分裂后只继续新队首后继 lineage，其余后继保持排队状态。
- [x] 新进程中的下一次 `advance` 从持久 FIFO 队列的当前队首继续。
- [x] Character 移出原 Interaction Scope 后，不会收到移动事件之后发生在旧 Scope 的 Observation。
- [x] Character 移出原 Interaction Scope 后，下一 Wave 的 PerceptionFrame 不包含旧 Scope 中不可见的 Character、Object 或私有 Memory。
- [x] Segment Validator 对首轮即 `resolved` 返回稳定诊断，只有至少一轮已经完成的 Session 才允许正常关闭。
- [x] Segment Validator 在存在可由当前持久状态判定的未解决直接回应、进行中行动或关键 Commitment 时拒绝 `resolved`，且不引入异步角色时钟或占位状态。
- [x] 达到默认六个 Wave 时，Runtime 通过不含 World Event 的 `limit_reached` 控制提交关闭当前 Session 并将其移出队列。
- [x] `limit_reached` 控制提交恰好推进一个 World Version，成功 Batch Receipt 包含稳定警告。
- [x] 全员 `no_op` 且 Session 保持开放时只新增 Batch/Wave bookkeeping 与 Generation Trace，不新增 World Segment、World Event、Snapshot 或 World Version。
- [x] 全员 `no_op` 后合法 `resolved` 时，通过不含 World Event 的控制 Segment 原子关闭 Session、更新队列并恰好推进一个 World Version。
- [x] 队列为空时 `advance` 返回成功 `no_work` Receipt，不调用 ModelGateway，也不新增 Generation Batch、Generation Wave、Generation Trace、World Segment、Snapshot 或 World Version 记录。
- [x] 确定性 split Fixture 测试断言旧 Session 以 `partitioned` 关闭、全部后继及父引用被持久化，并验证 FIFO 顺序。
- [x] 确定性 split Fixture 测试断言提交后 Observation 与下一 Wave PerceptionFrame 均满足 Scope 隔离。
- [x] 确定性 split Fixture 测试断言当前 Batch 只续跑队首 lineage，下一 Batch 从剩余队首继续。
- [x] 确定性 merge Fixture 测试分别证明“仅共处 Scope 不合并”和“直接互动后建立多父后继 Session”。
- [x] `uv run pytest` 全部成功退出。
- [x] Ruff check 与 format check 全部成功退出。
- [x] Alembic 只有一个 head，且全新数据库可升级到该 head。
- [x] `WEBGAL_ROOT=/Users/yyu03/project/dev/MyGO_v3.1.1 npm test` 全部成功退出。

Constraints:
- 保持 ticket 01–03 的 World、Snapshot、Generation Trace、Batch Receipt、请求预算、重试/修复、取消恢复和原子提交行为；不得回退 ticket 06 的 Broadcast/Render 功能。
- World Committer 仍是 Character 位置、Session、Runnable Session Queue、Ledger、Memory、Observation、Snapshot 与 World Version 的唯一权威事务写入者。
- Proposal Validator 与 Segment Validator 保持确定性、无副作用；Session 分区、合并和 FIFO 排序不得交给 Character、Director 或 Provider。
- 一个 Generation Batch 只推进初始队首 Session 及其一个确定性后继 lineage，不实现多 Session 并发或轮转调度。
- 不使用 `focus_character_id`、UUID、模型优先级或 Broadcast 结果决定后继顺序。
- 不引入坐标、距离、寻路、异步角色时钟或跨 Wave 进行中行动；Interaction Scope 继续作为 Location 的值对象。
- 不因角色共处一个 Scope 自动创建或合并 Session；合并必须有已验证且已提交的直接互动证据。
- 新数据库结构通过 `0004_generation_batches` 之后的线性 Alembic 迁移加入，保持单一 migration head。
- 使用独立可复现 Fixture 验证 split/merge，不依赖真实 Provider 的随机输出或网络访问。
- 只修改 ticket 04 所需的 Runtime、领域契约、持久化、Fixture、CLI receipt 和测试；不实现 ticket 05 的 Skill/Memory 检索扩展或新的 Render 能力。

Context:
- `.scratch/mygo-world-mvp/spec.md`
- `.scratch/mygo-world-mvp/issues/04-schedule-event-session-lineages.md`
- `MVP.md`
- `CONTEXT.md`
- `docs/adr/0005-event-session-is-the-interaction-boundary.md`
- `docs/adr/0007-single-frontier-first-slice.md`
- `docs/adr/0015-bound-model-repair-and-session-generation.md`
- `docs/adr/0016-ledger-first-event-recognition.md`
- `docs/adr/0018-worlds-persist-across-batch-processes.md`
- `docs/adr/0019-use-lockstep-generation-waves-for-mvp.md`
- `docs/adr/0022-validate-before-atomic-world-commit.md`
- `src/mygo_world/contracts.py`
- `src/mygo_world/validators.py`
- `src/mygo_world/committer.py`
- `src/mygo_world/runtime.py`
- `src/mygo_world/perception.py`
- `tests_py/test_lockstep_batch.py`
