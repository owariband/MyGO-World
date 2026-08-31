# 09 — 接入真实 Provider 并完成 Anon × Soyo Live 验收

**What to build:** 在不改变 Fixture 验证过的 Runtime 语义下接入一个真实 OpenAI-compatible Provider，并显式运行 Anon 与 Soyo 的完整世界生成和真实 MyGO 素材渲染演示。

**Blocked by:** 08 — 交付可重复的本地 Fixture MVP Demo.

Status: ready-for-human

**Execution goal:** `../goals/09-run-real-provider-live-demo.md`

- [x] 真实适配器只通过 `ModelGateway` 接入，不让 Provider SDK 类型进入领域模型或 World Runtime。
- [x] Base URL、API key、model ID 和模型参数由进程环境提供，并可从未纳入版本控制的本地环境文件加载。
- [x] Provider 支持原生 JSON Schema 时使用原生结构化输出；不支持时解析 JSON 文本并使用同一 Pydantic Schema 校验。
- [x] Character、Director 和 Broadcast 默认共享全局 Provider/model 配置，MVP 不实现第二 Provider 或按 Agent 类型覆盖模型。
- [x] 真实调用沿用既定超时、传输重试、单次语义修复、并发限制和请求预算。
- [x] OpenTelemetry 能记录不含秘密的模型调用与 Runtime 链路信息。
- [x] 真实模型测试带显式 `live` 标记，并且普通测试与默认 CI 不会隐式执行它。
- [x] Live Scenario 使用 Anon 与 Soyo，并为两者加载独立 Character Skill 与隔离 Memory。
- [ ] Anon 和 Soyo 在验收运行中至少各产生一次通过 Validator 的有效行动。
- [ ] 目标 Event Session 在默认六个 Wave 内自然进入 `resolved`，`limit_reached` 不算主演示通过。
- [ ] Live `render` 使用真实 MyGO Asset Manifest、文件和 Live2D 元数据完成校验。
- [ ] Live 流程发布至少一个可通过 RenderCompiler 检查的 WebGAL 场景脚本。
- [ ] Live 验收保存完整脱敏 Generation Trace、CLI Receipt、最终 World Version 和产物 hash。
- [x] Pydantic Evals 可以对固定样例记录质量、延迟和成本结果，但其评分不替代结构、权限、因果、来源和素材硬校验。

## Comments

- 已实现 OpenAI-compatible Provider、显式环境文件与进程环境覆盖、两种结构化输出模式、统一模型参数、120 秒超时、传输重试/语义修复/请求预算、脱敏与 OpenTelemetry 链路。
- 已加入版本化 Anon × Soyo Live Scenario、四类独立 Skill、真实 MyGO Asset Manifest、`live-demo` 审计入口、Pydantic Evals 入口及显式 `live` 测试。离线 HTTP 端到端 harness 已验证生产 `init → advance → render`、Memory 隔离、自然 `resolved`、Ledger/Trace 交叉核对和发布 hash 回读。
- 验证通过：`uv run pytest`（115 passed, 1 deselected）、Ruff check/format、compileall、Alembic 单 head、真实 MyGO 素材/Live2D 元数据预检及 14 项 Node 测试。
- 2026-08-31 执行目标 Live 命令时在零网络请求、零 World 创建的预检阶段失败：仓库中不存在显式指定的 `.env.local`，进程环境也没有 Provider 凭据。依照 goal 的硬约束，真实付费 Live、对应产物以及最后五项主演示勾选项尚未验收，ticket 不标记为 `resolved`；提供有效 `.env.local` 后重跑文档命令即可继续。
