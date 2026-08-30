# 演出由多个不可变 Render 组成并保留事实来源

Broadcast 将一次 Broadcast Run 的新增 Event frontier 编排为多个有序、不可变且自包含的 Render，而不是一个巨大脚本或每个 World Event 一个 Render。包含事实或台词的 Beat 必须引用来源 World Event；BroadcastPlan 还记录被省略的事件及原因，纯转场和风格 Beat 可以没有来源，但不得陈述新的世界事实。

新 Render 必须至少处理一个此前未纳入或省略的 World Event；更早 Event 可以作为上下文，并可为倒叙或回顾再次引用，但不能单独触发无新内容的重复编排。增量边界由 Event 的 Broadcast Disposition 决定，不由 Generation Batch 决定。

Broadcast 只输出结构化 BroadcastPlan 与 Beat，不直接编写 WebGAL DSL。确定性 Render Planner 根据 Asset Manifest 校验角色、背景、Live2D motion/expression 等能力，再由现有编译器生成脚本。

BroadcastPlan 使用 World Event 和素材 ID 表达创作意图；确定性 RenderPlanner 验证 Event 覆盖、来源和自包含性，解析 Manifest 路径并生成 RenderJob；RenderCompiler 只将 RenderJob 转为 DSL。每个待处理 Event 必须恰好得到 `included` 或带原因的 `omitted` Disposition，`included` Event 至少被一个 Beat 引用。

Broadcast 只能从输入中提供的 Manifest 候选 ID 选择背景、BGM、Live2D model、motion、expression 和入场效果，不能填写文件路径或发明能力。每个 Render 必须自行建立背景、出场角色与所需 BGM，不继承前一个 Render 的舞台状态；一个不可变 Render 对应一个独立 WebGAL `.txt` 文件。

MVP 在 Python Runtime 内实现窄 `RenderCompiler` 接口，不依赖 Node 子进程。它只编译首期八种 Beat，通过 Pydantic、Asset Manifest、真实文件与 Live2D `model.json` 校验输入，再用封闭模板生成和转义 WebGAL DSL；模型不能直接提供 DSL，因此首期不实现通用 DSL Parser。现有 Node Authoring/Dynamic Render 输出只作为兼容性 Golden 样例，不是运行时依赖。

首期 Beat 限于 `chapter`、`bgm`、`stop_bgm`、`background`、`show`、`hide`、`dialogue` 和 `narration`。`show` 与 `dialogue` 可以携带经 Asset Manifest 和 Live2D `model.json` 校验的 `motion`、`expression` 与入场效果；逐帧控制、复杂动作混合、Choice、自由输入、视频和动态生成素材不属于首期。

引用角色发言的 `dialogue` 必须保留已提交 utterance 的原文，可以整句省略但不得润色成角色没有说过的话；`narration` 可以基于明确来源概括多个事实。单个 Render 默认最多 40 个 Beat、预计播放 8 分钟，超限时 Broadcast 必须拆分，限制可由运行配置覆盖。

Render Gateway 先在项目本地 `.mygo/renders/<render_id>/` 构建并完成 Schema、素材和 WebGAL 脚本校验，再将内容写入 WebGAL 目标目录中的临时文件，并在同一文件系统内原子重命名为不可变 hash 路径。文件成功发布后才以短 SQLite 事务写入 Render 元数据和 Broadcast Disposition；若进程在两者之间中断，重试校验已有目标文件，hash 相同则复用并补全数据库记录，不同则安全失败。

每个 World 使用独立 Render 锁串行执行 Broadcast Run，避免两个进程消费同一 Event frontier；Render 固定目标 World Version 后可以与 `advance` 并存。它对 Ledger 和 Snapshot 只读，但在不可变脚本发布成功后通过短 SQLite 事务与唯一约束写入 Render 元数据和 Broadcast Disposition，使重复执行能够安全检测已处理 Event；文件名中的内容 hash 与上述恢复协议共同关闭文件系统和 SQLite 之间无法共享事务的崩溃窗口。

发布文件使用不可变路径 `game/scene/generated/<world_id>/<render_id>-<content_hash>.txt`，不得覆盖 `game/scene/start.txt` 或固定的 `latest.txt`。WebGAL 当前按 `game/scene/<ref>` 解析场景引用，因此嵌套生成目录可以由后续入口场景引用。

WebGAL 根目录不写入 Scenario 或代码，由 CLI 参数覆盖进程环境配置；真实链路可以指向本地 MyGO 3.1.1 项目，但仓库和持久 World 不绑定该机器的绝对路径。
