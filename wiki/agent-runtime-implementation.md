# Python 3.12 + LangChain Core 强类型 Agent Runtime 一期落地方案

## 0. 文档状态与交接入口

本页是下一开发 Session 的一期执行依据，更新时间为 2026-08-31。阅读时必须区分实现事实与目标方案：

- **已实现并验证的 Render 底座：**`extensions/dynamic-render/` 已具备 Fixture Timeline、Render 校验/编译、进程内队列、Event 切换、黑屏 Host 和 `TEMP_SCENE` 注入。
- **已实现的 PersonAct Slice：**当前 `agent_runtime/` 已有严格 Manifest/Compiler、strict/frozen Persona Memory/State、固定 ActionProposal envelope + action union，以及 `PersonActAgent.decide`。真实 Decision path 已包含 prepare、perceive、retrieve、plan、propose；同一 Agent 的调用由实例锁串行化，成功后一次替换包含 state/memory/trace 的 immutable private snapshot，并暴露只读视图。
- **公开 PersonAct 边界：**外部 Event Scheduler 拥有循环，只调用 `PersonActAgent.decide(...)`；一次调用只为 `spec.agent_id` 对应的角色返回一个 strict/frozen `ActionProposal` 对象。需要 wire JSON 时调用 `proposal.model_dump_json(by_alias=True)`。Agent 不暴露 `Persona.move()`，不实现 Maze、path/tile movement 或 `execute`。
- **尚未实现：**reflection/commit feedback、持久化 Memory Store、Director、Temporal Binder、World Validator/Committer、World/Event Ledger、Event Scheduler、完整咖啡 Golden Trace、Broadcast、Render ingress、真实 ChatModel 和约 30 分钟领先库存。不得把 PersonAct Slice 表述成完整 Agent Runtime。
- **现行技术方向：**Python 3.12、`langchain-core==1.6.1`、Pydantic strict/frozen Model、pyright strict、ruff、pytest 与 uv。当前不使用 LangGraph。
- **继续有效的领域边界：**`agent / event / world` 所有权、Memory namespace 隔离、Agent 只产出 Proposal/Plan、Event Scheduler 独占循环、同 Event 串行与隔离 Event 并行，以及 `BroadcastPlan -> RenderJob -> Dynamic Render`。
- **Open Research：**真实 Prompt、Memory 检索权重/embedding 质量、Director 自身 latency、跨 Event 共享实体归约、30 分钟领先库存、自适应调度、Character Skill 提取与训练。

结论是：**PersonAct 单次认知与 Proposal 边界已经落地，但完整 Runtime 尚未实现。** 第一个完整里程碑仍是把提交后 feedback、世界提交、时间证据与多 Event 调度做成可重放 Trace，而不是先追求剧情质量。

## 1. 定位与技术边界

一期在本工程内建设一个 **Python Agent Runtime**。LangChain Core 只承担 `decide` 内部需要的可组合步骤、调用配置以及后续模型/Prompt/Tool 适配；项目代码继续持有角色认知语义、世界事实、调度、提交与 Render 边界。

```text
Python Agent Runtime
  Event Scheduler: choose actor -> build DecisionRequest -> call decide -> consume one Proposal
  PersonActAgent.decide: DecisionRequest -> one strict/frozen ActionProposal
  Director / Broadcast: 各自独立的 typed Runnable 或确定性策略（待实现）
  World Validator / Committer -> Ledger（待实现）
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

本仓库实现的是 **Generative Go World NPC ADK**：Creator Manifest、受信编译、Persona 私有 State/Memory、认知语义、ActionProposal 协议、权限校验和可重放 Trace 均由项目代码定义。LangChain Core 只是内部编排与模型接入依赖，不能替代这些领域能力，更不拥有 Scheduler 或 World Commit。

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
- 返回值只是一项候选意图；JSON 边界显式使用 `model_dump_json(by_alias=True)`。Scheduler 将 Proposal 交给 Director/Validator/Committer，提交后再决定后续唤醒；
- 不实现 `move` action，不接入 Maze、path、tile、地址解析、寻路或 `execute` 节点。

迁移 Generative Agents 时只吸收 Persona 私有的 perception、memory retrieval、planning/reacting、reflection 等**认知语义**；不迁移其顶层 loop、`Persona.move()` 门面、movement 或世界副作用。

### 1.3 强类型到底由谁保证

本方案把“强类型”拆成四层，不能把任何一层偷换成另一层：

| 层 | 责任 | 当前手段 |
|---|---|---|
| Python 静态类型 | 检查函数、Protocol、泛型与返回值接线 | 完整注解 + pyright strict |
| 运行时结构边界 | 拒绝未知字段、隐式类型转换、污染实例和可变契约对象 | Pydantic `StrictModel`：`strict=True`、`extra="forbid"`、`frozen=True`、`revalidate_instances="always"`、`validate_default=True` |
| Runnable 编排 | 组合显式输入/输出节点并传递 `RunnableConfig` | `Runnable[Input, Output]`、`RunnableLambda`、`RunnableSequence` |
| 领域语义 | 校验 namespace、affordance、target、evidence、world version 与副作用权限 | 项目自己的 Compiler、Validator 与 Committer |

必须准确理解 `Runnable.with_types()`：它为 Runnable 绑定或暴露输入/输出类型信息，方便 Schema、工具和观测系统理解边界，**不会自动在 `invoke()` 时执行 Pydantic runtime validation**。因此：

- 外部 JSON 必须显式使用 `model_validate_json(..., strict=True)` 或等价 strict Pydantic 入口；
- 模型/Tool 返回值必须先解析成 strict Pydantic Model，再进入下一个可信节点；
- Runnable 中间节点保持显式参数/返回注解，并由 pyright strict 检查；
- 语义合法性仍由项目代码检查，Pydantic 只能证明结构合法；
- 不以 `with_types()`、Python type hint 或 LangChain 泛型替代运行时校验。

当前不引入 LangGraph。`decide` 是一次有界决策调用；在出现经过证据验证的复杂分支、暂停恢复或持久执行需求前，不支付 LangGraph 的状态模型、checkpoint 和迁移成本。即使未来引入，Event Scheduler 的循环、World Snapshot、Ledger 和 commit protocol 也不能交给图状态。

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
- 新增依赖使用 `uv add <package>`，开发依赖使用 `uv add --dev <package>`；
- 不维护并行的 `requirements.txt`、Poetry/Conda 环境，也不直接执行 `pip install` 改写项目环境。

## 2. 目录与依赖方向

### 2.1 当前已实现目录

```text
agent_runtime/
├── __init__.py
├── model.py                         # 全局 StrictModel 策略
├── agent/
│   ├── memory/                       # 共享机制，数据按 namespace 隔离
│   ├── director/                     # Director Agent 类型边界；实现待补
│   ├── broadcast/                    # Broadcast Agent 类型边界；实现待补
│   └── personact/                    # 具体 Character Agent；Anon/Soyo 是实例
│       ├── manifest.py               # 不受信 agents.json 的严格模型与 loader
│       ├── compiler.py               # Manifest -> CompiledPersonActSpec
│       ├── state.py                  # Persona 私有 state；不是 World State
│       ├── agent.py                  # decide 门面、锁、replay、snapshot 事务
│       ├── loop.py                   # typed PersonAct 认知循环与五个阶段
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
    ├── test_persona_state.py
    └── test_memory.py
```

`PersonActAgent.decide` 已实现 prepare -> perceive -> retrieve -> plan -> propose。它包含所有权/时间校验、新日私有计划、确定性 attention、全 EVENT stream 上基于 canonical identity 的 novelty、event ID/revision 成对校验、受 write policy 约束的 EVENT Memory、literal + three-factor ranked retrieval、显式去重 touch、当前日程/action/focus 上下文，以及最终 capability/typed affordance/evidence 校验。本轮全部 Observation 与历史 retrieval 分离；部分日程合法，当前时间不落入任何 slot 时不伪造 schedule item；`active_action` 在 `decide` 中只读，等待未来 committed-outcome feedback 更新。Reflection 与 commit feedback 不在该路径中，完整 World Runtime 仍未实现。

### 2.2 一期目标目录

领域边界 package 已按以下方向创建；只有真实职责出现时才新增具体 Model、Runner 或 Service，不用空实现伪装进度：

```text
agent_runtime/
├── model.py
├── agent/
│   ├── personact/
│   │   ├── manifest.py
│   │   ├── compiler.py
│   │   ├── state.py
│   │   ├── agent.py                 # 一次决策门面；不拥有 Scheduler loop
│   │   ├── loop.py                  # prepare/perceive/retrieve/plan/propose
│   │   └── proposal.py              # Proposal authority boundary
│   ├── director/                    # SegmentDraft；待实现
│   ├── broadcast/                   # BroadcastPlan；待实现
│   └── memory/                      # in-memory scoped stream/retriever 已实现；持久化待实现
├── event/                           # EventSession/Scheduler/Recognizer；待实现
├── world/                           # Snapshot/Projector/Binder/Validator/Committer/Ledger
├── rendergateway/                   # RenderJob 出站适配器；待实现
├── tests/
└── testdata/
```

目录按所有权表达边界：

- `agent/personact/loop.py` 显式实现 typed `prepare -> perceive -> retrieve -> plan -> propose`；`agent.py` 的 `PersonActAgent.decide` 负责串行化、proposal-id replay 与成功后的 private snapshot 原子替换。它消费本人 `PerceptionFrame`、Persona 私有 state 与 scoped Memory，只返回一个 `ActionProposal`。Anon、Soyo 等由 Manifest 编译成该类型的不同实例；
- `agent/director/` 只读取 Snapshot、Proposal 与时间证据，返回待校验 `SegmentDraft`；
- `agent/broadcast/` 只读取已提交 Event，返回 `BroadcastPlan`；
- `agent/memory/` 提供共用机制，但每次访问都绑定 `agent_id + namespace`，共用实现不等于共享数据；
- `event/` 拥有 EventSession、轮次、Scheduler、Recognizer 和 Event 生命周期；Scheduler 决定何时、为谁调用 `decide`，不进入 Agent Runnable；
- `world/` 拥有客观状态、Location、感知投影、时间绑定、校验、提交与 Ledger，不依赖具体 Agent 内部状态；
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
  -> consume Snapshot + Proposal + measured span
  -> return SegmentDraft
agent/broadcast
  -> consume committed Event
  -> return BroadcastPlan

world
  -> 不依赖 agent 的私有 state
  -> 不依赖 MyGO/WebGAL
  -> 只有 Committer 能分配 world_version / commit_seq
```

禁止 PersonAct 持有其他 Agent 的 live object；禁止 `PersonActAgent.decide` 自己循环、选择下一角色、移动角色或执行动作；禁止 Director 直接写 Ledger；禁止 Broadcast 直接控制播放器；禁止把完整 WorldSnapshot 塞进模型消息绕过 PerceptionProjector。

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
- 原 `perceive.py` 混合空间学习、候选收集、距离筛选、新颖性、embedding 与记忆写入；本项目把硬可见性放在 World Projector，把注意力与主观解释留给 PersonAct；
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
| `perceive.py` | World Projector + PersonAct perceive | 前者裁剪可见性，后者处理注意力/新颖性 |
| `retrieve.py` | scoped Memory Retriever | 只查本 Agent namespace |
| `plan.py` | `CognitionStrategy.plan_action` | Fixture strategy 已接入，后续再接 ChatModel |
| `reflect.py` | 提交后的独立 feedback 路径 | 一期可 No-op，不和 Decision Run 混写 |
| `execute.py` | 无 | 不迁移；副作用只由 Committer/Gateway 产生 |
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
| 模型 SDK wrapper | 后续以 LangChain Core ChatModel 接口接入一个 provider adapter | model policy、预算、Trace 与输出校验 |
| 字符串 Prompt 替换 | LangChain Prompt Template | 模板内容、版本、证据选择和系统约束 |
| 手工截 JSON | 显式 strict Pydantic parse | Schema、错误分类和领域校验 |
| 裸异常重试 | 有界技术重试 + 单次语义 repair | 可重试分类、repair 上限与 Trace |
| 调用日志 | `RunnableConfig` tags/metadata/callbacks | world version、evidence、耗时与 commit 关联 |
| 手写向量 plumbing | 规模需要时再接 Retriever/VectorStore adapter | 召回融合、认知解释、namespace 与 provenance |
| 多份 JSON 全量重写 | Fixture 保留 Golden JSON；运行存储评估 SQLite | append-only、事务和回放语义 |
| 文件 mailbox / busy polling | typed HTTP/WebSocket adapter | RenderJob 幂等、状态机和恢复 |
| Maze / movement / Selenium / 浏览器仿真 | 不迁移 | 语义 Scene、affordance 与 Projector |

LangChain Runnable 的运行状态不能替代 World Snapshot/Ledger、`world_version`、PerceptionProjector、Event 隔离、Temporal Binder、Validator/Committer、Event Recognizer 或 Render Planner。

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

以下是目标主循环。循环属于外部 Event Scheduler；Director/World Commit 等仍未实现：

```text
load committed snapshot
-> Scheduler 选择一个可运行 EventSession
-> 选择下一 Character 决策机会
-> PerceptionProjector 构造该角色的 strict PerceptionFrame
-> PersonActAgent.decide(DecisionRequest)
-> 得到该角色唯一一个 strict/frozen ActionProposal；跨 wire 时序列化为 camelCase JSON
-> Director 对 Proposal + snapshot + measured span 生成 SegmentDraft
-> Temporal Binder 绑定完整 actual elapsed
-> Validator 检查世界不变量与 based_on_world_version
-> Committer 原子提交 WorldSegment / Ledger
-> Event Recognizer 更新 WorldEvent
-> outcome 投影回相关 Agent 的 feedback 路径
-> Broadcast 读取 committed Event，生成 BroadcastPlan
-> deterministic Render Planner 生成 RenderJob
-> RenderGateway 投递 Dynamic Render Plugin
```

### 4.1 三类 Agent 的共享 AgentLoop 生命周期

三类 Agent 共享以下生命周期语义；当前唯一真实实现是 `agent/personact/loop.py` 中的 `PersonActLoop`，由 `PersonActAgent.decide` 调用。外部 Event Scheduler 不属于 AgentLoop：

```text
observe/perceive -> retrieve -> plan -> propose
Runtime validate/commit
observe_outcome -> conditional reflect
```

三类 Agent 共享生命周期和通用 Memory/Model plumbing；typed 输入、输出、State、Strategy、Prompt、Memory namespace、触发方式和具体节点必须分开：

| Agent | 输入 | 输出 | 副作用权限 |
|---|---|---|---|
| PersonAct | 本人 Frame、Persona、scoped Memory | `ActionProposal` | 无 |
| Director | Snapshot、Proposal、latency、Narrative Thread | `SegmentDraft` / `DiscoveryPlan` | 无 |
| Broadcast | committed Event range、Viewer/Buffer 状态 | `BroadcastPlan` | 无 |

Character 的公共边界固定为 `decide -> one ActionProposal`。`agent.py` 保持 Agent 门面，`loop.py` 明确承载真实 sequence；Director/Broadcast 后续沿用相同生命周期，但分别实现自己的入口、Strategy、State、Prompt、namespace 和 Proposal 类型。只有第二个真实实现产生稳定重复代码后，才提取跨 Agent 的公共 runner。

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
Character Proposal
Director Stimulus / Segment Completion
System / Tool / Player Input
                |
                v
       Validator / Committer
                |
                v
Committed WorldSegment / WorldEvent
                |
                v
       PerceptionProjector
                |
                v
strict PerceptionFrame[当前 Character]
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

`PerceptionFrame` 只能包含 Projector 判定可见的 Location Fact/Info、candidate、pending response、affordance 与 evidence；不能携带完整 `LocationView`。Pydantic 能验证 Frame 结构，却不能证明信息真的可见，硬可见性仍由 Projector 和 Golden Trace 保证。

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

EventSession 是调度容器；WorldEvent 是已提交事实，不能共用 ID 或生命周期。

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

Location 只保存 Event 引用，Event 正文仍由唯一 WorldEvent Ledger 持有。

### `PerceptionFrame` / `ActionProposal`

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
| `respond` | 恰好一个 character target 与非空 content |
| `wait` | 带有世界语义的等待，包含 description 与 `nextWakeup` |
| `no_op` | Scheduler yield，只包含 `nextWakeup` |

没有 `move` variant，也没有 `locationId` 或多目标 `targetIds`。`interact` 的 character/object 类型显式进入 JSON，不能靠 ID 猜测；单目标避免部分授权、部分提交与顺序歧义。`utter/respond` 不能指向 object。

`agentId` 是唯一 actor 字段，由受信 `PersonActAgent` 从 `spec.agent_id` 注入；CognitionStrategy/模型输出只允许提供 `action + evidenceIds` 等候选内容，不能选择 actor。最终 `decide` 返回经过 strict Pydantic validation 的 `ActionProposal` 对象；跨进程时再用 `model_dump_json(by_alias=True)` 生成 camelCase wire JSON。

World contract 与 `PersonActAgent.decide` 已使用这组 strict/frozen 类型；这证明单次 Persona cognition 与 Proposal authority boundary 已落地，但仍只是 Agent 边界，不是 World Commit。

### `SegmentDraft` / `WorldSegment`

```text
SegmentDraft
  segment_draft_id / generation_id / based_on_world_version
  character_proposal_ids / temporal_constraints / action_transitions
  object_deltas / location_fact_changes / location_info_changes
  bridge_events / event_candidates / perceptual_footprints / evidence_ids

WorldSegment
  segment_id / generation_id / based_on_world_version / world_version
  world_time_start / world_time_end / measured_elapsed_ms
  ordered_facts / state_deltas / location_revision_refs
  event_evidence / commit_seq
```

Director 只返回 Draft；Binder 绑定时间；Validator 返回诊断；只有 Committer 分配 world version 与 commit sequence。

### `BroadcastPlan` / `RenderJob`

```text
BroadcastPlan
  broadcast_plan_id / event_session_id / based_on_world_version
  source_log_seq_start / source_log_seq_end / projection_mode
  camera_viewpoint / reveal_scope / transition
  target_render_duration_ms / artistic_reason / evidence_ids

RenderJob
  schema_version / producer_id / world_id / runtime_session_id
  render_id / event_session_id / event_revision / attempt_id
  based_on_world_version / log_seq_start / log_seq_end
  evidence_transaction_ids / title / location / characters
  structured_beats / estimated_play_ms / content_hash
```

Render Plugin 必须对 JSON 再做自己的边界校验，并基于 canonical input 重算 `content_hash`。Python 对象的类型正确不能替代跨进程接收方验证。

## 7. Dynamic Render Plugin 边界

现有插件已支持 Beat 校验、DSL 编译、Render Queue、黑屏、Event 切换和 `TEMP_SCENE` 注入。完整 Runtime 后续只增加一条投递边界：

1. `POST /api/runtime/render-jobs` 接收不可变 RenderJob；
2. 同 `render_id + content_hash` 重试幂等，同 ID 不同 hash 拒绝；
3. 返回 `accepted / validation_error / ready / playing / completed / failed / unknown`；
4. `timeline.json` 保留为 Fixture Adapter；
5. 保存最小 played cursor，避免进程重启后全部重播。

WorldEvent 不直接进入 WebGAL Plugin。Python Runtime 内的 Broadcast 只输出 BroadcastPlan，确定性 Render Planner 读取 committed Event Log 生成 RenderJob，再由 RenderGateway 发送。

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
- strict/frozen Pydantic 基础模型、Persona 私有 Memory/State 与检索能力已经存在；
- World-owned contract 已定义固定 `ActionProposal` envelope、六类 discriminated action union，以及 character/object typed target。
- `PersonActAgent.decide` 已实现 prepare/perceive/retrieve/plan/propose，一次只返回一个 Proposal；
- prepare 校验 ownership、world time/world version 并判定新日；perceive 在全 EVENT stream 上做稳定 attention/canonical novelty，并按 write policy 写本人 EVENT Memory；retrieve 将本轮全部 Observation 与历史记忆分离，再执行 literal + recency/relevance/importance 排序与显式 touch；plan 先基于本轮感知/召回生成新日私有计划，再结合 state、部分 schedule、当前 slot 剩余时长与 focus 生成 action；propose 注入 actor 并校验 capability、typed affordance 与 evidence；
- 同一 Agent 的 `decide` 由实例锁串行化；只有最终 Proposal 校验成功后，才一次替换包含 frozen state/memory/trace 的 private snapshot。

仍未完成：

- reflection 与 commit feedback；`decide` 只累计 reflection trigger state，不在未提交阶段生成反思；
- Event Scheduler、World commit、Director、Render 与真实模型质量；
- 持久化 Memory adapter 与完整咖啡 Golden Trace。

### 8.2 下一条咖啡 Golden Trace

```text
1. 编译 Anon/Soyo 的受限 Manifest，固定各自 spec digest
2. 建立 EventSession A：Anon / Soyo 在咖啡店
3. 建立隔离 EventSession B：Tomori 独处
4. Projector 从 A 的 committed snapshot 为 Anon 构造 strict PerceptionFrame
5. Fixture `PersonActAgent.decide` 返回一个 utter ActionProposal：“轮到我们了，要这个吗？”；`agentId=anon` 由 spec 注入
6. Director Fixture 返回 SegmentDraft
7. Binder + Validator + Committer 追加 WorldSegment
8. Recognizer 更新 WorldEvent；Projector 只为 Soyo 生成 direct-interaction candidate
9. Scheduler 在新 world version 调用 Soyo 的 `decide`，得到一个 respond Proposal；Tomori 不获得该 evidence
10. Broadcast Fixture 返回 BroadcastPlan；Render Planner 生成 RenderJob
11. RenderGateway 投递 Dynamic Render，接收完整状态
12. 固定输入、时钟、ID 与随机源后，重放得到相同 Trace
```

## 9. 明确延期

- Memory 的持久化 adapter、生产 embedding 与 recency/relevance/importance 权重调优；
- Reflection 阈值与真实 Prompt；
- Character Skill 自动提取与微调；
- Director 长期目标函数、RL 与复杂刺激策略；
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
- Director 不能直接写 Persona Memory 或 Ledger；
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
- 保留 Pydantic runtime validation 与 `with_types()` 非校验行为的契约测试；
- 所有时钟、ID、Fixture CognitionStrategy 输出和随机源可注入；
- 不引入 LangGraph。

完成条件：ruff、pyright strict、pytest 与既有 npm 测试均通过。

### Phase 1｜持久化 Memory 与 append-only World 存储

- 增量实现第 6 节 strict/frozen Pydantic Model；
- 保持已实现的 scoped in-memory stream/retriever 与 namespace 拒绝边界；
- 增加持久化 Memory adapter；
- 对 JSONL 与 SQLite 做小型 spike，再冻结一期 adapter；
- 实现 World Ledger / Generation Trace append 与重放读取；
- 保持 Manifest Compiler 为纯函数；scope 固定派生为 `project/{project_id}/persona/{agent_id}`，project/format version 纳入 spec digest，Trace 固定该 digest。

完成条件：跨 namespace 读取失败；同一输入序列化稳定；重启后能重放相同 snapshot。

### Phase 2｜补齐 feedback 并接 Fixture Runtime

- 保持现有 prepare/perceive/retrieve/plan/propose 语义，不迁移 `Persona.move()` 或 `execute`；
- 在 World Commit 之后以独立 feedback 输入实现 outcome memory 与 conditional reflection；
- 为 Director/Broadcast 分别建立最小 typed contract 与 Fixture 策略，不复制 PersonAct 内部拓扑；
- 用 `RunnableConfig` 统一 run name、tags、metadata 和 callback adapter；
- 每个外部或模型边界显式 Pydantic parse。

完成条件：三个 Fixture 都返回各自 strict Model；无 `Any` 泄漏越过公开边界，无 `if agent_type` 万能分发。

### Phase 3｜单 Event 咖啡 Golden Trace

- 实现 Snapshot、Projector、Director Fixture、Binder、Validator、Committer 与 Recognizer；
- 跑通 Anon utter -> committed Event -> Soyo Observation -> Soyo respond；
- 保存每步 snapshot、Observation、Memory write、retrieval、Proposal、Draft、diagnostic 与 evidence。

完成条件：固定 seed/fixture 下 Trace 一致；Tomori 不获得咖啡店 Observation；非法知识或对象跳变被拒绝。

### Phase 3.1｜地点事实与到访前发现

- 实现版本化 Location Fact/Info 与同 world version 查询；
- 确定性过滤无效、已知和不可披露 Info；
- Director 只返回 `NoOp / DiscoveryPlan`；
- 新传播行为必须先提交 Event，再投影给 PersonAct。

### Phase 4｜隔离多 Event 调度

- 同 Event 串行，`addressed_to_me` 优先，`no_op + next_wakeup` 防空转；
- 证明隔离后再用 `asyncio.TaskGroup` 并行 Event；
- 共享角色、对象、资源或因果集合交集时返回 `EVENT_NOT_ISOLATED`。

### Phase 5｜Broadcast 与 Dynamic Render

- 实现 `BroadcastPlan -> deterministic Render Planner -> RenderJob`；
- 增加 append、幂等、乱序/gap 检查与持久 cursor；
- 增加 Runtime ingress 与状态回传；
- 保持 `timeline.json` 只是 Fixture。

### Phase 6｜端到端后再接真实模型

- Python unit：strict models、Memory、Projector、Binder、Validator、Committer、Recognizer、Scheduler、PersonAct；
- Python integration：两个 Event 的 Fixture Golden Trace；
- Node contract：RenderJob ingress、幂等、恢复和状态回传；
- 端到端 smoke：Python Runtime -> Node Plugin -> WebGAL；
- 只有失败能被 Trace 精确归因后，才按 PersonAct -> Director -> Broadcast 顺序接真实 ChatModel。

真实模型接入必须保持相同 Pydantic 与语义校验边界，只替换 `decide` 内的 CognitionStrategy，不扩张 NPC Manifest 权限。

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

Fixture Vertical Slice 的所有权、强类型边界、临时调度规则与验收目标已足够开工。Temporal Binder 最终算法、Director latency、跨 Event barrier、真实 Prompt 和 30 分钟库存仍未解决；若临时规则破坏已确认不变量，必须回写[未决问题](open-questions.md)和[难点账本](difficulty-ledger.md)。

执行任何 Git 写操作前仍须确认 `git rev-parse --show-toplevel` 指向本工程；未经用户明确授权，不执行 `git init`、commit 或历史改写。

可复制的启动指令：

> 在 `/Users/bytedance/workspace/other-project/generative_go_world` 继续实现 Agent Runtime。先完整阅读 `AGENT.md`、`wiki/index.md`、`wiki/agent-runtime-implementation.md`、`wiki/decisions.md` 和 `wiki/difficulty-ledger.md`。现行技术栈是 Python 3.12 + `langchain-core==1.6.1`；输入、输出和持久化边界使用 strict/frozen Pydantic Model，静态检查使用 pyright strict。外部 Event Scheduler 独占循环，只调用 `PersonActAgent.decide`；一次调用只为 `spec.agent_id` 返回一个固定 envelope + discriminated action union 的 strict/frozen `ActionProposal`，跨 wire 时再以 camelCase JSON 序列化。不得实现/暴露 `Persona.move()`，不得迁移 Maze/path/tile/movement/execute。LangChain `Runnable.with_types()` 不做运行时校验，当前不使用 LangGraph。所有 Agent 只产生 Proposal/Plan，只有 World Committer 和 Render Gateway 产生副作用。Reflection/commit feedback、Director、World Commit 与完整 Runtime 尚未实现。修改后按 AGENT.md 运行检查；不要擅自 commit 或改写历史。
