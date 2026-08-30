Goal: 完成 ticket 03 的多 Character Lockstep Generation Batch，并让失败、取消与进程中断都从最后一个完整 World Version 恢复

Completion criteria:
- [x] 同一 Wave 的全部 Character 从同一 World Version 与 World Time 构造 PerceptionFrame。
- [x] Character 请求并行执行，并受默认容量为四的全局信号量限制；Director 只在全部 Proposal 验证成功后运行。
- [x] Segment Validator 执行五分钟默认 Wave 上限、Wave 内时间/因果边界、Proposal 唯一结果和跨 Wave 禁令。
- [x] `wait` 的持续时间进入语义时间，`no_op` 不生成 Character 行动或独立 World Event；全员 `no_op` 的开放 Wave 不推进版本。
- [x] 网络、限流和 Provider 5xx 错误最多重试两次，Provider 单次请求默认超时为 120 秒。
- [x] Character 与 Director 的 Schema/语义错误只修复对应调用一次，并把稳定诊断带入修复请求。
- [x] `advance` 默认请求预算为 40，传输重试和语义修复均计入预算；耗尽时 Batch 失败。
- [x] 任一 Character 传输失败会使整个 Wave 失败，且不会降级为 `no_op`。
- [x] SIGINT/SIGTERM 触发协作式取消，当前未提交 Wave 不产生事实，Batch 尽可能持久化为 `cancelled`。
- [x] 新 Batch 启动时把遗留 `running` Batch/Wave 标记为 `interrupted`，恢复点保持在最后完整 World Version。
- [x] Batch Receipt 稳定包含 run ID、起止版本、Wave 数、状态、警告、错误码和请求计数。
- [x] 已提交 Wave 在后续 Wave 失败时保留；当前失败 Wave 不产生部分 World Version。
- [x] `uv run pytest` 与现有 Node 测试成功退出。

Constraints:
- Character Agent 仍只能提出自己的单个原子 Action Proposal；Director 不获得事实写入权限。
- 同一 World 的 `advance` 仍持有 mutation lock，World Committer 仍是权威世界状态的唯一写入者。
- 不实现异步角色时钟、多 Session 并发、Player Event Request 或 Broadcast/Render。
- 默认 Fixture 不发起网络请求，并保留 ticket 01/02 与现有 Node 工具行为。

Context:
- `.scratch/mygo-world-mvp/spec.md`
- `.scratch/mygo-world-mvp/issues/03-run-lockstep-batch.md`
- `MVP.md`
- `CONTEXT.md`
- `docs/adr/0003-concurrent-character-proposals.md`
- `docs/adr/0013-world-time-is-semantic-time.md`
- `docs/adr/0015-bound-model-repair-and-session-generation.md`
- `docs/adr/0018-worlds-persist-across-batch-processes.md`
- `docs/adr/0019-use-lockstep-generation-waves-for-mvp.md`
- `docs/adr/0021-use-an-explicit-model-gateway.md`
- `docs/adr/0022-validate-before-atomic-world-commit.md`
