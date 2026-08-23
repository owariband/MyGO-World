# Generative MyGO Agent Wiki

这里维护「多事件 AI Native 世界剧场」的持续架构结论。Wiki 用于沉淀讨论、决策、机制和未决问题，不替代当前仓库源码；涉及现有行为时仍以代码为准。

## 当前结论

- 世界不属于 Galgame 引擎。外部 World / Agent Runtime 才是 `world_time`、角色状态、局部认知、互动、World Transaction 和 World Event Ledger 的唯一权威；WebGAL/MyGO 不拥有也不推进世界时间。
- WebGAL/MyGO 是可替换的 Render Backend：消费已提交 World Event 的 `RenderArtifact`，动态加载背景、人物、台词和演出，并上报当前播放游标。暂停、快进、黑屏、切换和历史回放都不能反向修改世界。
- 论文明确描述了 Sandbox time-step action loop：Agent 在每个 time step 感知、决定继续计划或反应，Sandbox Server 更新共同世界并进入下一步；`world_tick / world_version / atomic commit / canonical WorldEvent Ledger` 是本项目基于该思想补充的工程化 Runtime 契约，不是论文原字段。
- 产品默认采用“生成完成驱动的 `World Time = Actual Runtime`”：Agent 推理、工具调用和异步生成的真实耗时可以进入世界时间，但外部 timer 不会在 Agent 尚未返回时独立提交剧情状态。Character 结果返回后，由 Director 对该轮生成做 Temporal/Causal Completion，补齐事件区间、先后关系、对象结果和桥接 Event；Broadcast 再决定这些世界时间怎样投影成 Galgame 演出。
- 外置能力分成 World / Agent Runtime 与 Render Plugin/Adapter。前者负责世界模拟、Event 产出、BroadcastPlan 和 RenderJob 规划；后者负责 RenderJob 校验/编译、播放队列、Event Hub、Viewer Cursor 和黑屏等待态。原始引擎源码、压缩 Bundle、内部 Store、Backlog 和存档机制仍视为第三方黑盒。
- 世界可同时存在多个主视角 Event，例如 `Anon / Soyo`、`Tomorin`、`Saki / Mutsumi / Uika`。玩家选择当前观察窗口并可随时切换；未被观看的 Event 仍可继续推进。
- Agent 初步分为 Character Agent、Director Agent 和 Broadcast Agent。Character 基于 Persona 与局部认知生成行为；Director 至少承担生成结果返回后的时间/因果补完，并在更高层维护剧情约束与未解决线程；补完结果仍须经最小一致性校验后才成为客观 Event。Broadcast 只负责展示选择、摘要、镜头和时间投影。
- Character Agent 产出结构化 `ActorPerformance / ActionProposal`；Director 基于角色输出与 measured latency 形成待校验 `SegmentDraft`；Temporal Binder、Minimal Validator 和 Committer 只负责绑定实耗、守住世界不变量并提交 Ledger；Event Recognizer 再聚合 `WorldEvent`。任何模型都不能绕过提交链直接控制播放器或宣称事实。
- 宏观算法以 Generative Agents 的 Character Agent / Memory / Planning / Reflection / Sandbox Action Loop 为认知底座，再增加 Director Agent 做每轮 Temporal/Causal Completion 和低频叙事干预、Broadcast Agent 做 World Timeline → Render Timeline 的观看投影；Binder、Validator、Ledger、Committer 与 Event Recognizer 是最小确定性治理，但不硬编码故事时长。
- Timeline 不是 Event Hub 的 UI 控件或 Render Queue，而是 Agent 世界的执行语义：它统一承载动作区间、角色认知获得时间、互动生命周期、计划/承诺变化、WorldTransaction 因果历史和 Viewer 回放位置；MyGO/WebGAL 解析只是该世界向 Galgame 媒介投影的副产物。
- World 由一等 `LocationModel` 维护地点身份、版本化客观事实、当前有效的 LocationInfo，以及指向唯一 WorldEvent Ledger 的地点 Event 索引。地点事实不会被 Agent 文本覆盖；Director 在角色前往地点前查询该地点上下文，只能提出合法的信息传播机会，角色仍须依据已提交的 Fact/Info 或传播 Event，经 PerceptionProjector -> `perceive` 才能真正获知。
- 后续算法方向包括从 MyGO 番剧视频中归纳带证据的 Character Skill，再用于 Character Agent 的 Persona、关系条件策略、语言风格和行为偏好；该方向尚未实现，不等于已完成 VLM/微调能力。
- 当前固定版本的 Bundle 会自动连接同源 `/api/webgalsync`，可通过 `TEMP_SCENE` 接收完整临时场景。它允许零 Bundle 改动注入 Render，但属于内部同步协议，必须锁定版本并加契约测试。
- 产品规则是“有 Ready Render 就加载，没有 Render 就保持黑屏”。黑屏由 Plugin Host 控制；WebGAL 可在遮罩下预热。
- “播放前先运行约 30 分钟 Agent 流”不是普通性能优化，而是生成世界与观看世界的核心解耦机制：玩家消费已提交的演员剧本，Agent 在其前方持续生成和补完。它不代表一次性写死永久未来，但必须形成真实可消费库存，而不只是远端计划。
- **已实现并验证：**Dynamic Render MVP 已支持结构化 Fixture Timeline、Render 校验/编译、进程内逐段队列、Event 切换、黑屏 Host、`webgalsync / TEMP_SCENE` 注入和文件热加载；2026-08-22 本地 `npm test` 为 14/14 通过。
- **一期已冻结但尚未实现：**在项目内新增 Go `agent_runtime/`，使用 Eino ADK 作为 Agent 执行框架；Persona、Director、Broadcast 对外实现 `adk.Agent`。Character 的工作名称为 `PersonActAgent`，内部用 Eino Compose Graph 承载 `perceive -> retrieve -> plan -> propose` 与有界回环，而不是移植 Stanford 的手写 Python Runtime。`agent / event / world` 所有权分层、Memory namespace 隔离、Fixture Vertical Slice，以及 `BroadcastPlan -> RenderJob -> Dynamic Render` 方向继续保留。
- **必须在实现中验证：**`PersonActAgent` 的 ADK 适配与 Graph checkpoint、Director/Broadcast 是否值得采用同构 Graph、Temporal Binder 如何处理 Director 自身耗时、跨 Event 共享实体如何归约、Prompt Contract、真实模型质量和约 30 分钟领先库存。它们不阻塞 Fixture 骨架开工，但不能被表述成已经解决。

## 导航

- [架构](architecture.md)：产品语义、Agent 分工、状态模型和主链路。
- [导演与导播层](director-broadcast.md)：Generative Agents 底座之上的叙事约束、角色自治和观看投影研究框架。
- [难点、卡点与代价账本](difficulty-ledger.md)：以问句维护设计问题，重点追踪 Runtime↔Galgame、无 Maze 外在事件和 `Persona.perceive()` 的局部感知边界，并保留被否决答案、当前代价与未决部分。
- [Agent Runtime Server 一期落地方案](agent-runtime-implementation.md)：**下一开发 Session 的首要入口**；包含 Go + Eino ADK、`PersonActAgent` 内部认知 Graph、`agent / event / world` 分层、Memory 边界、外部库替代矩阵、Fixture Vertical Slice、分阶段 Plan 与启动指令。
- [地点 World Model](location-world-model.md)：地点稳定事实、周期/时效 Info、Event 挂载索引、版本化更新，以及 Director 到访前查询与角色获知链。
- [关键机制](mechanisms.md)：零侵入插件、动态编译、黑屏、切换和失败恢复。
- [决策记录](decisions.md)：已确认决策、当前建议和产品目标。
- [未决问题](open-questions.md)：需要实现或实验回答的问题。
- [维护日志](log.md)：Wiki 维护历史。

## 当前源码锚点

- [仓库 README](../README.md)：当前轻量制作层和结构化 Story 编译边界。
- [Authoring 编译器](../tools/mygo-author-lib.mjs)：Beat、素材和 Live2D 能力校验及 DSL 编译。
- [开发服务器](../tools/mygo-author.mjs)：编译、监听和虚拟文件挂载。
- [项目模型](../tools/mygo-project.mjs)：`project.json / story.json / build` 组织。
- [播放器 Bootstrap](../tools/player-bootstrap.js)：外部启动脚本入口。
- [WebGAL/MyGO Bundle](../../MyGO_v3.1.1_ForScript/assets/index-982c8eaa.js)：兄弟目录中的固定版 `/api/webgalsync` 客户端和 `TEMP_SCENE` 行为。
- [版本说明](../../MyGO_v3.1.1_ForScript/webgal-engine.json)：兄弟目录中的 MyGO `3.1.1`、WebGAL `4.5.19`。
- [算法研究](../../../designs/generative-agents-world-event-algorithm/README.md)：外部 World Runtime、Agent 局部认知、互动握手、WorldEvent 识别和评测的当前设计。
- [Generative Agents 论文](https://arxiv.org/html/2304.03442v2)：Sandbox time-step action loop 的论文证据。
- [Eino ADK Agent 接口](https://github.com/cloudwego/eino/blob/v0.9.15/adk/interface.go#L447-L467)：一期 Agent 对外生命周期契约。
- [Eino ADK ReAct Graph](https://github.com/cloudwego/eino/blob/v0.9.15/adk/react.go#L354-L558)：`adk.Agent` 内部使用 Compose Graph 与条件回边的源码依据。
- [本地 WorkflowAgent 先例](../../../go-project/agent_core/agent/workflow/workflow_agent.go)：`compose.Workflow -> Runnable -> adk.Agent` 的现有工程实现。

