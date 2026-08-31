# Wiki 维护日志

## 2026-08-20

- 初始化 MyGO Agent 架构 Wiki。
- 沉淀“多事件 AI Native 世界剧场”：并发 Event、Event Hub、独立 Event Log 和观察视角切换。
- 记录 Character Agent、Director Agent、Broadcast Agent 与确定性 World Resolver 的边界。
- 将“WebGAL/MyGO 引擎零侵入、Plugin 外置封装”设为最高优先级架构不变量。
- 基于当前源码记录 `webgalsync / TEMP_SCENE / SYNCFC` 黑盒动态 Render 方案及版本、ACK、重连和安全风险。
- 记录“有 Ready Render 就加载、无 Render 就黑屏”及约 30 分钟预运行覆盖目标。
- 明确当前仍处于设计阶段，Plugin 与 Agent Runtime 尚未落地。
- 落地外置 Dynamic Render MVP：结构化 Timeline、Render 编译、逐段状态机、黑屏/Timeline Host、双 WebSocket 通道和三 Event 示例。
- 增加 `npm run dynamic` 可选入口，普通 `dev/serve` 保持原行为；未修改 WebGAL/MyGO Bundle、`assets/` 或 `game/`。
- 增加动态编译、非法输入、空 Event 黑屏、热更新不改写 active Render 和真实 WebSocket 段间推进测试。
- 使用原版 MyGO/WebGAL Bundle 完成浏览器验证：第一段实际渲染背景、Live2D 与台词，段尾自动接力第二段，队列耗尽后回到黑屏，其它 Event 保持 Ready。
- 冻结新的核心边界：外部 World / Agent Runtime 是世界唯一权威，MyGO/WebGAL 只是动态编译和播放 `RenderArtifact` 的 Render Backend。
- 核对 Generative Agents 论文证据：论文明确采用 Sandbox time-step action loop，但 `world_version / snapshot / atomic commit / canonical WorldEvent Ledger` 属于本项目工程化增强。
- 明确 WebGAL 的 Sentence、Auto/Fast Timer、Pixi Ticker 与 `SYNCFC` 都是演出/观看时钟，不能作为 World Tick。
- 将链路细化为 `ActionProposal -> WorldTransaction -> WorldEvent -> RenderArtifact`，记录 World Cursor、Generation Cursor、Viewer Cursor 三游标分离。
- 更新世界与 Event 未决问题，移除“World Time 是否由 Render 推进”这一已解决分歧，转向 Scheduler、Resolver、Recognizer、Ledger 和 Buffer 水位的实现参数。
- 按用户关键判断将时间语义修正为 `World Time = Actual Runtime`：模型推理和工具调用延迟会使世界自然前进，但不自动编译成 WebGAL 等待。
- 冻结 Broadcast Agent 的 Temporal Projection 职责：识别技术空档与叙事时间，通过省略、压缩、切镜、蒙太奇或明确艺术停顿生成 Render Timeline。
- 确认宏观算法为 Generative Agents Character 认知底座 + Director Agent + Broadcast Agent，并保留 Scheduler/Resolver/Ledger/Event Recognizer 的确定性治理边界。
- 将“MyGO 番剧视频 → 带证据 Character Skill”记录为后续算法方向；要求先用人工 Gold Skill 验证 Runtime，当前不宣称已有 VLM/微调结果。
- 将“Timeline 是世界执行语义、MyGO 解析只是投影副产物”固化为项目典范定义，避免再次把 Render Queue 当成核心。
- 确认 Character Runtime 以 Generative Agents 认知循环作为 Baseline，同时保留 Actual Runtime、认知分层、Ledger 和冲突治理等必要改造。
- 新增导演与导播层专题：Director 控制未来机会/压力/约束，Broadcast 控制 Event 选择与观看投影，二者都不能改写世界事实。
- 将 Director 目标函数、剧情锚点、刺激选择、自治/可控权衡，以及 Broadcast 的时间压缩、连续性、信息控制和 Buffer-aware 策略列为下一阶段宏观算法研究问题。
- 增加 Director/Broadcast 反馈防火墙：同局观众热度不驱动世界，显式玩家输入必须作为正式 WorldEvent，局后指标只用于下一局多目标评估。
- 将 NarrativeDirector 收敛为低频最小干预控制器：`NoOp` 常驻、干预算/TTL/冷却、线程允许 missed/expired，角色拒绝不是执行失败。
- 增加 Broadcast event watermark 和 fact/quote/inference/unknown 事实成熟度，防止迟到结果造成“先解说、后改事实”。

## 2026-08-21

- 根据用户纠正，否决“外部 monotonic timer 在 Agent 尚未返回时独立推进叙事状态”和“通用程序硬编码咖啡/排队 duration”的设计。
- 将 Actual Runtime 修正为生成完成驱动：Character/工具真实响应耗时作为时间证据，Director 在结果返回后做 Temporal/Causal Completion，Binder 在 Director 返回后绑定完整 measured span，Validator 只校验硬不变量。
- 拆分 Director 的两种职责：每个 generation wave 必需的 Segment Completion，以及低频、可 `NoOp` 的 Narrative Intervention；前者属于 Character Runtime 闭合 Event 的基础能力。
- 明确 Director Completion 与 Broadcast Projection 分权：前者决定世界中的时间/因果和对象结果，后者只决定已提交世界时间如何被玩家看见。
- 修正“30 分钟覆盖目标”为“开播前约 30 分钟真实 Warm-up”：分别度量 wall-time、committed world lead、ready render playable time 和 production/consumption ratio；只有 Event Plan 不算防卡顿库存。
- 新增 [难点、卡点与代价账本](difficulty-ledger.md)，首批回填 25 项历史与当前问题，包含首次提出日期、交换代价、否决方案、当前方向和未解决项。
- 将 Director 自身补完调用也消耗时间、可能形成递归解释的问题标为推导风险和 Open Research，没有冒充用户已确认结论。
- 扩展无 Maze 语义补完、多 Event generation wave、自然时长、对象/资源约束、Golden/Badcase 和 Buffer 水位等未决问题。
- 收紧 Director/Broadcast 的时间判定权：Director Completion 在 Committed Segment 中标注 generation gap、角色犹豫、环境等待和叙事动作；Broadcast 只能消费标注做观看投影，不能重新解释世界语义。
- 完成全 Wiki 相对链接与语义一致性审计：未发现断链；为 mechanisms、director-broadcast 和 open-questions 补充难点账本入口。
- 将难点账本改为 Question-ledger：所有 H-001～H-025 的核心问题统一使用问句，旧答案被否决后问题仍保留。
- 恢复两个最早的母问题：“如何串联 Agent Runtime 与底层 Galgame 引擎？”以及“去掉 Maze 和部分 Entity 渲染后，外在事件如何处理、如何权衡设计？”
- 为两个母问题补充当前回答、收益、失去的能力、协议/产品/算法代价与仍待回答的边界。
- 记录新的感知难点：“去掉 Maze 后，Persona.perceive() 如何获得外部信息，同时避免上帝视角、串行先手偏差和过度物理模拟？”
- 建议将官方 `perceive()` 的六项职责拆开：World Runtime 负责语义场景与硬可见性投影，Persona 负责注意力、新颖性和带 provenance 的记忆固化。
- 初步冻结同一 generation wave 读取同一 committed version；本轮未提交角色输出必须等提交后才能成为下一轮 Observation，以消除 Python `for` 顺序先手。
- 新增串行调度问句：第一版可以保留稳定 `for` 循环降低实现复杂度，但串行只能用于计算，不能产生串行可见性或循环内世界提交。
- 建议先封装 `freeze snapshot -> project all frames -> collect proposals -> Director completion -> validate -> one commit`；禁止 Persona 读取/改写他人的 live Scratch，打乱调用顺序应得到同一结果。
- 明确感知来源不是 Director 单源：Character、Director、系统/工具/玩家输入统一经 Validator/Committer 成为 committed WorldEvent，随后 PerceptionProjector 才按角色隔离并投影；Director 不直写 Persona Memory。
- 按用户一期取舍修正串行策略：同一 Event 内稳定串行并逐步提交，相互隔离的 Event 并行；取代“全世界同一 wave 收齐后一次提交”的过强约束。
- 明确 `no_op` 不是当前 Agent 用来猜测“该让谁说话”；Scheduler 分配下一决策机会，`addressed_to_me / pending_response` 告诉目标角色存在待回应事件，而回复、拒绝、延后或不行动仍由角色决定。
- 明确纯 `no_op` 只写 Decision Trace、让出 Event 游标并携带唤醒条件，不生成占位 WorldEvent；具有世界语义的主动等待才提交为 `wait`。
- 用户确认开始进入落地设计，并要求 Runtime 位于 MyGO 内、按 Generative Agents 源码目录组织；否决先前通用 `runtime/world.py + contracts/` 分层草案。
- 新增独立一期落地文档 `agent-runtime-implementation.md`：Agent Runtime 是纯 Python Server，不承担游戏设计；保留 Persona 认知目录，替换 Maze/execute 世界接口，并通过 RenderGateway 连接现有 Dynamic Render Plugin。
- 明确第一阶段先用 Fixture/Stub 跑通两个 Event、感知隔离、Director Completion、WorldEvent、RenderJob 和 WebGAL 播放状态；Memory/Reflection 等算法细节延期。
- 根据用户纠正重构一期目录：`persona / director / broadcast` 统一归入 `agent/`，`event/` 与 `world/` 在 Agent 外部平级；修正此前将各类模块扁平堆在 `agent_runtime/` 根目录的错误。
- 补充目录依赖方向：Agent 不拥有 World/Event，Director 不直写 Ledger，Broadcast 不直控播放器，World 不依赖 WebGAL。
- 将目录设计错误登记为 H-028：领域顶层按 `agent / event / world` 分层，只有 `agent/persona` 沿用 Generative Agents 认知子树；补充避免包循环的依赖规则。
- 根据用户纠正，将 Memory 从 Persona 私有目录提升为 `agent/memory/` 通用能力，供 Persona、Director、Broadcast 复用；`scratch` 和 `spatial_memory` 仍保留为 Persona 私有状态。
- 明确“共用 Memory 模块不等于共享记忆数据”：所有记录以 `agent_id + namespace` 隔离，Director/Broadcast 的内部记忆不得泄露给 Persona。
- 新增三类 Agent 认知基座问题：Persona、Director、Broadcast 共享 AgentLoop 阶段协议、Memory 与模型/Prompt 基础设施，但各自保留 cognitive strategy、prompt template、输出类型和 namespace。
- 将通用循环修正为 `observe/perceive -> retrieve -> plan -> propose -> Runtime commit -> observe_outcome -> reflect`；`execute` 不属于 Agent 通用能力，避免 Agent 绕过 World/Render 权威直接产生副作用。
- 新增 D-033：Memory 是所有 Agent 共用的基础模块，但内容按 `agent_id + namespace` 隔离；一期只实现 append/get/recent/tag search/provenance，复杂评分由各 Agent retrieve strategy 负责。
- 收敛实现优先级：骨架落地后的主要难点是 Prompt Contract 与 Runtime Scheduling；第一步用 Fixture 隔离调度问题，再逐类接入 Persona/Director/Broadcast Prompt。
- 完成面向下一开发 Session 的交接审计与收口：区分已实现/已冻结/待验证/Open Research，依据本地 Generative Agents 源码校准可复用与必须重写的边界，补齐共享 AgentLoop 到 RenderGateway 的完整链路、最小数据契约、Memory 写权限、Render 四端口、Phase 0-6 开发顺序、测试完成条件和可复制启动指令。

## 2026-08-22

- 根据用户确认，将一期 Agent Runtime 从“纯 Python + 自研通用 AgentLoop”修正为 Go + Eino ADK：Character 使用自定义 `PersonActAgent` 包装内部 Compose Graph，Decision Run 在 Proposal 后结束、World Commit 后另走 Feedback Run；Eino 接管 Agent/Runner、Graph/Chain、模型/Tool、Prompt、retry/failover、Callback 与可选 Agent checkpoint，MyGO 保留感知权限、Memory 语义、World/Event 调度、时间补全、Validator/Committer、Ledger 和确定性 Render，并记录 option 映射、`CustomizedOutput` 浅复制、checkpoint 分层、Graph 副作用幂等等适配风险。同步验证当前 `npm test` 为 14/14 通过。
- 将 Wiki、结构化作品、开发工具、测试与 Dynamic Render Adapter 从固定版 WebGAL/MyGO 目录迁入同级 `generative_go_world`；底层播放器和大体积素材继续留在 `MyGO_v3.1.1_ForScript`，开发服务器以 `devRoot -> webgalRoot` 双根方式联调，后者默认使用兄弟目录并支持 `WEBGAL_ROOT` 覆盖。
- 新增 Location World Model：地点以稳定 ID、版本化 Fact/Info 和 WorldEvent 引用构成可回放 LocationView；地点事实不能被自然语言覆盖，周期 Info 不等于实际 Event；角色前往地点前由 Runtime 触发 Director 查询并提出 DiscoveryPlan，角色只能依据已提交的 Fact/Info 或传播 Event 经感知投影后获知。

## 2026-08-24

- 明确 NPC DIY 使用“受限创作者 Manifest -> 受信 CompiledPersonActSpec -> 共享 PersonActAgent”的窄接口，不开放任意 Eino Graph、脚本、URL/MCP、Memory namespace、provider 或 World/Render 写权限。
- 基于 Eino v0.9.15 源码核对 Agent、Runner、Compose Graph、Callback、Tool 与 checkpoint 能力，保留 Eino 执行状态和项目长期 Memory/World Ledger 的分层。
- 落地无网络 Fixture PoC：严格 Manifest 读取、Catalog 权限收敛、稳定 digest、typed PersonAct Graph、ADK Runner 适配，以及跨 namespace、越权 Tool、非法 Prompt/Proposal、非 affordance target 和不可见 evidence 的拒绝测试。

## 2026-08-31

- 将现行 Agent Runtime 技术方案从 Go + Eino ADK 迁移为 Python 3.12 + `langchain-core==1.6.1`；保留 `agent / event / world`、Proposal/Commit、Memory namespace 与 World/Render 分权，D-034 标记为被 D-038 取代。
- 建立强类型边界：统一 strict/frozen Pydantic Model 负责运行时结构校验，pyright strict 负责静态接线，ruff/pytest/uv 负责质量与环境；明确 LangChain `Runnable.with_types()` 只提供类型/Schema 元数据，不做 runtime validation。
- 当前 PersonAct 使用 `RunnableLambda + RunnableSequence`，不引入 LangGraph；只有出现真实复杂分支、暂停恢复或持久执行需求后才重新评估，任何框架状态都不能替代 World Snapshot、Ledger 或 commit protocol。
- 将 NPC DIY 文档迁移为 `npc-diy.md`，并按当前 Python 源码校准实现事实：只完成 Manifest Compiler、`CompiledPersonActSpec`、Fixture Planner、typed PersonAct Pipeline 与 Proposal 权限测试；完整 Agent Runtime 仍未实现。
- 根据用户最新纠正，将 Character 公共边界收敛为外部 Event Scheduler 调用 `PersonActAgent.decide`：一次只为 `spec.agent_id` 返回一个 strict/frozen ActionProposal 对象，跨 wire 时再显式序列化 JSON；不实现或暴露 `Persona.move()`，不迁移 Sandbox loop、Maze、path/tile movement 或 `execute`。
- 冻结 Proposal wire contract：固定 `proposalId / agentId / eventSessionId / basedOnWorldVersion / action / evidenceIds` envelope，`action.kind` 为 `act / interact / utter / respond / wait / no_op` 判别联合；`interact` 只有一个显式 character/object target，`utter/respond` 只能指向 character，且无 `move/locationId/targetIds`。
- 随并行代码落地再次校准实现状态：`PersonActAgent.decide` 已完成 prepare/perceive/retrieve/plan/propose，返回 strict/frozen `ActionProposal` 对象；`proposal.py` 收敛为 Proposal 构造与权限校验。Reflection/commit feedback、Event Scheduler、World Commit 与完整 Runtime 仍未实现，且本次文档修改不宣称测试结果。
- 记录 PersonAct review 收敛：同一 Agent 的 `decide` 由实例锁串行化并一次替换 immutable private snapshot；novelty 覆盖完整 EVENT stream 且使用 canonical identity，event ID/revision 成对校验；当前事件不参与自身 retrieval；部分日程合法，active action 使用当前 slot 剩余时长。
- 将 `perceive -> retrieve -> plan -> ActionProposal -> Director / Validator / Committer -> Committed WorldEvent` 提升为项目最高优先级不变量，明确 Proposal 是角色意图而不是执行结果。
- 正式将 Creator Manifest、Compiler、Persona State/Memory/Cognition、Action Contract、权限校验与 Trace 定位为项目自研的 `Generative Go World NPC ADK`；LangChain Core 只提供内部编排与模型接入基础设施。
- Python 环境管理统一使用 uv：`.python-version` 固定解释器，`pyproject.toml` 声明依赖，`uv.lock` 锁定解析结果，安装与工具执行统一走 `uv sync --frozen` / `uv run`。
