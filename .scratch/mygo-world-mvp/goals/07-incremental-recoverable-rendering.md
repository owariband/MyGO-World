Goal: 完成 ticket 07，审计、补强并验证跨 Batch 与 Event Session 的增量 Broadcast 编排和可恢复 Render 发布

Completion criteria:
- [ ] `render` 在调用开始时固定目标 World Version，并只把不晚于该版本且没有 Broadcast Disposition 的 Event 纳入 frontier。
- [ ] frontier 能同时包含来自不同 Generation Batch 的未处理 Event。
- [ ] frontier 能同时包含来自不同 Event Session lineage 的未处理 Event。
- [ ] 每个 frontier Event 恰好持久化一个不可变的 `included` 或 `omitted` Disposition。
- [ ] 每个 `included` Event 至少被一个 Beat 的 provenance 引用。
- [ ] 每个 `omitted` Event 都持久化非空且稳定的省略原因。
- [ ] RenderPlanner 确定性拒绝缺少 Disposition、重复 Disposition 或引用 frontier 之外新事实的 Broadcast Plan。
- [ ] 早于 frontier 的 Event 只作为模型上下文或带显式 provenance 的回顾来源使用。
- [ ] 只有旧 Event 且没有新 frontier Event 时，`render` 返回 `no_work`。
- [ ] 一次 Broadcast Run 可以生成多个有稳定顺序的 Render。
- [ ] 每个 Render 都显式建立背景、出场角色和必要 BGM，不依赖前一个 Render 的舞台状态。
- [ ] 每个 Render 不超过 40 个 Beat。
- [ ] 每个 Render 的预计时长不超过 480000 毫秒。
- [ ] `no_work` 路径不调用 ModelGateway。
- [ ] `no_work` 路径不新增 Generation Trace、Broadcast Run、Render 或 Broadcast Disposition。
- [ ] `no_work` 路径不创建或修改发布文件。
- [ ] Render 在固定目标版本上执行时，后续 `advance` 产生的新 Event 不进入本次 frontier，且保持未处理状态。
- [ ] 同一 World 的两个并发 `render` 调用由 Render 锁串行化，测试证明同一个 frontier Event 不会被重复消费。
- [ ] Render Gateway 在目标文件系统内先写临时文件，再原子发布到由 World、Render ID 和内容 SHA-256 确定的不可变路径。
- [ ] 同一次 Broadcast Run 的全部文件发布成功后，才在一个短事务中记录 Render 元数据与 Broadcast Disposition。
- [ ] 文件发布后、数据库提交前中断时，重试会验证并复用内容 hash 相同的已有文件，再补齐数据库记录。
- [ ] 不可变目标已存在但内容 hash 不一致时，重试安全失败且不写入 Render 元数据或 Broadcast Disposition。
- [ ] Planner 校验失败后，原 frontier 保持可重试。
- [ ] Compiler 失败后，原 frontier 保持可重试。
- [ ] 任一文件发布失败后，原 frontier 保持可重试，且数据库中没有该次 Run 的部分 Render 元数据或 Disposition。
- [ ] 数据库唯一约束或不可变触发器阻止同一 Event 被多个成功 Run 重复消费，并阻止已记录的 Render/Disposition 被原地修改或删除。
- [ ] `uv run pytest` 全部成功退出。
- [ ] `uv run ruff check .` 全部成功退出。
- [ ] `uv run ruff format --check .` 全部成功退出。
- [ ] `uv run python -m compileall src tests_py` 全部成功退出。
- [ ] Alembic 只有一个 head，且空数据库可以升级到该 head。
- [ ] `WEBGAL_ROOT=/Users/yyu03/project/dev/MyGO_v3.1.1 npm test` 全部成功退出。

Constraints:
- ticket 06 已实现固定目标版本、全历史上下文、Render 锁、Planner/Compiler、不可变 hash 路径、文件先于数据库发布、`no_work` 与部分恢复测试；先审计现状，只补齐 ticket 07 未满足或证据不足的行为，不重写已验证链路。
- 保持 ticket 01–06 的 World、Ledger、Snapshot、Generation Trace、Session lineage、Memory、Runtime Skill、Batch Receipt、预算、重试、取消恢复和原子提交语义。
- 07 的并行写入边界是 `src/mygo_world/broadcasting.py`、`src/mygo_world/rendering.py`、`src/mygo_world/contracts.py` 中仅 Broadcast/Render 相关类型、Render 持久化模型与迁移，以及 `tests_py/test_rendering.py`。
- 不修改 `src/mygo_world/cli.py`、`src/mygo_world/gateways.py`、demo/fixture/canonical-export 模块或 `tests_py/test_fixture_demo.py`；这些文件由并行 ticket 08 拥有。
- 不实现 demo、版本化 Fixture 查找、规范领域导出或 golden fixture。
- Broadcast 只能选择已提交 Event 与 Asset Manifest 白名单素材，不能新增世界事实、泄露事件发生时不可知信息或制造虚假因果。
- 不覆盖 WebGAL 入口场景，不创建可变 `latest` 别名，不自动启动 WebGAL 播放器。
- 新数据库结构必须通过 `0006_session_lineages` 之后的线性 Alembic 迁移加入并保持单一 migration head。
- 默认测试不读取 Provider 凭据、不访问网络，也不依赖外部 MyGO 资源目录。

Context:
- `.scratch/mygo-world-mvp/spec.md`
- `.scratch/mygo-world-mvp/issues/07-incremental-recoverable-rendering.md`
- `MVP.md`
- `CONTEXT.md`
- `docs/adr/0006-broadcast-controls-presentation-order.md`
- `docs/adr/0008-batch-broadcast-with-global-agents.md`
- `docs/adr/0017-renders-are-segmented-and-traceable.md`
- `docs/adr/0018-worlds-persist-across-batch-processes.md`
- `src/mygo_world/broadcasting.py`
- `src/mygo_world/rendering.py`
- `src/mygo_world/contracts.py`
- `src/mygo_world/db/models.py`
- `src/mygo_world/db/migrations/versions/`
- `tests_py/test_rendering.py`
