# 06 — 将已提交 Event 编译为首个 WebGAL Render

**What to build:** 让操作者能够显式渲染一个已有 World Version，由 Fixture Broadcast 选择已提交 Event 和白名单素材，并通过确定性 Python 流程产生一个可校验、自包含且不可变的 WebGAL 场景脚本。

**Blocked by:** 02 — 通过 Fixture 推进一个完整 Generation Wave.

Status: resolved

- [x] `advance` 不会调用 Broadcast；只有显式 `render` 或 `demo` 的渲染阶段会创建 Broadcast Run。
- [x] `render` 固定目标 World Version，并选择该范围内尚无 Broadcast Disposition 的 World Event。
- [x] Fixture Broadcast 输出结构化 Broadcast Plan 和 Beat，不直接输出 WebGAL DSL 或文件路径。
- [x] Broadcast 只能选择输入 Asset Manifest 中给出的稳定素材 ID。
- [x] RenderPlanner 校验 Event 来源、included/omitted 完整性、自包含舞台状态、Beat 上限和素材能力，并解析出 RenderJob。
- [x] 支持 chapter、开始 BGM、停止 BGM、background、show、hide、dialogue 和 narration 八类 Beat。
- [x] dialogue 保留来源 utterance 原文；narration 只能概括明确引用的已提交事实。
- [x] show/dialogue 中的 Live2D model、motion、expression 与入场效果会依据 Manifest 和模型元数据校验。
- [x] RenderCompiler 使用确定性 Python 模板完成转义、WebGAL DSL 生成和内容 hash 计算，不启动 Node 子进程。
- [x] 每个 Render 都显式建立背景、出场角色和必要 BGM，不依赖上一 Render 的舞台状态。
- [x] Render Gateway 能把一个完成校验的本地规范产物发布为不可变的生成场景文件，且不覆盖 WebGAL 入口场景或可变 latest 别名。
- [x] `render` 不会自动启动 WebGAL 播放器。
- [x] 仓库内最小假素材能够在无外部 MyGO 安装时通过默认测试。
- [x] 现有 Node 编译样例作为 WebGAL 语法与内容 hash 的兼容性参考，同时现有 Node 测试保持通过。

## Comments

### 2026-08-31 — Implemented and audited

- 新增显式 `render` CLI、结构化 Broadcast Plan/八类 Beat、白名单 Asset Manifest、固定 World Version frontier 与不可变 disposition/render Schema。
- RenderPlanner 校验来源、原文台词、自包含舞台、Beat/时长上限、素材文件和 Live2D model/motion/expression/入场能力；RenderCompiler 以 Python 封闭模板生成和转义 WebGAL DSL 并计算 SHA-256。
- Render Gateway 先保存本地规范产物，再以原子 no-replace 方式发布 hash 命名场景；中断重试复用相同内容，冲突内容安全失败，不写入口场景或 `latest`，也不启动播放器。
- 验证：`uv run pytest`（52 passed）、Ruff check/format、Python compileall；以 `WEBGAL_ROOT=/Users/yyu03/project/dev/MyGO_v3.1.1` 运行 Node 回归（14 passed）。
