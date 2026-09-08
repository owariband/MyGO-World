# 未决问题

问题背景、已支付代价和被否决方案统一保留在[难点、卡点与代价账本](difficulty-ledger.md)；本页只维护仍需设计或实验回答的部分。Director 的权限边界以 [D-044](decisions.md#d-044director-只管理由已提交事实触发的-eventstaff) 为准，不再把 Segment Completion、Narrative Intervention 或 DiscoveryPlan 当作候选方向。

## 开工分级

- **Phase 0-2 必须冻结：**`RespondAction.inReplyToEventId`、Scenario 引用完整性、SQLite 首批 schema、WorldUpdater 事务边界、AgentView 硬可见性。
- **进入 EventSession 自由互动前必须冻结：**哪些自然行为触发 join/leave、邀请是否需接受、同地是否自动同组、跨 root 发言的语义、连续 `no_op` 唤醒。
- **进入 EventStaff Director 前必须冻结：**首批 Staff kind、process-start 事件、enqueue/release/cancel affordance、时钟与 deadline fallback。
- **进入 Dynamic Render 真接入前必须冻结：**RenderJob envelope、幂等/乱序语义、状态回传、Viewer Cursor 和 Artifact 持久化。
- **接生产 Provider 前必须具备：**完整 GenerationTrace 持久化、World commit 关联、Prompt 输入权限、结构化失败分类和 live evaluation。
- **产品化前才阻塞：**真实 30 分钟领先库存、成本预算、安全鉴权、复杂 barrier、迁移和高可用。

## Plugin 与 WebGAL 适配

1. 是否长期锁定 MyGO `3.1.1` / WebGAL `4.5.19`，还是最终从上游提炼正式 Plugin API？
2. `TEMP_SCENE` 没有 request ID 和 ACK，如何确认 `render_id` 已开始、播放到哪里并完成？
3. 断线后重建 iframe，还是刷新同一播放器？
4. MVP 只用 `TEMP_SCENE`，还是同时实现动态场景文件 + `JUMP`？
5. Plugin Server 的会话身份、Origin、消息限长和频率限制如何定义？
6. 协议升级后的契约测试与兼容矩阵如何组织？

## Render 与播放

1. 最小 BroadcastPlan/RenderJob 支持哪些 Beat；是否先排除 Choice、自由输入和动态素材？
2. Render 的目标时长、最大台词数、素材数和生成超时是多少？
3. 切回 Event 时默认继续上次 Sentence、从第一条未读开始，还是跳到 Live？
4. 用 `SYNCFC` 恢复语句游标时，快速重放会不会重复触发 BGM、SFX 或动画？
5. Render 完成由 Sentence 上限、末尾标记还是超时判断？
6. 产品黑屏是否永远无文字；调试诊断如何与产品界面隔离？

## World、Event 与持久化

已确定：外部 Runtime 持有 World 权威；SQLite 保存关系型当前状态，`world_events` 保存唯一 append-only 客观历史；MVP 不保存逐版本 Snapshot/Entity Revision/WorldSegment。MyGO Sentence、动画帧和 `SYNCFC` 都不是 World Tick。

1. 每次 Character commit 怎样推进 `world_time`；Generation elapsed 只做 Trace，还是提供受控的最小推进证据？
2. EventStaff 的 `next_check_at` 使用故事时间、受控 wall clock，还是二者映射？
3. 进程暂停、重启和离线预跑时，world time 与到期 Staff 如何恢复？
4. 跨 root 并行计算时，共享 Character、Object、Location resource 和因果依赖怎样形成隔离证明？
5. expected-version 冲突后，哪些 Proposal 可以重基，哪些必须重新调用 Character？
6. `interaction_requests` 中哪些 utter/interact 创建 request；多个 pending request 如何排序、延期、取消和过期？
7. Event 是否永远一项 commit 对应一条，还是某些原子状态变化需要稳定排序的多条 Event？
8. `details_json` 的 subtype registry 和迁移版本如何管理，避免重新退化成任意 JSON？
9. Generation Trace 的隐私、保留期和原始模型输出引用如何设计？
10. 出现真实历史查询需求后，增加 typed effect log 还是周期 checkpoint？
11. 第一阶段是否严格只观察；若允许玩家 Choice，如何使介入点后的库存失效？

## EventSession 与自由互动

已确定：每个 Character 只有一个稳定 EventSession node；`UnionPart[T]` 维护当前 root 与成员 set；merge/split 不创建 successor Session。WorldEvent 保存 `root_session_id_at_commit`，不会被当前分区改写。

1. Character 什么自然动作表达“加入互动”：靠近、打招呼、明确请求加入，还是目标回应后才 merge？
2. 邀请是否必须由目标接受后才能 merge；拒绝、忽略和超时分别怎样落 Event/request？
3. 同地点但未参与对话的人是否保持不同 root；他们可听见 ambient 信息的范围是什么？
4. 跨 root 点名发言先作为 targeted message，还是直接产生 join request？
5. Character 离开当前地点是否必然 split；临时离席但仍能听见如何表达？
6. split 的业务 Plan 怎样完整覆盖原 part，并选择各新 root？
7. 同一 root 的轮转是 round-robin、pending request 优先，还是显著 Candidate 优先？
8. 连续 `no_op` 的退避、最大次数和重新唤醒条件是什么？
9. merge/split 与 Character/Object 变更发生在同一 Proposal 时，原子 transaction 的事件顺序如何固定？
10. 五人 Fixture 之外，UnionPart 的 O(n) 重标何时才值得替换？

## AgentView 与 Character 认知

已确定：World 侧 AgentViewBuilder 只做硬可见性、字段裁剪和 affordance；Character 的 PersonActLoop/CognitiveController 负责 attention、novelty、Memory、planning 和 proposal。Director 不进入这条认知链。

1. `self / direct_interaction / same_scene / targeted_message / commitment_update` 各允许哪些字段？
2. EventStaff release 是否需要独立 `environment_process` channel？
3. `mandatory / relevant / ambient` 三层预算怎样设置，既不漏直接互动又允许错过背景线索？
4. `addressed_to_me / pending_response` 怎样提高优先级而不强迫回复？
5. 同一 WorldEvent revision、重复 occurrence 和持续状态如何做 novelty？
6. Character Skill 能否改变 Observation 的主观措辞/置信度；怎样证明它没有扩大可见范围？
7. `AgentView.affordances` 如何替代 spatial memory + Maze address；KnownPlace、可到达地点和当前可执行动作怎样分层？
8. 当前 `InteractAction.target + description` 无法稳定标识 Object operation；应增加 World-issued `affordance_id`，还是改成每类操作的 typed action union？
9. Persona private state 哪些字段能在 `decide` 内更新，哪些必须等待 committed outcome？
10. 何时出现足够重复职责，值得把 PersonActLoop 纳入更宽的 `CognitiveController`？

## Location World Model

已确定：地点使用稳定 ID；MVP 先保存关系型当前 Fact/Info，历史直接按 `world_events.location_id` 查询。Director 不参与信息披露；既有公开信息由 AgentViewBuilder 确定性过滤，需要传播行为时由 Character/System 自己提交。

1. `LocationFact` key registry 的第一批键有哪些；设施何时升级为有生命周期的 Object？
2. Disclosure 一期只支持 `public / restricted / private`，还是需要角色组、关系和时间窗？
3. 周期 Info 如何触发 Runtime wakeup，而不把“预计发生”提前写成 Event？
4. LocationView 的 recent Event 窗口、分页上限和 Prompt budget 如何设置？
5. Location 别名、父子地点和世界时区哪些必须进入一期？
6. Render Planner 如何把 `location_id` 映射到 WebGAL `scene_id`？

## EventStaff Director

已确定：Director 只能读取按触发源裁剪的 `DirectorView`，并从 World 给出的 affordance 中选择 `enqueue / keep / release / cancel / no_op`。它不能读取未提交 Proposal、Character 私有认知、无关 Session、InteractionRequest 调度或 Viewer/Broadcast 数据；也不能输出角色行为、Session 变更或任意 World patch。

1. MVP 首批支持哪些 Staff kind；是否只做 `coffee_brewing_completion` 一个垂直例子？
2. 每个 Staff kind 的 process-start Event、subject state、completion Event、release guard 和 invalidation Event 怎样注册？
3. 什么情况下 World 自动生成 enqueue affordance，什么情况下仍需 Director 选择？
4. `earliest_release_at / due_at / hard_deadline` 是否都需要；到 deadline 是否由 Runtime 确定性 release？
5. Director `keep` 能否任意推迟 `next_check_at`，还是必须落在 World 给定区间？
6. 同一 subject 上互斥或重复 Staff 怎样拒绝；唯一键是否足够？
7. cancel 是否总生成 WorldEvent；“过程被取消”和“后台任务记录取消”怎样区分？
8. Staff 绑定稳定 `session_id` 后，split 到多个 root 时默认跟 anchor root；是否存在必须改为 location delivery scope 的真实用例？
9. 单 World 单 `director_event_cursor` 是否足够；若未来多个 consumer，游标和 lease 怎样设计？
10. enqueue/no-op 与 cursor、release/cancel 与对象/Event/version 的故障注入用例有哪些？
11. Director 使用 LLM、规则策略还是混合策略；怎样证明 LLM 只在 affordance 内做“何时”判断？
12. Director 超时、结构错误和语义越权时，默认 `keep/no_op`、有限 retry 与硬 deadline fallback 如何组合？

## Broadcast

1. Event Selection、Temporal Projection、Information Control 和 Continuity 如何联合优化？
2. Ready Render 不足时，切镜/摘要到什么程度才不构成因果欺骗？
3. 使用什么 event watermark 和事实成熟度，避免读取半提交状态？
4. 每个 beat 的 `fact / quote / inference` 怎样校验和评测？
5. 当前局玩家兴趣能否只反馈 Broadcast；如何避免反馈旁路进入 DirectorView/AgentView？
6. 多玩家是否共享一个 World、各自拥有独立 Broadcast/Viewer Cursor？
7. 训练/评测怎样使用完整 EventHistory，避免只从精选片段学习产生 spectacle selection bias？

## 约 30 分钟 Warm-up、领先库存与成本

1. 约 30 分钟 `warmup_wall_time` 是所有 Event 共用还是每个世界实例独立？
2. 当前模型、角色数和 root 数下能产生多少 `committed_world_lead` 与 `ready_render_playable_time`？
3. 开播时最低需要多少 committed Event 和 Ready Render？
4. `production_to_consumption_ratio < 1` 时，优先减少 root、降低 Character 频率、压缩 Render 还是延长黑屏？
5. 玩家只看一个 Event 时，其它 root 应全速生成还是降频？
6. Character/Director/Broadcast 模型预算怎样隔离，避免慢 Staff 检查拖垮 Character loop？
7. 长期运行的 Token、Generation Trace、WorldEvent 和 Render 存储上限是什么？

## Agent 能力与数据

1. Character Skill 的 exact version/hash 已落地；后续怎样设计重绑、兼容、evidence 和长期一致性评测？
2. 第一版使用通用模型 + Skill，还是角色微调模型？
3. Director 与 Broadcast 是否共享底层模型实例，但保持完全不同的 input/output contract？
4. 评测集如何覆盖角色一致性、认知越权、Staff 越权、跨 Event 因果、Render 合法率和黑屏率？
5. 人工 Gold Character Skill 如何定义；自动 Skill 如何在保留集比较？
6. ModelCallTrace 怎样映射成可持久 GenerationTrace，并关联 Proposal、Validator、World commit 与 Render？
7. Tool Catalog 的资源级 scope、版本兼容和配额怎样表达？
8. Manifest 更新后，运行中 Session 固定旧 digest 到结束，还是支持显式迁移？
9. 什么证据足以重新评估 LangGraph，并怎样保证其 checkpoint 不替代 World 持久化？

## 代码与交付

1. Plugin 的目录名、启动命令和独立包边界尚未冻结。
2. 原有 `npm run dev / compile / test` 必须兼容；Plugin 命令应独立。
3. MyGO 角色、音乐、背景和 Live2D 资产是否允许公开视频或发行，需要单独确认。
4. EventStaff MVP 实现前，是否先把源码中的 Director stub 注释与旧 SegmentDraft 术语同步清理？
