# 架构决策

## 状态图例

- **已实现：**仓库中存在对应代码与测试证据。
- **已确认 / 一期决策：**设计边界已经冻结，可以据此实现；不表示代码已经存在。
- **建议方向 / 待验证：**允许在 Fixture Vertical Slice 中按该假设实现，但必须通过 Trace、测试或实验决定是否保留，不能升级成不可变架构事实。
- **Open / Research：**仍需设计或实验回答；若某项不阻塞 Fixture Slice，会在一期落地方案中明确给出临时规则。

## D-001｜原 WebGAL/MyGO 引擎不可侵入

- **状态：已确认**
- **日期：2026-08-20**
- **决策：** Agent 功能必须作为新封装层实现，不修改原始引擎源码、压缩 Bundle、内部 Store 或现有静态播放链。
- **原因：** 避免再次陷入底层框架重造，保留成熟 Render、Live2D、UI、Backlog 和素材能力。
- **后果：** 任何要求 patch `assets/index-982c8eaa.js` 的方案默认拒绝。

## D-002｜WebGAL 是黑盒 Render Backend

- **状态：已确认**
- **日期：2026-08-20**
- **决策：** WebGAL 只播放当前 Render，不维护 World State、Event Log、Agent Memory 或多 Event 状态。
- **后果：** 切换可重置其内部状态；恢复依赖 Plugin 数据。

## D-003｜多事件观察而非静态分支树

- **状态：已确认**
- **日期：2026-08-20**
- **决策：** 世界允许多个 Event 同时推进，玩家从 Event Hub 选择主视角并可随时切换。
- **后果：** 必须新增 World State、Event Channel、Event Log 和 Viewer Cursor。

## D-004｜Character / Director / Broadcast 三类 Agent

- **状态：三类 Agent 方向保留；Director 职责由 D-044 重定义**
- **日期：2026-08-20**
- **历史边界（已被 D-044 否决）：**Character 提出角色局部行动；Director 维护剧情约束、优先级、未解决线程并承担 Segment Completion。
- **现行边界：**Character Proposal 直接经 Validator/WorldUpdater 提交；Director 只管理 EventStaff；Broadcast 负责展示选择、摘要和镜头入口。

## D-005｜Agent 与 Render 生成均使用分层结构化契约

- **状态：已确认**
- **日期：2026-08-20**
- **决策：** Character Agent 输出结构化 `ActionProposal`，不能直接生成客观 Event；Broadcast Agent 根据已提交 WorldEvent 输出 `BroadcastPlan`，确定性 Render Planner 再生成 `RenderJob`；任何 Agent 都不直接执行正式 WebGAL DSL。
- **原因：** 必须分别校验认知权限、世界约束、Event evidence，以及素材、角色、能力、命令和世界版本，并返回可定位错误。

## D-006｜有 Ready Render 就加载，无 Render 就黑屏

- **状态：已确认**
- **日期：2026-08-20**
- **决策：** 没有 Render 时不显示占位剧情；保持纯黑并继续生成。
- **后果：** 黑屏由 Host 控制，可在遮罩下预热 WebGAL。

## D-007｜Event Log 独立于 WebGAL Backlog

- **状态：已确认**
- **日期：2026-08-20**
- **决策：** Event 使用 append-only Log；WebGAL Backlog 只作为当前 Render 的临时 UI 历史。

## D-008｜MVP 只运行一个 WebGAL 实例

- **状态：MVP 已实现**
- **日期：2026-08-20**
- **决策：** 多 Event 在世界层并发，展示层只保留一个播放器。
- **后果：** Render 必须自包含；切换由黑屏保护。若协议不稳则降级为重建单个 iframe。

## D-009｜MVP 优先复用 `webgalsync / TEMP_SCENE`

- **状态：MVP 已实现并完成真实浏览器视觉验证**
- **日期：2026-08-20**
- **决策：** Plugin Server 实现同源 `/api/webgalsync`，用 `TEMP_SCENE` 注入完整 Render，原 Bundle 不变。
- **后果：** 锁定 MyGO `3.1.1` / WebGAL `4.5.19`，补鉴权、串行注入、超时和断线恢复，并保留 iframe 重建兜底。

## D-010｜30 分钟作为预运行覆盖目标

- **状态：旧表述已由 D-025 修正**
- **日期：2026-08-20**
- **原决策：** Agent 流在首播前预运行，并以约 30 分钟未来覆盖为目标。
- **修正原因：**“运行 30 分钟”和“覆盖 30 分钟可播放内容”不是同一指标；只有远端 Event Plan 也不能解决渲染卡顿。详见 D-025。

## D-011｜World / Agent Runtime 是世界唯一权威

- **状态：已确认**
- **日期：2026-08-20**
- **决策：** `world_time / world_tick / world_version`、角色状态、局部认知、互动、关系型当前状态和 WorldEventHistory 全部归外部 World / Agent Runtime；MyGO/WebGAL 不拥有也不推进世界。
- **依据：** Generative Agents 论文明确描述 Sandbox time-step action loop 与 Server 更新共同世界；MyGO/WebGAL 源码只存在 Sentence、Auto/Fast、动画帧和同步心跳等演出时钟。
- **后果：** 玩家点击、快进、暂停、黑屏、断线和历史回放都不能推进或回滚 World Time；World Runtime 必须可在无播放器、headless 状态下独立运行和重放。

## D-012｜World Event 与 Render Artifact 解耦

- **状态：Event/Render 解耦保留；Director Completion 流程被 D-044 取代**
- **日期：2026-08-20**
- **历史流程（已被 D-044 取代）：**Character Proposal -> Director Segment Completion -> Binder/Validator/Committer -> WorldEvent。
- **现行流程：**Character ActionProposal -> Validator/WorldUpdater -> committed WorldEvent；EventStaff release 也经相同提交链；RenderArtifact 只是 Event 的演出投影。
- **原因：** 防止模型台词直接污染世界事实，并允许低显著 Event 只留 Log、Render 失败后世界继续、历史 Event 重渲染。
- **后果：** Event 是否发生与是否值得完整渲染必须分别决策；Render 不能创造 Ledger 中不存在的事实。

## D-013｜播放器状态只推进 Viewer Cursor

- **状态：已确认**
- **日期：2026-08-20**
- **决策：** WebGAL 的 Scene/Sentence、Auto/Fast、Pixi Ticker、`SYNCFC` 和完成标记只属于 Viewer/Render 状态。
- **后果：** 这些信号可用于继续播放、黑屏切换、ACK 近似与失败恢复，但不得作为 World Tick 或 Agent 唤醒源。

## D-014｜论文 Time Step 与项目 World Runtime 的证据边界

- **状态：已确认**
- **日期：2026-08-20**
- **决策：** 对外表述为“论文明确采用 Sandbox time-step action loop；本项目将其工程化为外部 World Runtime”。
- **原因：** 论文没有提出 `world_version`、关系型当前状态、atomic commit、canonical WorldEventHistory 或 EventStaff。
- **后果：** 简历、文章和设计文档必须把论文事实与本项目设计分开，不能将后者冒充论文原算法。

## D-015｜World Time 默认锚定 Actual Runtime

- **状态：耗时可记录的原则保留；D-026 的 Director 绑定机制被 D-044 取代；精确推进规则待实验**
- **日期：2026-08-20**
- **用户原话：**“可以改变，因为不影响，我们导播 Agent 自己知道分辨这个时间是否是正常的……这就是我们可以用 `World Time = Actually Runtime` 的原因。”
- **保留原则：**Agent 推理、工具调用和异步生成的真实耗时可以计入 World Time。
- **被修正部分：**不再让 monotonic timer 在 Agent 尚未返回时独立完成叙事动作，也不把返回结果默认当作因世界已被 timer 推进而过期。详见 D-026。

## D-016｜计算延迟不自动变成 Galgame 等待

- **状态：已确认**
- **日期：2026-08-20**
- **用户原话：**“从 WebGAL 的渲染感知而言，这个根本不会出现，最后编译成脚本，Galgame 里面不存在等待几秒的说法，除非确定的艺术手法。”
- **决策：** Broadcast Agent 负责 World Timeline 到 Render Timeline 的 Temporal Projection；纯技术延迟默认省略或由其它 Event 填充，只有角色真实犹豫、沉默、赶路等具有叙事意义并被明确选择时才编译为停顿、转场或蒙太奇。
- **后果：** Render Compiler 不按 wall-clock gap 自动插入 wait；Temporal Projection 必须保留来源区间、模式和艺术理由，供评测与回放。

## D-017｜Generative Agents 底座上增加 Director 与 Broadcast

- **状态：Character/Broadcast 方向保留；Director Segment Completion 与 Narrative Intervention 被 D-044 取代**
- **日期：2026-08-20**
- **历史方案：**曾计划让 Director 做每轮 Segment Completion 与低频 Narrative Intervention；该职责已被 D-044 否决。
- **现行边界：**继续以 Generative Agents 的 Perception、Memory、Retrieval、Planning/Reacting 与 Reflection 作为 Character 认知参考；新增 EventStaff Director 与 Broadcast；确定性治理改为 EventSessionRunner、AgentViewBuilder、Validator/WorldUpdater、WorldEventHistory 和 Render Planner。

## D-018｜MyGO 番剧到 Character Skill 作为后续算法方向

- **状态：Open Research Direction**
- **日期：2026-08-20**
- **用户原话：**“如果我们进一步，还可以考虑怎么从视频角色性格提取角色 Skill（MyGO 番剧 → 角色 Skill），这些都是算法啊。”
- **方向：**从视频、字幕/ASR、说话人、表情动作和关系上下文提取带证据的角色策略，服务 Persona、关系条件决策、语言风格与行为偏好。
- **前置：**先用人工 Gold Skill 验证 Runtime，再比较视频自动 Skill；当前不得写成已完成 VLM、微调或角色一致性提升。

## D-019｜Timeline 是 Agent 世界执行语义，解析只是投影副产物

- **状态：已确认**
- **日期：2026-08-20**
- **决策：**Timeline 统一承载动作区间、互动生命周期、角色认知获得时间、计划/承诺变化、WorldTransaction 因果历史、WorldEvent 区间和 Viewer 回放位置；它不是 Timeline UI 或 Render Queue。
- **后果：**项目主语是 Timeline-Driven Multi-Agent World Runtime；MyGO/WebGAL Parser/Compiler 是 `WorldEvent -> RenderArtifact` 的 Projection Adapter，不能反客为主。

## D-020｜Character Runtime 以 Generative Agents 为认知 Baseline

- **状态：认知语义方向保留；运行接口由 D-039 收敛**
- **日期：2026-08-20**
- **决策：**Character Agent 迁移 Generative Agents 中可验证的 Perception、Private Memory、Retrieval、Planning/Reacting 与 Reflection 语义。
- **明确不迁移：**Sandbox loop、`Persona.move()` 门面、Maze、地址/寻路/逐 tile movement 和 `execute.py`。外部 Event Scheduler 独占循环，Character 只做一次 `decide` 并返回 Proposal。
- **保留改造：**Generation Trace、局部事实/信念分层、world version、关系型当前状态、WorldEventHistory、多人冲突治理和 trace 重放不照搬原源码；Director Segment Completion 已被 D-044 否决。

## D-021｜Director 只控制未来机会、压力与约束

- **状态：被 D-044 取代；Director 不再控制机会、压力或叙事约束**
- **日期：2026-08-20**
- **已否决答案：**让 Director 管理 Narrative Thread/World Stimulus，或补齐尚未提交的 Character wave。
- **现行替代：**Director 不能读取尚未提交 Proposal，只能通过 D-044 的受限 DirectorView 管理已经客观启动的 EventStaff。

## D-022｜Broadcast 只控制观看方式，不控制世界事实

- **状态：权力边界已确认，算法待研究**
- **日期：2026-08-20**
- **决策：**Broadcast 从已提交 Event 中选择视角，并用省略、压缩、切镜、蒙太奇、摘要和艺术停顿生成 Render Timeline；不能改写 Event、补造台词或把技术延迟机械解释成角色行为。
- **后果：**BroadcastPlan 必须携带 source interval、evidence 和 artistic reason；玩家兴趣只能影响展示和未来弱反馈，不能改写过去。

## D-023｜Director 与 Broadcast 之间设置反馈防火墙

- **状态：方向已确认，指标与延迟窗口待研究**
- **日期：2026-08-20**
- **决策：**当前局只允许 EventStaff affordance/校验诊断反馈给 Director、技术/Buffer 状态反馈给 Broadcast；热度、弹幕和高潮评分不得进入 DirectorView 或 AgentView。
- **原因：**防止“高刺激片段更受欢迎”形成自我强化，使世界退化为注意力优化和角色 OOC。
- **例外：**玩家显式输入若要影响世界，必须被 Runtime 建模为带来源、可感知、可拒绝的正式 WorldEvent；局后聚合指标可用于下一局多目标评估。

## D-024｜Director 采用低频最小干预而非逐 Tick 控制

- **状态：被 D-044 取代；Narrative Intervention 不再属于 Director**
- **日期：2026-08-20**
- **决策：**`NoOp` 永远是低频 Narrative Intervention 的候选动作；只有线程停滞、deadline 风险或不可达时才按干预等级升级，并受预算、TTL 和冷却约束。每轮 Segment Completion 不受 `NoOp` 约束，因为它是世界段提交前的基础闭合步骤。
- **后果：**角色拒绝、误解、错过机会是合法叙事结果；Thread 必须允许 `completed / transformed / missed / expired`，不能无限重试到角色服从。

## D-025｜约 30 分钟指开播前真实 Warm-up，而非固定覆盖换算

- **状态：已确认，水位参数待实验**
- **日期：2026-08-21**
- **用户纠正：**“已经说过了提前跑 30min 解决这样错位的问题，只有这样才能解决渲染卡顿，同时动态生成一个活灵活现的世界演员剧本。”
- **决策：**首播前让完整 Agent 流真实运行约 30 分钟，产生已经提交的 WorldEvent/演员剧本并处理相应 EventStaff，把近端内容编译成 Ready Render；开播后继续在 Viewer 前方生产。
- **指标拆分：**`warmup_wall_time`、`committed_world_lead`、`ready_render_playable_time` 和 `production_to_consumption_ratio` 分别测量，不能用一个“30 分钟覆盖”字段代替。
- **后果：**只有 Event Plan 不算防卡顿库存；多 Event 预算、最低 Ready 水位和追赶/降级策略必须通过真实 Agent latency 压测确定。

## D-026｜World Time 采用生成完成后的事后语义绑定

- **状态：Agent 耗时可记录的原则保留；Director Completion 机制被 D-044 取代**
- **日期：2026-08-21**
- **用户纠正：**“那些事件花的时间，我并不认为可以通过程序硬编码决定……Agent Runtime 里面的 Agent 响应时间也可以算时间……不能用一个外部计时器忽视 Agent 响应时间，而更多是 Agent 响应后让导演 Agent 补完。”
- **历史决策（已被 D-044 取代）：**Character/工具调用的实际响应耗时作为时间证据；由 Director 做 Temporal/Causal Completion，再由 Binder 绑定到 World Segment。
- **现行替代：**measured elapsed 进入 Generation Trace，但 Director 不解释为角色行为或对象完成。Character Proposal 直接提交；环境过程只有在 committed process-start 后通过 EventStaff 推进。World time 与 Staff clock 的精确映射待 Golden Trace。
- **否决：**不在 Agent 尚未返回时用外部 timer 自动完成排队、冲煮等叙事动作；不把具体故事时长写死在通用程序规则里。
- **保留的硬编码：**单调时间、角色不可双重占用、知识权限、对象前后状态、因果引用、Staff release guard 和 append-only WorldEventHistory 等世界不变量。
- **后果：**Director latency 不再形成递归补完；代价是放弃“所有 wall time 都必须被剧情语义化”的强目标。

## D-027｜Director Completion 与 Broadcast Projection 分权

- **状态：Broadcast 只做观看投影的边界保留；Director Completion 被 D-044 取代**
- **日期：2026-08-21**
- **历史决策：**曾让 Director 决定生成结果的时间与因果；该部分被 D-044 否决。
- **现行分权：**Director 只请求 release 已验证 Staff；WorldUpdater 决定是否提交完成 Event；Broadcast 只决定 committed Event 怎样被观众看见。Broadcast 不能创造咖啡完成，Director 也不输出 WebGAL wait 或镜头命令。

## D-028｜专设难点、卡点与代价账本

- **状态：已确认**
- **日期：2026-08-21**
- **决策：**所有用户明确指出的难点、卡点、代价、错位和不合理设计，统一维护在 [难点、卡点与代价账本](difficulty-ledger.md)。
- **记录要求：**每个卡点先写成以 `？` 结束的设计问题，再保留首次提出时间、交换代价、当前回答、否决历史和未解决部分；被否决的是旧答案，不是问题。形成决定时同步本页，仍待研究时同步 `open-questions.md`，但不从账本删除历史。

## D-029｜无 Maze 感知采用世界投影与 `decide` 内部注意力分层

- **状态：建议方向，待 Golden Trace 验证**
- **日期：2026-08-21**
- **问题：**去掉 Maze 后，`PersonActAgent.decide` 内部如何获得外部信息，同时避免上帝视角、串行先手偏差和过度物理模拟？
- **建议：**世界侧 `AgentViewBuilder` 从关系型当前状态和已提交 WorldEvent 为当前角色生成 `AgentView`，负责硬可见性与字段裁剪；View 同时携带当前语义 affordances，替代 Maze spatial memory 给 planning 提供的对象选择；角色侧 `decide` 内部 perceive 阶段只负责注意力、新颖性和本人 Memory 写入，它不是额外公开方法。当前源码类型名 `PerceptionFrame` 可在实现该阶段时受控迁移。
- **顺序约束：**每次 Agent 调用只读取一个不可变 committed version；未提交输出不可见。同一 Event 内上一 Agent 一旦完成提交，下一 Agent 可以在新版本上感知；隔离 Event 可独立推进。
- **边界：**EventStaff release 形成的客观事件可以带经 World 校验的 perceptual footprint，但 Director 不能直接选定某个 Character 的主观理解或写入角色记忆；Character Skill 可以重排候选、影响主观解释，但不能扩大硬可见范围。
- **来源边界：**Character Proposal、EventStaff release、System/Tool/Player Input 都必须经 Validator/WorldUpdater 成为 WorldEvent 后，才能由 AgentViewBuilder 投影给 Persona。Director 不是 `perceive()` 的直连输入。
- **待验证：**咖啡外部事件、Secret Leak、Missed Cue、Repeated Revision 和对话轮次 Trace。

## D-030｜一期采用 Event 内串行、隔离 Event 并行

- **状态：一期决策，跨 Event 隔离条件待验证**
- **日期：2026-08-21**
- **问题：**串行调用 Persona 时，如何保留第一版实现简单性，同时消除执行顺序对世界事实、对话先手和角色认知的污染？
- **决策：**同一 Event 内由 Scheduler 按稳定顺序调用各 Persona 的 `decide`，每次消费 `act / interact / utter / respond / wait / no_op` 中一个 Proposal 并完成提交后，下一参与者才基于最新 committed Event state 再行动；没有共享角色、对象或因果依赖的 Event 可并行运行。
- **轮次：**Scheduler 决定谁获得下一次决策机会，不要求当前 Agent 猜测“应该让谁说话”。被点名角色会在 Frame 中收到 `addressed_to_me / pending_response`，但仍有回复、拒绝、延后或不行动的自主权。纯 `no_op` 只写 Decision Trace 并让出游标，不生成占位 WorldEvent；有世界语义的等待使用显式 `wait`。
- **禁止：**一个 Persona 直接修改另一 Persona 的 live Scratch；Event 间存在共享实体或因果依赖时不得假装隔离并行。
- **修正历史：**此前“全世界同一 immutable wave 收齐所有 Proposal 后一次提交”约束过强，一期改为 Event 内逐步提交；保留 Event 间隔离并行。
- **待验证：**单 Event 对话连贯性、`no_op` 不死循环、跨 Event 共享角色 barrier 和相同 Event seed 的可重放性。

## D-031｜Agent Runtime 按 Agent / Event / World 分层，并提供 Agent 通用 Memory

- **状态：领域分层继续有效；技术实现由 D-038 更新**
- **日期：2026-08-21**
- **原决策与历史修正：**一期最初计划纯 Python + 手写 AgentLoop，2026-08-22 曾切换到 Go + Eino；D-038 又将执行层迁回 Python 3.12 + LangChain Core。两次技术栈变化都不改变 `agent / event / world` 的领域所有权：Character、Director、Broadcast 归入 `agent/`；EventSession 与 Runner 归入 `event/`；语义环境、WorldEventHistory、WorldUpdater 和 AgentViewBuilder 归入 `world/`。
- **当前映射：**Python `agent/personact/loop.py` 通过 typed `RunnableSequence` 实现 Character 的真实认知 loop，`agent.py` 只保留 `PersonActAgent.decide` 的并发、replay 与 snapshot 事务门面；Anon、Soyo 等由 Manifest 形成不同实例。`agent/memory/` 提供隔离数据的共享机制。不迁移 `Persona.move()`、Maze、`path_finder.py` 或 `execute.py`。
- **新增边界：**Runtime 装配入口和 `rendergateway` 保持装配/出站职责；不提前建立游戏设计层、素材层、播放器层或通用企业分层。
- **Memory 边界：**共用 Memory 实现不等于共享 Memory 内容。每个 Character 使用独立 `agent_id + namespace`。MVP 的 Director 不建立全局剧情记忆，其可恢复状态是 World-owned EventStaff 与 cursor；Broadcast 覆盖历史属于 Render/Viewer 存储，二者都不得被 Character 检索。
- **后果：**目录直接表达“Agent 是决策主体，Memory 是 Agent 通用能力，Event 是互动容器，World 是事实环境”；Reflection、Prompt 和复杂检索算法可逐步替换，WebGAL 相关代码继续隔离在 `extensions/dynamic-render/`。

## D-032｜三类 Agent 不预设共享 AgentLoop，只共享基础设施

- **状态：原“共享生命周期”结论已修正；现行边界由 D-044 与 MVP 计划收紧**
- **日期：2026-08-21**
- **问题：**Character、Director、Broadcast 是否需要共享同一个 AgentLoop 生命周期？
- **决策：**当前只共享 strict Pydantic、Model Gateway、`RunnableConfig` 和 Trace 等基础设施，不假设共享 `perceive -> retrieve -> plan -> propose`、Memory 或 CognitiveController。PersonActLoop 是未来更宽 CognitiveController 的一部分，但 MVP 不新增基类/注册中心；等至少两类真实实现出现稳定重复后再抽取。
- **执行边界：**Character 的一次 `decide` 输出 strict/frozen ActionProposal；Director 输出 EventStaffDecision；Broadcast 输出 BroadcastPlan。三者的触发、输入和权限不同。`execute` 和 Runtime loop 都不进入 Agent Runnable，副作用由 WorldUpdater 或 Render Gateway 完成。
- **目录边界：**具体 Agent 分别位于 `agent/personact`、`agent/director`、`agent/broadcast`，共享 Memory 位于 `agent/memory`。Anon、Soyo 等是 PersonAct 实例，不建立独立源码目录。
- **实现方式：**当前 Character 的真实 loop 显式位于 `agent/personact/loop.py`，`agent.py` 是稳定门面；Director/Broadcast 各自先实现最小入口，避免提前制造万能 BaseAgent。
- **待验证：**三类 Fixture Agent 都返回各自 strict Pydantic Model，且 Memory、Prompt、权限和副作用完全隔离。

## D-033｜Memory 是 Agent 通用能力，但记忆内容按 namespace 隔离

- **状态：已确认基础边界，复杂检索算法待研究**
- **日期：2026-08-21**
- **决策：**`agent/memory/` 为需要认知记忆的 Agent 提供统一 MemoryRecord、MemoryStore 和 Retriever 接口；所有读写必须携带 `agent_id + namespace`。MVP 首先只给 Character 使用。
- **最小能力：**一期实现 append、get、recent、tag search、provenance 和持久化；不把 recency/relevance/importance 的具体评分固化进 Store。
- **私有状态：**Persona 的 Scratch、KnownPlace/Spatial Knowledge 不属于通用 Memory；它们保留在 Persona 内。
- **安全边界：**跨 namespace 默认禁止。Director 的 EventStaff/cursor 和 Broadcast 的 Artifact/Viewer 状态不属于 Character Memory；只有 committed WorldEvent 经 AgentViewBuilder 才能进入 Character 认知。

## D-034｜Agent Runtime 采用 Go + Eino ADK，Character 实现为 PersonAct Agent

- **状态：superseded（已由 D-038 取代）**
- **日期：2026-08-22**
- **决策：**一期 Agent Runtime 使用 Go。Persona、Director、Broadcast 对外实现 Eino `adk.Agent`；Character 的工作名称为 `PersonActAgent`，内部使用 Eino Compose Graph 表达 Generative Agents 风格的 `perceive -> retrieve -> plan -> propose` 与有限补检索/修复回环。
- **依据：**Eino 自身的 `adk.ChatModelAgent` 也是 ADK Agent 外壳加内部 ReAct Graph；本地 `agent_core` 也已验证 `compose.Workflow -> Runnable -> adk.Agent` 的薄适配模式。Graph 是 Agent 内部实现，不是与 ADK 对立的另一套方案。
- **源码依据：**Eino [`adk.Agent`](https://github.com/cloudwego/eino/blob/v0.9.15/adk/interface.go#L447-L467)、[`compose.Runnable`](https://github.com/cloudwego/eino/blob/v0.9.15/compose/runnable.go#L28-L37) 与 [ADK ReAct Graph](https://github.com/cloudwego/eino/blob/v0.9.15/adk/react.go#L354-L558)；本地已有 [`WorkflowAgent`](../../../go-project/agent_core/agent/workflow/workflow_agent.go) 包装 Eino Workflow Runtime 的实现先例。
- **运行边界：**一次 Character Decision Run 在产生 `ActionProposal` 或 `no_op` 后结束；Runtime 完成 Director Completion、Validator 与 Commit 后，再用独立 Feedback Run 触发 `observe_outcome -> conditional reflect`。一期不以 ADK interrupt 长时间挂起等待世界提交。
- **状态边界：**Eino Graph state/checkpoint 只服务一次 Agent 执行的中断恢复；不能替代 Persona 长期 Memory、World Snapshot、World Ledger、Event scheduler cursor 或 commit protocol。
- **历史后果：**该决策在 2026-08-22 至 2026-08-30 期间指导了 Eino PoC；其 `agent / event / world`、Proposal/Commit 和框架状态不替代 World 状态等领域边界继续保留。Go/Eino、ADK Runner、Compose Graph 与 checkpoint 的技术映射不再是现行方案。

## D-035｜成熟通用能力优先复用，世界域与认知语义保持自研

- **状态：原则继续有效；组件映射由 D-038 更新**
- **日期：2026-08-22**
- **当前映射：**LangChain Core 负责 Runnable 组合、`RunnableConfig`、模型/Prompt/Tool 抽象与 callback/tracing 接线；Pydantic strict/frozen Model 负责运行时结构边界，pyright strict 负责静态接线。`with_types()` 不做 runtime validation。禁止复制 Stanford 原型中的旧 OpenAI wrapper、手工 Prompt 占位替换、字符串截 JSON、裸异常重试、文件 mailbox、全量 JSON memory 重写和几何寻路。
- **保留自研：**AgentViewBuilder 的硬可见性、PersonAct 认知语义、Memory namespace/provenance 与召回融合、EventSessionRunner、world version、Validator/WorldUpdater、WorldEventHistory、EventStaff queue/cursor，以及 BroadcastPlan 到 RenderJob 的确定性转换。
- **原则：**外部库替代 plumbing，不替代决定“角色知道什么、为什么行动、哪些事实可以提交”的项目算法。
- **一期取舍：**Fixture Strategy 与 `ModelCognitionStrategy` 共享 `CognitionStrategy` Protocol 和 strict Pydantic contract；ChatModel adapter、显式 Pydantic parse、有限 repair 与调用级 Trace 已有离线契约验证，生产 Provider、向量数据库和 LangGraph 仍不提前引入。

## D-036｜地点是一等、版本化的 World Model

- **状态：语义边界已确认；逐 Fact/Info revision 的持久化被 D-042 延后到自由互动 MVP 之后**
- **日期：2026-08-22**
- **决策：**每个地点使用稳定 `location_id`，由 World 层维护 Location identity、客观 Fact 与有效 Info；地点发生的当前/历史 Event 通过 `location_id` 查询唯一 WorldEventHistory，不复制 Event。完整模型可增加 Fact/Info revisions，但 D-042 将其延后到自由互动 MVP 之后。
- **事实边界：**`LocationFact` 是客观事实。例如 `羽丘高中 / facility.piano.present=true` 一经提交，普通 Agent 输出、台词、Memory 或 Event summary 不能改写；缺少事实是 `unknown`，不是 `false`。MVP 真实变更以 expected world version 经 Validator/WorldUpdater 原子提交。
- **Info 边界：**“Tomori 每周六在 RiNG 独自 Live”作为带有效期、recurrence 和 disclosure 的 LocationInfo；它证明安排存在，不自动证明本周活动已经发生。实际到达、开演和结束仍需 committed WorldEvent。
- **单一真相：**私下意图留在 Persona 私有计划；承诺、预约或公告一旦提交，LocationInfo 成为公开世界中的 canonical record，Persona/Scheduler 只引用 `info_id + revision`，不得再维护可独立改写的副本。
- **获知链：**角色到达地点或取得合法远程渠道后，Runtime 查询同一 world version 的 LocationView。读取既有公开 Fact/Info 由 AgentViewBuilder 确定性形成 Candidate；消息/告知等传播必须先由对应 Character/System 经 WorldUpdater 形成 WorldEvent。两条路径最终都由 PersonAct 决定是否注意并写入自己的 Memory。
- **自治边界：**Director 不参与信息披露；需要角色主动传播时必须先经过该 Character 的 Proposal/interaction handshake。
- **后果：**LocationModel 不是 Location Agent，也不是 Prompt 拼接缓存；它属于 World 权威。Director 只有在某条 EventStaff 直接引用地点时，才获得完成条件所需的裁剪状态。详细模型见[地点 World Model](location-world-model.md)。
- **标识边界：**World/Event 契约统一使用 `location_id`；`scene_id` 仅属于 Render/WebGAL 场景资源，由 Render Planner 映射，不再作为第二套世界地点 ID。

## D-037｜NPC DIY 采用受限 Manifest 编译，不开放任意 Runnable/Graph

- **状态：现行决策，PersonAct 单次认知 Slice 已实现**
- **日期：2026-08-24**
- **问题：**如何让创作者 DIY NPC，同时不让配置绕过角色认知、Memory namespace、World Commit 与 Render 权限？
- **决策：**创作者在项目内提供受限 `agents.json`，只描述 Persona、初始私有记忆、允许的 Proposal 类型、受信 Tool 引用、有限行为参数、精确版本 Character Skill 引用和 Prompt Profile。Python Runtime 以 strict/frozen Pydantic Model 和语义校验将其编译成不可变 `CompiledPersonActSpec`，派生 `project/{project_id}/persona/{agent_id}` Memory scope，把 project/format version、Skill ID/version/整文件 hash 纳入稳定 digest，并解析 Tool/Prompt/Skill 精确版本；受信 `PersonActAgent` 消费该 Spec，而不是为每个角色生成代码或开放 Runnable/Graph 编辑。
- **权限边界：**Manifest 不允许声明 namespace、provider/secret、任意 URL/脚本/MCP、World/WorldEventHistory/Render 写工具。Tool Catalog 只向 PersonAct 暴露只读 query 或纯 compute；运行时 Proposal 还必须与当前 `PerceptionFrame.affordances` 和 visible evidence 相交，随后直接经过 Validator/WorldUpdater。
- **LangChain 边界：**LangChain Core 只能作为 `PersonActAgent.decide` 的内部模型/策略接线，`RunnableConfig` 只携带调用上下文；`with_types()` 不做 runtime validation。真正的运行时结构校验由 Pydantic 完成，领域权限由 Compiler 与 Proposal Validator 完成。当前不使用 LangGraph。
- **一期取舍：**先用无网络 Fixture `CognitionStrategy` 验证编译、认知与权限边界；不做可视化编辑器、热更新、远程插件、创作者自选模型或 Director/Broadcast DIY。完整设计见 [NPC DIY](npc-diy.md)。

## D-038｜Agent Runtime 采用 Python 3.12 + LangChain Core 强类型边界

- **状态：现行决策；PersonAct 单次认知 Slice 已实现，完整 Runtime 未实现**
- **日期：2026-08-31**
- **问题：**如何让 Agent 编排贴合当前 Python 生态，同时在动态语言与 Runnable 组合中维持可审查、可运行时拒绝的强类型契约？
- **决策：**Agent Runtime 使用 Python 3.12 与 `langchain-core==1.6.1`。Agent 步骤以带显式输入/输出注解的 `RunnableLambda` 和 `RunnableSequence` 组合；外部输入、模型/Tool 输出、跨模块契约与持久化边界使用统一 strict/frozen Pydantic Model；静态接线由 pyright strict 检查，ruff 与 pytest 作为质量门。环境和锁文件由 uv 管理。
- **校验边界：**`Runnable.with_types()` 只提供类型与 Schema 元数据，不在 `invoke()` 时自动执行 runtime validation。所有不受信数据必须显式进入 Pydantic validation；通过结构校验后仍需领域代码检查 namespace、affordance、target、evidence、world version 与副作用权限。
- **编排边界：**PersonAct 的公共 API 是一次 `decide`，不是 Runnable Pipeline 或 Agent Loop。只有出现真实的复杂分支、暂停恢复或持久执行需求，并经过 ADR 与契约测试后才重新评估 LangGraph；任何框架状态都不能替代 EventSessionRunner、Persona 长期 Memory、SQLite 当前状态、WorldEventHistory 或 commit protocol。
- **所有权边界：**保留 D-031 至 D-037 的 `agent / event / world`、Memory namespace、Proposal/Commit、World/Render 分权。LangChain 只替代通用调用 plumbing，不拥有世界事实。
- **实现事实：**当前已完成受限 NPC Manifest 编译、Persona Memory/State 与检索基础、固定 ActionProposal envelope + action union、`PersonActAgent.decide` 的 prepare/perceive/retrieve/plan/propose Slice，以及 Runtime Skill、typed Model Gateway 和 `ModelCognitionStrategy` 的契约级实现。Reflection/commit feedback、EventStaff Director、Validator/WorldUpdater、Event/World Runtime、Broadcast、生产 Provider 装配和完整 Generation Trace 持久化仍未实现。

## D-039｜迁移 Persona 认知语义，不迁移 `Persona.move()` 或 movement

- **状态：现行纠正；PersonAct 单次认知 Slice 已实现，外部 Runtime 仍待实现**
- **日期：2026-08-31**
- **用户纠正：**Character Runtime 不应实现或暴露 `Persona.move()`，也不需要 Maze、path、tile movement 或 `execute`；外部 Event Scheduler 才拥有循环。
- **决策入口：**Scheduler 基于 committed world version 选择一个角色，并以只含 `proposalId + frame` 的 `DecisionRequest` 调用 `PersonActAgent.decide`；spec/state/memory/strategy 由受信 Agent 实例持有。一次调用只返回该角色一个 strict/frozen `ActionProposal` 对象，然后立即结束。需要 wire JSON 时显式调用 `model_dump_json(by_alias=True)`。下一角色、下一轮、退避和重新唤醒都由 Scheduler 决定。
- **Actor 边界：**公开 envelope 固定为 `proposalId / agentId / eventSessionId / basedOnWorldVersion / action / evidenceIds`。`agentId` 由受信 Agent 从 `CompiledPersonActSpec.agent_id` 注入，CognitionStrategy、模型和创作者不能选择或覆盖 actor。
- **Action 边界：**`action` 是以 `kind` 判别的 union，固定 `act / interact / utter / respond / wait / no_op`；`interact.target` 是 `{kind: character|object, id}` 的显式单目标，`utter/respond.target` 必须是 character。没有 `move` variant、`locationId` 或多目标 `targetIds`。
- **迁移范围：**迁移 perception、private memory、retrieval、planning/reacting 与 reflection 等认知语义；不迁移 Stanford 顶层循环、`Persona.move()` 门面、Maze、寻路、逐 tile movement、`execute.py` 或跨 Persona 原地写状态。
- **实现校准：**World contract 与 `PersonActAgent.decide` 已采用固定 envelope 与 discriminated union；prepare/perceive/retrieve/plan/propose、actor 注入和 Proposal 权限边界已落地。同一 Agent 的调用由实例锁串行化，并以单个 immutable private snapshot 原子替换 state/memory/trace。不得把它扩大表述成 reflection/feedback、外部 Scheduler、movement、World Commit 或完整 Runtime。

## D-040｜项目自研领域型 NPC ADK，LangChain 只作为内部基础设施

- **状态：现行决策；PersonAct 核心 Slice 已实现**
- **日期：2026-08-31**
- **决策：**将 Creator Manifest、受信 `CompiledPersonActSpec`、Persona State/Memory/Retrieval、`PersonActAgent.decide`、ActionProposal schema、权限校验和 Trace 统称为 **Generative Go World NPC ADK**。这些契约和认知语义由本项目拥有。
- **框架边界：**LangChain Core 只提供 Runnable、调用配置以及 ChatModel/Prompt/Tool plumbing，不定义角色能力、可见性、Memory scope、Action 语义或 World authority；因此本项目不是“给 LangChain 配一层 Prompt”，也不是重新实现 LangChain。
- **核心不变量：**`perceive -> retrieve -> plan -> ActionProposal -> Validator / WorldUpdater -> Committed WorldEvent`。Proposal 是角色意图，不是执行结果；只有 WorldUpdater 能把通过校验的候选动作变成世界事实。Director 位于提交之后，只消费受限的 committed Event/Staff View。
- **当前完成度：**可以称为自研 NPC ADK 的核心 Slice；Skill/ChatModel Strategy seam 已存在，但外部 Scheduler、commit feedback/reflection、Director、World Validator/Committer、Ledger、生产 Provider 装配、完整 Generation Trace 和完整 Runtime 仍待实现。
- **环境约束：**Python 版本、虚拟环境、依赖与锁文件只使用 uv 管理，以 `.python-version + pyproject.toml + uv.lock` 为唯一口径；安装使用 `uv sync --frozen`，命令通过 `uv run` 执行，不并行维护 pip requirements、Poetry 或 Conda 配置。

## D-041｜`origin/mvp` 只做契约级选择性融合，不整体迁移

- **状态：现行决策；Skill/Gateway/PersonAct Strategy 已选择性融合**
- **日期：2026-09-01**
- **分支事实：**`origin/mvp@febf9d1` 与当前 `master` 没有 merge base，属于独立历史，不能作为普通功能分支整体 merge。它是机制参考，不是当前架构的祖先或继承基线。
- **融合范围：**按当前已冻结的 PersonAct、Proposal/Commit 分权与 `agent / event / world` 所有权，重新实现版本化 Runtime Skill、整文件 hash pin、typed Model Gateway、structured output、transport retry、最多一次 schema/semantic repair 和非秘密调用 provenance；`ModelCognitionStrategy` 仍只是现有 `PersonActLoop` 的策略实现。
- **明确拒绝：**不迁入 same-snapshot lockstep Generation Wave、同 Event 全角色并发提案、`Persona.move()`/Maze/path/movement/execute、Proposal 内 `memory_changes`、一轮 Proposal barrier 后统一提交，以及 `origin/mvp` 的完整 SQLite World DB、Director、Broadcast、Scheduler、Committer 或 Render Runtime。
- **时序边界：**当前主线继续采用同一 Event 内“一个角色读取最新 committed version -> 一个 Proposal -> commit -> 下一角色读取新 version”；隔离 Event 才可并行。
- **配置分权：**Manifest Persona 保存身份、当前目标、关系事实和硬权限；Character Skill 保存稳定气质、驱动力、判断倾向与表达风格；Prompt Profile 保存后端任务模板身份与输出契约。Skill 不能覆盖 Persona、可见事实、affordance 或权限。
- **版本边界：**`characterSkill` 成为必填字段属于公开 Manifest 的破坏性变更，因此 schema 从 v1 升到 v2；旧 v1 输入显式拒绝，不在 loader 中静默猜测角色 Skill。
- **未完成：**当前 `ModelCallTrace` 只是有界、进程内、非秘密调用 provenance；可持久化、可重放并与 World/Validator/Commit 关联的完整 Generation Trace 仍待实现。

## D-042｜自由互动 MVP 使用关系型当前状态与单一 WorldEvent 历史

- **状态：现行 MVP 决策，代码未实现**
- **日期：2026-09-07**
- **问题：**`origin/mvp` 同时保存 Segment、WorldVersion、EntityRevision、WorldEvent 和逐版本 Snapshot，同一世界状态在多处 JSON 中重复；这对当前五角色自由互动 MVP 的实现、阅读和权威来源判断都过重。
- **决策：**SQLite 首批只保存明确的关系型当前状态（World、Location、WorldFact、AgentWorldState、Object、稳定 EventSession root binding、轻量 InteractionRequest，以及 D-044 增补的 EventStaff queue）、append-only `world_events` 和隔离的 Agent Memory。`worlds.current_version` 是乐观版本入口；一次 `World.apply_change()` 在同一 transaction 中更新受影响当前行、Session root、InteractionRequest/EventStaff、Event 和版本。
- **明确删除：**首批 schema 不建立 `world_changes / world_versions / entity_revisions / world_snapshots`，也不建立可由 root binding 派生的 `event_session_members`。MVP 不承诺仅靠事件还原任意历史版本；出现真实历史查询或恢复性能证据后再增加 typed effect log 或周期 checkpoint。
- **初始化边界：**作品的 `scenario.yaml` 声明开场公共世界、初始 Session 分区，以及面向 all agents 或显式角色集合的知识分配；根级 Scenario loader/bootstrap 先完整校验，再协调一个 SQLite 初始化 transaction。`world/initializer.py` 只解释公共客观部分，知识分配按接收者展开到各自 `agent/memory`；共享 transaction 只保证不会留下半初始化存档，不改变领域所有权。模型常识不复制进 World DB，会影响合法动作的条件必须由 World Fact/Affordance 明示；Character Skill/Persona 继续作为版本化配置资产。
- **复用范围：**继续复用 `origin/mvp` 的 Scenario Seed 严格校验、Seed hash、初始化事务、已有存档不静默覆盖和故障恢复测试；不复用其重 Event Sourcing 表布局。

## D-043｜互动事实、待处理请求、角色记忆与导播产物分层持久化

- **状态：现行 MVP 决策，代码未实现**
- **日期：2026-09-07**
- **问题：**Agent 对话、EventSession merge/split、EventStaff release 和 Broadcast/WebGAL 脚本如果分别维护同义日志，会出现事实不一致；如果全部塞进同一个 JSON，又无法区分客观发生、当前待回应、角色主观理解和观众看到的版本。
- **客观历史：**所有已经提交的 Character 互动、对象/环境变化、Session 分区变化和 EventStaff release 共用 append-only `world_events`。Event 至少携带稳定顺序、`source_kind/source_id`、故事内 actor/target、`root_session_id_at_commit`、回应/因果引用和发生时间；不为每个 EventSession 复制 conversation log。
- **当前待处理状态：**新增轻量 `interaction_requests` 当前状态表，以发起 `world_event` 为主键来源，记录 request kind、请求者、接收者、状态和解决 Event。创建/解决 request 与相应 Event、world version 在同一 transaction 更新；纯 `no_op` 不生成 Event，request 保持 pending。
- **Director 分权：**该条原有的 Completion/Narrative Intervention 表述已由 D-044 取代。Director 只消费 committed Event 并管理 EventStaff；Character commit 不经过 Director，EventStaff release 只有经 Validator/WorldUpdater 后才成为客观 Event。Director 不能直接写 Agent Memory。
- **Session 边界：**稳定 EventSession/UnionPart 节点只属于故事内 Character；Director 与 Broadcast 是 Runtime Agent，不加入互动分区。Director 只通过按触发源裁剪的 `DirectorView` 管理 EventStaff，Broadcast 面向 committed Event 做全局只读选择。
- **认知与观看：**Agent Memory 只保存该 Agent 实际注意到的主观记录，并引用 `source_event_id`。Broadcast 只读 committed Event，通过 root-at-commit、reply/cause 与 SessionPartitionChanged 连接多条流；BroadcastPlan、RenderJob 及 dialogue/narration beat 必须保存 `source_event_ids` 和 `fact/quote/inference` 标记，WebGAL DSL 未承载的追溯信息保存在 Artifact sidecar。它们属于展示投影，不回写 World。
- **实现校准：**现有 Dynamic Render 的 Fixture Timeline、`played`、active Render 和 selected Event 均为文件/进程内原型，尚无真实 WorldEvent feed、RenderJob ingress 或持久 Viewer Cursor；这些留在 Render 侧 Phase 7 实现，不增设同义 World 表。

## D-044｜Director 只管理由已提交事实触发的 EventStaff

- **状态：权限边界为现行决策；字段与唤醒策略是 MVP 默认，待 Golden Trace 验证；代码未实现**
- **日期：2026-09-07**
- **用户纠正：**Director 完全不能干涉 Character Agent；它不负责角色行为补完、剧情压力、Narrative Thread 或主动刺激，只负责角色意愿之外、已经由客观事实触发的待处理环境事件。典型流程是把已开始的咖啡冲煮记录为 EventStaff，在合适时机 release 为“咖啡好了”。
- **提交顺序：**Character `ActionProposal -> Validator/WorldUpdater -> committed WorldEvent` 不经过 Director。DirectorRunner 之后只消费 committed Event；它可以 enqueue/keep/release/cancel EventStaff，但不能读取未提交 Proposal 或改写 Character 行为。EventStaff release 再经 Validator/WorldUpdater 形成新的客观 WorldEvent。
- **因果底线：**“Anon 说我要煮咖啡”只证明该 quote 发生，不能证明冲煮已开始；仅凭 utterance 创建 `coffee_ready` Staff 等同于替 Character 执行动作。必须先有 Character 自己触发并已提交的 `coffee_brewing_started` 或等价客观 process-start Event，World 才能向 Director 暴露相应 Staff affordance。
- **Director 可见性：**每次调用只接收 strict `DirectorView`：一个确实产生 Staff affordance 的 committed source Event 的 process 投影或待检查 Staff、同 process/subject 的有限因果、完成条件所需的 Object/Location 机器状态、不透明稳定 `session_id`、冲突 Staff，以及 World 计算的允许操作。当前 root/members 不进入 Director；它们仅在 release 后由 World/AgentViewBuilder 解析收件人。没有 affordance/相关 Staff 时由 Runner 确定性推进 cursor，不调用 Director。明确排除 Character 台词正文（除非受信 process contract 明确需要某字段）、私有 Memory/Scratch/Goal/Plan/Reflection、未提交/被拒 Proposal、无关 Session、InteractionRequest 决策、Broadcast/Viewer 数据和任意 World patch。
- **EventStaff：**首轮字段为 `event_staff_id / session_id / source_event_id / staff_kind / subject_type+subject_id / completion_event_type / status / created_world_version+time / next_check_at? / release_event_id? / details_json`。Queue 由 `event_staff` 查询顺序直接形成，不建重复 queue 表；source/kind/subject 唯一。enqueue/no_op 与全局 Director event cursor 原子推进，release/cancel 与 Staff、对象状态、新 WorldEvent 和 world version 原子提交。
- **稳定 Session 绑定：**Staff 绑定 Character 的稳定 `session_id`，不绑定可变 root。pop 时才执行 `rootOf(staff.session_id)` 并获取当前成员，再由 AgentViewBuilder 做地点/渠道硬可见性过滤；merge/split 不迁移或复制 Staff。跨多个 Session 的地点广播若出现真实需求，再显式增加 location delivery scope。
- **输出权限：**Director 只能返回受 affordance 限制的 `enqueue / keep / release / cancel / no_op`。completion event type 与 subject 在 enqueue 时已经受校验；release 只能决定“现在释放”，不能临场改成其它事件、生成角色台词、修改关系或 Session。
- **前置契约：**当前 `Affordance.kind+target / InteractAction.target+description` 无法稳定证明 Object operation。EventStaff 实现前必须增加 World-issued `affordance_id/operation_id` 或等价 typed operation；自由文本 description 不能单独触发 Object state change/Staff enqueue。
- **术语边界：**Director release 并不是 Broadcast Agent 的“广播”。WorldUpdater 提交客观 Event，AgentViewBuilder 将它投影给可见 Character；Broadcast Agent 只负责稍后怎样让玩家看见该 Event。
- **分支复用结论：**`origin/mvp` 会向 Director 暴露完整 Snapshot 和未提交 Proposals，并授权 Segment Completion，因此该 Director input/strategy 不复用；可复用的仍只有 strict contract、source/evidence、事务与故障测试思路。
