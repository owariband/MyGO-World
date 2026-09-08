# 难点、卡点与代价账本

这是一份项目级问题账本，专门保留用户在讨论中指出的“难点”“卡点”“不合理之处”“为了获得某种能力必须支付的代价”，以及后来对旧方案的纠正。**每个卡点必须先写成一个可继续追问的问句**，再记录当前回答、权衡和代价。它不等同于 [未决问题](open-questions.md)：未决问题面向下一步研究；本账本保留问题是怎样出现、否决过什么、目前付出了什么代价，避免架构讨论在多轮迭代后丢失原始矛盾。

## 记录规则

1. 用户明确指出新的难点、卡点、代价、错位或不合理设计时，必须新增条目或更新原条目。
2. 每条的主标题或表格核心问题必须使用问句，并以 `？` 结束；不能只写成“某方案存在某风险”的结论句。
3. 每条至少记录：提出时间、问题、为什么构成难点、得到什么而付出什么、当前回答或研究方向、仍未解决之处。
4. 用户原话适合保留时使用引用；其余内容标记为“忠实转述”或“推导风险”，不把推导冒充用户结论。
5. 条目不能因方案变化而删除。旧答案应标记为“已否决”或“已被修正”，并链接新决策；被否决的是答案，不是问题。
6. [决策记录](decisions.md)回答当前决定；本账本回答我们在解决什么问题、为什么难、为当前回答付出了什么代价。

## 两个母问题

### Q-ROOT-001｜如何串联起 Agent Runtime 和底层 Galgame 引擎，同时避免再次陷入底层框架？

这是项目最早期的工程母问题。它派生出 H-001、H-002、H-003、H-006 和 H-023：世界由谁运行、两层通过什么产物连接、没有产物时显示什么、播放器信号能否推进世界，以及内部注入协议失效时如何恢复。

### Q-ROOT-002｜去掉 Maze 和部分 Entity 渲染后，一个外在事件如何处理？我们该如何权衡设计？

这是当前的算法母问题。它派生出 H-010、H-011、H-012、H-015、H-016、H-018、H-024 和 H-025：外在事件从哪里来、怎样持续和结束、对象状态挂在哪里、谁能观察、多个 Event 如何对时，以及生成式补完怎样避免自相矛盾。

## 状态说明

- `OPEN`：问题仍未形成可执行答案。
- `PARTIAL`：方向已确定，但算法、契约或实验尚未闭合。
- `DECIDED`：边界已经确认，仍可能存在实现成本。
- 旧答案被否决时，问题仍保留，并把状态改为 `DECIDED` 或 `PARTIAL`；否决历史写在“当前回答 / 研究方向”和详述中。

## 卡点总表

| ID | 首次提出 | 状态 | 核心问题 | 为当前回答支付的代价 | 当前回答 / 研究方向 |
|---|---|---|---|---|---|
| H-001 | 2026-08-20 | DECIDED | 如何串联起独立 Agent Runtime 和底层 Galgame 引擎，同时避免再次重造或绑定底层框架？ | 放弃对播放器内部循环的完全控制，必须接受黑盒协议和适配成本 | 用 `WorldEvent / RenderArtifact` 作为边界，MyGO/WebGAL 只做 Render Backend，所有世界能力外置 |
| H-002 | 2026-08-20 | PARTIAL | 在不修改 MyGO/WebGAL 源码的前提下，Plugin 如何动态加载 Runtime 产物并处理确认、断线和升级？ | 只能依赖版本相关的 `TEMP_SCENE / webgalsync` 或 iframe 重建，ACK、升级和调试能力较弱 | 独立 Host/Adapter，锁版本并做契约测试 |
| H-003 | 2026-08-20 | DECIDED | 当目标 Event 没有 Ready Render 时，播放器应该展示什么，系统又怎样承担这个选择的体验代价？ | 生成跟不上时会暴露黑屏，产品体验直接受 Buffer 健康度影响 | 黑屏由 Host 控制，世界与播放器不互相阻塞 |
| H-004 | 2026-08-20 | PARTIAL | 多个 Event 如何同时推进、分别留存 Log，并允许玩家随时切换观察视角？ | 不能再使用一条传统线性脚本；需要独立 Event 身份、历史、观看游标和跨 Event 因果 | 世界并发推进，展示层只选择一个观察窗口 |
| H-005 | 2026-08-20 | DECIDED | 什么才是真正的 Timeline，为什么按 Event 分组的 Render Queue 不足以承担世界时间？ | 必须额外设计世界执行语义，而不能靠 UI/队列名掩盖问题 | 已否决“Render Queue 就是 Timeline”；Timeline 属于 Agent World，Render Queue 只是投影缓存 |
| H-006 | 2026-08-20 | DECIDED | MyGO/WebGAL 没有 World Tick 时，世界时间应由谁持有，又如何与 Viewer Time 分离？ | 失去借用播放器点击/帧循环推进世界的便利，需单独维护 World Runtime | 世界推进位于 headless Agent Runtime，播放器只推进 Viewer Cursor |
| H-007 | 2026-08-20 | DECIDED | 如何用开播前约 30 分钟的 Agent 预运行吸收生成延迟，避免玩家观看时卡顿？ | 首播必须等待预热；持续运行需要额外模型、存储和多 Event 预算 | Agent Runtime 在播放前先跑约 30 分钟，并持续保持领先库存 |
| H-008 | 2026-08-20 | DECIDED | Agent 推理和工具响应耗时如何计入 World Time，同时不被机械渲染成等待？ | 世界生成速度受真实模型延迟影响，吞吐和叙事时间耦合；测试也更难 | 响应耗时允许进入 World Time，生成完成后由 Director 补完，Broadcast 再做观看投影 |
| H-009 | 2026-08-21 | DECIDED | 为什么不能让外部 timer 在 Agent 尚未返回时独立推进剧情状态，替代方案是什么？ | 若采用旧答案，需要处理迟到意图、追溯取消、并发 timer 和大量过期状态 | 已否决 timer 旁路推进；wall clock 只提供本轮耗时，结果返回后再补完和提交 |
| H-010 | 2026-08-21 | OPEN | Anon 与 Soyo 排到什么时候、咖啡煮到什么时候、咖啡发生了什么，应该由谁决定？ | 放弃简单确定性离散事件表后，时间、结果和因果一致性必须由 Agent 生成并被校验 | Character 生成行为；Director 在响应后补齐 World Segment 的时间、结果和桥接 Event |
| H-011 | 2026-08-21 | PARTIAL | 去掉 Maze 和部分 Entity 渲染后，一个外在事件如何处理？我们该如何权衡设计？ | 几何成本下降，语义世界建模与连续性成本上升 | 不重造 2D 物理；用生成后的语义 Segment、Event/Observation 和最小 Validator 恢复必要世界语义 |
| H-012 | 2026-08-20 | PARTIAL | 如何把 Generative Agents 的临时 tile 四元组升级成具有全局身份、生命周期和共享历史的一等 Event？ | 必须新增 Event/Segment/Ledger，不能原封不动复用源码数据结构 | 将 `Intent / generated segment / committed WorldEvent / Observation` 分开 |
| H-013 | 2026-08-20 | DECIDED | 如何复用 Generative Agents 的认知循环，同时避免照搬其浏览器 mailbox、串行执行和顺序偏差？ | 引入 Eino 依赖与一层 ADK/领域契约适配，但避免重复实现通用 Agent 基础设施 | Go + Eino ADK；自定义 `PersonActAgent` 内部用 Compose Graph 承载认知流程，世界循环仍由 MyGO Runtime 管理 |
| H-014 | 2026-08-20 | DECIDED | Character Runtime、Event、Director 和 Broadcast 应按什么顺序开发，才不会让高层优化掩盖底座问题？ | 高层算法研究必须延后，短期 Demo 的“好看程度”可能较低 | 先做 Character Runtime + Event；但保留最小 Director Segment Completion 作为时间闭合所需能力 |
| H-015 | 2026-08-20 | PARTIAL | 全局 Runtime 知道多处事件时，如何保证每个角色仍只依据自己的观察、记忆和误解行动？ | 需要为同一事实维护客观状态、角色观察、记忆和误解多个层次 | Director 可读全局；Character 只读 Observation；禁止把全局剧情直接塞进角色记忆 |
| H-016 | 2026-08-21 | PARTIAL | Director 和 Broadcast 应如何划分时间判定权，避免把世界事实与演出剪辑混在一起？ | 分层增加了一次生成与校验成本，但不分层就会把世界事实和演出剪辑混在一起 | Director 补完并标注世界段的语义时间；Broadcast 只消费标注决定观看方式 |
| H-017 | 2026-08-21 | OPEN | 多 Event 的生成速度和玩家消费速度持续漂移时，如何长期维持约 30 分钟领先库存？ | 需要持续算力、背压、降级和 Event 间预算，否则仍会追上黑屏 | 同时观测 Generation Frontier、Ready Render Frontier 与 Viewer Cursor |
| H-018 | 2026-08-20 | OPEN | 多个 Event 是否共享严格同一时间，并发 Agent 响应和跨地点因果应如何汇合？ | 全局排序可牺牲并行性；完全独立又会破坏共享角色和因果 | 由 Director 对一个完成波次做全局 Temporal/Causal Completion；精确 commit 规则待实验 |
| H-019 | 2026-08-20 | OPEN | 如何从 MyGO 番剧视频中提取带证据的 Character Skill，使角色具有稳定人格策略？ | 需要视频切分、说话人/角色对齐、证据片段、关系条件行为和评测数据 | 先人工 Gold Skill，再研究视频到 Skill 的归纳 |
| H-020 | 2026-08-20 | OPEN | 微调是否必须先沉淀 Goodcase/Badcase，什么数据和评测闭环成熟后才值得做 SFT/DPO？ | 数据闭环、标注和评测成本高；过早微调会把 Runtime 缺陷固化进模型 | 先沉淀 Golden/Badcase 与可归因失败，再决定训练 |
| H-021 | 2026-08-20 | DECIDED | 如何让项目的核心成为对标 JD 的 Agent 算法，而不是只剩 DSL 解析和动态加载？ | 必须投入真正的 World Runtime、Memory、Planning、Event、时间补完和 Eval，而不是只做演示层 | 解析被定位为算法体系的副产物，核心追求是 Timeline-Driven Agent World Runtime |
| H-022 | 2026-08-20 | PARTIAL | 如何区分 Generative Agents 论文事实、官方源码行为和本项目新增机制，避免把设计冒充论文结论？ | 写论文学习、简历或设计时需要持续做证据分类，表达成本更高 | 明确 `论文 Confirmed / 源码 Confirmed / 本项目 Decision / Open Research` |
| H-023 | 2026-08-20 | PARTIAL | 动态注入依赖固定 Bundle 的内部协议时，如何控制版本漂移、无 ACK、鉴权和重连风险？ | 上游升级可能失效，且缺 request ID、ACK、鉴权和可靠重连 | 版本锁定、契约测试、黑屏保护和 iframe/虚拟文件降级 |
| H-024 | 2026-08-21 | PARTIAL | 不硬编码故事时长后，如何防止 Director 补完产生资源冲突、对象跳变和不可重放结果？ | 需要日志、Assembler、Validator、Golden Trace 和修复循环；一致性不能再天然来自固定状态机 | Director 只返回创意 Resolution；Runtime 确定性组装权威字段并校验，仍需继续扩展资源冲突规则 |
| H-025 | 2026-08-21 | OPEN | Director 自己的补完调用也消耗时间时，如何避免为解释自身 latency 而无限递归？ | 若再次调用去解释这段耗时会形成递归；若忽略又违反“Agent 响应时间可计时” | 提交器先记录未解释的 Director latency；是否下一波闭合、两阶段补完或只交给 Broadcast 省略，待实验 |
| H-026 | 2026-08-21 | PARTIAL | 去掉 Maze 后，`Persona.perceive()` 如何获得外部信息，同时避免上帝视角、串行先手偏差和过度物理模拟？ | 必须新增语义场景、感知通道、字段裁剪、注意力与 provenance；认知链比扫描附近 tile 更显式 | `Committed WorldSegment -> PerceptionProjector -> PerceptionFrame -> Persona attention -> Observation/Memory`；待咖啡与对话 Trace 验证 |
| H-027 | 2026-08-21 | DECIDED | 一期如何调度 Persona，既先把流程串起来，又允许角色知道何时回应或选择什么都不做？ | 同一 Event 吞吐受串行限制；跨 Event 并行必须识别共享角色、对象和因果依赖 | 同一 Event 内稳定串行并逐步提交；隔离 Event 并行；Scheduler 给决策机会，Frame 给待回应信号，Agent 可 `no_op` |
| H-028 | 2026-08-21 | DECIDED | 纯 Agent Runtime 的目录怎样表达 Agent、Memory、Event 与 World 的真实所有权，而不是把所有模块扁平堆在一起？ | 需要约束 Go package 依赖、namespace 与循环引用，目录比简单平铺多一层 | `agent/{memory,personact,director,broadcast}` 与 `event/`、`world/` 平级；Memory 共用实现但隔离数据 |
| H-029 | 2026-08-21 | DECIDED | Persona、Director、Broadcast 是否应共享同一套认知流程，从而让 cognitive_modules、memory_structures、prompt_template 成为通用基座？ | 共享过少会重复实现执行设施；共享一张 Graph 又会把 Persona 语义强塞给导演导播 | 对外共享 Eino `adk.Agent`/Runner/模型基础设施，各 Agent 保留专属 Graph/strategy、Prompt、namespace 和 Proposal |
| H-030 | 2026-08-22 | DECIDED | Generative Agents 自造的 Loop、模型调用、Prompt、解析、重试和存储，哪些应继续自研，哪些应交给成熟库？ | 引入 Eino 及 adapter 会增加依赖与版本升级成本，但能删除大量重复且脆弱的 plumbing | Eino 接管 Agent 执行与模型基础设施；MyGO 只自研认知语义、Memory 权限/融合和 World/Event 治理 |
| H-031 | 2026-08-22 | DECIDED | 地点事实、地点关联信息和发生于该地点的 Event 如何持久化，才能防止 Agent 杜撰或前后口径漂移？ | 需要增加版本化 Location 模型、Fact/Info CAS、地点索引和额外查询成本 | Location 是 World 层一等模型；Fact/Info append-only，Event 只挂 Ledger 引用，只有 Committer 可修改 |
| H-032 | 2026-08-22 | PARTIAL | 角色前往某地点时，Director 如何利用地点已有 Info 决定其获知方式，又不把全局知识直接灌进角色 Memory？ | 每次到访多一次候选查询；有歧义时增加 Director 调用，并需验证披露与时空条件 | Runtime 查询 LocationView 并先确定性过滤；Director 返回 NoOp/DiscoveryPlan，已有信息由 Projector 投影，新传播行为先提交 Event，最终由 Persona 决定实际获知 |
| H-033 | 2026-09-08 | DECIDED | 如何防止 Director 修复一个复制错误时删除已接受的 Character Proposal Event？ | Runtime 必须维护 Action-specific canonical assembly、局部引用和 Entity change 合并规则，并迁移 fixtures/trace 名称 | 缩窄为 DirectorResolution；Segment Assembler 从权威输入构造 Draft，只有创意字段可请求 Director repair |

## 关键卡点详述

### H-001 / H-002｜如何串联起 Agent Runtime 和底层 Galgame 引擎，同时保持底座零侵入？

当前回答是建立严格的世界—演出产物边界：

```text
Agent / World Runtime
  -> Committed WorldSegment / WorldEvent
  -> BroadcastPlan / structured RenderArtifact
  -> Validator + Dynamic Compiler
  -> Plugin Host / WebGAL Adapter
  -> MyGO/WebGAL 播放

反向只返回 Viewer Cursor / ready / playing / completed / failed
```

Agent Runtime 决定“发生了什么”，Plugin 将已经提交的世界产物编译成自包含 WebGAL 脚本，MyGO/WebGAL 只决定“怎样播放”。当前版本通过同源 `webgalsync / TEMP_SCENE` 动态注入；如果协议升级失效，则降级为虚拟场景文件与 iframe 重建。播放器的点击、Sentence、Backlog 和 `SYNCFC` 不能反向成为 World Tick。

这个回答的权衡是：

- 得到：不修改原引擎源码，静态作品保持兼容，Render Backend 可替换，Agent Runtime 可以 headless 预运行；
- 失去：无法依赖播放器内部 Store 做可靠事务，当前协议缺 request ID/ACK，升级需要契约测试，恢复可能只能重建播放器；
- 产品代价：目标 Event 没有 Ready Render 时必须黑屏，不能拿未提交剧情填充；
- 算法代价：必须单独维护 World/Generation/Viewer 三个前沿，并用约 30 分钟 warm-up 隔离生成与消费。

仍需回答：正式 Plugin API 是否值得从上游提炼、ACK/鉴权/消息上限怎样设计，以及何时从 `TEMP_SCENE` 切换到稳定 Adapter。

### H-011｜去掉 Maze 和部分 Entity 渲染后，一个外在事件如何处理？我们该如何权衡设计？

这里的“外在事件”包括角色之外或跨角色的事实，例如“前一位顾客离开”“咖啡机空出来”“雨开始下”“咖啡完成”“Tomori 从门口出现”。去掉 Maze 后，不能再用 tile 到达、碰撞或物体动画暗示它已经发生。

当前回答把一次外在事件分成五层：

```text
World / Director Context
  提供天气、场所、活动对象和当前外部条件

Character Agent Output
  角色依据局部观察选择行动和反应

Director Temporal/Causal Completion
  在响应后补出外部变化、先后、持续、对象结果和 Event 边界

Minimal Validator / Committer
  只检查单调时间、角色占用、知识权限、对象前后状态和因果引用

WorldEvent -> Observation / RenderArtifact
  同一客观事件分别投影给角色记忆与 Galgame 演出
```

设计权衡不是“保留 Maze 或什么都不模拟”，而是选择模拟层级：

| 选择 | 得到 | 代价 |
|---|---|---|
| 保留完整 Maze/Entity | 到达、邻近和占位有天然判据 | 地图素材、寻路、碰撞、逐帧同步和渲染耦合重新回来 |
| 全部交给自然语言 | 最灵活、最少代码 | 外在事件无稳定身份，角色会双重占用，对象可凭空跳变，难以回放 |
| 当前方向：语义 Segment + 最小不变量 | 不需要 2D 材质，又能保留 Event、对象、观察和因果 | Director 补完与一致性评测成为新的核心算法成本 |

因此不是删掉 Entity，而是把“需要在未来被引用的 Entity”从可视对象改成语义对象。咖啡、订单、雨、消息等可以拥有稳定 ID 和状态；桌椅纹理、逐帧位置等若不影响后续因果则无需进入世界模型。

仍需回答：哪些外部变化必须有语义 Entity、哪些只是一段描述；资源占用是硬不变量还是 Director 判断；外在事件何时允许跨 generation wave 保持 ACTIVE；以及怎样用 Golden/Badcase 判断补完是否自然。

### H-007 / H-008 / H-009｜如何用 30 分钟预运行吸收 Agent 响应时间，而不引入独立 timer 错位？

用户本轮修正：

> 我们已经说过了提前跑 30min 解决这样错位的问题，只有这样才能解决渲染卡顿，同时动态生成一个活灵活现的世界演员剧本。
>
> Agent Runtime 里面的 Agent 响应时间也可以算时间。不能用一个外部计时器忽视 Agent 响应时间。

这意味着本项目不是“播放器实时等待 Agent，再由一个独立仿真时钟继续跑”。它维护两个有距离的前沿：

```text
Generation / World Frontier  ---- 至少领先一段库存 ---->  Viewer Frontier
       Agent 正在继续生成                                  玩家消费已完成剧本
```

Agent 的真实生成耗时发生在玩家前方。首播前约 30 分钟的 warm-up 和播放中的持续补货，使玩家通常只消费已经提交、已经编译的演员剧本，因此模型响应慢不自动表现成画面卡顿。

### H-010｜排队与煮咖啡的持续时间和结果应该由谁决定？

用户指出的例子是：Anon 与 Soyo 排队或煮咖啡时，究竟排到什么时候、煮到什么时候、咖啡发生了什么。随后进一步否决了“在 `ActionSchema` 写死 120 秒后完成”的回答。

当前方向不是让 Runtime 预先知道现实世界全部动作时长，而是：

```text
Character Agent(s) 生成这一轮行为与互动
        |
        | 实际响应耗时也是时间证据
        v
Director Temporal/Causal Completion
  补齐先后、等待、持续、转折、对象结果、Event 边界
        |
        v
Minimal Validator
  只检查单调时间、角色占用、因果引用、对象前后状态等不变量
        |
        v
Committed World Segment / Actor Script
```

Director 可以判断某段真实响应时间在剧情里是自然等待、可省略劳动、角色迟疑，还是需要重新标定的叙事区间。Runtime 不用 timer 在 Agent 返回前宣布“咖啡已经完成”。

仍未解决：

- Director 可以在多大范围内调整实际耗时，而不使 `World Time = Actual Runtime` 失去含义；
- 多 Agent 返回时间不同时，按单个结果、完成波次还是整个 Scene 做补完；
- 排队、物品所有权和共享资源哪些属于不可违反的硬约束；
- Director 补完失败后是重试、请求 Character 续写还是保留未闭合 Event；
- 如何用 Golden/Badcase 评价“自然”而不是只评价 JSON 合法。

### H-025｜Director 自身也消耗响应时间时，如何避免递归补完？

这是从本轮方案推出、但尚未由用户冻结答案的风险：Director 只能在 Character 响应之后补齐世界段；然而 Director 自己也会花费真实时间。若再请求一次模型解释 Director 的耗时，第二次调用又产生新耗时，可能无限递归。

首版可以把时间拆为：

```text
character_generation_span  Director 可语义补完
director_generation_span   Committer 只记录 measured gap，不再次递归解释
render_projection          Broadcast 默认可省略 measured gap
```

这只是候选止损方案，不是已确认决定。需要通过 trace 验证它是否仍符合“Agent 响应时间可以算 World Time”的直觉。

### H-011 补充｜移除 Maze 究竟交换掉了什么？

移除 Maze 得到：

- 不需要地图材质、碰撞、寻路、逐 tile 同步和浏览器参与仿真；
- MyGO/WebGAL 可以专注演出，不需要渲染完整世界。

同时失去：

- 到达与相遇的天然判据；
- 空间占位和资源争用的隐式约束；
- 动作何时开始/结束的外部参照；
- 物体状态变化可挂靠的实体；
- “谁能看见什么”的几何近似。

因此复杂度不是消失，而是从几何仿真迁移到语义生成、全局连续性和事后因果闭合。这是项目必须主动支付、也最有算法价值的代价。

### H-026｜去掉 Maze 后，`Persona.perceive()` 如何获得外部信息？

官方实现把六件事放在同一个函数里：空间学习、候选事件收集、可见性过滤、注意力筛选、新颖性判断和记忆写入。移除 Maze 后，不应寻找一个新的“附近坐标算法”，而应重新划分权责：

```text
Committed WorldSegment / WorldEvent
  -> PerceptionProjector
       只做硬可感知性与字段裁剪
  -> PerceptionFrame[character]
       当前局部状态 + 本轮可感知候选 + 当前可执行 affordances
  -> Persona.perceive(frame)
       注意到什么 + 是否新颖 + 怎样进入角色记忆
  -> retrieve -> plan -> reflect
```

这里必须区分四个对象：

- `WorldEvent`：客观上已经提交了什么；
- `PerceptCandidate`：按语义场景、直接交互、消息接收者等规则，该角色有机会感知什么；
- `Observation`：该角色本轮实际注意到了什么；
- `Memory`：角色如何长期保存、解释或误解这次 Observation。

当前建议的最小感知通道只有五类：`self`、`direct_interaction`、`same_scene`、`targeted_message`、`commitment_update`。不模拟像素视线；用 `location_id / participant / recipient / modality / visible_fields` 表达写作层面的可感知范围。`scene_id` 只用于 Render/WebGAL 场景资源。Director 可以在 Creative External Event 中提出授权的感知内容，但 Segment Assembler 与 Validator/Projector 负责防止地点、隐私和字段越权。

原版 `perceive()` 还会把附近地点和物体写入 spatial memory，后续 `plan()` 再据此挑选 sector/arena/object。无 Maze 版本不能把这条依赖留空：`PerceptionFrame` 应直接携带当前可执行的语义 affordances，例如 `talk_to(soyo)`、`join_queue(counter)`、`pick_up(coffee#42)`、`leave_scene(cafe)`；角色只有在亲自到访、看到地图或被告知后才新增 `KnownPlace`。因此 `plan()` 最终也应从 Frame/DecisionContext 选择 affordance，而不是继续读取 Maze address。

注意力采用三级稳定策略，而不是“距离最近的前三条”：

1. `mandatory`：自身动作结果、被直接点名、当前承诺/排队状态变化、必须处理的中断；永不被带宽丢弃；
2. `relevant`：关联当前目标、互动对象和 Character Skill 关注点；
3. `ambient`：同场景背景事件；只在剩余预算内进入 Observation。

新颖性不能继续使用最近五条 `(subject, predicate, object)`。建议以 `(source_event_id, revision, visible_fields_hash)` 做幂等键：同一 Event 的同一 revision 不重复记忆；`queued -> brewing -> ready` 等新 revision 必须可再次感知；相同 SPO 的不同 occurrence 不能合并。

每次 Persona 调用都基于一个不可变 committed world version 构造 Frame，任何角色都看不到其他 Agent 尚未提交的输出。同一 Event 内允许逐步推进：`Anon utterance commit -> Soyo direct observation -> Soyo reply`；隔离 Event 则各自拥有推进游标。这样既保留一期串行互动，也消除读取 live Scratch 的隐式先手。

仍需回答：

- `PerceptionFrame` 的最小字段和每个通道的 field mask；
- affordance 的生成、失效和 evidence 如何表达，`KnownPlace` 与当前可执行 affordance 怎样分离；
- 同场景但忙于别事时，注意力预算怎样变化；
- Character Skill 只能重排可感知候选，还是可以影响误解与置信度；
- 低显著 Observation 是否全部写长期 Memory，还是只保留结构化 Observation Log；
- Director 补出的外部事件若想影响角色，是否必须先提交到上一 Segment，禁止事后声称角色已经看见；
- 如何用 Secret Leak、Missed Cue、Repeated Event 和 Dialogue Turn Golden Trace 验收。

咖啡场景的最小 Trace：

```text
v100: Director/Committer 提交“前一位顾客离开，队列向前移动”
  -> Anon：因持有 queue ticket，收到 mandatory commitment_update
  -> Soyo：因属于同行 party，收到 direct/relevant candidate
  -> Tomori（店外）：没有 candidate

v101: Anon 提交“轮到我们了，要这个吗？”的 utterance
  -> 只有在 utterance 提交后，Soyo 下一 Frame 才收到 mandatory direct_interaction
  -> Soyo 的 reply 不依赖 Python for-loop 排在谁后面

v120: 提交“咖啡机提示音响起，coffee#42 ready”
  -> Anon/Soyo：看到或听到 ready，并可见自己的订单关联
  -> 同店旁观者：最多听到提示音、看到一杯咖啡，不可见订单隐私
  -> 店外角色：不可感知
```

这个 Trace 中不存在“读取当前 Render 画面判断发生了什么”。Render 与角色 Observation 都来自同一个 committed WorldEvent，但使用不同投影规则。

### H-027｜一期如何调度 Persona，既先把流程串起来，又让角色知道何时回应或选择什么都不做？

原版问题不只是“调用比较慢”，而是 `plan()` 会读取其他 Persona 的可变 Scratch，聊天反应甚至会同时改写发起者与目标 Persona 的 action。于是先执行的 Persona 已改变内存状态，后执行者看到的是另一版世界；`persona_names` 顺序可能决定谁先开口、谁被迫进入聊天。

一期不必马上解决同一 Event 内的并行协商。当前决策是按 Event 分区：

```text
parallel:
  Event A runtime
  Event B runtime
  Event C runtime

inside Event A:
  choose next participant
  -> build latest PerceptionFrame
  -> persona chooses act / utter / respond / no_op
  -> Director completion + validate + commit
  -> next participant
```

Agent 不需要自己推断“现在应该让对方说话”。Event Scheduler 在 A 提交以后把决策机会交给下一位；如果 A 的 utterance 明确指向 B，B 的 `PerceptionFrame` 会出现 `addressed_to_me / pending_response`。这些字段提高回应优先级，但不强迫 B 回复：B 可以回答、拒绝、延后，或者 `no_op`。

纯 `no_op` 不是客观世界事件：它只进入 Decision Trace、让出决策游标并声明下一次唤醒条件，避免 Ledger 充满“某人什么都没做”。若角色明确选择等待咖啡或等待回应，则用有语义的 `wait` Proposal/Event。

硬约束：

- Persona 不能持有或修改另一个 Persona 的 live object/Scratch；
- 每次动作必须先提交成该 Event 的事实，下一位角色才能感知；
- `no_op` 必须带下一次唤醒条件或 Event 退避策略，防止空转；
- 只有没有共享角色、对象、地点资源和因果依赖的 Event 才能并行；
- 角色跨 Event 时，Runtime 必须加 barrier、迁移 ownership 或合并 Event。

这意味着一期接受 Event 内顺序本身就是局部世界因果；并行只发生在隔离 Event 之间。未来是否升级为同一 Event 内并发 Proposal，是性能与交互丰富度优化，不阻塞底座封装。

仍需回答：Scheduler 使用 round-robin、被点名优先还是 Director 建议；连续 `no_op` 多少次后 Event 休眠；共享角色从一个 Event 切到另一个 Event 时怎样建立 barrier。

### H-028｜纯 Agent Runtime 的目录怎样表达 Agent、Memory、Event 与 World 的真实所有权？

已否决的旧结构把 `persona.py / director.py / broadcast.py / event.py / world.py` 全部平铺在 `agent_runtime/` 根目录。它虽然文件少，却把三类 Agent 与外部环境实体混成同一抽象级。

当前答案：

```text
agent_runtime/
├── go.mod
├── cmd/runtime/
└── internal/
    ├── agent/{memory,personact,director,broadcast}/
    ├── event/
    ├── world/
    └── rendergateway/
```

`agent/` 表示 Agent 体系：`personact / director / broadcast` 是决策主体，`memory/` 是它们共同依赖的基础能力；`event/` 表示互动容器、轮次和事件生命周期；`world/` 表示不依赖任何具体 Agent 的客观状态、感知投影、校验与提交。`agent/personact/` 对外实现 Eino `adk.Agent`，内部 Compose Graph 沿用 Generative Agents 的认知阶段语义。

共用 Memory 模块不能演变成全员共享知识库。每个 Agent 都有独立 namespace：Persona 保存观察、情节与反思；Director 保存 Narrative Thread、补完历史与失败诊断；Broadcast 保存已覆盖区间和连续性状态。跨 namespace 读取必须通过显式授权或公开 WorldEvent，不能因为共用一个 Store 就越权。

代价是必须认真约束 package 依赖，避免 Event Scheduler 调 World、World 又反向导入 Scheduler。第一期从具体 package 显式导入，不建立万能 facade。

### H-029｜Persona、Director、Broadcast 是否应共享同一套认知流程？

答案是“共享 ADK 协议和基础设施，不共享一张 Persona Graph”。三类 Agent 都需要获取输入、检索历史、形成计划、输出提案和在结果后反思；但它们观察的对象、决策频率、权限与输出完全不同。

```text
共用：
  Eino adk.Agent / Runner / AgentEvent / cancel / callback / checkpoint
  MemoryRecord / Store / Retriever
  Eino ChatModel、Prompt Template、Tool、retry/failover

专属：
  PersonAct / Director / Broadcast 的 Graph 或 strategy
  各自 prompt_template
  各自 Memory namespace
  各自 Proposal 类型
```

Stanford 的三个目录不能整体搬运：`memory_structures` 只保留领域数据语义；Persona 的 Scratch/KnownPlace 保持私有；Character 认知节点进入 PersonAct Graph；Prompt 内容按 Agent 隔离，通用渲染和模型调用交给 Eino。

通用流程也不机械照搬原版顺序。原版 `reflect()` 是阈值触发的记忆归纳，并不是 execute 后反馈；原版 `execute()` 是 Maze 路径计算。在本项目中统一为提案前认知与提交后反馈：

```text
observe/perceive -> retrieve -> plan -> propose
Runtime validate/commit
observe_outcome -> reflect
```

PersonAct 的 Decision Run 在 Proposal 后结束，World 提交完成后再发起 Feedback Run。仍需用 Fixture 验证：ADK adapter 与 Graph state 是否清晰，Director/Broadcast 的 reflect 是否需要一期实现，还是先使用 No-op strategy。

### H-030｜如何避免重写 Generative Agents 已经有成熟替代品的基础设施？

当前答案是按“通用机制 / 领域算法”切开：

- Eino ADK/Compose 接管 Agent 接口、Runner、Graph/Chain、事件流、取消、Callback 和单次 Agent checkpoint；
- Eino/Eino-ext 接管模型 provider、Tool、Prompt Template、retry/failover；后续需要语义检索时接 Embedder/Indexer/Retriever；
- Go typed struct 与严格 JSON decode 取代字符串切片、`literal_eval` 和吞异常式 fallback；
- 不迁移 Django 文件 mailbox、busy polling、固定 Tick、Selenium、Tiled/Phaser 世界和 `path_finder.py`；
- MyGO 自己持有 PerceptionProjector、角色记忆语义、namespace/provenance、检索融合、World/Event Scheduler、Temporal Binder、Validator/Committer、Ledger 与 Render Planner。

这里仍有两项实现级选择：一期 Memory 存储使用 JSONL 还是 SQLite；`PersonActAgent` 是否在 Phase 0 就实现 `ResumableAgent`，还是先只实现 `adk.Agent`。它们不改变上述责任边界。

### H-031 / H-032｜地点如何保持客观口径，又怎样成为角色可发现的信息环境？

旧方案只有 `scene_id / location_id` 引用，没有定义地点本身如何保存事实、安排和历史。这会造成两个相反错误：模型可以临场把“羽丘有钢琴”改写成“没有钢琴”；或者为了避免矛盾，把 RiNG 的全部全局信息直接塞给每个角色，造成上帝视角。

当前回答是新增 World 层 `LocationModel`，但不新增 Location Agent：

```text
Location identity
+ LocationFact revisions
+ LocationInfo revisions
+ WorldEvent refs by location_id
= LocationView @ world_version / world_time
```

`LocationFact` 保存客观环境事实；`LocationInfo` 保存带有效期、周期和披露范围的可发现上下文；实际 Event 仍只存在于 WorldEvent Ledger。普通自然语言、台词和 Memory 都不能覆盖 Fact，正式变更必须经过 revision CAS 与 World Commit。

角色形成前往地点的有效意图后，Runtime 查询对应 LocationView，先确定性过滤无效、已知或不可披露信息；有候选时 Director 只能提出 `NoOp / DiscoveryPlan`。既有公开 Fact/Info 可在校验后由 Projector 直接产生 Candidate；消息/告知等改变世界的传播必须先由 Committer 提交 WorldEvent。PersonAct 最终决定是否注意和记住。完整设计见[地点 World Model](location-world-model.md)。

已支付的代价是：World 模型新增 Fact/Info revision、地点查询索引与 disclosure 校验；还需要确定 pre-arrival 与 post-arrival 两种 hook、周期 wakeup、地点历史窗口及 SQLite/JSONL 存储选择。

### H-033｜如何防止 Director 修复一个复制错误时删除已接受的 Character Proposal Event？

真实 Provider trace 暴露了宽接口的非局部失败：爱音已经提出并通过校验的 60 秒 `wait`，Director 第一次完整保留 Proposal Event 但把 World Version 抄成下一版；修复调用纠正版本时却清空了 Proposal Event。旧 repair 只有诊断，没有上一版输出，而且模型仍拥有删除角色事件、改写来源或角色 payload 的结构权限。

当前答案不是在完整 SegmentDraft 返回后覆盖几个字段，而是缩窄模型接口：

```text
ActionProposal + DirectorResolution
  -> deterministic Segment Assembler
  -> Segment Validator
  -> World Committer
```

DirectorResolution 只含相对时长、结果摘要、Creative External Event、合法 Entity State Change 和 Session intent。Assembler 从 Snapshot、Session、Proposal 与 Director trace 身份生成版本、绝对时间、Proposal Event、事件键、来源、因果引用和 `move` 位置变化。只有 Resolution 自有字段错误可修复一次，repair 同时携带上一版输出与完整诊断；权威上下文失败直接视为 Runtime 缺陷。

得到的是角色意图保真和可归因修复，付出的代价是 Runtime 必须明确维护五种 Action 的 canonical payload 与合并规则，并在契约变更时同步 fixture 输入哈希和 trace 名称。历史 `segment_draft` trace 保留可读，避免用迁移抹掉失败证据。对应决策见 [D-037](decisions.md#d-037director-契约缩窄segment-由-runtime-按权限确定性组装)。

## 后续维护要求

以后每轮出现新的用户卡点时：

1. 先把新卡点写成一个以 `？` 结束的问题，再在本页新增或修订 `H-*`；
2. 若形成稳定选择，再同步到 [决策记录](decisions.md)；
3. 若仍需实验回答，再同步到 [未决问题](open-questions.md)；
4. 在 [维护日志](log.md) 留下一条本轮变更；
5. 不因“已经找到一个方案”而抹去方案所支付的代价。
