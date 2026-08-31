# 08 — 交付可重复的本地 Fixture MVP Demo

**What to build:** 提供一个无需网络或外部 MyGO 安装即可运行的完整本地演示，从新 Scenario Seed 初始化 World、推进多 Character Session、处理分裂并增量渲染，且在重复运行时产生完全一致的规范结果。

**Blocked by:** 04 — 跨 Batch 调度 EventSession lineage; 05 — 让私有 Memory 与 Runtime Skill 跨 Batch 生效; 07 — 实现增量编排与可恢复 Render 发布.

Status: resolved

**Execution goal:** `../goals/08-deliver-deterministic-fixture-demo.md`

- [x] `demo` 只针对不存在的新 World 串联初始化、推进和渲染，并返回包含 World、Batch、版本和产物信息的稳定 Receipt。
- [x] 相同 World ID 已存在时 `demo` 安全失败，不续跑、不删除也不覆盖已有 World。
- [x] Fixture 使用版本化目录组织；场景和调用清单使用 YAML，结构化 Agent 响应使用 JSON。
- [x] Fixture 响应通过 Agent type、Agent ID、Batch/Wave 和 call kind 定位，并断言规范化输入 hash。
- [x] 测试注入固定时钟、UUID 生成器、随机种子和所有 Character/Director/Broadcast 响应。
- [x] 主 Fixture 从空目录完整执行 CLI `init → advance → render`，不绕过任何生产 Validator、Repository、Committer、Recognizer、Projector、Planner、Compiler 或 Gateway。
- [x] 两次独立运行产生完全相同的规范 Ledger、Snapshot、Memory、Broadcast Plan 和 WebGAL 脚本文本。
- [x] 两次独立运行产生完全相同的 Snapshot checksum 和脚本内容 hash。
- [x] Golden 比较基于规范领域导出而不是 SQLite 原始文件字节。
- [x] 分裂 Fixture 验证旧 Session 关闭、所有后继确定性入队、感知隔离及下一 Batch 从队首继续。
- [x] 故障 Fixture 验证 Validator 最终失败、事务回滚、请求预算耗尽、取消恢复和 Render 发布恢复。
- [x] 默认 pytest 不读取 Provider 凭据、不访问网络，也不依赖外部 MyGO 资源目录。
- [x] 完整 Python 测试套件通过。
- [x] 现有 Node 测试套件通过。

## Comments

- 已实现版本化离线 Fixture、严格的 Batch/Wave/调用类型响应定位与规范化输入 hash 校验、`demo` CLI、确定性依赖注入及 canonical domain export。
- 主 Fixture 通过生产 `init → advance → render → advance → render` 入口覆盖多 Character Wave、Session 分裂/FIFO、Scope 隔离、跨 Batch 私有 Memory、Runtime Skill 请求正文和增量 Render frontier。
- 两个独立空目录的 canonical export 与 WebGAL 脚本逐字节一致；验证通过：`uv run pytest`（94 passed）、Ruff check/format、Python compileall、Alembic 单 head/空库升级，以及指定 `WEBGAL_ROOT` 的 14 项 Node 测试。
- ticket 05 已为 `resolved`，ticket 07 当前仍为 `claimed`；依照 execution goal，ticket 08 保持 `claimed`，待 07 明确完成后才能标记 `resolved`。
- 依赖复核完成：ticket 05 与 ticket 07 均已明确标记为 `resolved`；ticket 08 的实现与验收条件全部完成，现已标记为 `resolved`。
