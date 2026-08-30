# 01 — 初始化并重新打开持久 World

**What to build:** 提供可直接运行的 Python CLI 基础，使操作者能够从版本化 Scenario Seed 初始化一个持久 World、查看稳定 Receipt，并在新进程中重新打开相同 World；同时保留现有 Node 创作工具的行为。

**Blocked by:** None — can start immediately.

Status: resolved

- [x] Python 3.13 项目可以通过 `uv` 创建环境、安装锁定依赖并运行 CLI 与 pytest。
- [x] `init` 能校验一个最小 Scenario Seed，并在单个事务中创建命名 World、Genesis Segment、World Version 1、初始 Entity Revision、初始 Event Session、Runnable Session Queue 和 Snapshot checksum。
- [x] WorldInitializer 只负责临时数据库生命周期与原子发布，Genesis 权威记录由 World Committer 根据 Genesis Commit Plan 写入。
- [x] World Committer 的领域时间和 ID 生成器可注入，Fixture 能断言固定 Genesis Segment、Entity Revision ID 与时间戳；临时文件名不进入领域结果。
- [x] Genesis Segment 不生成普通剧情 World Event。
- [x] 已存在的 `world_id` 再次初始化时返回稳定错误与非零退出码，且原数据库内容不变。
- [x] 新进程能够从该 World 的独立 SQLite 数据库读取相同的当前版本和 Snapshot。
- [x] Scenario Seed 修改不会改变已经初始化的 World，World 只保留 Seed 身份、声明版本和内容 hash。
- [x] CLI 同时提供简洁人类输出和可机器校验的 `--json` Receipt。
- [x] Alembic 可以创建最新数据库 Schema；非初始化命令遇到旧 Schema 时拒绝继续。
- [x] Ledger Repository 不暴露更新或删除操作，并由 SQLite Trigger 拒绝对已提交 Ledger 记录执行 UPDATE/DELETE。
- [x] 自动化测试使用临时目录，不读写真实用户 World。
- [x] 现有 Node 测试仍然通过。

## Comments

### 2026-08-31 — Implemented

- 新增 Python 3.13 `uv` 工程、锁文件、`mygo-world init/show` CLI 和版本化最小 Scenario Seed。
- 初始化在迁移后的临时 SQLite 中以单个数据事务完成，再原子发布为独立 World 数据库；重复 ID、校验失败和物化失败均不会覆盖或发布部分 World。
- 新增 Alembic 初始 Schema、Snapshot SHA-256 校验、Seed provenance、append/read-only Ledger Repository 与数据库不可变 Trigger。
- 验证：`uv run pytest`（15 passed）、Ruff check/format、锁定依赖同步、Python compileall、Alembic migration check，以及具备完整临时素材根时的 `npm test`（14 passed）。
