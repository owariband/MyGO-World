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
WorldTransaction / WorldEvent Ledger     Viewer Cursor / 播放完成信号
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
                    Director / Validator / Committer
                                  |
                                  v
                         Committed WorldEvent
```

`ActionProposal` 只回答“这个角色基于当前可见信息想做什么”。它携带 actor、所依据的 world version、Action 和 evidence，但不能证明动作成功，也不能代表交互目标作出了回应。

例如 `Anon -> interact(character: Soyo)` 只表示 Anon 发起交互；Soyo 是否接受、拒绝或回应，必须由已提交 Event 投影到 Soyo 后，再由 Soyo 自己的 `decide` 决定。`Anon -> interact(object: coffee-machine-01)` 也只表示尝试操作物品；对象是否存在、是否可操作以及状态如何变化，由 Validator/Committer 裁决。

因此，Stanford `execute.py` 的寻路和逐 tile movement 不属于本项目 Agent：Agent 终止于 Proposal，World 执行层才拥有持久副作用。

论文层面的依据是 Sandbox time-step action loop：Agent 在每个 time step 感知和行动，Sandbox Server 更新共同世界后进入下一步。`world_tick / world_version / snapshot / resolver / atomic commit / canonical WorldEvent Ledger` 是本项目为可重放、多 Event 和冲突治理新增的工程契约，不冒充论文原实现。

证据分类：

- **Confirmed｜论文：**[§3.1.1](https://arxiv.org/html/2304.03442v2#S3.SS1.SSS1)、[§4.3.1](https://arxiv.org/html/2304.03442v2#S4.SS3.SSS1) 和 [§5](https://arxiv.org/html/2304.03442v2#S5) 明确描述 Sandbox time step、Agent action loop 与 Server 更新共同世界。
- **Confirmed｜源码：**当前底座是兄弟目录中的 [MyGO 3.1.1 / WebGAL 4.5.19](../../MyGO_v3.1.1_ForScript/webgal-engine.json)；固定 Bundle 的 [`webgalsync`](../../MyGO_v3.1.1_ForScript/assets/index-982c8eaa.js) 上报 Scene/Sentence/Stage，外置服务只将其用于 Render 播放确认。
- **Interpretation：**世界推进与演出推进是两个不同的因果系统，不能共享权威游标。
- **Decision：**外部 World / Agent Runtime 持有世界权威，Render Plugin/Adapter 动态编译其产物，MyGO/WebGAL 只播放。

### 2.0.2 生成完成驱动的 `World Time = Actual Runtime`

**用户确认的关键判断：**

> 可以改变，因为不影响，我们导播 Agent 自己知道分辨这个时间是否是正常的。
>
> 从 WebGAL 的渲染感知而言，这个根本不会出现，最后编译成脚本，Galgame 里面不存在等待几秒的说法，除非确定的艺术手法。
>
> 这就是我们可以用 `World Time = Actual Runtime` 的原因。然后做这个才相当于是算法，或者这个确实就是算法。如果进一步，还可以考虑怎么从视频角色性格提取 Character Skill（MyGO 番剧 → Character Skill），这些都是算法。

2026-08-21 进一步修正：Agent 响应时间可以成为世界时间，但不能因此再启动一个独立 timer，在 Agent 尚未返回时自行宣布排队结束、咖啡煮好或角色已经做完动作。这里的 `Actual Runtime` 是**一次生成波次实际花费的时间，在生成完成后被绑定进世界段**，不是 wall clock 对叙事状态的旁路写入权。

当前冻结的方向是：

```text
Committed World Snapshot @ T0
  -> Character Agent(s) 响应，记录各自 actual start/end
  -> Director 读取输出与 measured latency
  -> Director Temporal/Causal Completion
       补齐先后、等待、持续、对象变化、Event 边界和桥接动作
  -> Temporal Binder 在 Director 返回后取得本波次完整 actual elapsed
  -> Minimal Validator 检查单调时间、角色占用、知识边界和因果引用
  -> 原子提交 World Segment [T0, T1]

Broadcast Temporal Projection
  无叙事信息的生成空档 -> 默认省略
  无可见变化的空档   -> 压缩或切到其它 Event
  角色真实犹豫/沉默  -> 可作为明确艺术手法保留
  赶路/长动作        -> 转场、蒙太奇或摘要

Render Timeline / WebGAL Script
  只包含导播选择表达的演出时间，不复刻每一秒 World Time
```

因此需要区分：

- **Measured Generation Time：**Character、Director 和工具调用真实消耗的时间；
- **World Time：**在生成波次提交时，依据 measured elapsed 单调前移；生成过程中没有独立 timer 越过 Agent 提交新剧情事实；
- **Semantic Event Time：**Director 在已知 Character 输出和耗时后，为同一 World Segment 补出的动作、等待、转折和对象变化区间；
- **Render Time：**导播把一段 World Timeline 投影成多长的可见演出；
- **Viewer Time：**玩家当前看到了哪段 Render。

一次生成请求仍须携带 `started_at / returned_at / based_on_world_version / generation_id`，但其主要用途是让 Director 完成当前世界段，而不是因为外部 timer 已经偷偷推进了世界就把它机械判为过期。只有外部输入、另一已提交波次或共享角色冲突真的改变了基础 Snapshot 时，结果才需要重基、修补或拒绝。

Director 自己的响应也产生耗时。候选实现是让 Director 输出相对顺序、区间关系和可伸缩权重，等其返回后再由不创造剧情的 Temporal Binder 把结果绑定到完整 measured span；这避免为了“解释 Director 的耗时”再次调用 Director 而无限递归。该绑定算法仍属 Open Research，见[难点账本 H-025](difficulty-ledger.md#h-025director-补完自身耗时的递归)。

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
Character Agent 产生 Actor Performance / ActionProposal
  -> Director 对本轮结果做 Temporal/Causal Completion
  -> Temporal Binder / Minimal Validator 绑定实耗并校验不变量
  -> World Committer 原子提交 WorldSegment / WorldTransaction
  -> Event Recognizer 从 Segment 形成/更新 WorldEvent
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
              -> Director Temporal/Causal Completion
              -> Generation Coordinator / Temporal Binder
              -> Minimal Validator / World Committer
              -> World Transaction Ledger
              -> Event Recognizer / World Event Ledger
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
本项目新增的叙事控制层
  Director Agent：本轮生成后的时间/因果补完，以及更高层 Narrative Constraint
  Broadcast Agent：Event 选择、视角编排、World Timeline -> Render Timeline
             |
             v
本项目新增的最小世界治理
  Generation Coordinator / Temporal Binder / Validator / Ledger / Event Recognizer
             |
             v
MyGO / WebGAL Render Backend
```

所以“原底座是 Generative Agents，再增加导演 Agent 和导播 Agent”在宏观上成立。确定性部分仍存在，但它只硬编码世界不变量、测量和提交协议，不预写“咖啡固定 120 秒煮完”之类的故事答案。

上图的 `Action` 是 Generative Agents/论文中的认知阶段术语。本项目的工程接口统一叫 `propose()`：Agent 只能给出结构化意图或计划，真正的世界提交、RenderJob 生成和播放器副作用都在 Agent Runnable 之外完成。

### 三类 Agent 的共用基座

Persona、Director、Broadcast 共享 Python strict Pydantic 契约策略、LangChain Core Runnable/`RunnableConfig` 调用约定和模型适配基础设施，但不共用 Persona 的具体认知模块或顶层循环。Character 的公共边界是外部 Scheduler 调用一次 `PersonActAgent.decide`，得到一个 strict/frozen `ActionProposal`；跨 wire 时再显式序列化 JSON：

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
- Runnable 只是 Agent 的内部实现，不是 World Runtime 的顶层抽象。`Proposal -> Director -> validate -> commit -> outcome` 仍由 Event/World Runtime 显式编排；
- `agent/memory/` 提供通用 Record/Store/Retriever，但每个 Agent 使用独立 namespace；
- 模型、Prompt、Tool 与调用级观测优先复用 LangChain Core 接口；所有不受信输入/输出显式经过 strict/frozen Pydantic Model，静态接线由 pyright strict 检查；
- Persona、Director、Broadcast 各自保留具体 cognitive strategy、prompt templates、触发频率和输出类型；
- `execute` 不属于通用 Agent 能力。三类 Agent 只输出 Proposal/Plan，副作用由 World Committer 或 Render Gateway 完成。`Runnable.with_types()` 只提供类型/Schema 元数据，不做 runtime validation；任何未来框架 checkpoint 也不能替代 World Snapshot、Ledger 或 commit protocol。

当前仓库已实现 NPC DIY Manifest Compiler、Persona Memory/State 与检索基础、新的 World proposal contract，以及 `PersonActAgent.decide` 的单次认知 Slice。Reflection/commit feedback、Director、World Commit、Event Scheduler/Runtime 和 Broadcast 尚未落地。

三类 Agent 的阶段语义不同：Persona 规划角色行动，Director 规划 Segment/Stimulus，Broadcast 规划观看投影。因此共享的是流程协议，不是同一个 `perceive.py / plan.py / execute.py`。

### Character Agent

- 输入 Persona、目标、关系、局部记忆和角色可见的事实；
- 提出行动、台词和情绪变化；
- 不直接修改全局世界；
- 不能读取尚未获知的异地事件。

### Perception Projector / Persona Perceive

> `perceive` 的输入不是直接来源于 Director，而是来源于已经提交、并按角色隔离后的 Observation。Director 只是 WorldEvent 的来源之一。

感知的上游不是单一 Director，而是统一提交后的事件流：

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
PerceptionFrame[当前 Character]
                │
                ▼
PersonActAgent.decide 内部
→ perceive → retrieve → plan → propose ActionProposal

提交结果后或达到 Persona 自身阈值
→ observe_outcome / reflect
```

Director 是 WorldEvent 的候选来源之一，也是本轮时间/因果补完者；它不是 `PersonActAgent.decide` 内部 perceive 的直连数据源。任何来源都必须先经过统一提交，之后才能按角色做认知隔离。

Director 可以提出或补完突然下雨、前一位顾客离开、咖啡机提示音、短信到达和新角色进入 Scene 等外部事件，但不能把“咖啡好了”直接写进 Anon 的 Memory。正确链路必须是：

```text
Director 提出/补完 coffee#42 ready
→ Validator 检查对象前后状态
→ Committer 提交 WorldEvent
→ PerceptionProjector 判断各角色能看到哪些字段
→ `decide` 内部 perceive 决定是否注意并记住
```

Event 也不只来源于 Director：Anon 说话和 Soyo 接受邀请来自 Character Proposal；咖啡完成和天气变化可以来自 Director；玩家介入来自 Player Input；工具回调来自 System/Tool Input。它们统一提交后才成为客观 WorldEvent。

认知隔离至少分成四层：

```text
WorldEvent       客观发生了什么，拥有全局身份和生命周期
PerceptCandidate 某角色有机会感知的字段
Observation      某角色实际注意到的内容
Memory           角色如何保存、解释或误解 Observation
```

例如同一个 `coffee#42 ready`：Anon 可以得到“我们的咖啡好了”；Soyo 可以得到“咖啡机响了，我们的订单可以取了”；店内路人最多知道“有一杯咖啡完成”；店外 Tomori 没有 Observation。它们共享 `source_event_id`，但可见字段不同。

去掉 Maze 后，感知拆为世界侧与角色侧两层：

```text
Committed WorldSegment
  -> PerceptionProjector：判断该角色有机会感知哪些字段
  -> PerceptionFrame：本轮局部状态、候选刺激和 affordances
  -> `decide` 内部 perceive：注意力、新颖性与记忆固化
```

Projector 不使用 LLM，也不把全局 Ledger 暴露给 Character。它只依据语义场景、参与者、消息接收者、感知通道和字段权限裁剪候选。Persona 可以因 Character Skill、目标和当前互动而注意到不同候选，但不能注意到 Projector 没有提供的隐藏事实。

`PerceptionFrame` 还必须给 `plan()` 提供当前可执行 affordances，替代原版由 spatial memory + Maze address 提供的地点/物体选择。角色只在真实到访、看见地图或被告知后获得 `KnownPlace`；知道一个地点存在不等于当前能对它执行动作。

一期采用 Event 分区调度：相互隔离的 Event 可以并行；同一 Event 内的 Persona 按稳定顺序逐步生成并在每一步后提交该 Event 的最新事实。不同 Event 只有在没有共享角色、共享对象或因果依赖时才允许并行，存在依赖时由 Runtime 建立 barrier 或合并。

```text
parallel: Event A runtime | Event B runtime | Event C runtime

inside Event A:
  Scheduler -> Anon.decide -> validate/commit -> project next observation
  Scheduler -> Soyo.decide -> validate/commit -> project next observation
  ...
```

串行顺序只是“谁获得下一次决策机会”，不是强制谁必须说话。每次 `decide` 可选择 `act / interact / utter / respond / wait / no_op` 中的一项；Scheduler 在一次提交后自然把机会交给下一个参与者。若上一条 Event 明确点名 Soyo，Soyo 的 Frame 带 `addressed_to_me / pending_response`，但她仍可回复、拒绝、延后或 `no_op`。

`no_op` 只记录到 Decision Trace，并让出 Event 内决策游标，同时携带下一次唤醒条件；它本身不应为了占位而污染 WorldEvent Ledger。只有“等待某人回应”“等待咖啡完成”等对世界有语义的主动等待，才生成 `wait` Proposal/Event。

循环内仍禁止一个 Persona 直接改写另一个 Persona 的 Scratch；每一步都必须先形成 Proposal，再提交成 Event，下一位角色才能通过 PerceptionProjector 看到。

### Director Agent

- 读取全局状态和活动剧情线程；
- 在 Character Agent 结果返回后，读取本轮输出、响应耗时和起始 Snapshot，补齐 World Segment 的先后、持续、Event 边界、对象结果和必要桥接；
- 提出 Narrative Constraint、优先级和可感知刺激，维护节奏、伏笔和角色弧；
- 输出的是待校验 `SegmentDraft / TemporalConstraintGraph`，不能绕过 Validator 直接写 Ledger；
- 不负责输出 WebGAL DSL。

导演层的“生成后时间/因果补完”是 Character Runtime 能闭合 Event 的基础职责，不再全部推迟到高层剧情优化之后；目标函数、线程状态、刺激选择和自治/可控权衡仍是后续研究。

### Broadcast Agent

- 选择当前值得展示的 Event；
- 为 Event Hub 生成标题、摘要、推荐理由和展示优先级；
- 决定聚焦哪段互动，但不改变世界事实；
- 读取 Committed Segment 中由 Director Completion 给出的区间语义标注，例如 `narrative_action / character_hesitation / environment_wait / generation_gap`；
- 通过省略、压缩、切镜、蒙太奇、摘要或艺术停顿，把 World Timeline 投影为 Render Timeline。

导播层当前只冻结事实不可改写与 Temporal Projection 职责。它可以选择怎样表达区间，但不能把 Director 标记的 `generation_gap` 重新解释成角色犹豫，或把角色动作降格为纯技术空档；Event 选择、时间压缩、信息控制、连续性和 Buffer-aware 策略仍需单独研究。

### Minimal Validator / World Committer

这是确定性、但不替故事做时长决定的组件：

- 拒绝同一角色同一时间出现在两个地点；
- 校验角色认知权限；
- 校验对象前后状态、共享资源声明和因果引用；
- 校验 LocationFact/Info revision、地点存在性、披露范围和信息传播渠道；
- 检查 Director 产出的区间是否单调、有界且可提交；
- 发现冲突时返回结构化诊断，由 Director 修补，而不是用硬编码 duration 强行裁决；
- 以递增世界版本提交 State Delta；
- 只有基础 Snapshot 真被其它已提交事实改变时，才拒绝或重基生成结果。

### Generation Coordinator / Clock

- 位于外部 World / Agent Runtime，不位于 MyGO/WebGAL；
- 以生成波次为提交边界，记录完整 `generation_started_at / generation_finished_at / elapsed`；
- Agent 返回后才由 Director 完成语义时间，再由 Binder/Committer 推进 `world_time / world_version / commit_seq`；
- 它不在 Agent 推理期间用 timer 自动完成排队、冲煮或其它叙事动作；
- 下一波 Agent 唤醒来自上一已提交 Segment 的开放线程、互动请求、显著感知或外部输入；
- MyGO 播放速度、Sentence 游标和动画帧不参与 Scheduler 决策；
- 多 Event Agent 可以异步生成，但如何组成一个全局 completion wave、如何处理共享角色和跨地点因果仍待实验；
- 测试必须录制 Agent 输出和 measured latency，才能重放同一 World Segment。

### Character Skill Induction（后续算法方向）

- 输入 MyGO 番剧视频、字幕/ASR、说话人、画面动作、情绪和关系上下文；
- 输出带 Evidence Clip 的 Character Skill，而不是一句宽泛 Persona 总结；
- Skill 至少描述稳定价值观、目标/禁忌、关系条件策略、语言风格、情绪转移、动作偏好和冲突处理；
- 先人工构建 Gold Character Skill 验证 World Runtime，再研究视频自动提取，避免 Runtime 与 Skill Induction 错误互相污染；
- 当前为 Open Research Direction，尚无实现和效果证据。

### Event Recognizer

- 只消费通过校验并提交的 WorldSegment，不把 Character 的单方自然语言直接当客观 Event；
- Director 可以提出 Event 边界、类型和持续区间，Recognizer 检查其 evidence 并赋予稳定 ID；
- 确定性部分负责身份、引用、append-only 和 schema，不用固定时长表替 Director 决定 Event 何时结束；
- Event 是否发生与是否生成完整 Render 分离。

## 5. 最小状态模型

### World Snapshot

```text
world_version / world_tick / global_facts
character_locations / character_goals
character_relationships / character_knowledge
locations / location_fact_heads / location_info_heads
active_event_ids
```

### Location World Model

地点是一等 World Model，而不是每次 Prompt 临时生成的背景描述：

```text
Location identity
  + versioned LocationFact
  + versioned LocationInfo
  + WorldEvent Ledger refs by location_id
  = immutable LocationView @ world_version / world_time
```

- `LocationFact` 保存客观环境事实，例如 `羽丘高中 / facility.piano.present=true`。普通台词、Agent Memory 或 Event summary 都不能把它覆盖成 `false`；只有带 expected revision 和 evidence 的正式 World change 经 Validator/Committer 后才能变更。
- `LocationInfo` 保存地点关联且可被发现的时效信息，例如“Tomori 每周六在 RiNG 独自 Live”。它可以有 recurrence、有效期和披露范围，但不等于某次活动已经发生。
- `LocationEventRef` 只是对唯一 WorldEvent Ledger 的索引，不复制 Event 正文或生命周期。实际到达、开演和结束仍分别来自 committed WorldEvent。
- LocationModel 是确定性数据模型/聚合视图，不是 LLM Agent；`unknown` 与 `false` 必须区分。
- World/Event 契约统一使用 `location_id`；`scene_id` 只属于 Render/WebGAL 场景资源，并由 Render Planner 根据地点映射，不能成为第二套世界地点身份。

角色形成前往地点的有效意图后，Runtime 必须在其下一次基于目的地信息做决策前执行 pre-arrival discovery：

```text
go_to(location) accepted
-> Query LocationView at the same world_version
-> deterministic filter: effective / unknown-to-visitor / disclosure-allowed
-> Director: NoOp or DiscoveryPlan
-> Validator
-> existing Fact/Info discovery -> PerceptionProjector
   or state-changing delivery -> Committer -> WorldEvent -> PerceptionProjector
-> PersonAct perceive / MemoryWriteIntent
```

Director 决定的是“通过公开日程、海报、朋友告知、消息或到场后的声音，让角色获得什么发现机会”，不能直接写角色 Memory。读取已存在的公开 LocationInfo 可以直接形成 PerceptCandidate；只有会改变客观世界的消息、告知或公告才先提交 WorldEvent。无需选择且没有候选时走确定性空结果，不调用模型。完整契约、冲突规则和 Golden Trace 见[地点 World Model](location-world-model.md)。

### World Segment / Generation Span

```text
generation_id / based_on_world_version
generation_started_at / generation_finished_at / measured_elapsed
segment_world_start / segment_world_end
character_outputs / director_completion
temporal_relations / object_deltas / event_candidates / evidence
validation_status / commit_seq
```

`measured_elapsed` 是时间证据，`director_completion` 决定它在语义世界中如何形成等待、动作、并发和转折；二者不能互相冒充。

### Event Channel

```text
event_id / participants / location / status
started_at_tick / head_log_seq / summary
based_on_world_version
```

### Event Log Entry

```text
event_id / log_seq / world_tick / actor_id
entry_type / content / visible_to / state_delta
causal_event_ids
```

Event Log 是世界层 append-only 历史，即使玩家没观看也必须存在。它不同于只记录当前播放历史的 WebGAL Backlog。

### Viewer Cursor

```text
event_id / last_viewed_log_seq
current_render_id / current_sentence
```

`head_log_seq - last_viewed_log_seq` 决定未读数。观看游标不参与世界归约。

### Render

```text
render_id / event_id / based_on_world_version
log_seq_start / log_seq_end / content_hash
estimated_play_ms / structured_beats
compiled_webgal_script
```

Render 是 Event Log 的可视化投影，不是世界事实。已发布 Render 不再原地修改。

### 三个前沿

```text
Committed World Frontier  Agent Runtime 已生成、补完并提交到哪个 Segment
Ready Render Frontier     哪些已提交 Segment 已被编译成可播放 Render
Viewer Frontier           玩家正在消费哪个 Event/Render/Sentence
```

开播前约 30 分钟 warm-up 的目的，就是让 Committed World / Ready Render 前沿位于 Viewer 前方。三者允许产生距离，但必须持续观测；30 分钟实际运行换来多少可播放时长不是常数。

## 6. 主链路

### 后台推进

```text
Generation Coordinator 从已提交 Snapshot 开启一轮 generation
-> Character Agents 基于局部认知生成行为，记录各自响应耗时
-> 若 Proposal 涉及前往地点，Runtime 读取同版本 LocationView，Director 可提出信息发现机会
-> Director 对完成波次做 Temporal/Causal Completion
-> Binder 在 Director 返回后绑定完整 actual elapsed
-> Minimal Validator 检查硬不变量；失败则返回 Director 修补
-> Committer 原子提交 WorldSegment / Transaction / Event evidence
-> Event Recognizer 赋予稳定 Event 身份并更新 Ledger
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

- Character/Director 已经生成并提交一段真实 World Segment 历史；
- Broadcast/Compiler 至少把开播近端内容转换成 Ready Render；
- 玩家从这段历史的起点或某个可选入口开始观看；
- Agent Runtime 在 Viewer 前方继续生成、补完和编译。

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

