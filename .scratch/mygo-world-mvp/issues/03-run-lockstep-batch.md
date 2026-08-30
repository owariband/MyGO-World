# 03 — 运行多 Character 的可靠 Lockstep Batch

**What to build:** 让一次 `advance` 能够在共同决策边界上并行调用多个 Character，由一个 Director 统一结算多个 Wave，并在超时、修复、取消和预算边界下给出可恢复的 Batch 结果。

**Blocked by:** 02 — 通过 Fixture 推进一个完整 Generation Wave.

Status: resolved

**Execution goal:** `../goal.md`

- [x] 同一 Generation Wave 的全部 Character 收到相同起始 World Version 和 World Time。
- [x] Character 请求可并行执行，并受默认容量为四的全局信号量约束；等待并发许可不改变输入版本或世界时间。
- [x] Director 只在本 Wave 全部 Character Proposal 通过 Proposal Validator 后运行。
- [x] Director 能为提案分配非负整数毫秒表示的开始/结束时间和因果关系，Segment Validator 会执行五分钟默认 Wave 上限及跨 Wave 行动禁令。
- [x] 提前完成的 Character 被视为隐式空闲，Director 不生成填充行为，下一 Wave 也不追写上一 Wave 空档。
- [x] `wait` 可以推进语义时间，`no_op` 不生成角色行动或独立 World Event。
- [x] 网络错误、限流和 Provider 服务端错误最多重试两次，单次请求默认 120 秒超时。
- [x] Character 或 Director 的 Schema/语义错误只允许对应调用进行一次带稳定诊断的修复。
- [x] `advance` 默认 Provider 请求预算为 40；重试和修复计入预算，耗尽时 Batch 失败且 CLI 非零退出。
- [x] 任一 Character 在传输重试后仍失败会使当前 Wave 与 Batch 失败，不会被转换为 `no_op`。
- [x] SIGINT/SIGTERM 会取消在途请求、回滚未提交 Wave、保留此前完整版本并尽可能把 Batch 标记为 `cancelled`。
- [x] 下一次启动会把遗留的 `running` Batch 标记为 `interrupted`，恢复点仍是最后一个完整 World Version。
- [x] Batch Receipt 能稳定报告 run ID、起止版本、Wave 数、状态、警告和错误码。

## Comments

### 2026-08-31 — Implemented and verified

- `advance` 现在持久化 Generation Batch/Wave，并在共同 Snapshot 屏障上并行调用全部 Session Character；默认全局并发容量为 4。
- 新增请求预算、最多两次传输重试、Character/Director 各自一次诊断修复、协作式信号取消和 stale `running → interrupted` 恢复。
- `SegmentValidator` 拒绝 `no_op` 事件、重复 Proposal 结果及越界 `wait`，全员 `no_op` 只在 `limit_reached` 时提交无 Event 控制 Segment。
- 合并态验证：`uv run pytest`（64 passed）、Ruff check/format、Alembic 单一 head `0004_generation_batches`、`WEBGAL_ROOT=/Users/yyu03/project/dev/MyGO_v3.1.1 npm test`（14 passed），并覆盖 CLI completed/失败 Receipt。
