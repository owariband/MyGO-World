# 06 — 将已提交 Event 编译为首个 WebGAL Render

**What to build:** 让操作者能够显式渲染一个已有 World Version，由 Fixture Broadcast 选择已提交 Event 和白名单素材，并通过确定性 Python 流程产生一个可校验、自包含且不可变的 WebGAL 场景脚本。

**Blocked by:** 02 — 通过 Fixture 推进一个完整 Generation Wave.

Status: ready-for-agent

- [ ] `advance` 不会调用 Broadcast；只有显式 `render` 或 `demo` 的渲染阶段会创建 Broadcast Run。
- [ ] `render` 固定目标 World Version，并选择该范围内尚无 Broadcast Disposition 的 World Event。
- [ ] Fixture Broadcast 输出结构化 Broadcast Plan 和 Beat，不直接输出 WebGAL DSL 或文件路径。
- [ ] Broadcast 只能选择输入 Asset Manifest 中给出的稳定素材 ID。
- [ ] RenderPlanner 校验 Event 来源、included/omitted 完整性、自包含舞台状态、Beat 上限和素材能力，并解析出 RenderJob。
- [ ] 支持 chapter、开始 BGM、停止 BGM、background、show、hide、dialogue 和 narration 八类 Beat。
- [ ] dialogue 保留来源 utterance 原文；narration 只能概括明确引用的已提交事实。
- [ ] show/dialogue 中的 Live2D model、motion、expression 与入场效果会依据 Manifest 和模型元数据校验。
- [ ] RenderCompiler 使用确定性 Python 模板完成转义、WebGAL DSL 生成和内容 hash 计算，不启动 Node 子进程。
- [ ] 每个 Render 都显式建立背景、出场角色和必要 BGM，不依赖上一 Render 的舞台状态。
- [ ] Render Gateway 能把一个完成校验的本地规范产物发布为不可变的生成场景文件，且不覆盖 WebGAL 入口场景或可变 latest 别名。
- [ ] `render` 不会自动启动 WebGAL 播放器。
- [ ] 仓库内最小假素材能够在无外部 MyGO 安装时通过默认测试。
- [ ] 现有 Node 编译样例作为 WebGAL 语法与内容 hash 的兼容性参考，同时现有 Node 测试保持通过。
