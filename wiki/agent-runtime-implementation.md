# Python 3.12 + LangChain Core 强类型 Agent Runtime 一期落地方案

## 0. 文档状态与交接入口

本页是下一开发 Session 的一期执行依据，更新时间为 2026-09-07。阅读时必须区分实现事实与目标方案；更细的自由互动开发顺序以 [MVP 完善开发计划](design/MVP_dev.md) 为准：

- **已实现并验证的 Render 底座：**`extensions/dynamic-render/` 已具备 Fixture Timeline、Render 校验/编译、进程内队列、Event 切换、黑屏 Host 和 `TEMP_SCENE` 注入。
- **已实现的 PersonAct Slice：**当前 `agent_runtime/` 已有严格 Manifest/Compiler、strict/frozen Persona Memory/State、固定 ActionProposal envelope + action union，以及 `PersonActAgent.decide`。真实 Decision path 已包含 prepare、perceive、retrieve、plan、propose；同一 Agent 的调用由实例锁串行化，成功后一次替换包含 state/memory/trace 的 immutable private snapshot，并暴露只读视图。
- **已实现的模型 Strategy seam：**Manifest/Compiler 可绑定 exact Character Skill version/hash；`ModelCognitionStrategy` 覆盖 poignancy、daily plan 与 action draft，使用 typed LangChain ChatModel/Fixture Gateway、structured output、仅 transport retry、最多一次 schema/semantic repair，以及不保存 Skill/Memory 正文的调用 provenance。当前只完成离线契约验证，尚未完成真实 Provider acceptance 或 trace 持久化。
- **公开 PersonAct 边界：**外部 Event Scheduler 拥有循环，只调用 `PersonActAgent.decide(...)`；一次调用只为 `spec.agent_id` 对应的角色返回一个 strict/frozen `ActionProposal` 对象。需要 wire JSON 时调用 `proposal.model_dump_json(by_alias=True)`。Agent 不暴露 `Persona.move()`，不实现 Maze、path/tile movement 或 `execute`。
- **尚未实现：**reflection/commit feedback、持久化 Memory Store、EventStaff Director、WorldChangeValidator/WorldUpdater、WorldEventHistory、EventSessionRunner、AgentViewBuilder、完整咖啡 Golden Trace、Broadcast、Render ingress、真实 Provider acceptance、Generation Trace 持久化和约 30 分钟领先库存。不得把 PersonAct Slice 表述成完整 Agent Runtime。
- **现行技术方向：**Python 3.12、`langchain-core==1.6.1`、Pydantic strict/frozen Model、pyright strict、ruff、pytest 与 uv。当前不使用 LangGraph。
- **继续有效的领域边界：**`agent / event / world` 所有权、Memory namespace 隔离、Agent 只产出 Proposal/Plan、Event Scheduler 独占循环、同 Event 串行与隔离 Event 并行，以及 `BroadcastPlan -> RenderJob -> Dynamic Render`。
- **Open Research：**真实 Prompt 质量、Memory 检索权重/embedding 质量、World time 与 EventStaff 唤醒时钟、跨 Event 共享实体归约、30 分钟领先库存、自适应调度、Character Skill 自动提取与训练。

结论是：**PersonAct 单次认知与 Proposal 边界已经落地，但完整 Runtime 尚未实现。** 第一个完整里程碑是把 Scenario 初始化、World 提交、AgentView、EventSession 自由互动、EventStaff 和提交后 feedback 做成可重启 Trace，而不是先追求剧情质量。

## 1. 定位与技术边界

一期在本工程内建设一个 **Python Agent Runtime**。LangChain Core 只承担 `decide` 内部需要的可组合步骤、调用配置以及后续模型/Prompt/Tool 适配；项目代码继续持有角色认知语义、世界事实、调度、提交与 Render 边界。

```text
Python Agent Runtime
  Event Scheduler: choose actor -> build DecisionRequest -> call decide -> consume one Proposal
  PersonActAgent.decide: DecisionRequest -> one strict/frozen ActionProposal
  WorldChangeValidator / WorldUpdater -> WorldEventHistory（待实现）
  Director: DirectorView -> EventStaffDecision（待实现）
  Broadcast: committed Event range -> BroadcastPlan（待实现）
              |
              | BroadcastPlan -> deterministic Render Planner -> RenderJob
              v
Dynamic Render Plugin
  validate -> compile -> queue -> TEMP_SCENE
              |
              v
MyGO / WebGAL
```

LangChain Runnable 是 `decide` 内部的编排工具，不是 Agent 的公共生命周期，也不是世界状态机、调度器或权限系统。`RunnableConfig` 的 tags、metadata、callbacks 和 tracing 可以承载调用观测，但不能决定下一位角色、角色可见信息、World version 或提交权限。

### 1.1 自研 NPC ADK 与 LangChain 的关系

本仓库实现的是 **Generative Go World NPC ADK**：Creator Manifest、受信编译、Persona 私有 State/Memory、认知语义、ActionProposal 协议、权限校验和 Trace 契约均由项目代码定义。LangChain Core 只是内部编排与模型接入依赖，不能替代这些领域能力，更不拥有 Scheduler 或 World Commit；当前只有内存 DecisionTrace 与 ModelCallTrace，完整可持久化重放仍待实现。

```text
our NPC ADK = authoring contract + cognition + memory + action contract + safety boundary
LangChain    = runnable/model/prompt/tool plumbing
World        = scheduling + validation + commit + canonical facts
```

因此可以称它为“我们自己的 NPC ADK”，但当前完成度必须准确表述为 **PersonAct 核心 Slice 已实现，完整 Runtime 尚未实现**。

### 1.2 唯一的 Character 决策入口

`PersonActAgent` 不实现或暴露 Stanford `Persona.move()`。它只提供一次性决策入口：

```text
PersonActAgent.decide(DecisionRequest) -> ActionProposal
```

每次调用的硬边界：

- `DecisionRequest` 只包含 `proposalId` 与 `frame`；spec、Persona state、Memory 和 strategy 均由受信 Agent 实例持有，不允许调用方随请求替换；
- 输入由外部 Event Scheduler 基于一个 committed world version 构造；
- 一次只为当前 `CompiledPersonActSpec.agent_id` 对应的角色决策；
- 一次只返回一个 strict/frozen `ActionProposal` 对象，不在 Agent 内继续下一 tick、轮询其他角色或等待 World Commit；
- `agentId` 由受信代码从 `spec.agent_id` 注入，CognitionStrategy、模型和创作者配置都不能提供或覆盖 actor；
- 返回值只是一项候选意图；JSON 边界显式使用 `model_dump_json(by_alias=True)`。Scheduler 将 Proposal 交给 Validator/WorldUpdater，提交后再决定后续唤醒；
- 不实现 `move` action，不接入 Maze、path、tile、地址解析、寻路或 `execute` 节点。

迁移 Generative Agents 时只吸收 Persona 私有的 perception、memory retrieval、planning/reacting、reflection 等**认知语义**；不迁移其顶层 loop、`Persona.move()` 门面、movement 或世界副作用。

### 1.3 强类型到底由谁保证

本方案把“强类型”拆成四层，不能把任何一层偷换成另一层：

| 层 | 责任 | 当前手段 |
|---|---|---|
| Python 静态类型 | 检查函数、Protocol、泛型与返回值接线 | 完整注解 + pyright strict |
| 运行时结构边界 | 拒绝未知字段、隐式类型转换、污染实例和可变契约对象 | Pydantic `StrictModel`：`strict=True`、`extra="forbid"`、`frozen=True`、`revalidate_instances="always"`、`validate_default=True` |
| Runnable 编排 | 组合显式输入/输出节点并传递 `RunnableConfig` | `Runnable[Input, Output]`、`RunnableLambda`、`RunnableSequence` |
| 领域语义 | 校验 namespace、affordance、target、evidence、world version 与副作用权限 | 项目自己的 Compiler、Validator 与 WorldUpdater |

必须准确理解 `Runnable.with_types()`：它为 Runnable 绑定或暴露输入/输出类型信息，方便 Schema、工具和观测系统理解边界，**不会自动在 `invoke()` 时执行 Pydantic runtime validation**。因此：

- 外部 JSON 必须显式使用 `model_validate_json(..., strict=True)` 或等价 strict Pydantic 入口；
- 模型/Tool 返回值必须先解析成 strict Pydantic Model，再进入下一个可信节点；
- Runnable 中间节点保持显式参数/返回注解，并由 pyright strict 检查；
- 语义合法性仍由项目代码检查，Pydantic 只能证明结构合法；
- 不以 `with_types()`、Python type hint 或 LangChain 泛型替代运行时校验。

当前不引入 LangGraph。`decide` 是一次有界决策调用；在出现经过证据验证的复杂分支、暂停恢复或持久执行需求前，不支付 LangGraph 的状态模型、checkpoint 和迁移成本。即使未来引入，EventSessionRunner 的循环、SQLite 当前状态、WorldEventHistory 和 commit protocol 也不能交给图状态。

### 1.4 Python 环境统一使用 uv

Python 版本、项目虚拟环境、依赖解析和锁文件统一由 uv 管理：

```bash
uv sync --frozen
uv run ruff format --check agent_runtime
uv run ruff check agent_runtime
uv run pyright
uv run pytest
```

- `.python-version` 固定 Python 3.12；
- `pyproject.toml` 是依赖与工具配置源；
- `uv.lock` 是可重现依赖锁；
- `pyyaml==6.0.3` 只用于读取 strict Runtime Skill frontmatter；
- 新增依赖使用 `uv add <package>`，开发依赖使用 `uv add --dev <package>`；
- 不维护并行的 `requirements.txt`、Poetry/Conda 环境，也不直接执行 `pip install` 改写项目环境。

## 2. 目录与依赖方向

### 2.1 当前已实现目录

```text
agent_runtime/
├── __init__.py
├── model.py                         # 全局 StrictModel 策略
├── model_gateway.py                 # typed ChatModel/Fixture structured-output seam
├── agent/
│   ├── memory/                       # 共享机制，数据按 namespace 隔离
│   ├── skill.py                      # 版本化 Runtime Skill 与整文件 hash pin
│   ├── director/                     # Director Agent 类型边界；实现待补
│   ├── broadcast/                    # Broadcast Agent 类型边界；实现待补
│   └── personact/                    # 具体 Character Agent；Anon/Soyo 是实例
│       ├── manifest.py               # 不受信 agents.json 的严格模型与 loader
│       ├── compiler.py               # Manifest -> CompiledPersonActSpec
│       ├── state.py                  # Persona 私有 state；不是 World State
│       ├── agent.py                  # decide 门面、锁、replay、snapshot 事务
│       ├── loop.py                   # typed PersonAct 认知循环与五个阶段
│       ├── model_strategy.py         # Skill 驱动的模型 CognitionStrategy
│       ├── errors.py
│       └── proposal.py               # 最终 Proposal 构造与权限校验
├── world/
│   └── contracts.py                 # World-owned 边界；不是完整 World 实现
├── event/                            # Scheduler/EventSession 类型边界
├── rendergateway/                    # RenderJob 出站类型边界
├── testdata/npc_diy/agents.json
└── tests/
    ├── test_personact.py
    ├── test_personact_agent.py
    ├── test_model_strategy.py
    ├── test_persona_state.py
    └── test_memory.py
```

`PersonActAgent.decide` 已实现 prepare -> perceive -> retrieve -> plan -> propose。它包含所有权/时间校验、新日私有计划、确定性 attention、全 EVENT stream 上基于 canonical identity 的 novelty、event ID/revision 成对校验、受 write policy 约束的 EVENT Memory、literal + three-factor ranked retrieval、显式去重 touch、当前日程/action/focus 上下文，以及最终 capability/typed affordance/evidence 校验。本轮全部 Observation 与历史 retrieval 分离；部分日程合法，当前时间不落入任何 slot 时不伪造 schedule item；`active_action` 在 `decide` 中只读，等待未来 committed-outcome feedback 更新。Reflection 与 commit feedback 不在该路径中，完整 World Runtime 仍未实现。

### 2.2 一期目标目录

领域边界 package 已按以下方向创建；只有真实职责出现时才新增具体 Model、Runner 或 Service，不用空实现伪装进度：

```text
agent_runtime/
├── model.py
├── model_gateway.py
├── bootstrap.py                     # Scenario/Manifest/World/Agents/Runner 的装配入口
├── scenario.py                      # 跨 World/Agent 的开场初始化契约与 loader
├── sqlite.py                        # 共享 engine/session/transaction；不包含领域规则
├── common/
│   └── union_part.py                # 无业务语义的 merge/split 分区结构
├── agent/
│   ├── skill.py
│   ├── personact/
│   │   ├── manifest.py
│   │   ├── compiler.py
│   │   ├── state.py
│   │   ├── agent.py                 # 一次决策门面；不拥有 Scheduler loop
│   │   ├── loop.py                  # prepare/perceive/retrieve/plan/propose
│   │   ├── model_strategy.py         # typed model calls and bounded repair
│   │   └── proposal.py              # Proposal authority boundary
│   ├── director/                    # DirectorView / EventStaffDecision；待实现
│   ├── broadcast/                   # BroadcastPlan；待实现
│   └── memory/                      # in-memory scoped stream/retriever 已实现；持久化待实现
├── event/                           # EventSessionNode/Runner 与互动调度；待实现
├── world/                           # 公共状态及其关系表、Initializer、Updater、AgentViewBuilder
├── rendergateway/                   # RenderJob 出站适配器；待实现
├── tests/
└── testdata/
content/
└── skills/characters/              # 版本化 Character Skill 配置资产
projects/<project-id>/
├── agents.json                     # 本作品角色实例与稳定私有配置
└── scenario.yaml                   # 开场公共世界、初始 Session 分区与全员/指定角色知识分配
```

角色 Skill 内容位于仓库根目录 `content/skills/characters/`；它们是配置资产，不是 Anon/Soyo 等角色的源码 package。

目录按所有权表达边界：

- `agent/personact/loop.py` 显式实现 typed `prepare -> perceive -> retrieve -> plan -> propose`；`agent.py` 的 `PersonActAgent.decide` 负责串行化、proposal-id replay 与成功后的 private snapshot 原子替换。它消费本人的 `AgentView/PerceptionFrame`、Persona 私有 state 与 scoped Memory，只返回一个 `ActionProposal`。Anon、Soyo 等由 Manifest 编译成该类型的不同实例；
- `agent/director/` 只读取按 committed Event/Staff 裁剪的 `DirectorView`，返回 `EventStaffDecision`；不读取 Character Proposal 或私有认知；
- `agent/broadcast/` 只读取已提交 Event，返回 `BroadcastPlan`；
- `agent/memory/` 提供共用机制，但每次访问都绑定 `agent_id + namespace`，共用实现不等于共享数据；
- `scenario.py + bootstrap.py` 负责跨域初始化编排：预先校验全部输入后，在一个初始化 transaction 内将公共客观部分交给 World，并把 knowledge assignment 按 all agents 或显式接收者展开到各自 Agent Memory；它们不解释角色认知或世界规则；
- `event/` 拥有 EventSession、轮次、Runner 和 Event 生命周期；Runner 决定何时、为谁调用 `decide`，不进入 Agent Runnable；
- `world/` 拥有客观当前状态、Location、SQLite transaction、初始化物化、硬可见 AgentView 与原子更新，不依赖具体 Agent 内部状态；
- `rendergateway/` 只投递不可变 RenderJob 并接收播放状态；
- 装配入口只连接依赖，不成为万能 facade。

### 2.3 依赖方向

```text
runtime entrypoint
  -> event scheduler
  -> agent decide calls
  -> world services
  -> rendergateway

event
  -> 调用 agent 获取 Proposal/Plan
  -> 调用 world 校验并提交

agent/personact
  -> consume one DecisionRequest + trusted spec/private context
  -> return one ActionProposal
agent/director
  -> consume one DirectorView for committed Event or pending Staff
  -> return enqueue | keep | release | cancel | no_op
agent/broadcast
  -> consume committed Event
  -> return BroadcastPlan

world
  -> 不依赖 agent 的私有 state
  -> 不依赖 MyGO/WebGAL
  -> 只有 WorldUpdater 能推进 worlds.current_version 并追加 WorldEvent
```

禁止 PersonAct 持有其他 Agent 的 live object；禁止 `PersonActAgent.decide` 自己循环、选择下一角色、移动角色或执行动作；禁止 Director 直接写 World Store；禁止 Broadcast 直接控制播放器；禁止把完整 World 当前状态塞进模型消息绕过 AgentViewBuilder。

### 2.4 `decide` 内部 Runnable 组合规则

- Runnable 只能存在于 `decide` 的内部实现；公共 API 仍是一次请求、一个 Proposal 对象；
- 三类 Agent 不共享一条万能 Pipeline，只共享 LangChain Core 调用协议、`RunnableConfig` 约定、严格契约策略和观测字段；
- 每个 `RunnableLambda` 的 callable 必须显式注解输入/输出，节点之间传递 strict/frozen Pydantic 值；
- `RunnableSequence` 可能在内部把中间泛型擦除为 `Any`，因此 pyright 必须检查每个命名节点，测试必须覆盖实际接线；
- `with_types()` 可用于描述边界，但调用前后仍显式 Pydantic 校验；
- 技术性瞬时失败可有限使用 Runnable retry；结构错误、越权或语义失败不得靠盲重试掩盖；
- 模型、Tool、Callback 和 tracing 只能通过窄 adapter 接入，不能让 LangChain 对象渗入 World 领域模型；
- Runnable 不拥有 Persona 顶层 loop、Event Scheduler 或 World commit；
- 当前 PersonAct 无需 LangGraph。若未来确实出现复杂有状态执行，再以 ADR 和契约测试重新评估。

## 3. 与 Generative Agents 的映射

### 3.1 已核对的原型行为

以下结论来自本地 [`generative_agents`](../../generative_agents/) 源码：

- `ReverieServer.start_server()` 使用浏览器文件同步 Persona 与 Maze，再按 Persona 字典顺序执行 `move()`；该顶层循环、文件 mailbox、固定步长与顺序副作用全部不迁移；
- `Persona.move()` 只是 `perceive -> retrieve -> plan -> reflect -> execute` 的门面，真实实现由 `import *` 注入；本项目不实现或暴露这个门面，只把其中可验证的认知语义重新组织到单次 `decide`；
- 原 `perceive.py` 混合空间学习、候选收集、距离筛选、新颖性、embedding 与记忆写入；本项目把硬可见性放在 AgentViewBuilder，把注意力与主观解释留给 PersonAct；
- 原 `plan.py` 会直接修改另一个 Persona 的 Scratch；本项目禁止 Agent 修改其他 Agent 的 live state；
- 原 `execute.py` 处理地址、寻路与逐 tile 移动；本项目完全不迁移该模块，不提供 `move` 或 location action，只保留“认知结果成为 Proposal，副作用留在 World”这一边界；
- 原 associative memory 的 evidence、recency、relevance、importance 思路可借鉴，但 keyword 归一化与 retrieval 写副作用必须显式化。

因此迁移的是 perception、private memory、retrieval、planning/reacting、reflection 等认知语义，不是 `Persona.move()`、Sandbox loop、movement、Maze、mailbox、共享可变 Persona 或全量 JSON 存储。

| Generative Agents | 一期 Runtime | 处理方式 |
|---|---|---|
| `reverie.py` / Sandbox loop | 外部 `event/` Scheduler | 由 Runtime 重写并独占循环，不使用浏览器 mailbox |
| `maze.py` | 无运行时迁移 | 不复制 tile 世界；World 只维护项目自己的语义事实/对象 |
| `path_finder.py` | 无 | 不迁移几何寻路 |
| `persona/persona.py` / `Persona.move()` | 无 | 不实现门面；Scheduler 直接调用 `PersonActAgent.decide` |
| `cognitive_modules/*` | `decide` 内部领域策略 | 迁移认知语义，不按原调用链逐文件复制 |
| `perceive.py` | AgentViewBuilder + PersonAct perceive | 前者裁剪可见性，后者处理注意力/新颖性 |
| `retrieve.py` | scoped Memory Retriever | 只查本 Agent namespace |
| `plan.py` | `CognitionStrategy.plan_action` | Fixture 与 ModelCognitionStrategy 已共用同一 Protocol |
| `reflect.py` | 提交后的独立 feedback 路径 | 一期可 No-op，不和 Decision Run 混写 |
| `execute.py` | 无 | 不迁移；副作用只由 WorldUpdater/Render Gateway 产生 |
| `associative_memory.py` | `agent/memory/` | 保留领域语义，不共享 namespace |
| `scratch.py` | PersonAct 私有 state | 不升级为公共世界状态 |
| `spatial_memory.py` | KnownPlace/语义地点知识 | 只保存角色已经获知的地点信息 |
| Prompt wrapper | LangChain Prompt/ChatModel adapter | 模板内容与版本仍由项目维护 |

若后续复制具体 Apache-2.0 源码而不是重新实现接口，必须保留许可证与 NOTICE/归属要求；一期优先参考结构并重写最小逻辑。

### 3.2 成熟组件替代边界

原则是：**框架接管通用调用 plumbing，项目保留决定世界与角色语义的算法。**

| 原型中的自造能力 | 一期处理 | 项目仍负责 |
|---|---|---|
| 手写模型/策略调用接线 | `decide` 内按需使用 LangChain Runnable | 认知语义、输入权限、Proposal 契约、终止条件 |
| 模型 SDK wrapper | typed LangChain Core ChatModel Gateway 已实现；具体 Provider 尚未装配 | model policy、预算、Trace 与输出校验 |
| 字符串 Prompt 替换 | LangChain Prompt Template | 模板内容、版本、证据选择和系统约束 |
| 手工截 JSON | 显式 strict Pydantic parse | Schema、错误分类和领域校验 |
| 裸异常重试 | 有界技术重试 + 单次语义 repair | 可重试分类、repair 上限与 Trace |
| 调用日志 | `RunnableConfig` tags/metadata/callbacks | world version、evidence、耗时与 commit 关联 |
| 手写向量 plumbing | 规模需要时再接 Retriever/VectorStore adapter | 召回融合、认知解释、namespace 与 provenance |
| 多份 JSON 全量重写 | Fixture 保留 Golden JSON；运行存储评估 SQLite | append-only、事务和回放语义 |
| 文件 mailbox / busy polling | typed HTTP/WebSocket adapter | RenderJob 幂等、状态机和恢复 |
| Maze / movement / Selenium / 浏览器仿真 | 不迁移 | 语义 Scene、affordance 与 AgentViewBuilder |

LangChain Runnable 的运行状态不能替代 SQLite 当前状态、`world_version`、AgentViewBuilder、EventSession 隔离、Validator/WorldUpdater、WorldEventHistory、EventStaff queue 或 Render Planner。

### 3.3 依赖与工具链预算

现行基础依赖锁定为：

```text
Python >=3.12,<4.0
langchain-core==1.6.1
pydantic==2.13.5

dev:
pyright==1.1.411
pytest==9.1.1
ruff==0.16.5
uv 管理环境与锁文件
```

一期不同时引入 LangGraph、另一套 Agent 框架、向量数据库或 Web 框架。新增依赖必须对应当前已出现的职责，并由契约测试保护升级。

## 4. Runtime 主循环

以下是目标主循环。循环属于外部 EventSessionRunner；Director/World Update 等仍未实现：

```text
load committed relational current state
-> Runner 选择一个可运行 EventSession root
-> 选择下一 Character 决策机会
-> AgentViewBuilder 构造该角色的 strict AgentView
-> PersonActAgent.decide(DecisionRequest)
-> 得到该角色唯一一个 strict/frozen ActionProposal；跨 wire 时序列化为 camelCase JSON
-> Validator 检查世界不变量与 based_on_world_version
-> WorldUpdater 原子更新当前状态/request/session，并追加 WorldEvent
-> outcome 投影回相关 Agent 的 feedback 路径
-> DirectorRunner 按 cursor 消费 committed Event，构造 DirectorView
   -> enqueue/no_op 与 cursor 原子提交
   -> 到期 Staff 的 keep/release/cancel 再经 Validator/WorldUpdater
-> Broadcast 读取 committed Event，生成 BroadcastPlan
-> deterministic Render Planner 生成 RenderJob
-> RenderGateway 投递 Dynamic Render Plugin
```

### 4.1 不提前抽象三类 Agent 的统一 Controller

当前唯一真实认知 loop 是 `agent/personact/loop.py` 中的 `PersonActLoop`，由 `PersonActAgent.decide` 调用：

```text
observe/perceive -> retrieve -> plan -> propose
Runtime validate/commit
observe_outcome -> conditional reflect
```

三类 Agent 只共享 strict model、Model Gateway 和 Trace 等基础设施；typed 输入、输出、State、Strategy、Prompt、Memory namespace、触发方式和具体节点分开：

| Agent | 输入 | 输出 | 副作用权限 |
|---|---|---|---|
| PersonAct | 本人 AgentView、Persona、scoped Memory | `ActionProposal` | 无 |
| Director | 一个 committed Event 或 pending Staff 的受限 `DirectorView` | `EventStaffDecision` | 无 |
| Broadcast | committed Event range、Viewer/Buffer 状态 | `BroadcastPlan` | 无 |

Character 的公共边界固定为 `decide -> one ActionProposal`。`agent.py` 保持 Agent 门面，`loop.py` 明确承载真实 sequence。Director/Broadcast 不预设沿用 PersonAct 拓扑；只有第二个真实实现产生稳定重复代码后，才判断是否把 PersonActLoop 纳入更宽的 `CognitiveController`。

Decision Run 在一个 Proposal 后结束。World 完成校验与提交后，再以独立输入触发 `observe_outcome -> reflect`；不得让 `decide` 悬挂等待 World Commit，也不得在其中直接产生世界副作用。

### 4.2 Prompt Contract

真实模型接入前，每类 Agent 必须定义版本化 Prompt Contract：

- 输入字段与 evidence 来源；
- strict Pydantic 输出模型；
- forbidden knowledge 与禁止副作用；
- token/Tool/repair 预算；
- 结构失败、语义失败和依赖失败的错误分类；
- template/model/spec digest；
- Golden/Badcase 与 attribution 字段。

模型返回“看起来像 JSON”不等于契约成立。必须显式 parse 成 strict Model，随后再做 namespace、affordance、target、evidence 与 world-version 校验。

### 4.3 Event 隔离与调度

第一版 Event 间并行只允许满足以下条件的集合：

```text
participant_ids 不相交
owned_entity_ids 不相交
location resource 不共享
causal_dependency_ids 不相交
没有等待另一 Event commit 的 barrier
```

同一 Event 内按稳定顺序逐步提交；隔离 Event 后续可使用 Python `asyncio.TaskGroup` 并行，但在完成共享实体证明前不能把“不同 coroutine”当作“世界上彼此隔离”。一个 Event 失败不得取消无因果关系的其它 Event；错误必须进入各自 Trace。

Event 内下一位由 Scheduler 决定。被点名角色在 Frame 中收到 `addressed_to_me / pending_response`，但仍可回复、拒绝、延后或 `no_op`。`no_op` 必须携带 `next_wakeup`，只写 Decision Trace，不生成占位 WorldEvent。

## 5. 感知与 Event 边界

感知链必须固定为：

```text
Character ActionProposal
EventStaff release
System / Tool / Player Input
                |
                v
       Validator / WorldUpdater
                |
                v
       committed WorldEvent
                |
                v
          AgentViewBuilder
                |
                v
strict AgentView[当前 Character]
                |
                v
PersonActAgent.decide -> one ActionProposal
```

必须区分：

```text
WorldEvent       客观事实
PerceptCandidate 有机会感知的字段
Observation      角色实际注意到的内容
Memory           角色如何保存和主观解释
```

`AgentView` 只能包含 AgentViewBuilder 判定可见的 Location Fact/Info、candidate、pending response、affordance 与 evidence；不能携带完整 `LocationView`。Pydantic 能验证 View 结构，却不能证明信息真的可见，硬可见性仍由 AgentViewBuilder 和 Golden Trace 保证。当前源码类型仍名为 `PerceptionFrame`，实现该阶段时再做受控迁移。

## 6. 第一期最小数据契约

当前 PersonAct Slice 已实现 `Affordance / PerceptionFrame / ActionProposal`；本节其余类型是下一阶段契约，不代表代码已经落地。所有跨模块值都应继承统一 `StrictModel`，默认不可变、拒绝未知字段和隐式 coercion。

### `GenerationTrace` / `AgentOutcome`

```text
generation_id / agent_id / agent_kind / event_session_id
based_on_world_version / started_at / returned_at / elapsed_ms
input_evidence_ids / template_version / model_id / spec_digest
raw_output_ref / parsed_output / status / error / repair_count

AgentOutcome
  generation_id / accepted / committed_world_version
  produced_event_ids / validation_diagnostics / side_effect_status
```

`GenerationTrace` 是调用证据，不是 Memory；敏感原始输出应存引用或受控摘要。

### `PerceptCandidate` / `Observation`

```text
candidate_id / source_event_id? / target_agent_id
source_location_id / source_fact_refs / source_info_refs
channel / visible_fields / salience / mandatory

observation_id / source_event_id? / target_agent_id
observed_at_world_version / content / confidence
source_fact_refs / source_info_refs / visible_fields / provenance
```

### `EventSession` / `WorldEvent`

```text
EventSessionNode
  session_id / agent_id / root_session_id / updated_world_version

WorldEvent
  event_id / world_id / world_version / event_order / event_type
  source_kind / source_id / actor_id? / target_id? / location_id?
  root_session_id_at_commit?
  in_reply_to_event_id? / caused_by_event_id?
  started_at / ended_at? / content? / details_json

InteractionRequest
  request_event_id / request_kind / requester_agent_id / recipient_agent_id
  status / resolution_event_id? / updated_world_version
```

每个 Character Agent 只有一个生命周期稳定的 `EventSessionNode`；当前互动组由 `root_session_id` 等价类派生，merge/split 只重标 root，不创建 successor Session。`WorldEvent` 是单一 append-only 已提交历史，记录事件发生当时的 root、来源、actor 与因果，不能用当前 root 回写旧事件。`InteractionRequest` 只保存仍待回应/接受的当前状态与 Event 引用，不复制对话正文。

Character Proposal 直接经 Validator/WorldUpdater 成为 Event；Director 不参与补写。Director 只管理由 committed process-start Event 触发的 EventStaff，release 后的新 Event 使用 `source_kind=event_staff_release`，并保留 Staff/source Event 因果。Agent 私有 Memory 通过 `source_event_id` 引用自己实际注意到的 Event；Broadcast 只读 committed Event，不读未提交 Proposal 或 Character 私有 Memory。

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

Location 不复制 Event 正文，历史直接按唯一 WorldEventHistory 的 `location_id` 查询。自由互动 MVP 先保存关系型当前 Fact/Info；逐 Fact revision 结构延期到出现真实历史查询需求后。

### `AgentView / PerceptionFrame` / `ActionProposal`

```text
DecisionRequest
  proposal_id / frame

PerceptionFrame
  agent_id / event_session_id / based_on_world_version
  current_location_id / visible_evidence_ids / affordances

ActionProposal
  proposal_id / agent_id / event_session_id
  based_on_world_version / action / evidence_ids
```

公开 JSON 使用 camelCase：

```json
{
  "proposalId": "proposal-001",
  "agentId": "anon",
  "eventSessionId": "cafe",
  "basedOnWorldVersion": 7,
  "action": {
    "kind": "interact",
    "target": {"kind": "object", "id": "coffee-42"},
    "description": "拿起已经完成的咖啡"
  },
  "evidenceIds": ["coffee-ready"]
}
```

`ActionProposal` 是固定 envelope；变化只发生在 `action`。`action.kind` 是 Pydantic discriminated union 的唯一判别字段，固定六类：

| `action.kind` | Variant 约束 |
|---|---|
| `act` | 本角色自身行为；不携带 target 或 location |
| `interact` | 恰好一个 `target`，其结构为 `{kind: character|object, id}` |
| `utter` | 恰好一个 character target 与非空 content |
| `respond` | 恰好一个 character target、非空 content；Phase 0 增加指向 committed request Event 的 `inReplyToEventId` |
| `wait` | 带有世界语义的等待，包含 description 与 `nextWakeup` |
| `no_op` | Scheduler yield，只包含 `nextWakeup` |

没有 `move` variant，也没有 `locationId` 或多目标 `targetIds`。`interact` 的 character/object 类型显式进入 JSON，不能靠 ID 猜测；单目标避免部分授权、部分提交与顺序歧义。`utter/respond` 不能指向 object。

当前源码还有两个 Phase 0 缺口：

- `RespondAction` 尚未包含 `inReplyToEventId`，不能把通用 `evidenceIds` 当作明确回复关系；
- `Affordance` 只有 `kind+target`、`InteractAction` 只有 `target+description`，不能稳定区分同一 Object 上的 start/inspect/stop。EventStaff 前必须增加 World-issued `affordanceId/operationId` 或等价 typed operation union；自由文本 description 不得单独触发 Object mutation。

`agentId` 是唯一 actor 字段，由受信 `PersonActAgent` 从 `spec.agent_id` 注入；CognitionStrategy/模型输出只允许提供 `action + evidenceIds` 等候选内容，不能选择 actor。最终 `decide` 返回经过 strict Pydantic validation 的 `ActionProposal` 对象；跨进程时再用 `model_dump_json(by_alias=True)` 生成 camelCase wire JSON。

World contract 与 `PersonActAgent.decide` 已使用这组 strict/frozen 类型；这证明单次 Persona cognition 与 Proposal authority boundary 已落地，但仍只是 Agent 边界，不是 World Commit。

### `DirectorView` / `EventStaff` / `EventStaffDecision`

```text
DirectorView
  trigger: committed_event | event_staff_check
  world_id / based_on_world_version / world_time
  session_id as opaque delivery anchor
  source_event_projection / bounded_causal_events
  relevant_object_and_location_process_state
  selected_pending_staff? / conflicting_pending_staff
  event_staff_affordances

EventStaff
  event_staff_id / world_id / session_id / source_event_id
  staff_kind / subject_type / subject_id / completion_event_type
  status / created_world_version / created_world_time
  next_check_at? / release_event_id? / details_json

EventStaffDecision
  enqueue(affordance_id, next_check_at?)
  | keep(event_staff_id, next_check_at)
  | release(event_staff_id)
  | cancel(event_staff_id, caused_by_event_id)
  | no_op
```

DirectorView 是操作授权视图，不是完整 World Snapshot。当前 root/members 不进入 Director；World 只在 release 后解析它们并让 AgentViewBuilder 过滤收件人。Director 不能读取未提交 Proposal、Character 台词正文/私有 Memory 或 Viewer 数据，也不能输出角色行为、自由 World patch 或 Session transition。enqueue 只接受 World 给出的 affordance；release 只兑现 Staff 中已校验的 completion contract。详细边界见[EventStaff Director 与 Broadcast](director-broadcast.md)。

### `BroadcastPlan` / `RenderJob`

```text
BroadcastPlan
  broadcast_plan_id / based_on_world_version / event_watermark
  source_event_ids / source_world_interval / projection_mode
  camera_viewpoint / reveal_scope / transition
  target_render_duration_ms / artistic_reason / evidence_ids

RenderJob
  schema_version / producer_id / world_id / runtime_session_id
  render_id / attempt_id / based_on_world_version
  source_event_ids / source_root_session_ids_at_commit
  title / location / characters
  structured_beats with per-beat source_event_ids + truth_kind
  estimated_play_ms / content_hash / provenance_sidecar
```

Broadcast 可以把多个 root-at-commit 事件流编排成一段观看序列，但每个计划、Render 和 dialogue/narration beat 都必须保留 `source_event_ids`；beat 还要区分 `fact / quote / inference`。WebGAL DSL 不承载的追溯元数据保存在 Artifact sidecar。EventSession root 是当时的互动边界，不是永久剧情章节 ID。Render Plugin 必须对 JSON 再做自己的边界校验，并基于 canonical input 重算 `content_hash`。Python 对象的类型正确不能替代跨进程接收方验证。

## 7. Dynamic Render Plugin 边界

现有插件已支持 Beat 校验、DSL 编译、Render Queue、黑屏、Event 切换和 `TEMP_SCENE` 注入。完整 Runtime 后续只增加一条投递边界：

1. `POST /api/runtime/render-jobs` 接收不可变 RenderJob；
2. 同 `render_id + content_hash` 重试幂等，同 ID 不同 hash 拒绝；
3. 返回 `accepted / validation_error / ready / playing / completed / failed / unknown`；
4. `timeline.json` 保留为 Fixture Adapter；
5. 保存最小 played cursor，避免进程重启后全部重播。

WorldEvent 不直接进入 WebGAL Plugin。Python Runtime 内的 Broadcast 只输出 BroadcastPlan，确定性 Render Planner 读取 committed Event Log 生成 RenderJob，再由 RenderGateway 发送。

当前实现必须准确校准：`DynamicTimelineRuntime.played`、selected Event 和 active Render 仍只是进程内状态，`timeline.json` 也只是手写 Fixture；真实 RenderJob ingress、Artifact/queue 落盘与 Viewer Cursor 恢复都尚未实现。

```text
CommittedEventFeed   Runtime 内部，只暴露已提交 Event Log
RenderJobIngress     Python -> Node，追加不可变 RenderJob
RenderStatusSink     Node -> Python，回传播放状态
ViewerCursorStore    Render 侧持久化观看游标和终态
```

## 8. 当前 PersonAct Slice 与第一条 Vertical Slice

### 8.1 当前实现事实

当前可作为实现事实使用的部分：

- 不受信 `agents.json` 能被 strict Pydantic loader 拒绝未知字段和错误类型；
- Compiler 能收敛 Tool/Prompt/Proposal 能力、派生 Memory scope 并生成稳定 digest；
- Compiler 能解析 Character Skill exact version，并把整文件 hash 纳入 spec digest；
- strict/frozen Pydantic 基础模型、Persona 私有 Memory/State 与检索能力已经存在；
- World-owned contract 已定义固定 `ActionProposal` envelope、六类 discriminated action union，以及 character/object typed target。
- `PersonActAgent.decide` 已实现 prepare/perceive/retrieve/plan/propose，一次只返回一个 Proposal；
- prepare 校验 ownership、world time/world version 并判定新日；perceive 在全 EVENT stream 上做稳定 attention/canonical novelty，并按 write policy 写本人 EVENT Memory；retrieve 将本轮全部 Observation 与历史记忆分离，再执行 literal + recency/relevance/importance 排序与显式 touch；plan 先基于本轮感知/召回生成新日私有计划，再结合 state、部分 schedule、当前 slot 剩余时长与 focus 生成 action；propose 注入 actor 并校验 capability、typed affordance 与 evidence；
- 同一 Agent 的 `decide` 由实例锁串行化；只有最终 Proposal 校验成功后，才一次替换包含 frozen state/memory/trace 的 private snapshot。
- `ModelCognitionStrategy` 已覆盖 poignancy、daily plan 与 action draft；Fixture 与 ChatModel adapter 共用 typed Gateway，transport retry 与单次 schema/semantic repair 分离。

仍未完成：

- reflection 与 commit feedback；`decide` 只累计 reflection trigger state，不在未提交阶段生成反思；
- Event Scheduler、World commit、Director、Render、生产 Provider 装配与 live evaluation；
- 持久化 Memory adapter、完整 Generation Trace 与咖啡 Golden Trace；当前 `ModelCallTrace` 只是有界、进程内、非秘密 provenance。

### 8.2 下一条咖啡 Golden Trace

```text
1. 编译 Anon/Soyo 的受限 Manifest，固定各自 spec digest
2. Scenario 初始化五个稳定 EventSession node；Anon/Soyo 同 root，Tomori 独处
3. AgentViewBuilder 为 Anon 构造 strict AgentView
4. Anon 返回 utter：“我要煮个咖啡”；WorldUpdater 提交 E1
5. Director 消费 E1，但 World 没有 completion affordance，因此只能 no_op；cursor 与 no-op 原子推进
6. Soyo 可感知 E1；Tomori 不获得该 evidence
7. Anon 自主返回 interact(coffee-machine, start_brewing)
8. Validator/WorldUpdater 原子写 coffee.state=brewing，并追加 E2 coffee_brewing_started
9. DirectorView(E2) 只暴露 coffee_brewing_completion affordance；enqueue S1
10. S1 未满足 release guard 时，越权 release 被拒绝；Director keep
11. 到达 release window 后 release S1
12. 同一 transaction 写 coffee.state=ready、S1=released、E3 coffee_ready、world version
13. pop 时通过 S1 的稳定 session_id 解析当前 UnionPart root
14. AgentViewBuilder 只向此刻在 root 内且满足地点/渠道条件的 Character 投影 E3
15. Soyo 对直接互动用显式 inReplyToEventId 回应；interaction request 原子关闭
16. Broadcast Fixture 只读 E1/E2/E3，返回带 source_event_ids 的 BroadcastPlan
17. Render Planner 生成 RenderJob，RenderGateway 投递 Dynamic Render
18. 固定输入、时钟、ID 与随机源后，重启仍得到相同 root、Staff、cursor 与 Event 历史
```

## 9. 明确延期

- Memory 的持久化 adapter、生产 embedding 与 recency/relevance/importance 权重调优；
- Reflection 阈值与真实 Prompt；
- Character Skill 自动提取与微调；
- Director 的 Narrative Thread、剧情目标函数、RL 与刺激策略（已明确不属于本项目 Director，而不是待扩权项）；
- Broadcast 智能选镜与蒙太奇；
- 自适应 30 分钟 Buffer；
- 多节点、高可用与复杂鉴权；
- Sentence 级断点续播；
- LangGraph 与通用可视化 Agent Builder。

不延期的是严格输入/输出契约、Memory namespace、Proposal/Commit 分权、Golden Trace 和失败可归因性。

## 10. 验收标准

### PersonAct 决策边界

- `PersonActAgent.decide` 每次只返回一个 strict/frozen `ActionProposal`；wire JSON 使用 `model_dump_json(by_alias=True)`；
- envelope 只有 `proposalId / agentId / eventSessionId / basedOnWorldVersion / action / evidenceIds`；
- `action.kind` 只能是 `act / interact / utter / respond / wait / no_op`；
- `interact.target` 恰好一个并显式区分 character/object；`utter/respond.target` 只能是 character；
- `agentId` 只从 `spec.agent_id` 注入，模型伪造 actor 必须失败；
- 不存在 `move/location/targetIds` 兼容字段，不暴露 `Persona.move()`；
- Agent 不能越过本人 Memory scope、Frame affordance 或 visible evidence；
- `with_types()` 未被当作 runtime validation。

### 完整 Fixture Vertical Slice

- 无 WebGAL 时 Runtime 可独立推进两个 Event 并写 append-only Event Log；
- 同 Event 内上一角色提交后，下一角色才通过 Frame 感知；
- 隔离 Event 可并行，共享实体时拒绝并行；
- 只说“我要煮咖啡”不会创建 completion Staff；只有已提交 process-start Event 才有 enqueue affordance；
- Director 看不到未提交 Proposal、Persona Memory、无关 Session 或 Viewer 数据；
- EventStaff release 必须再次经过 Validator/WorldUpdater，且重复消费不会重复入队/释放；
- merge/split 后 Staff 用稳定 session node 解析当前 root，再由 AgentViewBuilder 过滤收件人；
- 纯 `no_op` 不产生 WorldEvent；
- 同一 RenderJob 重试不重复入队；
- WebGAL 可播放增量 Render 并回传状态；
- 无 Ready Render 时黑屏，切换 Event 不修改世界；
- Node 既有测试保持通过。

## 11. 按依赖排序的开发 Plan

### Phase 0｜已完成的 `decide` 契约基线

- 固定 Python 3.12、LangChain Core、Pydantic 与 dev tool 版本；
- 保持统一 `StrictModel` 策略；
- 保持 `PersonActAgent.decide` 为唯一公共 Character 决策入口；
- 保持 CognitionStrategy 输出显式 parse 为 action discriminated union，再由受信代码注入固定 envelope；
- 保护 actor 注入、typed target、单目标、非法 variant 字段和旧扁平字段拒绝契约；
- 为 Respond 增加 committed request Event 引用，为 Object interact 增加稳定 affordance/operation 引用；
- 保留 Pydantic runtime validation 与 `with_types()` 非校验行为的契约测试；
- 所有时钟、ID、Fixture CognitionStrategy 输出和随机源可注入；
- 不引入 LangGraph。

完成条件：ruff、pyright strict、pytest 与既有 npm 测试均通过。

### Phase 1｜Scenario 与 SQLite 权威状态

- 实现 strict `ScenarioSeed`、引用校验、seed hash 和一次性 bootstrap；
- SQLite 保存 World/Location/WorldFact/AgentWorldState/Object、稳定 Session root binding、`interaction_requests`、`event_staff`、append-only `world_events` 和隔离 Agent Memory；
- 不建立重复的 WorldSegment、WorldVersion、EntityRevision、Snapshot 或 event-session-members 表；
- 实现无效 Seed、重复初始化、事务中断和重启恢复测试。

完成条件：不会留下半初始化世界；重启恢复相同 version、root、Staff/cursor 和私有 Memory namespace。

### Phase 2｜WorldUpdater、AgentView 与 feedback

- 实现 expected-version Validator 与单事务 WorldUpdater；
- 实现 AgentViewBuilder 的地点、Session、recipient、channel 和 visible-fields 硬过滤；
- `RespondAction` 增加明确 `inReplyToEventId`，用 interaction request 表恢复待回应关系；
- World commit 后以独立 feedback 输入实现 outcome memory 与 conditional reflection；
- 保持现有 PersonActLoop，不迁移 `Persona.move()` 或 `execute`。

完成条件：Character Proposal 不能直接写 World/他人 Memory；Tomori 看不到隔离 root 的咖啡店互动；失败事务没有半状态。

### Phase 3｜EventSession 自由互动

- 实现无业务语义的 `UnionPart[T]` 与稳定 EventSession node；
- 同 root 内稳定轮转，每个有效 Proposal 立即提交；不同 root 只在隔离证明成立时并行计算；
- 业务 join/leave/merge/split 只通过受信 transition plan 调用 UnionPart；
- 跑通 Anon/Soyo 对话、Tomori merge、Anon split 和重启恢复。

完成条件：五个 Character 始终只有五个稳定节点；成员集合与 root mapping 始终一致。

### Phase 4｜EventStaff Director 与咖啡 Golden Trace

- 实现 `DirectorView / EventStaff / EventStaffDecision` strict contract；
- DirectorRunner 以持久 cursor 消费 committed Event，并另行选择到期 Staff；
- enqueue/no-op 与 cursor 原子提交，release/cancel 与 Object/Event/version 原子提交；
- World 先计算 affordance；Director 只能 `enqueue / keep / release / cancel / no_op`；
- 覆盖“只说不入队、启动后入队、提前释放拒绝、完成、取消、merge/split、重启和重复消费”。

完成条件：Director 无法替 Character 行动或说话，无法读取私有认知，无法绕过 completion contract。

### Phase 5｜Broadcast 与 Dynamic Render

- 实现 `BroadcastPlan -> deterministic Render Planner -> RenderJob`；
- 增加 append、幂等、乱序/gap 检查与持久 cursor；
- 增加 Runtime ingress 与状态回传；
- 保持 `timeline.json` 只是 Fixture。

### Phase 6｜端到端后再接生产 Provider

- Python unit：strict models、Memory、UnionPart、AgentViewBuilder、Validator、WorldUpdater、EventSessionRunner、DirectorRunner、PersonAct；
- Python integration：两个 Event 的 Fixture Golden Trace；
- Node contract：RenderJob ingress、幂等、恢复和状态回传；
- 端到端 smoke：Python Runtime -> Node Plugin -> WebGAL；
- 只有失败能被 Trace 精确归因后，才分别装配 PersonAct、EventStaff Director 与 Broadcast 的生产 Provider，并做 shadow/live evaluation。

生产 Provider 接入必须保持当前 Pydantic、Gateway 与语义校验边界，只替换受信 Runtime 装配，不扩张 NPC Manifest 权限。

## 12. 验证命令

```bash
uv sync --frozen
uv run ruff format --check agent_runtime
uv run ruff check agent_runtime
uv run pyright
uv run pytest
npm test
```

只修改文档时不需要假装这些代码检查是本次文档验证结果；真正改动 Python 或 Node 后按影响范围运行并报告。

## 13. 开工边界与下一 Session 指令

Fixture Vertical Slice 的所有权、强类型边界和开发顺序已足够开工。World time/Staff 唤醒时钟、互动参与协议、跨 Event barrier、真实 Prompt 和 30 分钟库存仍未解决；若临时规则破坏已确认不变量，必须回写[未决问题](open-questions.md)和[难点账本](difficulty-ledger.md)。

执行任何 Git 写操作前仍须确认 `git rev-parse --show-toplevel` 指向本工程；未经用户明确授权，不执行 `git init`、commit 或历史改写。

可复制的启动指令：

> 在 `/Users/bytedance/workspace/other-project/generative_go_world` 继续实现 Agent Runtime。先完整阅读 `AGENT.md`、`wiki/design/MVP_dev.md`、`wiki/index.md`、`wiki/agent-runtime-implementation.md`、`wiki/decisions.md` 和 `wiki/difficulty-ledger.md`。现行技术栈是 Python 3.12 + `langchain-core==1.6.1`；输入、输出和持久化边界使用 strict/frozen Pydantic Model，静态检查使用 pyright strict。外部 EventSessionRunner 独占循环，只调用 `PersonActAgent.decide`；一次调用只为 `spec.agent_id` 返回一个固定 envelope + discriminated action union 的 strict/frozen `ActionProposal`，跨 wire 时再以 camelCase JSON 序列化。不得实现/暴露 `Persona.move()`，不得迁移 Maze/path/tile/movement/execute。LangChain `Runnable.with_types()` 不做运行时校验，当前不使用 LangGraph。Character Proposal 直接经 Validator/WorldUpdater 提交；Director 只读取受限 DirectorView 并管理 EventStaff，不能读取未提交 Proposal 或 Character 私有认知。只有 WorldUpdater 和 Render Gateway 产生各自领域副作用。Reflection/commit feedback、EventStaff Director、WorldUpdater 与完整 Runtime 尚未实现。修改后按 AGENT.md 运行检查；不要擅自 commit 或改写历史。
