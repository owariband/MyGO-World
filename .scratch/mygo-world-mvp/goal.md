Goal: 完成 ticket 02 的单 Wave Fixture 纵切，并证明世界提交链可在不实现玩家输入的前提下兼容第二阶段 Player Event Request

Completion criteria:
- [x] FixtureGateway 和真实 Provider 实现同一个 ModelGateway interface，Fixture Wave 不发起网络请求。
- [x] PerceptionProjector 从 Snapshot、Session、Interaction Scope 与目标 Character Memory 生成权限受限的 PerceptionFrame。
- [x] Character Fixture 返回一个类型化 Action Proposal，并只记录简短 `intent_summary`。
- [x] Proposal Validator 对合法提案返回成功，并以稳定诊断分别拒绝错误 World Version、错误 actor、不可见目标、不可达目的地与非法 Memory 所有权。
- [x] Director Fixture 根据合法 Proposal 生成包含客观结果、语义时间、因果和 Session 意图的 Segment Draft。
- [x] Fixture Director 产生至少一个不带 Character actor 的环境或桥接 External Event Candidate。
- [x] External Event Candidate 保存通用 `source_kind` 与 `source_ref` 或 `evidence_refs`。
- [x] Segment Validator 对合法 Segment Draft 生成 Validated Commit Plan，并以稳定诊断分别拒绝意图篡改、非法状态迁移、越界时间与错误因果引用。
- [x] Segment Validator 接受合法无主体事件，并拒绝没有 Action Proposal 的持久 Character 台词、重要行动或动机变化。
- [x] 一次成功 Wave 只产生一个新 World Version，即使 Event Recognizer 识别出多个 World Event。
- [x] World Segment、Entity Revision、World Event、Observation、被接受的 Memory 变化、Snapshot 与 World Version 在一个 SQLite 事务中可见。
- [x] Event Recognizer 从待提交 Segment 确定性识别 Event，不修改候选事实，也不假设 Event 与 Action Proposal 一一对应。
- [x] 无主体 World Event 保留通用来源证据，并且 PerceptionProjector 只向有权限的 Character 生成 Observation。
- [x] 任一提交阶段注入失败时，不留下部分 Ledger、Memory、Snapshot 或 World Version。
- [x] Generation Trace 保存请求、原始 Fixture 响应、结构化结果、Skill/model 来源与校验结果，并且不含凭据。
- [x] 新进程能读取提交后的 World Version、World Event、Observation 与 Snapshot checksum。
- [x] `uv run pytest` 成功退出。

Constraints:
- 不实现 Player Event Request 的 CLI/UI、持久化表或自然语言解析；这些属于第二阶段。
- Director 只能输出 Segment Draft 或 External Event Candidate，不能直接写 Ledger、Memory 或 World Event。
- 不改变 Scenario Seed 只用于初始化、World Ledger append-only、World Committer 唯一事实写入者和 Character 行动所有权等既有决策。
- 不为了兼容未来输入建立空的 adapter/port；只让当前实际使用的 Segment Draft、Validated Commit Plan、Event Recognizer 与 provenance 数据契约不依赖 Character actor。
- 保留 ticket 01 已实现的行为和现有 Node 工具。

Context:
- `.scratch/mygo-world-mvp/spec.md`
- `.scratch/mygo-world-mvp/issues/02-advance-one-fixture-wave.md`
- `MVP.md`
- `CONTEXT.md`
- `docs/adr/0015-bound-model-repair-and-session-generation.md`
- `docs/adr/0022-validate-before-atomic-world-commit.md`
- `docs/adr/0023-director-interprets-player-event-requests.md`
