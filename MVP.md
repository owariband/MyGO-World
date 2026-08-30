# MVP 决策

> 状态：当前 MVP 实施基线  
> 更新：2026-08-25

若旧文档仍保留 Go + Eino 的技术栈描述，MVP 实现以本页为准；既有 World / Event / Agent 权限边界不因此改变。

## 技术栈

- **开发语言：**Python，便于接入模型、评测和 Agent 生态。
- **数据契约：**Pydantic v2，用于定义和校验结构化领域模型。
- **Agent 框架：**PydanticAI，只负责模型调用，不接管 World / Event Runtime。
- **持久化存储：**SQLite，适合 MVP 的单 Runtime 与串行提交，也便于本地重放。
- **数据库访问：**SQLAlchemy 2.x + Alembic，负责数据访问和 Schema 迁移。
- **测试与评测：**pytest + Pydantic Evals，覆盖确定性测试与模型效果评估。
- **可观测性：**OpenTelemetry，统一记录模型调用和运行链路 Trace。

## 范围与非目标

- **首个里程碑：**完成一条 Fixture 驱动、零模型请求的 Vertical Slice。
- **模型接入：**Fixture 链路可重放后再接入真实模型。
- **一期非目标：**不追求完整剧情质量、分布式部署或通用 Agent 平台。

## 所有权与权限

- **世界事实：**World Runtime 是唯一权威。
- **Agent 权限：**Character、Director 和 Broadcast 只产生 Proposal 或 Plan。
- **持久副作用：**只有 World Committer 和 Render Gateway 可以执行。
- **播放器边界：**WebGAL 只负责展示，不拥有或推进世界状态。

## 运行语义

- **生成链路：**Character 提案，Director 补完，Validator/Committer 提交，Broadcast 投影展示。

  ```text
  World Snapshot
  → PerceptionProjector
  → PerceptionFrame
  → Character / ActionProposal
  → Director / SegmentDraft
  → Temporal Binder
  → Validator
  → Committer
  → World Ledger / WorldSegment
  → Event Recognizer / WorldEvent
  → Broadcast / BroadcastPlan
  → Render Planner / RenderJob
  → Render Gateway
  → WebGAL
  ```

- **世界时间：**模型耗时只作为约束和证据，生成返回后再绑定叙事时间。
- **并发规则：**同一 Event 串行推进，不同 Event 可并行生成，世界提交保持串行。
- **展示规则：**有 Ready Render 就播放，否则保持黑屏。

## 数据与重放

- **事实记录：**World Ledger append-only，是世界事实的唯一重放源。
- **状态快照：**Snapshot 是可重建缓存，不替代 Ledger。
- **数据隔离：**World Ledger、Agent Memory 和生成 Trace 分开存储。
- **框架状态：**Agent checkpoint 只恢复生成流程，不代表世界重放。

## 质量与验收

- **确定性：**相同 Fixture、时钟、ID 和随机种子应产生相同 Golden Trace。
- **边界测试：**覆盖权限隔离、非法输出、并发冲突和提交原子性。
- **故障测试：**覆盖超时、取消、重试和 Render 失败恢复。
- **模型评测：**真实模型接入后以固定 Eval 数据集比较质量、延迟和成本。

## 风险与退出条件

- **Agent 框架：**若 PydanticAI 产生过多适配成本，则保留领域模型并退回自有 Agent Protocol。
- **数据库：**出现多实例并发写入、持续锁竞争或远程数据库需求时，再迁移 PostgreSQL。
