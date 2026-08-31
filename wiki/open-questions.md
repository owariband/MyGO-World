# 未决问题

问题的提出背景、已支付代价和被否决方案统一保留在[难点、卡点与代价账本](difficulty-ledger.md)；本页只维护仍需设计或实验回答的部分。

## 开工分级

- **不阻塞 Phase 0-3 Fixture Slice：**真实 Prompt、复杂 Memory 排序、Reflection、Director 长期目标、Director 自身 latency 的最终算法、30 分钟 Buffer 参数。先按[一期落地方案](agent-runtime-implementation.md#4-runtime-主循环)中的临时规则跑出可重放 Trace。
- **进入 Phase 4 前必须验证：**Event 隔离键、`no_op` 唤醒、共享实体拒绝语义，以及单 Event 失败不阻塞其它 Event。
- **进入 Phase 5 前必须冻结：**RenderJob envelope、Runtime/Plugin 幂等与乱序语义、状态回传、Viewer Cursor 持久化。
- **接真实模型前必须具备：**Prompt 输入权限、结构化输出校验、GenerationTrace 和可定位失败分类。
- **已确定且不再作为当前选型问题：**Agent Runtime 使用 Python 3.12 + LangChain Core 1.6.1；PersonAct 当前是 strict Pydantic 值之间的 `RunnableSequence`。`with_types()` 不做 runtime validation；当前不使用 LangGraph，只有出现真实的复杂分支、暂停恢复或持久状态需求后才重新评估。
- **产品化前才阻塞：**真实 30 分钟领先库存、成本预算、安全鉴权、复杂 barrier/迁移和高可用。

## Plugin 与 WebGAL 适配

1. 是否长期锁定 MyGO `3.1.1` / WebGAL `4.5.19`，还是最终从上游提炼正式 Plugin API？
2. `TEMP_SCENE` 没有 request ID 和 ACK，如何确认 `render_id` 已开始、播放到哪里并完成？
3. 断线后是重建 iframe，还是刷新同一播放器？
4. MVP 只用 `TEMP_SCENE`，还是同时实现动态场景文件 + `JUMP`？
5. Plugin Server 的会话身份、Origin、消息限长和频率限制如何定义？
6. 协议升级后的契约测试与兼容矩阵如何组织？

## Render 与播放

1. 最小 Render Proposal Schema 包含哪些 Beat？是否先排除 Choice、自由输入和动态素材？
2. Render 的目标时长、最大台词数、素材数和生成超时是多少？
3. 切回 Event 时默认继续上次 Sentence、从第一条未读开始，还是跳到 Live？
4. 用 `SYNCFC` 恢复语句游标时，快速重放会不会重复触发 BGM、SFX 或动画？
5. Render 完成由 Sentence 上限、末尾标记还是超时判断？
6. 产品黑屏是否永远无文字？调试诊断应与产品界面隔离。

## 世界与 Event

已确定：World Time 由外部 World / Agent Runtime 持有；MyGO Sentence、动画帧和 `SYNCFC` 均不作为 World Tick。Agent 真实响应耗时可以进入 World Time，但采用生成完成后的事后语义绑定：Character 返回后由 Director 补完当前 World Segment，外部 timer 不在响应前独立完成叙事动作。生成耗时也不自动转成 Render 等待。

1. 一次 generation wave 的边界是什么：等待本 Event 全部 Character 返回、等待所有受影响 Event，还是允许部分提交？
2. Agent timeout/fallback 后，measured span 如何进入 Director Completion：保持未闭合 Event、生成缺席事实，还是重试？
3. Director 自己的补完调用也花时间，首版是否采用“相对区间输出 + 返回后 Temporal Binder”，怎样处理无法分配的尾部 latency？
4. 多 Event 异步返回时，共享角色、对象和跨地点因果如何组成一个全局一致的 World Segment？
5. 哪些规则只作为硬不变量，哪些可以让 Director 生成：角色双占用、资源容量、对象所有权、排队公平性各属于哪一边？
6. 同一 Event 允许跨多少 generation wave 保持 `ACTIVE`，怎样避免 Director 为了闭合 JSON 而过早宣布动作完成？
7. Runtime 暂停、进程重启和离线预跑时，measured latency 与 World Time 如何恢复；是否允许加速生成但仍保持事后绑定？
8. 同一角色进入另一个 Event 时，旧 Event 如何结束或移交？
9. 如何表达角色局部认知、误解和秘密，防止全员共享上帝视角？
10. Minimal Validator 的最小不变量、诊断类型和 Director 修补上限有哪些？
11. Event Recognizer 的聚合键、边界规则、显著性阈值和 evidence validator 如何用 Golden/Badcase 校准？
12. World Ledger 使用 SQLite 单写者还是其它存储；Snapshot、Generation Trace 与 Replay 的保留策略是什么？
13. 第一阶段是否严格只观察？如果允许 Choice，如何使介入点之后的已生成库存失效？
14. Broadcast Temporal Projection 的压缩率、因果覆盖率和艺术停顿规则如何评测？

## Location World Model

已确定：地点使用稳定身份、append-only Fact/Info revision 和对唯一 WorldEvent Ledger 的引用形成 `LocationView`。地点事实与角色认知分离；周期 Info 不自动等于实际 Event；Director 只能提出信息传播机会，不能直接写角色 Memory。详见[地点 World Model](location-world-model.md)。

1. 一期 Location 存储是否直接采用 SQLite 单写者，还是先用 JSONL adapter 完成 Golden Trace 后再迁移？
2. `LocationFact` key registry 的第一批键有哪些；钢琴等设施何时从标量 Fact 提升成拥有独立生命周期的 Entity？
3. `LocationInfo.DisclosurePolicy` 一期只支持 `public / restricted / private`，还是需要角色组、关系和时间窗条件？
4. DiscoveryPlan 如何定义去重键、过期时间和一次访问中的最大信息量，避免角色重复到访时反复收到同一提示？
5. 周期 Info 如何生成 Scheduler wakeup，而不把“预计发生”提前写成 WorldEvent？
6. `LocationView` 的 recent Event 窗口、分页上限和 Prompt budget 如何设置，避免热门地点历史无限增长？
7. Location 别名、父子地点和世界时区哪些必须进入一期；Render Planner 如何把世界 `location_id` 映射到 WebGAL `scene_id`？

## 无 Maze 后的语义补完

1. “Anon/Soyo 排队、煮咖啡”第一条 Golden Trace 中，哪些事实由 Character 输出，哪些由 Director 补完，哪些只能由 Validator 拒绝？
2. 不硬编码动作 duration 时，Director 如何引用常识、场景 Skill 或历史分布，又不把每次咖啡都写成任意时长？
3. 排队结束应由下一轮角色/环境输出触发、由 Director 判断，还是需要一个非硬编码的 affordance/tool 查询？
4. 对象（咖啡）需要稳定 ID 和生命周期到什么程度，才能支持后续角色引用而不重造完整物理模拟？
5. 资源争用到底是世界硬约束还是导演叙事判断；如果两人同时宣称使用同一台机器，修补策略是什么？
6. 如何评价 Segment Completion 的“自然”：只测 schema/一致性不够，是否需要 pairwise human preference、Golden/Badcase 和反事实 trace？
7. Director 补出的桥接 Event 是否会侵蚀 Character 自主性；怎样限制它只能连接角色输出而不能替角色做重大选择？
8. `PerceptionFrame` 的最小字段是什么；`self / direct_interaction / same_scene / targeted_message / commitment_update` 各能看到哪些字段？
9. `mandatory / relevant / ambient` 三层注意力预算如何设置，才能既不漏掉直接互动，又允许角色错过背景线索？
10. 同一 WorldEvent 的 revision、重复 occurrence 和持续状态如何做新颖性判断，避免 SPO 去重错误？
11. Character Skill 能否影响 Observation 的主观解释和置信度；怎样保证它不能扩大硬可见范围？
12. Event 内 Scheduler 使用 round-robin、被点名优先还是 Director 建议；怎样避免固定顺序形成单一对话节奏？
13. `PerceptionFrame.affordances` 如何替代原版 spatial memory + Maze address；`KnownPlace`、可到达地点和当前可执行动作怎样分层？
14. Event 内串行时，Persona 私有 Scratch 的哪些字段允许本地更新，哪些必须由提交后的 Observation 更新？
15. `addressed_to_me / pending_response` 怎样影响回应优先级而不强迫回复；连续 `no_op` 怎样避免 Event 空转？
16. 隔离 Event 的判定键是什么；共享角色、对象、地点资源和跨 Event 因果出现时怎样建立 barrier 或合并 Event？

## 约 30 分钟 Warm-up、领先库存与成本

1. 约 30 分钟 `warmup_wall_time` 是所有 Event 共用还是每个世界实例独立；冷启动允许多大浮动？
2. 真实运行 30 分钟在当前模型、角色数和 Event 数下，能产生多少 `committed_world_lead` 和 `ready_render_playable_time`？
3. 开播时最低需要多少完整演员剧本和 Ready Render；Event Plan 为什么不能计入防卡顿水位？
4. `production_to_consumption_ratio` 低于 1 时，先减少 Event、压缩 Render、降低 Agent 频率还是延长黑屏？
5. 玩家只看一个 Event 时，其它 Event 应全速生成、降预算，还是只维持低频世界演化？
6. 多 Event 间怎样分配 Character/Director/Broadcast 模型预算，避免一个慢 Event 拖垮全局 completion wave？
7. 如何限制长期运行 Token、模型调用、Generation Trace 和 Render 存储成本？
8. 低水位、黑屏率、生成耗时、补完重试率、编译失败率和切换等待目标是什么？

## Agent 能力与数据

已确定：NPC DIY 使用受限 Manifest，经 strict Pydantic + 语义编译得到 frozen `CompiledPersonActSpec`；不开放任意 Runnable/Graph、namespace、provider、URL/MCP 或 World/Render 写工具。详见 [NPC DIY](npc-diy.md)。

1. Character Skill 如何在当前 Persona/Goal/Relationship/Voice 基础上表达带 evidence 的关系条件策略，并做版本发布与兼容？
2. 第一版使用通用模型 + Skill，还是角色微调模型？没有真实训练数据前不应承诺微调。
3. Director 和 Broadcast 是否共享模型？
4. 评测集如何覆盖角色一致性、认知越权、跨 Event 因果、剧情推进、Render 合法率和黑屏率？
5. MyGO 番剧视频的镜头切分、角色/说话人识别、字幕对齐和 Evidence Clip 契约是什么？
6. 人工 Gold Character Skill 如何定义，自动 Skill 应如何在保留集上比较关系条件行为与角色一致性？
7. 三类 Fixture Agent 共享 strict Model policy、`RunnableConfig` 与 Trace adapter 时，哪些调用代码可以复用，哪些 Pipeline/strategy 必须分开，才能避免大量 `if agent_type` 分支？
8. Director/Broadcast 的 reflect 一期应是 No-op、阈值触发还是提交结果后触发；怎样避免把 Persona poignancy 机制机械复用？
9. 通用 MemoryRecord 在一期最小字段已收敛为 `memory_id / agent_id / namespace / memory_type / created_at / content / tags / source_refs / metadata`；仍需实验哪些 metadata 值得结构化，以及跨 namespace 的显式授权是否一期需要。
10. Prompt Contract 如何保证 Persona、Director、Broadcast 的输入权限和输出 Schema 不串层；第一批 Fixture 何时替换为真实模型？
11. LangChain callback/tracing 与项目 `GenerationTrace` 的映射是否足以区分 Prompt、检索、Runnable 节点、调度、Validator 和 Render 失败？
12. ChatModel/Tool 的返回怎样统一显式 parse 为 strict Pydantic Model，并保证 `RunnableSequence` 的中间 `Any` 不越过公开边界？
13. 当前固定 Pipeline 不使用 LangGraph；若未来出现复杂分支、暂停恢复或持久执行，什么证据足以触发重新评估，并怎样保证其 state/checkpoint 与 World Snapshot/Ledger 物理分离？
14. 运行存储最终选 SQLite 还是 JSONL adapter；在单进程本地运行、原子提交、索引查询、重放和未来迁移之间如何取舍？
15. Tool Catalog 的资源级 scope、版本兼容和配额如何表达，才能在增加工具时维持默认拒绝？
16. Manifest 更新后，正在运行的 Session 固定旧 digest 到结束，还是支持显式迁移；旧 Trace 如何继续解析？

## Director / Broadcast 高层算法

已拆分：每个 generation wave 必需的 `Director Segment Completion` 属于基础 Runtime；以下主要是低频 `Narrative Intervention` 与 Broadcast 优化。

1. Director 的 Segment Completion 与 Narrative Intervention 应由同一模型/上下文完成，还是分成两个 Agent？
2. Director 的 Narrative Thread 最小状态、目标函数和决策频率是什么？
3. 硬剧情锚点约束“必须发生的世界事实”，还是只约束“必须创造的机会”？
4. Character Agent 连续拒绝剧情机会时，Director 如何升级压力而不破坏角色自治？
5. Director 的 World Stimulus Catalog 如何限制，避免万能巧合和作者之手？
6. Broadcast 的 Event Selection、Temporal Projection、Information Control 和 Continuity 如何联合优化？
7. Director Completion 应依据哪些 evidence 对 generation gap、角色真实犹豫和不可见动作分类；Broadcast 如何消费已提交分类而不重新裁决世界语义？
8. Ready Render 不足时，导播可以切镜/摘要到什么程度，才不构成因果欺骗？
9. 玩家兴趣能否成为 Director 的弱反馈；如何防止系统只追逐高刺激和狗血事件？
10. 多玩家是否共享一个 World/Director、各自拥有独立 Broadcast；其观察是否影响世界？
11. Director 与 Broadcast 使用同一模型还是隔离模型、Memory、Tool 和 Reward？
12. Director 最小干预目标如何量化；`NoOp`、预算、TTL、冷却和升级阈值怎样冻结？
13. Broadcast 使用什么 event watermark 和事实成熟度，避免补完结果尚未提交就“先解说、后改事实”？
14. 训练/评测应如何使用完整 Event Log，避免只从 Broadcast 精选片段学习产生 spectacle selection bias？
15. 隐形观察和世界内直播分别产生多大 observer effect，是否需要 broadcast/no-broadcast 成对实验？

## 代码与交付

1. Plugin 的目录名、启动命令和独立包边界尚未冻结。
2. 原有 `npm run dev / compile / test` 必须兼容；Plugin 命令应独立。
3. MyGO 角色、音乐、背景和 Live2D 资产是否允许公开视频或发行，需要单独确认。
4. 当前目录缺少可用于个人归属的独立 Git 历史；进入简历前需建立可追踪提交、Demo 和实验记录。

