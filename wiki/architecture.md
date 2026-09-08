# 多事件 AI Native 世界剧场架构

## 1. 方案原点

本方案来自以下连续判断：

1. 不再自研或侵入底层 Galgame 播放器，复用成熟的 WebGAL/MyGO Runtime、Live2D、背景、音乐和 UI。
2. 在引擎外增加 Agent 编排与动态编译 Plugin；给定角色 Skill 或后续自有模型后，系统根据事件背景与世界状态生成剧情。
3. 世界中可同时发生多个 Event。玩家不是沿预写分支树前进，而是在多个正在发展的角色事件间选择观察视角。
4. 目标运行形态中，Character Agent、Director Agent 和 Broadcast Agent 在后台异步运行，提前积累可播放 Render，WebGAL 只动态展示当前 Render；当前代码已实现 Dynamic Render MVP 与 PersonAct 单次认知 Slice，完整三类 Agent Runtime 尚未落地。
5. 为掩盖生成延迟，目标上让 Agent 流在首播前预运行约 30 分钟，并在观看中持续补货。

这不是“给传统 Galgame 加聊天框”，而是把作品的未来从静态脚本改为由世界状态与 Agent 持续生成。

更精确地说，Timeline 不是展示组件，而是 Agent 世界的执行语义；MyGO/WebGAL 动态解析只是该世界面向 Galgame 的 Projection Adapter。完整的导演层与导播层研究框架见[导演与导播层](director-broadcast.md)。

## 2. 最高优先级不变量

### 2.0 世界权威与演出权威必须分开

```text
World / Agent Runtime                    MyGO / WebGAL Render Backend
---------------------                    ----------------------------
world_time / world_version               current scene / sentence
角色状态、位置、目标、局部认知             背景、Live2D、台词、BGM、动画
ActionProposal / InteractionSession      RenderArtifact 播放
World current state / WorldEventHistory  Viewer Cursor / 播放完成信号
```

**已确认决策：**真实世界状态与 Galgame 引擎没有所有权关系。外部 World / Agent Runtime 负责决定“发生了什么”；Render Backend 只决定“怎样把已经发生的 Event 演出来”。

因此：

- 玩家点击下一句不能推进 `world_time`；
- Auto/Fast、逐帧动画、`SYNCFC` 心跳都不是 World Tick；
- 暂停、黑屏、浏览器降频、历史回放或播放器崩溃不能回滚或阻塞已经提交的世界；
- Agent 不能读取 WebGAL Stage/GameVar/Backlog 作为世界真相；
- Render 失败只能影响可观看性，不能撤销 WorldEvent。

### 2.0.1 Proposal 是意图，Commit 才是事实

这是自研 NPC ADK 与 World Runtime 之间最重要的分界：

```text
perceive -> retrieve -> plan -> ActionProposal
                                  |
                                  v
                     Validator / WorldUpdater
                                  |
                                  v
                         Committed WorldEvent
```

`ActionProposal` 只回答“这个角色基于当前可见信息想做什么”。它携带 actor、所依据的 world version、Action 和 evidence，但不能证明动作成功，也不能代表交互目标作出了回应。

例如 `Anon -> interact(character: Soyo)` 只表示 Anon 发起交互；Soyo 是否接受、拒绝或回应，必须由已提交 Event 投影到 Soyo 后，再由 Soyo 自己的 `decide` 决定。`Anon -> interact(object: coffee-machine-01)` 也只表示尝试操作物品；对象是否存在、是否可操作以及状态如何变化，由 Validator/Committer 裁决。

因此，Stanford `execute.py` 的寻路和逐 tile movement 不属于本项目 Agent：Agent 终止于 Proposal，World 执行层才拥有持久副作用。

论文层面的依据是 Sandbox time-step action loop：Agent 在每个 time step 感知和行动，Sandbox Server 更新共同世界后进入下一步。`world_tick / world_version / relational current state / atomic commit / canonical WorldEventHistory` 是本项目为可重启、多 Event 和冲突治理新增的工程契约，不冒充论文原实现。

证据分类：

- **Confirmed｜论文：**[§3.1.1](https://arxiv.org/html/2304.03442v2#S3.SS1.SSS1)、[§4.3.1](https://arxiv.org/html/2304.03442v2#S4.SS3.SSS1) 和 [§5](https://arxiv.org/html/2304.03442v2#S5) 明确描述 Sandbox time step、Agent action loop 与 Server 更新共同世界。
- **Confirmed｜源码：**当前底座是兄弟目录中的 [MyGO 3.1.1 / WebGAL 4.5.19](../../MyGO_v3.1.1_ForScript/webgal-engine.json)；固定 Bundle 的 [`webgalsync`](../../MyGO_v3.1.1_ForScript/assets/index-982c8eaa.js) 上报 Scene/Sentence/Stage，外置服务只将其用于 Render 播放确认。
- **Interpretation：**世界推进与演出推进是两个不同的因果系统，不能共享权威游标。
- **Decision：**外部 World / Agent Runtime 持有世界权威，Render Plugin/Adapter 动态编译其产物，MyGO/WebGAL 只播放。

### 2.0.2 World Time、生成耗时与 EventStaff 时间

模型调用的真实耗时仍应记录在 Generation Trace 中，但 D-044 已经否定“让 Director 把 latency 解释成角色犹豫、桥接动作或对象完成”的旧方案。

当前边界是：

```text
Character generation elapsed
  -> 作为调用 provenance 与调度观测
  -> 不自动等于角色沉默、移动或环境完成

Committed Character action
  -> Validator / WorldUpdater
  -> 可推进 world_version，并按冻结规则写 event time

Committed process-start Event
  -> EventStaff(next_check_at / release guard)
  -> 到期检查后才能形成 process-completion Event

Committed WorldEvent
  -> Broadcast 可省略、压缩或艺术化展示
  -> Render Time 不反向改变 World Time
```

因此必须区分：

- **Measured Generation Time：**Character、Director 或工具调用真实消耗的 wall time，只是可审计证据；
- **World Time：**客观世界的单调时间，由 World Runtime 的明确规则推进；
- **EventStaff Time：**已经启动的环境过程的检查时间、释放窗口和失效条件；
- **Render Time：**Broadcast 把 committed World Timeline 投影成多长的演出；
- **Viewer Time：**玩家当前消费到了哪个 Render。

MVP 仍须冻结“每次 Character commit 如何推进 world time”以及 `next_check_at` 使用故事时间还是受控 wall clock。无论最终取值为何，都不能仅因模型等待了若干秒就宣布咖啡完成，也不能让 Director 自由解释这段 latency。Director 只在 World 暴露的 EventStaff affordance 内操作。

### 2.1 原引擎零侵入

WebGAL/MyGO 是可替换的黑盒 Render Backend：

- 不修改原始引擎源码；
- 不 patch `assets/index-982c8eaa.js`；
- 不依赖或修改内部 Redux Store；
- 不要求 WebGAL 理解 Agent、Event、World State 或长期记忆；
- 不把 WebGAL Backlog、Save 或 GameVar 当作世界权威数据；
- 原有静态作品的构建和播放方式必须继续可用。

新增能力只能放在独立 World / Agent Runtime 与 Render Plugin/Adapter，通过虚拟文件挂载或当前版本已有同步协议驱动播放器。

### 2.2 非确定性生成与确定性渲染分离

```text
Character Agent 产生 ActionProposal
  -> WorldChangeValidator 校验 version、affordance 与不变量
  -> WorldUpdater 原子更新当前状态并追加 WorldEvent
  -> DirectorRunner 只消费 committed Event，维护 EventStaff
  -> EventStaff release 再经 Validator / WorldUpdater 形成客观 Event
  -> Render Planner 生成 RenderArtifact
  -> Render Compiler 校验并编译
  -> Render Queue 发布不可变 Render
  -> WebGAL/MyGO 确定性播放
```

模型不能直接控制 DOM、引擎 Store 或 Live2D Node，也不能绕过校验发送任意 DSL。

## 3. 产品形态

### 3.1 Event Hub

玩家首屏是当前世界的 Event Hub：

```text
World Time 08:30
├── Anon / Soyo            RiNG     进行中 · 3 条未读
├── Tomorin                天文馆    Live · 已观看至 #18
└── Saki / Mutsumi / Uika  月之森    新事件 · 尚未观看
```

每个 Event 至少展示参与角色、地点和世界时间、当前摘要、状态、未读数，以及 `继续观看 / 跳到 Live / 查看 Log`。

### 3.2 观察者语义

第一阶段玩家是观察者：

- 世界推进不取决于玩家是否观看；
- 切换视角只改变展示窗口，不改变世界时间；
- 玩家离开后，该 Event 仍可发展并写入 Log；
- 玩家返回时可从观看游标继续、先看摘要再跳到 Live，或浏览完整 Log。

允许玩家介入属于后续阶段。介入前必须冻结 Choice/Action 如何进入 World Event，以及如何使其它 Event 的未来失效。

## 4. Agent Runtime、Render Plugin 与确定性组件

```text
Character Skills / Persona / 可选模型
              -> Character Agents
              -> WorldChangeValidator / WorldUpdater
              -> WorldEventHistory
              -> DirectorRunner / EventStaff Queue
              -> EventStaff release -> WorldUpdater
              -> Broadcast Agent
              -> Render Planner
              -> Validator / Dynamic Compiler
              -> Render Queue
              -> Plugin Host -> WebGAL/MyGO
```

### 宏观算法来源与新增层

```text
Generative Agents 认知语义参考
  Perception / Memory Retrieval / Planning / Reflection
  不迁移 Sandbox Loop / Persona.move / movement / execute
             |
             v
本项目外部 Event Scheduler
  选择角色 -> 调用一次 PersonActAgent.decide -> 消费一个 Proposal
             |
             v
本项目新增的受限后台过程与观看层
  Director Agent：只管理由 committed Event 触发的 EventStaff
  Broadcast Agent：Event 选择、视角编排、World Timeline -> Render Timeline
             |
             v
本项目新增的最小世界治理
  EventSessionRunner / AgentViewBuilder / Validator / WorldUpdater / WorldEventHistory
             |
             v
MyGO / WebGAL Render Backend
```

所以“原底座是 Generative Agents，再增加 Director Agent 和 Broadcast Agent”在宏观上成立，但 Director 不是剧情控制器。确定性部分维护世界不变量、提交协议和 EventStaff affordance；具体过程时长可以来自场景/Object Process 配置或受限策略，但 Director 不能跳过已经提交的 process-start 事实。

上图的 `Action` 是 Generative Agents/论文中的认知阶段术语。本项目的工程接口统一叫 `propose()`：Agent 只能给出结构化意图或计划，真正的世界提交、RenderJob 生成和播放器副作用都在 Agent Runnable 之外完成。

### 三类 Agent 的共用基础设施

Character、Director、Broadcast 可以共享 Python strict Pydantic、Model Gateway、调用 Trace 和 `RunnableConfig` 等 plumbing，但当前不假设它们共享同一个 AgentLoop、Memory 或 CognitiveController。当前唯一真实 loop 是 `agent/personact/loop.py`，由 `PersonActAgent.decide` 调用；外部 EventSessionRunner 不属于 AgentLoop。Character 的公共边界是 Runner 调用一次 `decide`，得到一个 strict/frozen `ActionProposal`：

```text
observe/perceive -> retrieve -> plan -> propose
                     |
                     v
           Runtime validate / commit
                     |
                     v
          observe_outcome -> reflect
```

- `PersonActAgent.decide` 每次只为 `spec.agent_id` 产生一个 Proposal；`agentId` 由受信代码注入，Agent 不选择下一角色或下一轮；
- `decide` 内部使用带显式类型注解的 Runnable 组织 prepare/perceive/retrieve/plan/propose；当前不使用 LangGraph；
- Runnable 只是 Agent 的内部实现，不是 World Runtime 的顶层抽象。`Proposal -> validate -> commit -> outcome` 由 Event/World Runtime 显式编排；
- `agent/memory/` 提供通用 Record/Store/Retriever，但每个 Agent 使用独立 namespace；
- 模型、Prompt、Tool 与调用级观测优先复用 LangChain Core 接口；所有不受信输入/输出显式经过 strict/frozen Pydantic Model，静态接线由 pyright strict 检查；
- `agent/personact/loop.py` 显式承载当前真实 loop，`agent.py` 只保留 PersonActAgent 门面与 snapshot 事务；`agent/personact/`、`agent/director/`、`agent/broadcast/` 各自保留 typed input、state、strategy、prompt、namespace 和 proposal。跨 Agent 公共 runner 等第二个真实实现出现后再提取；
- Anon、Soyo 等是 Character Agent 类型的配置实例，不为每个 NPC 建独立源码目录；
- `execute` 不属于通用 Agent 能力。Character/Director/Broadcast 只输出各自受限的 Proposal/Decision/Plan，副作用分别由 WorldUpdater 或 Render Gateway 完成。`Runnable.with_types()` 只提供类型/Schema 元数据，不做 runtime validation；任何未来框架 checkpoint 也不能替代 SQLite 当前状态、WorldEventHistory 或 commit protocol。

当前仓库已实现 NPC DIY Manifest Compiler、Persona Memory/State 与检索基础、新的 World proposal contract，以及 `PersonActAgent.decide` 的单次认知 Slice。Reflection/commit feedback、Director、World Commit、Event Scheduler/Runtime 和 Broadcast 尚未落地。

三类 Agent 的阶段语义不同：Character 规划自己的行动，Director 选择受限 EventStaff 操作，Broadcast 规划观看投影。因此现在只共享基础设施，不先抽象同一个 `CognitiveController`；等两个以上真实实现出现重复控制逻辑后再提取。

### Character Agent

- 输入 Persona、目标、关系、局部记忆和角色可见的事实；
- 提出行动、台词和情绪变化；
- 不直接修改全局世界；
- 不能读取尚未获知的异地事件。

### AgentViewBuilder / Character Perceive

`perceive` 的输入只来源于已经提交、并按 Character 硬隔离后的 `AgentView`。Director 既不是 Character perceive 的直连输入，也不能直接写入 Character Memory。

```text
Character ActionProposal
EventStaff release
System / Tool / Player Input
                │
                ▼
      Validator / WorldUpdater
                │
                ▼
       committed WorldEvent
                │
                ▼
          AgentViewBuilder
                │
                ▼
       AgentView[当前 Character]
                │
                ▼
PersonActAgent.decide 内部
→ perceive → retrieve → plan → propose

提交反馈后或达到 Persona 自身阈值
→ observe_outcome / reflect
```

认知隔离至少分成四层：

```text
WorldEvent       客观发生了什么
PerceptCandidate 某 Character 有机会感知的字段
Observation      该 Character 实际注意到的内容
Memory           该 Character 如何保存、解释或误解 Observation
```

例如同一个 `coffee_ready` Event：当前 Session 中且在咖啡机附近的 Anon/Soyo 可以收到不同措辞但同一 `source_event_id` 的 Candidate；已 split 且离开地点的 Tomori 不收到。Director 只 release Staff，实际收件人由 AgentViewBuilder 根据当前 UnionPart、地点、渠道和字段权限决定。

去掉 Maze 后，世界侧和角色侧明确分权：

```text
committed World state/events
  -> AgentViewBuilder：硬可见性、字段裁剪、affordances
  -> AgentView：指定 world version 的不可变局部输入
  -> PersonActLoop：attention、novelty、Memory、plan、propose
```

AgentViewBuilder 不使用 LLM，也不把全局 WorldEventHistory 暴露给 Character。Character Skill 可以影响注意和解释，但不能扩大硬可见范围。`AgentView.affordances` 替代原版 spatial memory + Maze address 提供合法对象与互动入口。

一期采用 EventSession root 分区调度：相互隔离且不共享对象/因果的 root 可并行计算；同一 root 内按稳定顺序给 Character 决策机会，并在每个非 no-op Proposal 后立即提交最新事实。

```text
parallel: root A runtime | root B runtime | root C runtime

inside root A:
  Runner -> Anon.decide -> validate/commit -> build next AgentView
  Runner -> Soyo.decide -> validate/commit -> build next AgentView
```

串行顺序只代表“谁获得下一次机会”，不强制谁必须说话。被明确点名的 Character 会收到 mandatory/direct-interaction Candidate，但仍可回应、拒绝、延后或 `no_op`。`no_op` 只进入 Decision Trace，不伪造 WorldEvent；待回应关系由 `interaction_requests` 保持。

### Director Agent

- 只消费一个尚未处理的 committed Event，或一条到期待检查的 EventStaff；
- 读取由 Runtime 裁剪的 `DirectorView`，不读取完整 Snapshot、未提交 Proposal 或 Character 私有认知；
- 只能从 `event_staff_affordances` 中选择 `enqueue / keep / release / cancel / no_op`；
- release 只兑现 enqueue 时已经校验的 completion contract，不能临场创造其它 Event；
- 不生成角色台词、行动、Session 变更、Narrative Constraint 或 WebGAL DSL。

Director 的“可见性”是操作授权，不是角色感知。完整字段、咖啡因果链和 merge/split 语义见[EventStaff Director 与 Broadcast](director-broadcast.md)。

### Broadcast Agent

- 只读取稳定到某个 watermark 的 committed WorldEvent；
- 选择当前值得展示的 Event，为 Event Hub 生成标题、摘要和推荐理由；
- 可省略、压缩、切镜、蒙太奇、摘要或加入明确标记的艺术推断；
- 每个 beat 保留 `source_event_ids + truth_kind`，不能改变世界事实或 Character Memory；
- 不读取 Director 私有调用过程、未提交 Proposal 或 Viewer 之外的角色秘密。

Event 选择、时间压缩、信息控制、连续性和 Buffer-aware 策略仍需单独研究，但这些算法只有观看投影权。

### WorldChangeValidator / WorldUpdater

这是确定性的世界写入口：

- 校验 expected world version、actor、target、evidence 和当前 affordance；
- 拒绝同一 Character 同时出现在两个地点；
- 校验对象前后状态、共享资源、Location/WorldFact 和因果引用；
- 校验 EventSession merge/split 与 interaction request 不变量；
- 校验 DirectorDecision 是否只引用当前 `DirectorView` 中的 Staff affordance；
- 在一个 SQLite transaction 中更新受影响当前行、Staff/request、追加 WorldEvent 并推进 `current_version`；
- 冲突时返回结构化诊断给原候选来源；Director 不负责修补 Character Proposal。

### EventSessionRunner / Clock

- 位于 World / Agent Runtime，不位于 MyGO/WebGAL；
- 一次 Character Event step 是提交候选边界，不等待同一 Session 全体角色组成 lockstep wave；
- 下一次 Character 唤醒来自已提交 Event、`interaction_requests`、显著 Candidate、外部输入或退避规则；
- DirectorRunner 使用独立 committed-event cursor，并按 `next_check_at` 查询 pending EventStaff；
- MyGO 播放速度、Sentence 游标和动画帧不参与调度；
- 隔离 root 可以并行做模型计算，但 MVP 以 expected version 和单写者串行提交；
- Generation Trace 记录模型 elapsed；它不能自行完成咖啡或被 Director解释为角色行为；
- World time 与 EventStaff 检查时钟的精确绑定仍需 Golden Trace 冻结。

### Character Skill Induction（后续算法方向）

- 输入 MyGO 番剧视频、字幕/ASR、说话人、画面动作、情绪和关系上下文；
- 输出带 Evidence Clip 的 Character Skill，而不是一句宽泛 Persona 总结；
- Skill 至少描述稳定价值观、目标/禁忌、关系条件策略、语言风格、情绪转移、动作偏好和冲突处理；
- 先人工构建 Gold Character Skill 验证 World Runtime，再研究视频自动提取，避免 Runtime 与 Skill Induction 错误互相污染；
- 当前为 Open Research Direction，尚无实现和效果证据。

### WorldEventHistory

- `world_events` 只保存已经由 WorldUpdater 提交的客观历史；
- Character 台词可以成为“某人说了什么”的事实，但其中的意愿不自动成为对象状态变化；
- EventStaff release、System/Tool/Player Input 也必须经过相同 Validator/WorldUpdater；
- 稳定 ID、source、reply/cause、append-only 和 root-at-commit 由确定性代码维护；
- MVP 不为“Event Recognizer”保留只有一对一改名作用的空抽象层。

## 5. 最小状态模型

### World Current State / Read View

```text
world_id / current_version / world_time
locations / current_world_facts / agent_world_states / objects
stable_event_session_nodes / current_root_bindings
pending_interaction_requests
pending_event_staff / director_event_cursor
```

这里的 Read View 是从 SQLite 关系型当前状态在指定 committed version 构建的不可变输入，不代表 MVP 持久化逐版本 Snapshot。Character 私有 Goal/Knowledge 位于自己的 State/Memory，不进入公共 World current state。

### Location World Model

地点是一等 World Model，而不是每次 Prompt 临时生成的背景描述：

```text
Location identity
  + current LocationFact
  + current LocationInfo
  + WorldEventHistory refs by location_id
  = immutable LocationView @ world_version / world_time
```

- `LocationFact` 保存客观环境事实，例如 `羽丘高中 / facility.piano.present=true`。普通台词、Agent Memory 或 Event summary 都不能把它覆盖成 `false`；只有带 expected world version 和 evidence 的正式 World change 经 Validator/WorldUpdater 后才能变更。
- `LocationInfo` 保存地点关联且可被发现的时效信息，例如“Tomori 每周六在 RiNG 独自 Live”。它可以有 recurrence、有效期和披露范围，但不等于某次活动已经发生。
- 地点历史直接按 `world_events.location_id` 查询，不复制 Event 正文或生命周期。实际到达、开演和结束仍分别来自 committed WorldEvent。
- LocationModel 是确定性数据模型/聚合视图，不是 LLM Agent；`unknown` 与 `false` 必须区分。
- World/Event 契约统一使用 `location_id`；`scene_id` 只属于 Render/WebGAL 场景资源，并由 Render Planner 根据地点映射，不能成为第二套世界地点身份。

角色到达地点或获得合法远程信息渠道后，AgentViewBuilder 在同一 committed version 上确定性生成可见 Candidate：

```text
go_to(location) accepted
-> Query LocationView at the same world_version
-> deterministic filter: effective / unknown-to-visitor / disclosure-allowed
-> existing public Fact/Info -> AgentViewBuilder
   or state-changing delivery -> Character/System Proposal
      -> Validator / WorldUpdater -> WorldEvent -> AgentViewBuilder
-> PersonAct perceive / MemoryWriteIntent
```

Director 不参与信息披露。读取既有公开 LocationInfo 可以直接形成 PerceptCandidate；需要朋友告知、发送消息或发布公告时，必须由对应 Character/System 自己提交行为。无需候选时返回确定性空结果。完整契约、冲突规则和 Golden Trace 见[地点 World Model](location-world-model.md)。

### WorldChangePlan

```text
source_kind / source_id / based_on_world_version
actor_id? / evidence_ids / current_world_time
validated_state_changes
session_transition?
interaction_request_transition?
event_staff_transition?
world_events_to_append
```

这是 Validator 产出的 typed transient plan，不建立 `world_segments` 表。Character Proposal 和 EventStaff Decision 都只能先变成这种受限 Plan，再由 WorldUpdater 原子提交。Generation elapsed 单独留在调用 Trace，不混入任意 World patch。

### EventSession Node / Current Interaction Partition

```text
session_id / agent_id / root_session_id / updated_world_version
```

每个 Character Agent 有一个稳定节点；当前参与者集合由相同 root 的节点派生。merge/split 只更新 root binding，不创建新的会话历史实体。

### WorldEvent / InteractionRequest

```text
WorldEvent
  event_id / world_version / event_order / event_type
  source_kind / source_id / actor_id? / target_id? / location_id?
  root_session_id_at_commit? / in_reply_to_event_id? / caused_by_event_id?
  started_at / ended_at? / content? / details_json

InteractionRequest
  request_event_id / request_kind / requester_agent_id / recipient_agent_id
  status / resolution_event_id? / updated_world_version
```

`world_events` 是世界层唯一 append-only 互动历史，即使玩家没观看也必须存在。`interaction_requests` 是当前待处理状态，只引用发起/解决 Event，不复制正文。两者都不同于只记录当前播放历史的 WebGAL Backlog。

### EventStaff / Director Cursor

```text
EventStaff
  event_staff_id / session_id / source_event_id
  staff_kind / subject_type / subject_id / completion_event_type
  status / created_world_version / created_world_time
  next_check_at? / release_event_id? / details_json

World
  director_event_cursor
```

EventStaff 是待完成的客观过程，不是已发生历史；它以稳定 Session 节点为归属，释放时再解析当前 root 和成员。Director cursor、enqueue/no-op 必须同事务推进，release/cancel 与对象状态、WorldEvent 和 world version 必须同事务提交。

### Viewer Cursor

```text
viewer_id / selected_session_node_id?
current_render_id? / current_sentence?
played_artifact_refs / updated_at
```

观看游标属于 Render 侧，不参与世界归约；多 root 切换下的最终存储形态仍须在 Phase 7 通过 Render ingress/恢复测试冻结。当前 Dynamic Render 只在内存保存 selected/active/played，进程重启会丢失。

### Render

```text
render_id / based_on_world_version / source_event_ids
content_hash / estimated_play_ms
structured_beats with per-beat source_event_ids + truth_kind
compiled_webgal_script / provenance_sidecar
```

Render 是 WorldEvent History 的可视化投影，不是世界事实。已发布 Render 不再原地修改；WebGAL DSL 未承载的 Event 追溯关系保存在 sidecar。

### 三个前沿

```text
Committed World Frontier  Agent Runtime 已提交到哪个 WorldEvent/version
Ready Render Frontier     哪些已提交 Event 已被编译成可播放 Render
Viewer Frontier           玩家正在消费哪个 Event/Render/Sentence
```

开播前约 30 分钟 warm-up 的目的，就是让 Committed World / Ready Render 前沿位于 Viewer 前方。三者允许产生距离，但必须持续观测；30 分钟实际运行换来多少可播放时长不是常数。

## 6. 主链路

### 后台推进

```text
EventSessionRunner 从已提交 AgentView 选择一个 Character
-> Character 基于局部认知返回一个 ActionProposal
-> WorldChangeValidator 检查 version、evidence、affordance 和硬不变量
-> WorldUpdater 原子更新当前状态/request/session，并追加 WorldEvent
-> AgentViewBuilder 为下一位 Character 构建新视图
-> DirectorRunner 消费新的 committed Event
   -> enqueue/no-op 与 cursor 原子提交
   -> 到期 Staff 的 keep/release/cancel 受 release guard 限制
-> EventStaff release 经 Validator/WorldUpdater 形成新的 WorldEvent
-> Broadcast Agent 更新 Event Hub
-> Render Planner 选择待可视化 Log
-> Compiler 生成并校验 Render
-> Ready Render 入队
```

### 玩家切换 Event

```text
记录当前 Viewer Cursor
-> Plugin Host 覆盖黑屏
-> 查询目标 Event Ready Render
   ├── 有：注入 WebGAL/MyGO
   └── 无：保持黑屏，等待生成
-> 确认开始播放
-> 解除黑屏
```

第一阶段只运行一个 WebGAL/MyGO 实例。并发发生在世界和 Event 层，不通过同时启动多个播放器实现。

## 7. 播放前约 30 分钟 Warm-up

“提前跑约 30 分钟 Agent 流”首先指**真实预运行时长**，不是程序承诺“精确生成 30 分钟 Render”。在玩家开始观看之前：

- Character 已经生成并提交一段真实 WorldEvent 历史，Director 已处理相应 EventStaff；
- Broadcast/Compiler 至少把开播近端内容转换成 Ready Render；
- 玩家从这段历史的起点或某个可选入口开始观看；
- Agent Runtime 在 Viewer 前方继续生成、提交、处理 Staff 和编译。

必须分别度量：

```text
warmup_wall_time              开播前 Agent 实际运行多久，目标约 30 min
committed_world_lead          World Frontier 领先 Viewer 的世界时间
ready_render_playable_time    已可播放内容按 Viewer Time 估算能撑多久
production_to_consumption     后台产出速度 / 玩家消费速度
```

只有 Event Plan 不能消除播放卡顿；真正的安全库存必须至少包含已提交演员剧本，近端还必须有 Ready Render。30 分钟 warm-up 是否足够、各 Event 如何分配算力，以及播放过程中如何维持水位，属于必须实验回答的问题。

## 8. 状态边界

### 已由源码确认

- 已有结构化 `story.json -> WebGAL DSL` 校验和编译链；
- 开发服务器可虚拟挂载 `config.txt / start.txt / player.json`；
- 固定 Bundle 会自动连接同源 `/api/webgalsync`；
- `TEMP_SCENE` 可接收完整临时场景，但会重置 WebGAL 内部 Stage、Backlog 和 GameVar；
- WebGAL 每秒上报当前 Scene、Sentence 和 Stage。

### Dynamic Render MVP 已实现

- `extensions/dynamic-render/` 提供 Timeline 编译、运行时队列、Host UI 和 WebSocket 适配；
- `projects/rain-after/timeline.json` 提供三 Event 渐进 Render 示例；
- `npm run dynamic -- --project rain-after` 可选启用，普通静态制作和播放不受影响；
- Render 状态按 `ready -> loading -> playing -> played` 推进，队列耗尽后回到黑屏；
- Timeline 热更新只允许追加未来 Render，不能改写 active/played Render。

### 仍是设计

- 除 PersonAct 单次认知 Slice 外的完整三类 Agent 执行链；
- 多 Event 世界归约；
- Event Log 与 Viewer Cursor；
- 约 30 分钟真实 Warm-up、领先库存维持与多 Event 产消比；
- 玩家介入后的跨 Event 重规划。
