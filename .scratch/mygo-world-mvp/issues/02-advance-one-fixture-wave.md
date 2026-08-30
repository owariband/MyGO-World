# 02 — 通过 Fixture 推进一个完整 Generation Wave

**What to build:** 让操作者能够对一个最小 World 执行一次无网络的 `advance`，使单个 Character 的固定提案经过真实感知、Director、两阶段 Validator 和原子提交链路，形成可重新读取的世界事实与新版本；同时用一个 Director 产生的无主体环境/桥接事件证明提交链没有与 Character actor 或 Action Proposal 一一绑定。

**Blocked by:** 01 — 初始化并重新打开持久 World.

Status: resolved

**Execution goal:** `../goal.md`

- [x] `FixtureGateway` 与真实 Provider 共享同一个窄 `ModelGateway` 契约，并且运行此切片时不会发起网络请求。
- [x] PerceptionProjector 能从共同 Snapshot、Session、Interaction Scope 和该 Character 的 Memory 生成受权限约束的 PerceptionFrame。
- [x] Character Fixture 返回一个且仅一个类型化 Action Proposal，并记录简短 `intent_summary` 而非思维链。
- [x] Proposal Validator 能接受合法提案，并以稳定诊断拒绝错误 World Version、错误 actor、不可见目标、不可达目的地和非法 Memory 所有权。
- [x] Director Fixture 能根据合法 Proposal 产生包含客观结果、语义时间、因果和 Session 意图的 Segment Draft。
- [x] Segment Draft 能表示不带 Character actor 的 External Event Candidate，并为候选事实保存通用 `source_kind` 与 `source_ref` 或 `evidence_refs`；本 ticket 不实现 Player Event Request 输入或存储。
- [x] Segment Validator 能产生 Validated Commit Plan，并以稳定诊断拒绝意图篡改、非法状态迁移、越界时间和错误因果引用。
- [x] Segment Validator 能接受合法的无主体环境/桥接事件，并拒绝没有对应 Action Proposal 却通过外部事件候选写入的 Character 台词、重要行动或动机变化。
- [x] 一次成功 Wave 只产生一个新 World Version，即使它识别出多个 World Event。
- [x] World Segment、Entity Revision、World Event、Observation、被接受的 Memory 变化、Snapshot 和新 World Version 在一个 SQLite 事务中对外可见。
- [x] Event Recognizer 基于事务内待提交 Segment 确定性产生 World Event，且不反向修改候选事实。
- [x] Event Recognizer 不假设 World Event 与 Action Proposal 一一对应；Fixture 中至少一个无主体 Event 能保留通用来源证据并由 PerceptionProjector 只投影给有感知权限的 Character。
- [x] 任一提交阶段注入失败时，当前 Wave 不留下部分 Ledger、Memory、Snapshot 或版本记录。
- [x] Generation Trace 保存请求、原始 Fixture 响应、结构化结果、Skill/model 来源和校验结果，但不包含凭据。
- [x] 新进程能够读取提交后的版本、事件、Observation 和 Snapshot checksum。

## Comments

### 2026-08-31 — 第二阶段 Player Event Request 兼容 seam

- 玩家自由文本入口、请求持久化和 Director 自然语言解析仍延期；本 ticket 只验证 Director 无主体事件走同一 `candidate → validate → commit` 链。
- Director 仍无提交权限。未来 Player Event Request 只能成为 Director 的版本绑定输入，不能直接成为 World Event，也不能绕过 Character 行动所有权。

### 2026-08-31 — Implemented and audited

- 新增共享 `ModelGateway`、无网络 `FixtureGateway` 与 OpenAI-compatible Provider；`advance` 默认执行一个确定性 Fixture Wave。
- 新增权限受限的 `PerceptionFrame`、类型化 Action Proposal/Segment Draft、两阶段纯 Validator、无主体 External Event provenance、Event Recognizer 与 Generation Trace。
- World Committer 在单事务中提交一个 Segment/Version、一个 Entity Revision、两个 World Event、四个 Observation、一个已接受 Belief 与 checksum Snapshot；七个提交阶段均有回滚注入测试。
- 验证：`uv run pytest`（37 passed）、Ruff check/format、Python compileall、Alembic head、跨进程 CLI 重读。暂时移除未生成的 `game/bgm/for-the-band/*.mp3` 故事引用后，以实际素材根 `WEBGAL_ROOT=../MyGO_v3.1.1` 运行 Node 回归为 14 passed。
