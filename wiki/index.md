# Generative MyGO Agent Wiki

这里维护「多事件 AI Native 世界剧场」的持续架构结论。Wiki 用于沉淀讨论、决策、机制和未决问题，不替代当前仓库源码；涉及现有行为时仍以代码为准。

> 最后更新：2026-09-09。最新 MVP 边界以 [MVP_dev.md](design/MVP_dev.md) 为准；下文部分历史概述保留原术语。执行顺序和验收请看总计划 / 阶段记录，不据旧概述推断当前实现。

## 核心模型：Agent 提案，World 提交

> **这是项目最高优先级的运行边界。**`ActionProposal` 表达“角色想做什么”，不是执行结果，更不是已经发生的世界事实。

```text
perceive -> retrieve -> plan -> ActionProposal
                                  |
                                  v
                     Validator / WorldUpdater
                                  |
                                  v
                         Committed EventEntry
                                  |
                    +-------------+-------------+
                    v                           v
 DirectorView -> pending EventEntry      AgentViewBuilder
```

- `PersonActAgent.decide` 每次只为当前角色产出一个 Action；`agentId` 由受信 `CompiledPersonActSpec` 注入。
- `interact` 可以指向 Character 或非 Agent Object，但 target 类型必须显式进入契约；对 Character 发起交互不代表对方已接受或已经行动。
- Director 不读取或补全 Character Proposal；它只在 Proposal 已提交后，通过受限 DirectorView 管理 pending EnvironmentEntry。
- Agent 不执行 Maze 移动、不修改其他 Persona、不直接写 World/EventEntry，也不控制 Render。

## 项目定位：自研领域型 NPC ADK

当前建设的是 **Generative Go World 自己的 NPC ADK**，不是对 LangChain 的二次包装，也不是通用 Agent Builder。项目自研并拥有 Creator Manifest、受信 Compiler、Persona State/Memory、认知阶段、Action Schema、权限校验和 Trace；LangChain Core 只是内部 Runnable 与模型接入基础设施。当前已落地 PersonAct 单次认知 Slice、Runtime Skill 与模型 Strategy seam，外部 Scheduler、Director、World Commit、reflection feedback 和完整 Runtime 仍按下文边界继续实现。

## 当前结论

- 世界不属于 Galgame 引擎。外部 World / Agent Runtime 才是 `world_time`、角色状态、局部认知、互动、关系型当前状态和 committed EventEntry 的唯一权威；WebGAL/MyGO 不拥有也不推进世界时间。
- WebGAL/MyGO 是可替换的 Render Backend：消费已提交 EventEntry 的 `RenderArtifact`，动态加载背景、人物、台词和演出，并上报当前播放游标。暂停、快进、黑屏、切换和历史回放都不能反向修改世界。
- 论文明确描述了 Sandbox time-step action loop：Agent 在每个 time step 感知、决定继续计划或反应，Sandbox Server 更新共同世界并进入下一步；`world_tick / world_version / atomic commit / canonical EventEntry history` 是本项目基于该思想补充的工程化 Runtime 契约，不是论文原字段。
- Agent/Tool 的真实耗时进入 Generation Trace，但不自动解释为角色犹豫、移动或对象完成。Character Proposal 直接经 Validator/WorldUpdater 提交；已经客观启动的后台过程由 pending EnvironmentEntry 的检查时间和 release guard 管理。World time 的精确推进规则仍需 Golden Trace 冻结，Broadcast 再把已提交时间投影成 Galgame 演出。
- 外置能力分成 World / Agent Runtime 与 Render Plugin/Adapter。前者负责世界模拟、Event 产出、BroadcastPlan 和 RenderJob 规划；后者负责 RenderJob 校验/编译、播放队列、Event Hub、Viewer Cursor 和黑屏等待态。原始引擎源码、压缩 Bundle、内部 Store、Backlog 和存档机制仍视为第三方黑盒。
- 世界可同时存在多个主视角 StoryLine，例如 `Anon / Soyo`、`Tomorin`、`Saki / Mutsumi / Uika`。玩家选择当前观察窗口并可随时切换；未被观看的互动线仍可继续推进。
- Agent 分为 Character Agent、Director Agent 和 Broadcast Agent。Character 基于 Persona 与局部认知决定自己的行为；Director 只管理由已提交事实触发的 pending EnvironmentEntry；Broadcast 只负责展示选择、摘要、镜头和时间投影。
- Character Agent 产出结构化 `ActionProposal`，Validator/WorldUpdater 守住世界不变量并原子追加 `EventEntry`。Director 只能输出受 affordance 限制的 `emit / schedule / keep / release / cancel / no_op`；release 仍要再次经过同一提交链。任何模型都不能绕过 WorldUpdater 宣称事实。
- 宏观算法借鉴 Generative Agents 的 Perception、Memory、Retrieval、Planning/Reacting 与 Reflection 认知语义，再增加 pending EnvironmentEntry Director 和观看投影 Broadcast。其 Sandbox loop、`Persona.move()`、Maze/path/tile movement 和 `execute` 不迁移；EventSessionRunner、AgentViewBuilder、Validator、WorldUpdater、EventEntry 与 Render Planner 是项目的确定性治理。
- Timeline 不是 Event Hub 的 UI 控件或 Render Queue，而是 Agent 世界的执行语义：它统一承载动作区间、角色认知获得时间、互动生命周期、计划/承诺变化、WorldTransaction 因果历史和 Viewer 回放位置；MyGO/WebGAL 解析只是该世界向 Galgame 媒介投影的副产物。
- World 由一等 `LocationModel` 维护地点身份、客观事实、当前有效的 LocationInfo，并直接按 committed EventEntry 查询地点事件。地点事实不会被 Agent 文本覆盖；自由互动 MVP 先使用明确的关系型当前状态，逐 Fact/Info revision 与任意历史版本查询延后。Director 不参与信息披露；既有公开信息由 AgentViewBuilder 硬过滤，需要传播行为时由 Character/System 自己提交。
- 已迁入五份人工 Gold Character Skill，作为版本化、hash-pinned 的稳定创作配置；从 MyGO 番剧视频自动归纳带证据 Skill 的方向仍未实现，不等于已完成 VLM/微调能力。
- 当前固定版本的 Bundle 会自动连接同源 `/api/webgalsync`，可通过 `TEMP_SCENE` 接收完整临时场景。它允许零 Bundle 改动注入 Render，但属于内部同步协议，必须锁定版本并加契约测试。
- 产品规则是“有 Ready Render 就加载，没有 Render 就保持黑屏”。黑屏由 Plugin Host 控制；WebGAL 可在遮罩下预热。
- “播放前先运行约 30 分钟 Agent 流”不是普通性能优化，而是生成世界与观看世界的核心解耦机制：玩家消费已提交的演员剧本，Agent 在其前方持续生成、提交、处理 EventStaff 和编译。它不代表一次性写死永久未来，但必须形成真实可消费库存，而不只是远端计划。
- **已实现并验证：**Dynamic Render MVP 已支持结构化 Fixture Timeline、Render 校验/编译、进程内逐段队列、Event 切换、黑屏 Host、`webgalsync / TEMP_SCENE` 注入和文件热加载；2026-08-22 本地 `npm test` 为 14/14 通过。
- **现行 Agent Runtime 技术方案：**Python 3.12 + `langchain-core==1.6.1`。Agent 步骤使用显式类型的 `RunnableLambda / RunnableSequence` 组合；外部输入、模型/Tool 输出和跨模块契约使用 strict/frozen Pydantic Model；静态接线由 pyright strict 检查。Python 版本、虚拟环境、依赖与锁文件统一由 uv 管理，ruff/pytest 也统一通过 `uv run` 执行；当前不使用 LangGraph。
- **NPC DIY 已形成最小边界：**创作者只提交受限 `agents.json`；Python Runtime 严格校验并收敛为 frozen `CompiledPersonActSpec`。World contract 已落地固定 `ActionProposal` envelope、六类 `action.kind` union 与 character/object typed target；配置不能声明 Memory namespace、模型密钥、任意 Tool/URL、Runnable 拓扑、World Commit 或 Render 权限。
- **PersonAct 单次认知 Slice 已落地：**`PersonActAgent.decide` 已实现 prepare/perceive/retrieve/plan/propose；外部 Event Scheduler 独占循环，一次调用只为 `spec.agent_id` 返回一个 strict/frozen `ActionProposal`。需要 wire JSON 时显式使用 `model_dump_json(by_alias=True)`；Agent 不实现 `Persona.move()` 或 movement。
- **远端 MVP 机制已做选择性融合：**`origin/mvp@febf9d1` 与 `master` 没有 merge base，因此不做整体 merge；只迁入版本化 Character Skill、整文件 hash pin、typed Model Gateway、structured output、transport retry、单次 schema/semantic repair 与非秘密调用 provenance。它们作为 `CognitionStrategy` 实现接入现有 PersonAct Loop，不取代 `loop.py`。
- **明确没有迁入：**MVP 的 same-snapshot lockstep、同 Event 多角色并发、`move`、Proposal 内 `memory_changes`、Wave/World DB Runtime 和一次整轮提交；本项目继续坚持同 Event 内“一个角色 Proposal -> commit -> 下一角色读取新版本”。
- **自由互动 MVP 的持久化已收敛：**SQLite 只保存明确的关系型当前状态、稳定 EventSession root binding、轻量 InteractionRequest、append-only `event_entries` 与隔离 Agent Memory；不建立重复的 `world_events / world_changes / world_versions / entity_revisions / world_snapshots`。作品 `scenario.yaml` 初始化公共世界与初始分区，全员/指定角色知识由 bootstrap 按接收者展开到各自 Agent Memory。
- **M2 Project World 已实现、验收并推送：**`5d2f496` 为每个 Project 提供独立 `.runtime/<project-id>/world.sqlite`，同库可容纳多个 `world_id`；严格 Scenario、九表首版 schema、公共/私有状态原子初始化、五稳定 EventSession 节点、跨进程 paused load 和配置钉住均已落地。M2 尚无 EventEntry、WorldUpdater、Runner、Director 或 Broadcast，不能表述为 Agent 已可自由互动；证据见 [M2 开发记录](design/M2_dev_log.md)。
- **M3 已进入设计 Review、尚未实现：**详细设计收口为唯一 `EventEntry` 历史、稳定 World operation/affordance、World/Agent 原子单步提交、`AgentViewBuilder` 和可重载 `CharacterStep`；文件树、预计行数、六个冻结点与验收门禁见 [M3 开发记录](design/M3_dev_log.md)。
- **互动记录与演出投影已经分权：**所有已提交的 Character 互动、Session merge/split 和 Director environment release 共用 `event_entries`；`interaction_requests` 只保存仍待 Character 处理的当前状态，待完成的客观过程使用同一 EventEntry 的 pending 状态，Agent Memory 通过 `source_entry_id` 保存主观认知。Broadcast 据此生成带来源的 Render Artifact，不能把 WebGAL 脚本回写为世界事实。
- **Director 权限已经收紧：**Director 不做 Segment Completion、Narrative Thread 或剧情刺激。它只能读取一个 committed source Entry/待检查 pending Entry 的受限 DirectorView，并从 World 提供的 affordance 中选择 `emit / schedule / keep / release / cancel / no_op`。一句“我要煮咖啡”不足以 schedule，必须先有角色自己提交的 `coffee_brewing_started`。
- **基础代码不等于完整 Runtime：**Project World、PersonaState/Memory 初始持久化已经完成；reflection/commit feedback、pending EnvironmentEntry Director、Validator/WorldUpdater、EventSessionRunner/EventEntry history、Broadcast、真实 Provider 验收、Generation Trace 持久化与完整咖啡 Golden Trace 仍未实现。
- **必须准确理解强类型：**`Runnable.with_types()` 只提供类型/Schema 元数据，不做 runtime validation。真正的运行时结构校验由 Pydantic 完成，领域合法性由 Compiler、AgentViewBuilder、Validator 与 WorldUpdater 保证。
- **必须在实现中验证：**World time/pending Entry 唤醒时钟、EnvironmentEntry affordance/取消规则、跨 StoryLine 共享实体如何归约、Prompt Contract、真实模型质量和约 30 分钟领先库存。它们不阻塞 Fixture 骨架开工，但不能被表述成已经解决。

## 导航

- [架构](architecture.md)：产品语义、Agent 分工、状态模型和主链路。
- [EventStaff Director 与 Broadcast](director-broadcast.md)：Director 的受限可见性、EventStaff 队列/释放/恢复、角色自治边界与观看投影。
- [难点、卡点与代价账本](difficulty-ledger.md)：以问句维护设计问题，重点追踪 Runtime↔Galgame、无 Maze 外在事件和 `decide` 内部 perceive 的局部感知边界，并保留被否决答案、当前代价与未决部分。
- [Agent Runtime 一期落地方案](agent-runtime-implementation.md)：**下一开发 Session 的首要入口**；包含 Python + LangChain Core 强类型边界、`PersonActAgent.decide`、ActionProposal union、`agent / event / world` 分层、Memory 边界、Fixture Vertical Slice、分阶段 Plan 与启动指令。
- [MVP 完善开发计划](design/MVP_dev.md)：以直观命名整理 SQLite 世界事实底座、可选择复用的 `origin/mvp` 模型、EventSession 互动/重组闭环、分阶段交付与待重新设计问题。
- [MVP 分阶段执行计划](design/dev_plan_MVP.md)：固定 dev_plan，维护 M1–M7 的范围、实现前文件级设计、验收和 Review 状态；当前重点是 M3 单步世界与认知提交。
- [M1 开发记录](design/M1_dev_log.md)：固定阶段 dev_log，记录 Gateway、WorldRef / Plan queue、UnionPart 的实际文件与测试证据。
- [M2 开发记录](design/M2_dev_log.md)：记录 Project SQLite、Scenario、初始 World/Agent 状态、跨进程加载的实际 schema、文件与测试证据。
- [M3 开发记录](design/M3_dev_log.md)：从设计阶段开始维护 M3.1–M3.4 的详细 schema、文件树、Review 冻结点；开工后继续在同一文件补实际 diff、测试与提交证据。
- [NPC DIY](npc-diy.md)：创作者配置、Pydantic 受信编译、`PersonActAgent.decide`、Proposal contract、当前实现证据与下一步。
- [地点 World Model](location-world-model.md)：地点稳定事实、周期/时效 Info、Event 查询、确定性可见性与角色获知链。
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
- [Python 依赖与质量配置](../pyproject.toml)：Python 3.12、LangChain Core、Pydantic、pyright strict、ruff 与 pytest 的锁定配置。
- [统一 StrictModel](../agent_runtime/model.py)：strict、frozen、拒绝未知字段的运行时契约策略。
- [NPC Manifest Compiler](../agent_runtime/agent/personact/compiler.py)：不受信配置到受信运行规格的能力收敛。
- [World-owned Contracts](../agent_runtime/world/contracts.py)：固定 ActionProposal envelope、六类 action union 与 typed target。
- [PersonAct Agent](../agent_runtime/agent/personact/agent.py)：`decide` 门面、并发串行化、proposal replay 与 private snapshot 原子替换。
- [PersonAct Loop](../agent_runtime/agent/personact/loop.py)：typed RunnableSequence 与 prepare/perceive/retrieve/plan/propose 真实认知阶段。
- [Model Cognition Strategy](../agent_runtime/agent/personact/model_strategy.py)：消费已编译 Persona、Character Skill 与局部上下文的模型型策略，以及最多一次 repair。
- [Runtime Skill](../agent_runtime/agent/skill.py)：严格 Markdown frontmatter、版本化 Catalog 与整文件 SHA-256 pin。
- [Model Gateway](../agent_runtime/model_gateway.py)：LangChain ChatModel/Fixture 的 typed structured-output seam 与非秘密调用 trace。
- [Proposal Authority Boundary](../agent_runtime/agent/personact/proposal.py)：最终 Proposal 构造、actor 注入与 capability/affordance/evidence 校验。
- [Project SQLite Boundary](../agent_runtime/sqlite.py)：每 Project 独立数据库、身份、迁移、连接约束与首次创建原子发布。
- [Scenario Loader](../agent_runtime/scenario.py)：开场公共状态、初始分组、知识接收者与 canonical hash 的严格加载。
- [World Bootstrap](../agent_runtime/bootstrap.py)：公共 World、PersonaState、Memory 的原子创建与 paused load。
- [NPC DIY 契约测试](../agent_runtime/tests/test_personact.py)：类型、权限、namespace、affordance 和 evidence 拒绝路径。
- [PersonAct 认知测试](../agent_runtime/tests/test_personact_agent.py)：attention、novelty、Memory retrieval、state 原子更新和单 Proposal 边界。
