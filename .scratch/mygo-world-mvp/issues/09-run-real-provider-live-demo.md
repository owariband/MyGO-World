# 09 — 接入真实 Provider 并完成 Anon × Soyo Live 验收

**What to build:** 在不改变 Fixture 验证过的 Runtime 语义下接入一个真实 OpenAI-compatible Provider，并显式运行 Anon 与 Soyo 的完整世界生成和真实 MyGO 素材渲染演示。

**Blocked by:** 08 — 交付可重复的本地 Fixture MVP Demo.

Status: ready-for-agent

**Execution goal:** `../goals/09-run-real-provider-live-demo.md`

- [ ] 真实适配器只通过 `ModelGateway` 接入，不让 Provider SDK 类型进入领域模型或 World Runtime。
- [ ] Base URL、API key、model ID 和模型参数由进程环境提供，并可从未纳入版本控制的本地环境文件加载。
- [ ] Provider 支持原生 JSON Schema 时使用原生结构化输出；不支持时解析 JSON 文本并使用同一 Pydantic Schema 校验。
- [ ] Character、Director 和 Broadcast 默认共享全局 Provider/model 配置，MVP 不实现第二 Provider 或按 Agent 类型覆盖模型。
- [ ] 真实调用沿用既定超时、传输重试、单次语义修复、并发限制和请求预算。
- [ ] OpenTelemetry 能记录不含秘密的模型调用与 Runtime 链路信息。
- [ ] 真实模型测试带显式 `live` 标记，并且普通测试与默认 CI 不会隐式执行它。
- [ ] Live Scenario 使用 Anon 与 Soyo，并为两者加载独立 Character Skill 与隔离 Memory。
- [ ] Anon 和 Soyo 在验收运行中至少各产生一次通过 Validator 的有效行动。
- [ ] 目标 Event Session 在默认六个 Wave 内自然进入 `resolved`，`limit_reached` 不算主演示通过。
- [ ] Live `render` 使用真实 MyGO Asset Manifest、文件和 Live2D 元数据完成校验。
- [ ] Live 流程发布至少一个可通过 RenderCompiler 检查的 WebGAL 场景脚本。
- [ ] Live 验收保存完整脱敏 Generation Trace、CLI Receipt、最终 World Version 和产物 hash。
- [ ] Pydantic Evals 可以对固定样例记录质量、延迟和成本结果，但其评分不替代结构、权限、因果、来源和素材硬校验。
