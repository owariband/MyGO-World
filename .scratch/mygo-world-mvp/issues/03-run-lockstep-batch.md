# 03 — 运行多 Character 的可靠 Lockstep Batch

**What to build:** 让一次 `advance` 能够在共同决策边界上并行调用多个 Character，由一个 Director 统一结算多个 Wave，并在超时、修复、取消和预算边界下给出可恢复的 Batch 结果。

**Blocked by:** 02 — 通过 Fixture 推进一个完整 Generation Wave.

Status: ready-for-agent

- [ ] 同一 Generation Wave 的全部 Character 收到相同起始 World Version 和 World Time。
- [ ] Character 请求可并行执行，并受默认容量为四的全局信号量约束；等待并发许可不改变输入版本或世界时间。
- [ ] Director 只在本 Wave 全部 Character Proposal 通过 Proposal Validator 后运行。
- [ ] Director 能为提案分配非负整数毫秒表示的开始/结束时间和因果关系，Segment Validator 会执行五分钟默认 Wave 上限及跨 Wave 行动禁令。
- [ ] 提前完成的 Character 被视为隐式空闲，Director 不生成填充行为，下一 Wave 也不追写上一 Wave 空档。
- [ ] `wait` 可以推进语义时间，`no_op` 不生成角色行动或独立 World Event。
- [ ] 网络错误、限流和 Provider 服务端错误最多重试两次，单次请求默认 120 秒超时。
- [ ] Character 或 Director 的 Schema/语义错误只允许对应调用进行一次带稳定诊断的修复。
- [ ] `advance` 默认 Provider 请求预算为 40；重试和修复计入预算，耗尽时 Batch 失败且 CLI 非零退出。
- [ ] 任一 Character 在传输重试后仍失败会使当前 Wave 与 Batch 失败，不会被转换为 `no_op`。
- [ ] SIGINT/SIGTERM 会取消在途请求、回滚未提交 Wave、保留此前完整版本并尽可能把 Batch 标记为 `cancelled`。
- [ ] 下一次启动会把遗留的 `running` Batch 标记为 `interrupted`，恢复点仍是最后一个完整 World Version。
- [ ] Batch Receipt 能稳定报告 run ID、起止版本、Wave 数、状态、警告和错误码。
