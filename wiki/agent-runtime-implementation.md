# Go + Eino ADK Agent Runtime 一期落地方案

## 0. 文档状态与交接入口

本页是下一开发 Session 的一期执行依据，更新时间为 2026-08-22。阅读状态时必须区分：

- **已实现并验证：**`extensions/dynamic-render/` 的 Fixture Timeline、Render 校验/编译、进程内队列、Event 切换、黑屏 Host 和 `TEMP_SCENE` 注入；2026-08-22 本地 `npm test` 为 14/14 通过。
- **设计已冻结、代码未实现：**`agent_runtime/` 使用 Go + Eino ADK；Persona、Director、Broadcast 对外实现 `adk.Agent`。Character 使用工作名 `PersonActAgent`，内部 Compose Graph 承载 Generative Agents 风格的认知流程。`agent / event / world` 目录边界、Memory namespace 隔离、Agent 只提案不执行副作用、同 Event 串行与隔离 Event 并行、第一条 Fixture Vertical Slice 继续保留。当前仓库中不存在 `agent_runtime/`。
- **建议方向、必须通过实现验证：**`PersonActAgent` 的 ADK 输入/输出适配与 Graph checkpoint；Director、Broadcast 是否也需要同构 Graph；Director Segment Completion；Temporal Binder；Event Recognizer；BroadcastPlan 到 RenderJob 的确定性规划。
- **Open Research：**真实 Prompt、复杂 Memory 排序、Director 自身 latency、跨 Event 共享实体归约、30 分钟领先库存、自适应调度、Character Skill 提取与训练。

结论是：**可以开始开发一期骨架和 Fixture Vertical Slice，但不能宣称完整 Agent Runtime 算法已经设计完或实现完。** 第一个里程碑的目的，是把权限、状态、时间证据和多 Event 调度做成可测试 Trace；不是先追求剧情质量。

## 1. 定位

一期要在本工程新增一个 **Go Agent Runtime Server**，并使用 Eino ADK/Compose 承担 Agent 生命周期与认知流程编排。它不承担 Galgame 引擎、素材、镜头、Live2D 或播放器状态；这些仍属于现有 Dynamic Render Plugin 与兄弟目录中的 MyGO/WebGAL。

目录首先按领域对象划分：`agent/` 收纳所有会推理和产生提案的 Agent，`event/` 与 `world/` 是 Agent 外部的运行环境；其中 `agent/personact/` 用自定义 `adk.Agent + compose.Graph` 重写本地 [`generative_agents`](../../generative_agents/) 的认知模块。复用的是 Persona 的认知语义，不是其 Python 框架代码、Maze、浏览器 mailbox 和共享可变 Persona。

```text
Go Agent Runtime Server
  Eino ADK Runner -> PersonActAgent / DirectorAgent / BroadcastAgent
  PersonAct Graph: perceive -> retrieve -> plan -> propose
  Feedback Run: observe_outcome -> conditional reflect
  WorldEvent / Observation / EventSession / Persona / Director / Broadcast
              |
              | BroadcastPlan -> deterministic Render Planner -> RenderJob
              v
Dynamic Render Plugin
  validate -> compile -> queue -> TEMP_SCENE
              |
              v
MyGO / WebGAL
```

## 2. 一期目录

```text
generative_go_world/
├── agent_runtime/
│   ├── go.mod
│   ├── cmd/runtime/main.go
│   ├── internal/
│   │   ├── agent/
│   │   │   ├── models.go
│   │   │   ├── personact/
│   │   │   │   ├── agent.go
│   │   │   │   ├── graph.go
│   │   │   │   └── state.go
│   │   │   ├── director/
│   │   │   │   └── agent.go
│   │   │   ├── broadcast/
│   │   │   │   ├── agent.go
│   │   │   │   └── render_planner.go
│   │   │   └── memory/
│   │   │       ├── record.go
│   │   │       ├── store.go
│   │   │       └── retriever.go
│   │   ├── event/
│   │   │   ├── event.go
│   │   │   ├── session.go
│   │   │   ├── scheduler.go
│   │   │   └── recognizer.go
│   │   ├── world/
│   │   │   ├── world.go
│   │   │   ├── models.go
│   │   │   ├── location.go
│   │   │   ├── perception.go
│   │   │   ├── temporal.go
│   │   │   ├── validator.go
│   │   │   ├── committer.go
│   │   │   └── ledger.go
│   │   └── rendergateway/gateway.go
│   └── testdata/
├── extensions/dynamic-render/
└── wiki/
```

目录按所有权表达边界：

- `cmd/runtime/main.go` 是装配入口；Event 调度保持在 `internal/event`，不塞进 Agent Graph；
- `internal/agent/` 收纳具体 `adk.Agent` 与共用 Memory；Eino ADK/Compose 提供 Runner、AgentEvent、Graph、模型/工具接口、Prompt 模板、Callback、取消和可选 checkpoint，不再自研同类框架；
- `internal/agent/models.go` 只保存跨 Agent 共用的 GenerationTrace、AgentOutcome 与 MemoryWriteIntent；World 接受的 Proposal/Draft 类型由不反向依赖 Agent 的稳定边界模块持有，Phase 0 必须先用编译测试证明 Go package 不成环；
- `agent/personact/agent.go` 实现 `adk.Agent` 薄适配，`graph.go` 只定义 Character 的认知 Graph；Graph 产出 `ActionProposal` 后立即结束；
- Director/Broadcast 同样实现 `adk.Agent`，但可根据复杂度使用单次 Chain、规则实现或专属 Graph，不为“统一”而复制 PersonAct 拓扑；
- `agent/memory/` 是所有 Agent 共用的记忆能力，提供基础 Record、Store 和 Retrieve；共用模块不等于共享数据，每个 Agent 使用独立 namespace；
- `agent/personact/` 沿用 Generative Agents 的认知阶段语言；Scratch/KnownPlace 成为明确的 Go state，Prompt 内容按 Agent 隔离并由 Eino ChatTemplate 渲染；
- `agent/director/` 先提供 Segment Completion，一期可使用 Fixture/Stub；
- `agent/broadcast/` 将已提交 Event revision 转成 `BroadcastPlan`，一期先用规则策略；确定性 Render Planner 再生成 `RenderJob`；
- 三类 Agent 的 `state/scratch` 保存当前工作状态，不属于长期 Memory：Persona 是当前动作，Director 是活动线程/待补完 Segment/干预预算，Broadcast 是覆盖游标/连续性/Buffer；
- `event/` 管理 EventSession、WorldEvent、Event 内 Scheduler、Recognizer 和生命周期，不包含 Agent 内部记忆；
- `world/` 替代 Maze 的环境职责，保存语义地点、对象、提交版本、Ledger、PerceptionProjector、Temporal Binder、Validator 和 Committer，不包含 tile、碰撞和寻路；`location.go` 维护稳定地点身份、版本化 Fact/Info 和对 WorldEvent Ledger 的查询索引；
- `agent/broadcast/render_planner.go` 确定性地把 `BroadcastPlan + committed Event Log` 转成 `RenderJob`，不调用模型、不创造世界事实；
- `rendergateway/gateway.go` 只负责把 RenderJob 发给 Node Dynamic Render Plugin；
- 运行存储通过接口注入。Fixture/Golden Trace 可以落 JSON；正式一期存储优先评估 SQLite，而不是复制原型的多份 JSON 全量重写。具体 driver 在 Phase 1 spike 后冻结。

一期不增加 `domain / application / infrastructure / services / managers / contracts` 等通用分层，也不创建游戏设计、素材或播放器模块。边界直接由领域 package、Go struct 与 JSON 契约表达；当真实复用点出现后再拆。

### 2.1 依赖方向

```text
cmd/runtime
  -> event
  -> agent
  -> world
  -> rendergateway

event
  -> 调用 agent 获取 Proposal
  -> 调用 world 校验并提交

agent/personact
  -> 只消费 PerceptionFrame / 输出 ActionProposal
agent/director
  -> 只消费 Snapshot + Proposal / 输出 DirectorResolution
agent/broadcast
  -> 只消费 committed Event / 输出 BroadcastPlan
  -> 内部确定性 render_planner 消费 Plan + Event Log / 输出 RenderJob
agent/memory
  -> 提供通用 MemoryRecord / MemoryStore / Retriever
  -> 按 agent_id + namespace 隔离数据
Eino ADK / Compose
  -> Runner 统一 Agent 生命周期、事件流、取消与可选 checkpoint
  -> PersonAct Graph 编排 perceive -> retrieve -> plan -> propose
  -> committed outcome 通过独立 Feedback Run 触发 observe_outcome -> reflect

world
  -> 不依赖 agent 内部状态
  -> 不依赖 MyGO/WebGAL
  -> LocationModel 只接受 Committer 提交的版本化变更

rendergateway
  -> 只发送 RenderJob 并接收播放状态
```

禁止 `agent/personact` 持有其他 Agent 的 live object，禁止 `agent/director` 直接写 Ledger，禁止 `agent/broadcast` 直接控制播放器。

为避免包循环，数据所有权进一步约束为：

- `world/models.go` 持有跨边界的 `PerceptionFrame / ActionProposal / DirectorResolution / SegmentDraft / WorldSegment`；Character 返回 Proposal，Director 只返回 Resolution，SegmentDraft 由 World/Runtime 的 Assembler 内部构造；`agent/models.go` 只放 GenerationTrace、AgentOutcome 与 MemoryWriteIntent，避免 `world <-> agent` 循环依赖；
- `agent/director` 返回 `DirectorResolution`，不引用 Segment Assembler 或具体存储实现；
- `agent/personact / director / broadcast` 依赖 `agent/memory` 的公开接口，`agent/memory` 不依赖具体 Agent；
- 三类 Agent 实现统一 `adk.Agent` 边界，但不共享一张万能 Graph；PersonAct、Director、Broadcast 的内部拓扑分别装配；
- `event` 定义 `EventSession / WorldEvent` 并驱动单 Event 轮次；
- `world` 持有 `WorldState`，消费 Proposal/Draft 并生成 committed WorldEvent；
- `event/scheduler.go` 可以调用 Agent 与 World 的公开接口；`world` 不反向调用 Scheduler；
- `agent/broadcast/render_planner.go` 消费 `BroadcastPlan` 与已提交 Event，不读取 Agent 私有 Memory；
- `cmd/runtime/main.go` 只负责依赖装配与进程生命周期。

Go package 不建立聚合式万能 facade；从拥有该契约的具体 package 显式导入，避免循环依赖和类似原版 `import *` 的隐式调用。

### 2.2 通用 Memory 的最小边界

所有 Agent 共用同一个 Memory API，但每次调用必须绑定调用者身份：

```go
memory := store.Scope(agentID, namespace)
memory.Append(ctx, record)
memory.Get(ctx, memoryID)
memory.Recent(ctx, limit)
memory.Search(ctx, tags, limit)
```

一期 `MemoryRecord` 只需要：

```text
memory_id / agent_id / namespace / memory_type
created_at / content / tags / source_refs / metadata
```

`scope()` 返回绑定身份的 MemoryView，后续调用不能临时切换到其它 namespace。基础 Store 负责 namespace 校验、append-only、持久化和稳定排序；不负责决定“什么值得记住”或如何打 relevance/importance 分。各 Agent 的 perceive/retrieve/reflect strategy 决定写入类型、检索条件和结果解释。

建议 namespace：

```text
persona/{persona_id}/episodic
persona/{persona_id}/semantic
persona/{persona_id}/reflection
director/threads
director/completions
director/failures
broadcast/coverage
broadcast/continuity
```

跨 namespace 默认禁止。角色需要知道 Director 已提交的外部事件时，只能通过 WorldEvent -> PerceptionProjector -> Observation 进入 Persona Memory；不能直接读取 `director/*`。

## 3. 与 Generative Agents 的映射

### 3.0 已核对的真实源码行为

以下结论来自本地 [`generative_agents`](../../generative_agents/) 源码，而不是只依据论文图：

- [`ReverieServer.start_server()`](../../generative_agents/reverie/backend_server/reverie.py) 先从前端环境文件同步所有 Persona 的 tile/Maze Event，再按 `self.personas` 字典顺序逐个调用 `persona.move()`，最后用固定 `sec_per_step` 推进时间；文件 mailbox、固定步长和顺序副作用都不能照搬。
- [`Persona.move()`](../../generative_agents/reverie/backend_server/persona/persona.py) 只是 `perceive -> retrieve -> plan -> reflect -> execute` 的门面；同名实现通过 `import *` 来自 `cognitive_modules/`。新 Runtime 必须显式依赖和显式返回，不能复制这层隐式调用。
- [`perceive.py`](../../generative_agents/reverie/backend_server/persona/cognitive_modules/perceive.py) 实际同时承担空间学习、同 arena 事件收集、距离 Top-K attention、retention 去重、embedding/poignancy 和记忆写入。MyGO 只保留角色侧注意力、新颖性和记忆固化；硬可见性与 affordance 由 World Projector 提供。
- [`plan.py`](../../generative_agents/reverie/backend_server/persona/cognitive_modules/plan.py) 的对话反应会直接修改另一个 Persona 的 Scratch，造成字典顺序先手；本项目禁止任何 Agent 持有或修改另一 Agent 的 live state。
- [`reflect.py`](../../generative_agents/reverie/backend_server/persona/cognitive_modules/reflect.py) 由累计 poignancy 阈值触发，且在原版 `execute()` 前调用；所以 PersonAct 只保留条件式 reflect 分支，不把原顺序误写成所有 Agent 的固定“执行后复盘”。
- [`execute.py`](../../generative_agents/reverie/backend_server/persona/cognitive_modules/execute.py) 是地址解析、寻路和逐 tile 移动适配器；本项目不迁移它，只保留“规划结果与实际世界副作用分离”的思想。
- [`associative_memory.py`](../../generative_agents/reverie/backend_server/persona/memory_structures/associative_memory.py) 的 `ConceptNode`、evidence/filling、recency/relevance/importance 思路可借鉴；移植时必须修复 keyword 写入 lowercase、读取未同样归一化的问题，并把 retrieval 的 `last_accessed` 更新改成显式副作用。

因此，“沿用 Generative Agents 文件架构”是为了保留可读的认知边界，不代表复制其 Maze、Scratch 大对象、Prompt 巨型文件、固定 Tick 或串行共享状态。

| Generative Agents | 一期 Runtime | 处理方式 |
|---|---|---|
| `reverie.py` | `cmd/runtime + event/scheduler.go` | 重写世界循环；不使用浏览器文件 mailbox，也不把它塞进 Agent Graph |
| `maze.py` | `world/world.go + location.go` | 用版本化 Location/Fact/Info 与语义对象替换 tile 世界 |
| `path_finder.py` | 无 | 一期删除几何寻路职责 |
| `persona/persona.py` | `agent/personact/agent.go` | 自定义 `adk.Agent`；输入适配为 PerceptionFrame，输出 ActionProposal |
| `cognitive_modules/*` | `agent/personact/graph.go` 的 typed nodes | 用 Compose Graph 显式编排，不复制 `import *` facade |
| `perceive.py` | PersonAct `perceive` node | 不扫 Maze，只做注意力、新颖性和 MemoryWriteIntent |
| `retrieve.py` | PersonAct `retrieve` node | 编排当前感知上下文；通过通用 Retriever 查询私有 namespace |
| `plan.py` | PersonAct `plan/propose` node | 从 Frame.affordances 选择意图，不读其他 Persona live state |
| `reflect.py` | PersonAct feedback branch | 保留阈值语义；一期允许 No-op |
| `execute.py` | 无对应 Agent 节点 | Agent 只生成 ActionProposal，具体副作用交给 World |
| `memory_structures/associative_memory.py` | `agent/memory/` | 提炼所有 Agent 可复用的基础 Record/Store/Retrieve，不共享 namespace |
| `memory_structures/scratch.py` | `agent/personact/state.go` | Persona 私有的 typed 当前状态，不做通用 Memory |
| `spatial_memory.py` | PersonAct `KnownPlace` state | 改为语义地点知识；第一阶段可最小实现 |
| `prompt_template/*` | 各 Agent 的版本化模板 | Prompt 内容仍自研，渲染复用 Eino ChatTemplate |
| `gpt_structure.py` | Eino ChatModel/provider | 不迁移旧 SDK wrapper、手写重试和字符串错误 |

原仓库是 Apache-2.0。若后续复制具体源码而不是重新实现接口，必须保留许可证与 NOTICE/归属要求；一期优先参考结构并重写最小逻辑。

### 3.1 成熟组件替代边界

原则是：**框架接管通用执行机制，MyGO 保留决定世界与角色语义的算法。** 不因为原型手写了某项基础设施就再次手写，也不因为 Eino 能编排就把世界权威交给 Agent 框架。

| 原型中的自造能力 | 一期处理 | 保留的 MyGO 责任 |
|---|---|---|
| `Persona.move()` 手写调用链 | 用自定义 `PersonActAgent` + Eino Compose Graph | 节点语义、输入权限、Proposal 契约和终止条件 |
| OpenAI SDK wrapper | 用 Eino ChatModel 与 Eino-ext provider | model policy、调用预算、GenerationTrace 字段映射 |
| `!<INPUT n>!` 模板替换 | 用 Eino ChatTemplate；静态模板可由 Go `embed` 装载 | Persona/Director/Broadcast 的模板内容与版本 |
| 数十个 `run_gpt_prompt_*` 包装函数 | 合并为少量按输出契约组织的 typed model nodes | 每个决策需要哪些证据、哪些字段由代码确定而非交给模型 |
| 手工拼接长历史上下文 | 后续按需使用 ADK history rewrite / summarization / reduction middleware | 长期语义 Memory、evidence ID 和不可删系统约束；上下文摘要不能冒充事实存储 |
| 手工截取 JSON、`split()`/`literal_eval` 解析 | provider structured output + Go typed decode；失败显式返回 | ActionProposal/DirectorResolution/BroadcastPlan Schema 与业务校验；SegmentDraft 只作 Runtime 内部契约 |
| 固定次数、裸异常重试 | 使用 `adk.ChatModelAgent` 时复用其 model retry/failover；自定义 Graph 则只做一层可测试的 provider retry adapter | 哪类错误可重试、语义 repair 上限及其 Trace |
| 模型/工具调用日志 | 用 Eino Callback 和 AgentEvent 采集 | 世界版本、evidence、实际耗时和提交结果关联 |
| 手写 embedding 请求 | 后续使用 Eino Embedder | 何时 embedding、模型版本与费用策略 |
| 手写向量检索 plumbing | 规模需要时接 Eino Indexer/Retriever 与成熟后端 | recency/relevance/importance 融合和角色认知解释 |
| 多份 JSON 全量重写 | Fixture 只保留 Golden JSON；运行存储用事务型 adapter，优先评估 SQLite | namespace、provenance、append-only 与世界提交语义 |
| Django + 文件 mailbox + busy polling | 不迁移；跨进程边界使用 typed HTTP/WebSocket adapter | RenderJob 幂等、状态机与恢复语义 |
| Selenium、地图寻路、Tiled/Phaser 世界 | 不迁移进 Agent Runtime | 无 Maze 的语义 Scene、affordance 与 PerceptionProjector |
| 问卷/脚本式 evaluation | Eino Callback + 可观测后端承担调用采集，Go testing/golden 工具承担回归 | 知识泄漏、角色一致性、Event 因果和自然度的 Golden/Badcase 判据 |

以下能力不能用 Eino checkpoint 或普通 Retriever 偷换：World Snapshot/Ledger、`world_version`、PerceptionProjector 的硬可见性、Event 隔离、Temporal Binder、Validator/Committer、Event Recognizer，以及 `BroadcastPlan -> RenderJob` 的确定性转换。Eino checkpoint 只恢复一次 Agent/Graph 执行；World checkpoint 必须由 World Runtime 自己定义。

### 3.2 Eino 适配注意事项

- Eino 当前没有通用的 `Graph -> adk.Agent` 构造器。`PersonActAgent` 需要实现 `Name / Description / Run`，在 `Run` 内调用编译后的 `compose.Runnable`，再把结果转换为 `AgentEvent`；若一期需要中断恢复，再额外实现 `ResumableAgent`。
- `AgentRunOption` 不会自动变成 `compose.Option`。adapter 必须显式映射 callback、cancel、checkpoint ID 和 PersonAct 专属输入；未映射的 option 不得静默丢失。
- `AgentOutput.CustomizedOutput` 的静态类型是 `any`，Eino 在复制 AgentEvent 时不会深拷贝它。第一期只放不可变值或稳定 JSON envelope，不把可变 `*ActionProposal` 在多个消费者之间共享。
- `adk.NewLoopAgent` 的语义是循环多个完整子 Agent，并共享工作流上下文；它不适合把 `perceive/retrieve/plan/reflect` 各自伪装成 Agent。PersonAct 的认知回环应由内部 Compose Graph 的条件边表达。
- Eino 的 Callback/AgentEvent 提供调用级观测，但不知道 `world_version / evidence_ids / commit_seq`。`GenerationTrace` 仍需显式关联这些领域字段。
- Graph 节点默认按可重试、可恢复来设计：不得在模型或检索节点中直接写 World 或永久 Memory。所有持久副作用都在成功输出后的受控边界执行，并用稳定 ID 幂等。
- 自定义 Graph 不会自动继承 `adk.ChatModelAgent` 的内部 retry/failover 和 model/tool event wrapper。若 PersonAct 的生成节点直接使用 ChatModel component，应只增加一层可测试的公共 adapter；若嵌套 ChatModelAgent，则显式桥接其 AgentEvent。不得复制 Eino 的内部非公开实现。
- transport retry 与语义 repair 必须分开：网络/限流错误由上面的 model 层处理；Schema 不合法或世界约束冲突只能走有上限、可审计的 repair，不能盲目重跑整张 Graph。
- ADK Agent input 以 Message 为中心，而 PersonAct 输入是 typed `PerceptionFrame`。一期通过专属 `AgentRunOption` 或稳定 envelope 传递领域输入；禁止把完整 WorldSnapshot 序列化成用户消息来绕过角色可见性。
- Eino 自带的 sequential/parallel/loop Agent 和 Agent transfer 不能替代 Event Scheduler。特别是全上下文共享会破坏角色私有认知；多 Event 并行必须先经过角色、对象和因果隔离检查。
- Director 不能把 Character 包装成 AgentTool 后自行调用并指挥。由 World/Event Scheduler 决定谁获得行动机会；Director 只消费已经产生的 Proposal 和授权的 WorldSnapshot。
- `perceive`、确定性过滤、tag lookup 和 Proposal 校验优先使用普通 Lambda/组件；第一版只让真正需要生成判断的 `plan/propose` 调模型。不能因为使用 Graph 就把每个节点都变成一次 LLM 调用。
- PersonAct 可使用的 Tool 必须是按角色权限裁剪的只读查询或计算工具；修改 World、写其他 Agent Memory、直接发布 Render 的工具不进入其 ToolSet，行动统一表达为 `ActionProposal`。
- 第一期以非流式结构化 Proposal 为主；ADK 的流式 `AgentEvent` 用于生命周期和诊断，不把半截 token 当成世界事实。只有完整输出通过解析与校验后才能进入 Director/Commit 链。
- 编译后的 Runnable 不保存某个角色或某次运行的可变字段；运行态由 `WithGenLocalState` 等每次调用生成，并以 `go test -race` 验证隔离 Event 并行时没有状态串扰。
- 若启用 Compose/ADK checkpoint，自定义 state 类型必须使用稳定注册名并制定 schema 迁移策略；不能把 Go 类型重命名当成无成本重构。
- Prompt/context summarization 只是 token 管理，不是长期记忆压缩；原始 MemoryRecord、source_refs 和 committed evidence 仍需保留，不能只剩一段模型摘要。
- 缓存键至少包含 agent/character skill 版本、Prompt 版本、model/provider、输入 evidence IDs 和 `based_on_world_version`；不能只按自然语言文本缓存，否则会跨角色或跨世界版本复用错误结果。
- Golden Trace 必须固定模型、Prompt/Skill 版本、seed（若 provider 支持）、Fixture latency 与输入 evidence；真实模型回归不能只断言整段文本完全相等，应分别检查 Schema、不变量、角色一致性与关键事实。

### 3.3 一期依赖预算

一期先把依赖限制为：

```text
必须
  github.com/cloudwego/eino/adk
  github.com/cloudwego/eino/compose
  github.com/cloudwego/eino/schema
  Go 标准库 context / encoding/json / net/http / testing

Phase 4 按需
  golang.org/x/sync/errgroup

Phase 1 若启用非 ASCII tag 检索
  golang.org/x/text/cases + unicode/norm

测试按需
  github.com/google/go-cmp/cmp

接真实模型时再加入
  一个 github.com/cloudwego/eino-ext/components/model/* provider
  provider 支持的 structured output / JSON Schema 适配

规模证明需要时再加入
  Eino Embedder / Indexer / Retriever 的具体实现
  SQLite driver 或外部向量存储
```

不同时引入 LangChain/LangGraph，也不把本地 `agent_core` 整体作为依赖；可以参考其 `Workflow -> Runnable -> adk.Agent` 与 provider 适配做法，但 MyGO 只依赖所需的上游 Eino 组件。Eino 版本在 Phase 0 锁定一个稳定 tag，并以契约测试保护 `Agent/Runner/AgentEvent/checkpoint` 接口，避免浮动版本或 alpha API 直接进入 Runtime。

## 4. Runtime 主循环

一期采用已经确认的调度策略：**同一 Event 内串行，相互隔离的 Event 并行。**

```go
func (r *Runtime) runWorld(ctx context.Context) error {
    var group errgroup.Group
    for _, eventID := range r.isolatedRunnableEvents() {
        eventID := eventID
        group.Go(func() error { return r.runEvent(ctx, eventID) })
    }
    return group.Wait()
}

func (r *Runtime) runEvent(ctx context.Context, eventID EventID) error {
    for r.events.IsRunnable(eventID) {
        actorID := r.scheduler.NextActor(eventID)
        snapshot := r.world.Snapshot(eventID)
        frame := r.projector.Project(snapshot, actorID)
        proposal, characterTrace, err := r.runPersonAct(ctx, r.personas[actorID], frame)
        if err != nil { return r.failTurn(eventID, characterTrace, err) }

        if proposal.Kind == NoOp {
            r.recordDecision(characterTrace, proposal)
            r.scheduler.Yield(eventID, actorID, proposal.NextWakeup)
            continue
        }

        resolution, directorTrace, err := r.runDirector(ctx, r.director,
            CompletionRequest{Snapshot: snapshot, Proposal: proposal, Trace: characterTrace})
        if err != nil { return r.failTurn(eventID, directorTrace, err) }

        draft, diagnostics := r.segmentAssembler.Assemble(
            snapshot, eventID, proposal, resolution, directorTrace)
        if diagnostics.HasInternalErrors() { return r.failTurn(eventID, directorTrace, diagnostics) }
        if diagnostics.HasDirectorErrors() { return r.repairDirector(eventID, diagnostics) }
        plan, diagnostics := r.validator.Validate(snapshot, proposal, draft)
        if diagnostics.HasErrors() { return r.rejectTurn(eventID, diagnostics) }

        segment := r.committer.Commit(snapshot, plan)
        events := r.recognizer.Apply(segment)
        r.observeOutcome(ctx, r.personas[actorID], segment, characterTrace)
        r.observeOutcome(ctx, r.director, segment, directorTrace)

        plan, broadcastTrace, err := r.runBroadcast(ctx, r.broadcast,
            BroadcastRequest{Events: events, ViewerCursor: r.viewerCursor, Buffer: r.buffer})
        if err != nil { return r.failBroadcast(eventID, broadcastTrace, err) }
        job := r.renderPlanner.Plan(plan, events)
        if err := r.renderGateway.Publish(ctx, job); err != nil { return err }
        r.observeOutcome(ctx, r.broadcast, job, broadcastTrace)
    }
    return nil
}
```

这里的函数名只是设计骨架，不要求逐行照抄。入口函数应保持一眼可见的主流程，不再把 mailbox、坐标同步、认知调用、世界提交和文件写出混成一个大循环。`GenerationTrace` 必须记录每次 Agent/工具调用的 `started_at / returned_at / elapsed / based_on_world_version / input_evidence / raw_output / parsed_output`，否则 Actual Runtime、失败归因和可重放都没有证据。

一期 Fixture Slice 暂时冻结以下运行规则，避免开发者被所有 Open Question 阻塞：

- 单 Event 每次只调度一个 Character；有效 Proposal 立即进入一次 Director Completion、绑定、校验和提交，不等待 Event 内所有 Character；
- `no_op` 不调用 Director、不产生 WorldEvent，只落 Decision Trace 并要求非空 `next_wakeup`；
- timeout 作为失败 Trace 结束本次机会，不把未返回内容提交为事实，也不自动生成“角色沉默”；
- System/Tool/Player Input 使用同一 candidate -> validate -> commit 路径，但第一条 Slice 只实现 Fixture System Input；
- 两个 Event 仅在参与角色、对象/资源和因果引用集合互斥时并行；第一条 Slice 遇到交集直接拒绝启动并返回诊断，不先实现 barrier/迁移；
- Director 修补循环一期上限为 0：Fixture Draft 校验失败就显式失败，先证明诊断可定位；真实模型接入时再引入有限 repair；
- Temporal Binder 第一期只绑定注入的可控测试耗时，不试图解决 Director latency 递归；完整算法继续保持 Open Research。

### 4.1 三类 Agent 的共享认知协议

三类 Agent 共享阶段语言，但不共享 Persona 的具体函数实现：

```text
提案前：observe/perceive -> retrieve -> plan -> propose
Runtime：validate -> commit / render
提交后：observe_outcome -> reflect
```

原版 `reflect()` 位于 `execute()` 前，并由累计 poignancy 阈值触发；它并不是“当前动作执行后复盘”。因此不能机械照搬源码顺序作为所有 Agent 的固定状态机。通用协议允许每种 Agent 在提交后、定期或满足阈值时触发 reflect。

| 阶段 | Persona | Director | Broadcast |
|---|---|---|---|
| perceive | 角色 Observation、待回应和 affordance | WorldSnapshot、Character Proposal、线程状态 | committed Event、Viewer Cursor、Buffer |
| retrieve | 人物经历、关系、信念、承诺 | Narrative Thread、补完历史、失败诊断 | 已覆盖区间、连续性、未揭示信息 |
| plan | 下一动作、台词或等待 | Segment Completion 或未来 Stimulus | Event 选择、Temporal Projection、镜头计划 |
| propose | `ActionProposal` | `DirectorResolution / DirectorProposal` | `BroadcastPlan` |
| reflect | 更新人物理解、关系与目标 | 复盘线程推进和干预效果 | 复盘重复、断裂和覆盖效果 |

不将 `execute()` 放入通用 Agent 基座。Agent 只负责 `propose()`：Persona 不执行世界动作，Director 不提交 Ledger，Broadcast 不直接控制 WebGAL。真正副作用分别由 World Committer 和 Render Gateway 执行。

第一期不建立拥有大量方法的 `BaseAgent` 层。Persona、Director、Broadcast 对外统一实现最小 `adk.Agent` 协议；它们内部按实际复杂度分别选择 Graph、Chain 或规则实现。`PersonActAgent` 的 Graph 负责角色认知节点与有界回环；ADK Runner 负责生命周期、事件流、取消和可选 checkpoint。

`PersonActAgent` 的一次 Decision Run 只到 `ActionProposal` 为止：

```text
START -> perceive -> retrieve -> plan -> propose -> proposal_check
                    ^                         |
                    |-- need_more_context ----|
                                              |-- ready / no_op -> END
```

`proposal_check` 只允许有界的补检索或结构修复，并受 max iteration、timeout、cancel 和错误终止保护。它不能把 Proposal 当成已经发生的事实，也不能形成 `propose -> perceive` 的自循环。World Runtime 完成 Director、Validate、Commit 后，再以独立 Feedback Run 将 `CommittedOutcome` 交给 `observe_outcome -> conditional reflect`。第一期不使用 `adk.NewLoopAgent` 把每个认知阶段伪装成子 Agent。

### 4.2 第一阶段真正困难的两部分

#### Prompt Contract

难点不是先写漂亮 Prompt，而是让每个阶段只看到被授权的信息，并稳定输出可校验对象：

- Persona Prompt 只能看到自身 PerceptionFrame、私有 Memory 与 affordance；
- Director Prompt 可看世界快照和 Character Proposal，但输出必须是窄 DirectorResolution，不得返回 Proposal Event 的权威字段，也不得直接写 Memory/Ledger；
- Broadcast Prompt 只看 committed Event、Viewer Cursor 与 Buffer，输出 BroadcastPlan；
- 所有 Prompt 必须记录 template version、model、输入 evidence IDs、原始输出和 repair 次数。

第一阶段先用 Fixture 固定输出把调度跑通，再逐个替换 Prompt；否则无法区分“流程错”与“模型输出错”。

Prompt/strategy 本身不持有 Store 写权限，只能返回 `MemoryWriteIntent`。PersonAct adapter/Runtime 只在两个受控检查点持久化：Decision Run 成功后，将该 Agent 确认注意到的 Observation 以稳定 ID 幂等写入**当前 Agent 自己**的 namespace；Feedback Run 只写入已提交/已接受的结果。两者都不能写 World Ledger 或其它 Agent 的 namespace。Graph 节点不直接落库，避免重试或恢复造成重复副作用。

#### Runtime Scheduling

调度难点包括：Event 内谁下一步行动、Event 间能否并行、Agent latency、`no_op` 休眠、跨 Event 共享角色 barrier，以及何时调用 Director/Broadcast。第一期只实现：

```text
Event 内稳定轮次 + addressed_to_me 优先
隔离 Event 使用 goroutine + `errgroup` 并行
每次有效 Proposal 后 Director Completion
每次 committed revision 后 Broadcast Projection
no_op 使用 next_wakeup 防空转
```

复杂优先级、动态预算、超时降级和 30 分钟 Buffer 调度在骨架稳定后实验。

### 4.3 Event 隔离条件

只有以下条件都满足，Event 才能并行：

- 没有共享角色；
- 没有共享对象或资源；
- 没有必须等待的跨 Event 因果；
- 当前没有角色切换 Event 的迁移事务。

若任一条件不满足，Runtime 建立 barrier、迁移 ownership，或合并 Event；不能假装隔离。

### 4.4 Event 内轮次

Scheduler 决定谁获得下一次机会，Persona 决定拿到机会后做什么：

```text
act | utter | respond | wait | no_op
```

- 被直接点名时，Frame 携带 `addressed_to_me / pending_response`；
- 这只提高回应优先级，不强迫回复；
- 纯 `no_op` 只写 Decision Trace、让出游标并带下一次唤醒条件；
- 有世界意义的主动等待使用 `wait` Proposal，并可提交为 WorldEvent；
- Persona 不得直接修改另一个 Persona 的 Scratch。

## 5. 感知与 Event 边界

> `perceive` 的输入不是直接来源于 Director，而是来源于已经提交、并按角色隔离后的 Observation。Director 只是 WorldEvent 的来源之一。

```text
Character Proposal
Director Stimulus / Segment Completion
System / Tool / Player Input
                │
                ▼
       Validator / Committer
                │
                ▼
Committed WorldSegment / WorldEvent
                │
                ▼
       PerceptionProjector
                │
                ▼
PerceptionFrame[每个 Character]
                │
                ▼
Persona.perceive()
→ retrieve → plan → propose ActionProposal

提交结果后或达到该 Agent 的触发条件
→ observe_outcome / reflect
```

Event 认知必须分为：

```text
WorldEvent       客观事实
PerceptCandidate 有机会感知的字段
Observation      实际注意到的内容
Memory           如何保存和主观解释
```

`PerceptionFrame` 同时携带 `visible_location_facts` 与 `affordances`，替代原版 Maze spatial memory 给 plan 提供的地点/物体选择。它不能携带完整 LocationView；只有 Projector 判定可见的 Fact/Info 才能进入角色输入。地点完整模型见[地点 World Model](location-world-model.md)。

## 6. 第一期只定义的最小数据

第一阶段先用 Go struct 表达内部契约，不先造独立 Schema 工程；RenderJob 在 Go → Node 边界处同时执行 Go 编码校验与 Node 侧 JSON 校验。

### `GenerationTrace` / `AgentOutcome`

```text
generation_id / agent_id / agent_kind / event_session_id
based_on_world_version / started_at / returned_at / elapsed_ms
input_evidence_ids / template_version / model_id
raw_output / parsed_output / status / error / repair_count

AgentOutcome
  generation_id / accepted / committed_world_version
  produced_event_ids / validation_diagnostics / side_effect_status
```

`GenerationTrace` 是事实证据，不是 Memory；`AgentOutcome` 由 Runtime 在校验、提交或投递以后生成，再交给 `observe_outcome -> reflect`。

### `PerceptCandidate` / `Observation`

```text
candidate_id / source_event_id? / target_agent_id
source_location_id / source_fact_refs / source_info_refs
channel / visible_fields / salience / mandatory

observation_id / source_event_id? / target_agent_id
observed_at_world_version / content / confidence
source_fact_refs / source_info_refs / visible_fields / provenance
```

Projector 先做硬可见性裁剪得到 PerceptCandidate；Persona `perceive()` 再做注意力、新颖性与主观解释，得到 Observation 并决定是否写入私有 Memory。

### `EventSession` / `WorldEvent`

```text
EventSession
  event_session_id / participant_ids / location_id / status
  owned_entity_ids / causal_dependency_ids / scheduler_cursor
  next_wakeup_at / head_log_seq

WorldEvent
  event_id / event_revision / event_session_id
  world_version / world_time / event_type
  participants / location_id / facts
  perceptual_footprint / evidence_ids
```

`EventSession` 是一个可调度的互动容器；`WorldEvent` 是从已提交 Segment 识别出的客观语义事实。二者不能共用 ID 或生命周期。

### `Location` / `LocationFact` / `LocationInfo` / `LocationView`

```text
Location
  location_id / canonical_name / aliases / kind / parent_location_id / timezone

LocationFactRevision
  fact_id / revision / location_id / fact_key / value / operation
  previous_revision / effective_world_version / disclosure / discovery_channels
  source_kind / source_ref / evidence_ids / commit_seq

LocationInfoRevision
  info_id / revision / location_id / info_kind / subject_ids / predicate / object_refs / summary
  valid_from / valid_until / recurrence / disclosure / discovery_channels / operation
  previous_revision / effective_world_version / source_kind / source_ref / evidence_ids / commit_seq

LocationView
  based_on_world_version / at_world_time / location
  active_facts / active_info / active_event_refs / recent_event_refs
```

`LocationFact` 是世界事实，缺失表示 unknown 而不是 false；只有带 expected revision 的正式提交才能 replace/retire。`LocationInfo` 表示当前有效的可发现信息，不证明某次活动已经发生。Location 只保存 Event 引用，Event 正文仍由唯一 WorldEvent Ledger 持有。详细状态机见[地点 World Model](location-world-model.md)。

### `PerceptionFrame`

```text
agent_id / event_session_id / based_on_world_version
current_location_id / local_state / visible_location_facts
candidates / pending_responses / affordances
```

### `ActionProposal`

```text
proposal_id / agent_id / event_session_id
based_on_world_version / kind / target_ids
location_id / content / evidence_ids / next_wakeup
```

### `DirectorResolution` / `SegmentDraft`

```text
DirectorResolution
  elapsed_ms / outcome_summary / creative_external_events
  entity_state_changes / session_intent

SegmentDraft（仅 Runtime 内部）
segment_draft_id / generation_id / based_on_world_version
character_proposal_ids / temporal_constraints / action_transitions
object_deltas / location_fact_changes / location_info_changes
bridge_events / event_candidates
perceptual_footprints / unresolved_at_end / evidence_ids

TemporalConstraint
  constraint_id / relation(before|after|overlaps|continues)
  subject_ref / object_ref / lower_bound_ms / upper_bound_ms / evidence_ids
```

Director 只返回 Resolution 的创意判断；Segment Assembler 从 Snapshot、Session、Proposal 与 Director trace 确定性构造 SegmentDraft，注入版本、绝对时间、Wave 末端 External Event、Proposal 来源关系与 `move` 位置变化。Director 不分配事件键、事件 offset、局部引用或 commit sequence，也不直接写 Ledger。

### `WorldSegment` / `ValidationDiagnostic`

```text
segment_id / generation_id / based_on_world_version / world_version
world_time_start / world_time_end / measured_elapsed_ms
ordered_facts / state_deltas / location_revision_refs
event_evidence / commit_seq

diagnostic_id / code / severity / object_refs
message / violated_invariant / evidence_ids / repairable
```

Temporal Binder 产生待校验的时间绑定；Validator 返回结构化诊断；只有 Committer 能分配新 `world_version / commit_seq` 并追加 Ledger。

### `BroadcastPlan`

```text
broadcast_plan_id / event_session_id / based_on_world_version
source_log_seq_start / source_log_seq_end / projection_mode
camera_viewpoint / reveal_scope / transition
target_render_duration_ms / artistic_reason / evidence_ids
```

Broadcast Agent 只决定观看投影。确定性 Render Planner 校验 source range 与 evidence 后，才把 Plan 和已提交 Event Log 转成 RenderJob。

### `RenderJob`

```text
schema_version / producer_id / world_id / runtime_session_id
render_id / event_session_id / event_revision / attempt_id
based_on_world_version / log_seq_start / log_seq_end
evidence_transaction_ids / title / location / characters
structured_beats / estimated_play_ms / content_hash
```

Plugin 必须基于 canonical structured input 重新计算并核对 `content_hash`，不能只信任 producer。状态回传至少携带 `attempt_id / render_id / event_session_id / status / error / viewer_cursor`，使 Runtime 能区分投递接受、编译完成、近似开始、完成、失败和未知。

## 7. Dynamic Render Plugin 的最小改动

现有插件已经支持 Beat 校验、DSL 编译、Render Queue、黑屏、Event 切换、`TEMP_SCENE` 注入和播放完成近似确认。第一期只增加一条 Runtime 投递边界：

1. 内部新增 `appendRenderJob(job)`；
2. `POST /api/runtime/render-jobs` 接收一个不可变 RenderJob；
3. 同 `render_id + content_hash` 重试幂等，同 ID 不同 hash 拒绝；
4. 返回 `accepted / validation_error / ready / playing / completed / failed / unknown`；
5. 当前 `timeline.json` 保留为 Fixture Adapter，内部也调用 `appendRenderJob()`；
6. 保存最小 played cursor，防止进程重启后全部重播。

WorldEvent 不直接进入 WebGAL Plugin。Broadcast 位于 Go Runtime 内输出 BroadcastPlan；确定性 Render Planner 把 `BroadcastPlan + committed Event Log` 转成 RenderJob，再由 RenderGateway 发送。

一期边界固定为四个端口：

```text
CommittedEventFeed   Runtime 内部：只向 Broadcast/Render Planner 暴露已提交 Event Log
RenderJobIngress     Go -> Node：追加不可变 RenderJob，校验版本、顺序与幂等
RenderStatusSink     Node -> Go：回传 accepted/ready/playing/completed/failed/unknown
ViewerCursorStore    Render 侧：持久化 event_id、last_viewed_log_seq、render_id、attempt 和终态
```

当前插件仍只在启动时读整份 `timeline.json`，`revision` 只检查为正整数，active/played 状态也只在进程内存中。Phase 5 必须修改 `DynamicTimelineRuntime` 本身的 append/idempotency/recovery，而不是只在 `server.mjs` 表面增加一个 POST 路由。

## 8. 第一条 Vertical Slice

第一阶段使用 Fixture/Stub 决策，不接复杂 Memory 检索算法和 Prompt；但通用 Memory 基础读写与 namespace 隔离必须是真实实现：

```text
1. 建立 EventSession A：Anon / Soyo 在咖啡店
2. 建立 EventSession B：Tomori 独处
3. Projector 从 Event A 的 committed snapshot 为 Anon 生成 PerceptionFrame；Event B 内容不可见
4. Eino Runner 调用 Fixture `PersonActAgent`，输出 utter：“轮到我们了，要这个吗？”并落 Character GenerationTrace
5. Eino Runner 调用 Director Stub Agent，输出 DirectorResolution 并落 Director GenerationTrace
6. Segment Assembler 由 Snapshot、Session、Proposal、Resolution 与 trace 身份构造内部 SegmentDraft；Validator 校验后由 Committer 追加 WorldSegment
7. Event Recognizer 形成/更新 WorldEvent；Projector 只为 Soyo 准备带 addressed_to_me 的 PerceptionFrame，Tomori 无候选 Observation
8. `observe_outcome` 只把 Anon 与 Director 的已提交结果写入各自 namespace；不能替尚未运行的 Soyo 决定她注意到了什么
9. Scheduler 选中 Soyo；Soyo 的 `perceive` 决定注意该 direct interaction，PersonAct Graph 才把 Observation 写入 persona/soyo namespace，并输出 respond；随后重复 completion -> bind -> validate -> commit
10. Broadcast Rule 通过标准 `adk.Agent` 边界输出 BroadcastPlan；Render Planner 生成两个 dialogue beats 和不可变 RenderJob
11. `observe_outcome` 在 broadcast namespace 记录已接受的覆盖范围，RenderGateway POST RenderJob
12. Dynamic Render 返回 accepted/ready/playing/completed，WebGAL 实际播放
```

并行验收：A 和 B 的 Agent Runtime 同时推进，任一 Event 失败不阻塞另一个；播放器仍只有一个，根据玩家选择消费相应 Render Queue。

## 9. 明确延期

- Memory 的复杂 recency/relevance/importance 排序公式；一期仍实现通用 MemoryRecord、namespace 隔离、append/read/recent 与 provenance；
- Reflection 阈值和 Prompt；
- Character Skill 自动提取与微调；
- Director 长期目标函数、RL 与复杂刺激策略；
- Broadcast 智能选镜与蒙太奇；
- 精细资源/物理模拟；
- 自适应 30 分钟 Buffer；
- 多节点、高可用和复杂鉴权；
- Sentence 级断点续播。

不延期的基础设施：Eino `adk.Agent`/Runner 接线、PersonAct Graph 骨架、Memory namespace 和强类型输出校验。真实 Prompt/模型接入与三类 Agent 的复杂检索、反思和策略算法延期；通用执行机制不得再以自研 Loop 重复实现。

这些只保留函数/模块位置，不提前填充抽象和字段。

## 10. 验收标准

- 不启动 WebGAL 时，Runtime 可以独立推进两个 Event 并落 append-only Event Log；
- 同 Event 内上一角色提交后，下一角色才通过 PerceptionFrame 感知；
- 两个隔离 Event 可以并行，存在共享角色时拒绝并行；
- Director 不能直接写 Persona Memory；
- Persona、Director、Broadcast 复用同一 MemoryStore 接口，但默认不能跨 namespace 读取；
- 纯 `no_op` 不产生 WorldEvent，显式 `wait` 可以；
- 同一 RenderJob 重试不重复入队，冲突内容被拒绝；
- WebGAL 实际播放增量 Render 并回传状态；
- 无 Ready Render 时黑屏，切换 Event 不修改世界；
- 现有静态 `dev/serve` 与 Dynamic Render 测试保持通过。

## 11. 按依赖排序的开发 Plan

### Phase 0｜保护现有底座

- 记录现有 14 项 Node 测试基线；
- 初始化独立 Go module，锁定稳定的 Eino ADK/Eino-ext 版本；使用标准库 `testing` 建立单元测试，不引入另一套 Agent 框架；
- 增加最小 Eino contract test，固定 `adk.Agent / Runner / AgentEvent / CustomizedOutput` 和 Graph branch 的当前行为，版本升级必须先过该测试；
- 不修改 WebGAL Bundle、`assets/` 或原静态播放入口；
- 所有时钟、UUID、Fixture Agent 输出和随机源可注入，保证 Golden Trace 可重放。

完成条件：现有 `npm test` 仍通过，`go test ./...` 的 Runtime 测试骨架可以独立运行。

### Phase 1｜契约、Memory 与 append-only 存储

- 实现第 6 节 Go struct、稳定 JSON 序列化与 schema version；
- 实现 `Location / LocationFactRevision / LocationInfoRevision / LocationView`、Fact key registry 和按 `location_id + commit_seq` 的 Event 查询；
- 实现 `MemoryStore.Scope(agentID, namespace)` 与 namespace 拒绝测试；先对 JSONL 与 SQLite 做小型 spike，再锁定一期持久化 adapter；
- 实现 World Ledger/Generation Trace append 与重放读取；
- 修复从 Generative Agents 借鉴检索时的 keyword 大小写不对称；若支持非 ASCII tag，使用 `golang.org/x/text/cases` 与 `unicode/norm` 统一归一化，不手写 case fold；所有检索副作用显式记录。

完成条件：同一输入序列化稳定；跨 namespace 读取失败；地点 Fact/Info 的 CAS 与历史查询正确；重启后能重放相同 snapshot 和 LocationView。

### Phase 2｜Eino ADK 接线、PersonAct Graph 与 Fixture Agents

- 实现 `PersonActAgent` 的 `adk.Agent` adapter，并以 typed Compose Graph 编排 `perceive -> retrieve -> plan -> propose`；
- 使用 ADK Runner、AgentEvent、Callback 和 context/cancel，映射统一 GenerationTrace、timeout、结构化错误和 `observe_outcome`；
- 用 PersonAct 专属 `AgentRunOption` 传递 DecisionRequest，输出使用不可变 typed value 或稳定 JSON string，验证 callback fan-out 不共享可变对象；
- Persona/Director/Broadcast 分别返回 ActionProposal、DirectorResolution、BroadcastPlan；SegmentDraft 只由 Runtime 内的 Segment Assembler 构造；
- Fixture 不访问网络，避免把调度错误和模型随机性混在一起。

完成条件：三个 Fixture 实现均可由 ADK Runner 驱动；PersonAct Graph 的回环有上限，`go test -race ./...` 无跨 Run 状态竞争，且没有 `if agent_type` 分发、越权 Memory 写入或 Agent 直接副作用。

### Phase 3｜单 Event 咖啡 Golden Trace

- 实现 World snapshot、Projector、Director Stub、Temporal Binder、Validator、Committer 和 Recognizer；
- 跑通 Anon utter -> committed Event -> Soyo addressed Observation -> Soyo respond；
- 对每一步保存 snapshot、Observation、Memory write、retrieval、Proposal、Draft、diagnostic 与 commit evidence。

完成条件：固定 seed/fixture 下 Trace 完全一致；Tomori 不获得咖啡店 Observation；非法知识或对象跳变被拒绝。

### Phase 3.1｜地点事实与到访前信息发现

- 初始化 `羽丘高中 / facility.piano.present=true` 与 `RiNG / Tomori 每周六独自 Live` LocationInfo；
- 实现 `QueryLocationContext`：先按 world version/time 折叠 LocationView，再过滤已知、无效和不可披露 Info；
- 角色形成 `go_to(RiNG)` 后，由 Runtime 在下一次目的地决策前调用 Director，Director 只返回 `NoOp / DiscoveryPlan`；
- DiscoveryPlan 必须经 Validator；读取已有公开 Fact/Info 时由 Projector 直接投影引用，产生新消息/告知等外部变化时才经 Committer 形成 WorldEvent；两种路径都不能直接写 Persona Memory；
- 验证角色关于“羽丘没有钢琴”的错误 Claim 不会改写 LocationFact，正式 replace 后新旧 world version 查询结果不同。

完成条件：地点事实口径稳定，LocationInfo 不自动变成已发生 Event，重复到访不重复写入同一信息，restricted Info 不泄漏。

### Phase 4｜隔离多 Event 调度

- Event 内串行，`addressed_to_me` 优先，`no_op + next_wakeup` 防止空转；
- Event A 与 Event B 用 goroutine + `errgroup` 并行；
- 第一版发现共享角色/对象/因果集合交集时直接拒绝并返回 `EVENT_NOT_ISOLATED`，不提前实现复杂 barrier。

完成条件：一个 Event 超时/失败不阻塞另一个；共享实体不会被双重推进。

### Phase 5｜Broadcast 与 Dynamic Render 边界

- 实现 `BroadcastPlan -> deterministic Render Planner -> RenderJob`；
- 在 `DynamicTimelineRuntime` 增加 append、幂等、乱序/gap 检查和最小持久化 cursor，不只新增 HTTP 路由；
- 增加 Runtime ingress 与状态回传：`accepted / validation_error / ready / playing / completed / failed / unknown`；
- `timeline.json` 继续作为 Fixture Adapter，不能作为上线后的世界真相。

完成条件：同 `render_id + content_hash` 重试不重复入队，同 ID 不同 hash 拒绝，进程重启不重播已完成 Render。

### Phase 6｜端到端验收后再接真实 Prompt

- Go unit：Memory、Projector、Binder、Validator、Committer、Recognizer、Scheduler 和 PersonAct Graph；
- Go integration：两个 Event 的 Fixture Golden Trace；
- Node contract：RenderJob ingress、幂等、恢复和状态回传；
- 端到端 smoke：Go Runtime -> Node Plugin -> 原版 WebGAL 播放，空队列时黑屏；
- 保持 `npm test` 全绿。

只有上述失败能够被 Trace 精确归因后，才按 Persona -> Director Completion -> Broadcast 的顺序接入 Eino ChatModel provider、ChatTemplate 与 structured output；不要三类同时接模型，也不要再建一套平行 Model/Prompt 框架。

## 12. 开工边界与非阻塞事项

一期 Fixture Vertical Slice 的目录、责任边界、临时调度规则和验收目标已经足够开工。Temporal Binder 的最终算法、Director 自身 latency、跨 Event barrier、真实 Prompt 质量与 30 分钟 Buffer 策略仍未解决，但不阻塞按 Phase 0-5 搭建可测试骨架。实现时如果临时规则无法维持已确认不变量，应回写[未决问题](open-questions.md)和[难点账本](difficulty-ledger.md)，不能静默改变架构。

另有一个仓库治理事项：迁移时 `generative_go_world` 尚未建立独立 Git 根，Git 命令会命中祖先目录。执行 Git 写操作前必须先确认 `git rev-parse --show-toplevel` 指向本工程；否则停止操作。是否初始化独立仓库由用户决定，未经明确授权不执行 `git init`、commit 或历史改写。

## 13. 下一 Session 启动指令

可以直接把下面这段交给执行 Session：

> 在 `/Users/bytedance/workspace/other-project/generative_go_world` 开发 Agent Runtime 一期。先完整阅读 `AGENT.md`、`wiki/index.md`、`wiki/agent-runtime-implementation.md`、`wiki/decisions.md` 和 `wiki/difficulty-ledger.md`，再按 `agent-runtime-implementation.md` 的 Phase 0 -> Phase 4 实现，不先接真实模型。Runtime 使用 Go + Eino ADK；Character 实现为自定义 `PersonActAgent`，内部用 typed Compose Graph 承载认知步骤，对外由 ADK Runner 驱动。不得复制 Stanford 的 LLM wrapper、Prompt 字符串替换、JSON mailbox 或全量 JSON memory。必须保持兄弟目录中的 WebGAL/MyGO Bundle 零修改，不把 World/Event 权威放进 Agent Graph 或 Dynamic Render。使用可注入时钟、ID、随机源和 Fixture Agent，跑通 Anon/Soyo 咖啡事件与 Tomori 隔离 Event 的可重放 Golden Trace；所有 Agent 只产生 Proposal/Plan，只有 Committer/Gateway 产生副作用。完成后运行 `go test ./...` 与现有 `npm test`，报告尚未实现项；不要擅自 `git init`、commit 或改写历史。
