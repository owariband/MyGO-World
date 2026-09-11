# M3 开发记录：单步世界与认知提交

> 状态：**DESIGN REVIEW**；M3.1–M3.4 代码尚未开始。
> 日期：2026-09-09
> 开发基线：`master@5d2f496`（M2 已提交并推送）。
> 总计划：[dev_plan_MVP.md](dev_plan_MVP.md#m3-单步世界与认知提交)
> 领域设计：[MVP_dev.md](MVP_dev.md)

本文从 M3 的设计 Review 阶段开始维护。当前记录的是预计 contract、文件范围、开发量和验收门禁，不表示代码已经实现；Review 通过并开始开发后，仍在本文件原位补充实际实现、真实 diff、测试结果、Review 修复和遗留边界，不另建同义的 `M3_dev_plan.md`。

## 1. M3 的完成边界

M3 先证明“一次角色决定”能安全地变成 World 结果，再把这条单步路径交给 M4 的持续 Runner 调用。阶段结束时必须能完整演示：

```text
读取同一 committed DB snapshot
-> 为 Anon 构建 AgentView
-> disposable PersonActAgent 产出一次 utter(expects_response=true)
-> WorldChangeValidator 形成 typed WorldUpdatePlan
-> 同一 SQLite transaction 提交 DialogueEntry、recipient、request、
   PersonaState、Memory、observation cursor 和 decision progress
-> 为 Soyo 构建包含 mandatory request 的新 AgentView
-> Soyo respond(in_reply_to_entry_id=...)
-> 原子提交 reply Entry 并解决 request
-> 关闭连接并重载，公共结果、私有结果和顺序完全一致
```

完成 M3 后只能准确表述为：

> 一次角色决策已经可以安全地变成可见、可追溯、可恢复的 World 结果。

以下能力明确不属于 M3：

- 持续运行的 EventSessionRunner、公平轮转、被点名优先、50 轮对话规则和空转退避；
- EventSession join/leave/transfer、UnionPart 运行时写回、StoryLine/StageView；
- 面向用户的 `pause_and_save / resume_world`、运行锁、调用额度和 Ctrl+C 收口；
- Director、pending EnvironmentEntry 的 schedule/keep/release/cancel 和咖啡完成流程；
- Broadcast、PresentationBinding、故事线 JSON、Worker 或 WebGAL 转译；
- Reflection/CognitiveController、自动结局、跨 root 并行和任意历史版本恢复。

## 2. 唯一 EventEntry 历史与所有权

新增代码统一使用 `EventEntry`。旧分支和历史 Wiki 中的 `WorldEvent`、`WorldChangeLog`、`EventStaff` 不再各建一张同义表：

```text
关系型当前状态        = 现在是什么
EventEntry(committed) = 已经发生了什么
InteractionRequest    = 哪条已发生 Entry 当前仍待谁回应
Agent Memory          = 某个角色如何注意、理解和记住它
```

M3 实际写入的判别联合只有：

```text
DialogueEntry = 已发生的 utter / respond
ActionEntry   = 已通过 World operation 生效的 Object interact
```

`JoinEntry / LeaveEntry` 在 M4 扩展同一个 union；`EnvironmentEntry(pending/committed/cancelled)` 在 M5 扩展同一张 `event_entries` 表。M3 不预先实现没有消费者的 Director 生命周期，不新增 EventStaff、pending queue、World Snapshot、WorldVersionRow 或第二份公共 Event 历史。

M3 的所有权边界保持不变：

- `ActionProposal` 只是角色意图；只有 WorldChangeValidator/WorldUpdater 能把已验证结果变成事实；
- World 计算公共状态变化，Agent 计算自己的 PersonaState/Memory；共享 transaction 只保证全有或全无，不改变领域所有权；
- Store、Builder、Validator、Updater 和 CharacterStep 都绑定完整 `WorldRef`，错 Project/World 必须在首个数据库写入前失败；
- 模型调用、attention、novelty、embedding 和 outcome feedback 都在写事务外完成；
- M4 必须复用 CharacterStep，不允许再写一套旁路提交协议。

<a id="m3-1"></a>

## 3. M3.1：Entry、回应与稳定 World operation

### 3.1 EventEntry 的关系型形态

`event_entries` 保存需要关联、过滤、排序和唯一约束的字段，不提供任意 `payload/state_patch`：

```text
event_entries
  world_id + entry_id                         composite PK
  status                                      M3 只允许 committed
  entry_kind                                  dialogue | action
  source_kind + source_id + source_index      character_proposal / proposal_id / 0
  world_version + entry_index                 commit_position；M3 entry_index 固定为 0
  root_session_id_at_commit + topology_version
  actor_agent_id
  target_agent_id? / target_object_id?        分列并分别使用 composite FK
  operation_id?                               ActionEntry 必填，DialogueEntry 为空
  audience_mode                               session | explicit
  delivery_channel                            direct | whisper | public
  occurred_at                                 World time
  text                                        已发生的可读结果
  created_at                                  wall-clock，仅用于诊断
```

M3 不需要 `details_json`。Dialogue/Action 当前真实使用的字段直接关系化；后续 Entry 类型确有无法独立查询的专属细节时，再由对应 strict/frozen contract 决定增加列还是受校验 JSON。自然语言 `description` 永远不能充当状态 patch。

同批新增三张规范化关系表：

```text
event_entry_links
  world_id + entry_id + relation_kind + related_entry_id
  relation_kind = previous | reply | cause
  relation_order

event_entry_recipients
  world_id + entry_id + agent_id
  PK(world_id, entry_id, agent_id)

interaction_requests
  world_id + request_entry_id                 PK；正文仍在 EventEntry
  request_kind                                M3 只允许 response
  requester_agent_id + recipient_agent_id
  status                                      pending | resolved
  resolution_entry_id?
  updated_world_version
```

数据库和 Store 至少保证：

- `UNIQUE(world_id, world_version, entry_index)` 固定提交位置，不使用 `MAX(event_order)+1` 或 row insertion order；
- `UNIQUE(world_id, source_kind, source_id, source_index)` 作为重试幂等键；
- Entry、link、recipient、request 的所有外键都携带 `world_id`，同 Project 两个 World 即使复用相同 ID 也不能串联；
- link 只能引用同 World、已 committed 且提交位置更早的 Entry；`reply` 最多一个，因此图不会成环；
- committed Entry、link、recipient 禁止 update/delete；Store 不提供通用修改入口，SQLite trigger 提供第二道防护；
- request 核心身份不可修改，只允许一次 `pending -> resolved`；resolution 必须是 recipient 对 source Entry 的有效 reply；
- recipient 是 commit 时物化的历史快照，未来 Session merge/split 不会扩大旧 Entry 的可见者；
- M3 一个 Proposal 最多产生一条 committed Entry，因此 `source_index=0 / entry_index=0`，不实现部分生效或无消费者的多 Entry batch。

### 3.2 Affordance 与 Object operation

当前 `Affordance(kind, target)` 与 `InteractAction(target, description)` 无法确定角色选择的是启动、检查还是停止对象。M3 使用 Scenario 声明的最小受信状态转换：

```text
ObjectOperationSeed
  operation_id
  from_state
  to_state
  result_text

WorldAffordanceResolver(current DB state + pinned Scenario)
  -> affordance_id = deterministic hash(
       WorldRef, world_version, agent_id, action kind,
       target, operation/channel/request source, current state
     )
```

- Object operation 随 Scenario canonical hash 钉住，不新建 Affordance 表，不接受模型提供 `to_state/result_text`；
- `InteractAction` 必须回传 World-issued `affordance_id`；World 在同一 version 上重新解析，成功后才使用受信 `operation_id/to_state/result_text`；
- utter/respond 也使用稳定 affordance 区分 direct、whisper 与 request source；
- `UtterAction` 显式携带 `expects_response`；`RespondAction` 显式携带 `in_reply_to_entry_id`；
- `evidence_ids` 继续只表示证据，不能兼任 reply、operation 或 audience；
- ActionEntry 的 `text` 来自受信 operation result；DialogueEntry 的 `text` 逐字来自角色 Proposal；自由文本不能被记录成没有发生的对象结果；
- 通用 `act` 或没有已注册 operation 的 interaction 是合法 `not_applied`，不写 ActionEntry。

首个 Fixture 给 `for-the-band` 的 `metronome` 增加一条 `stopped -> running` operation，并递增 Scenario seed version。Schema migration 只升级数据库结构，不静默迁移旧内容存档；旧 seed 的开发 World 继续因 seed hash/version 不匹配而拒绝加载，测试和开发使用新 World。

<a id="m3-2"></a>

## 4. M3.2：World 与 Agent 的一个原子提交

### 4.1 四类结果

`WorldUpdateResult` 明确区分以下结果，不把“合法但没有生效”伪装成审批拒绝或 Runtime 异常：

| 结果 | 含义 | World version / Entry | 私有状态 |
| --- | --- | --- | --- |
| `applied` | utter/respond 或注册 Object operation 已发生 | version +1；M3 最多一条 Entry | 保存认知结果；按 disposition 决定是否完成计划 |
| `not_applied` | Proposal 合法，但没有对应可执行 World operation，例如通用 act | 不推进；不写假 Entry | 保存确定性失败反馈；依赖成功的计划不得完成 |
| `wait` | 角色主动等待 | 不推进；不写 Entry/虚构时间 | 保存允许的认知、计划和 decision progress |
| `no_op` | 本次不采取有世界语义的行为 | 不推进；不写 Entry/虚构时间 | 同上 |

以下属于执行边界错误，必须丢弃整份工作结果，不返回 `not_applied`：

- WorldRef、Agent 或稳定 EventSession 身份不匹配；
- 伪造或已过期 affordance；
- 不可见、不是本人待处理或已解决的 reply source；
- World 不是 running；
- world version、control epoch、scheduler sequence 或 Persona state revision 已过期；
- strict contract、source、recipient 或关系约束非法。

### 4.2 Disposable PersonAct，而不是第二套 Agent 事务状态机

当前 `PersonActAgent.decide()` 会立即替换实例内的 immutable private snapshot。M3 不把该进程内实例当作持久权威，也不新增复杂的 `begin/publish/rollback` 生命周期；CharacterStep 每次从 committed DB 状态构造一个可丢弃的认知工作实例：

```text
DB PersonaState/Memory revision R
-> disposable PersonActAgent
-> decide() 后只改变 disposable instance
-> observe_outcome() 形成 PersonActStateUpdate
-> SQL transaction 成功：DB 成为 revision R+1 的唯一权威
-> SQL transaction 失败：丢弃整个 disposable instance
-> 下一步始终从 committed DB 重新构造
```

这保留现有 `decide() -> ActionProposal` 公共边界，同时从结构上消除“内存已经前进、World 却回滚”的半状态。M4 Runner 不得缓存并绕过 CharacterStep 维护另一份 live Agent truth。

`PersonActStateUpdate` 只包含当前 Agent 自己预先算好的：

- 下一份 strict `PersonaState`；
- 新增 `MemoryRecord` 与已有 `MemoryTouch`；
- observation cursor；
- 本次 decision ID / outcome；
- 私有 `PlanDisposition = keep | complete_on_applied | cancel`。

Plan disposition 不进入 `ActionProposal`，也不交给 World 解释。只有 `applied + complete_on_applied` 才能从现有 `plan_queue` 移除 active plan；`not_applied` 不能宣称计划完成。M3 不新增 daily、active_action、DecisionMemory 或第二条决策 queue；历史决定继续使用 `MemoryKind.PLAN`，当前执行真值仍只有 `plan_queue + active_plan_id`。首轮 Reflection 继续是 no-op。

### 4.3 固定写事务顺序

模型、attention、novelty、Memory embedding 和 outcome feedback 全部在事务外完成。唯一写事务按下列顺序执行：

```text
1. UPDATE worlds ... WHERE
     world_id/project_id/status=running/control_epoch/
     current_version/scheduler_seq 全部等于读取值
   -> scheduler_seq +1；仅 applied 时 current_version +1
2. 应用受限的 Object 当前状态变化（无任意 JSON patch）
3. 插入 EventEntry / link / recipient；创建或解决 request
4. UPDATE agent_runtime_states ... WHERE state_revision=R
   -> PersonaState、cursor、last_decision_id、state_revision=R+1
5. append 新 Memory，并只更新显式 touch 的 last_accessed_at
6. flush + COMMIT
```

任一步失败，1–5 全部回滚。`WorldUpdater` 不自行开启或提交 transaction，也不导入 PersonaState/Memory；它是唯一公共 World writer。`CharacterStep` 是唯一同时持有 World Store 与 Agent private Store 的跨域协调点。

`worlds` 在 M3 增加 `control_epoch` 与 `scheduler_seq`；`AgentView/ActionProposal` 携带读取时的 epoch，写入计划同时携带 scheduler sequence。M3 Fixture 通过受信测试准备把 World 置为 running；正式的 `resume_world/pause_and_save`、运行锁和额度属于 M4，不能为了单步测试开放 paused World 写入。

<a id="m3-3"></a>

## 5. M3.3：AgentViewBuilder 的硬可见性

`AgentViewBuilder` 只从当前 committed DB version 读取公开状态、recipient rows 和目标 Agent 的 pending response request：

```text
current public rows
+ committed EventEntry JOIN event_entry_recipients(agent_id)
+ unresolved InteractionRequest(recipient_agent_id)
+ WorldAffordanceResolver
-> strict/frozen AgentView
```

它不读取任何角色的私有 Memory/Plan，不做 attention、novelty、embedding 或 Memory 写入，也不接收未提交 Proposal。M3 没有历史 Snapshot，因此只允许构建数据库当前 version；请求旧 version 直接判 stale。

### 首版可见性矩阵

| 情况 | commit recipient snapshot | 目标 AgentView channel / tier |
| --- | --- | --- |
| actor 自己的 committed Entry | actor | `self / relevant` |
| direct utter，无 response request | commit 时同 root 全体 | target `direct_interaction / relevant`；其他成员 `same_scene / relevant` |
| direct utter，request pending | 同上 | 指定 target `direct_interaction / mandatory`；未解决前即使超过 cursor 仍重复提供 |
| whisper utter/respond | actor + target | target `targeted_message / mandatory` 或 `relevant`；同 root 其他人完全不可见 |
| Object ActionEntry | commit 时 actor 所在 root | actor `self`；其他 recipient `same_scene` |
| 同地点、不同 root 的角色 | 不获得另一 root 的 Dialogue recipient | 只可见允许公开的在场状态/对象等 ambient material，不获得跨 root utter/respond affordance |

M3 Character 只能对同 root 角色 utter/respond；跨 root 可见不会自动 merge，也不会自动得到互动权限。M4 再根据可见在场角色提供自主 join/leave affordance。Director 的 explicit audience 可以跨 root 定向通知，但属于 M5，不通过 Character whisper 提前实现。

`PerceptCandidate.source_event_id/event_revision` 在 M3 一次性迁移为 `source_entry_id`：committed Entry 不可变，不再维护没有领域意义的 revision。`MemoryRecord` 增加 nullable `source_entry_id` 作为结构化 provenance；现有 `source` 文本仍可说明生成来源，但不能替代关系引用。

可见性不变量：

- Character 只获得 recipient snapshot 包含自己的 committed Entry；pending、cancelled 和未提交 Proposal 永远不可见；
- actor 默认进入自己的 recipient snapshot；后续加入 Session 的角色不能看到过去 Entry；
- observation cursor 只表示处理过输入，不表示已经回应；pending request 必须独立召回；
- 对可见 Entry 的 reply/cause 如果指向隐藏 Entry，只能省略隐藏引用，不能沿关系边带出正文、字段或 evidence；
- 对同一个 committed snapshot，交换 AgentView 构建顺序不能改变输出。

<a id="m3-4"></a>

## 6. M3.4：CharacterStep 唯一单步协调路径

新增 `event/character_step.py`，只负责一次 Character step：

```text
CharacterStep.run(agent_id, decision_id)
  -> 短读事务：读取同一 snapshot、Agent state revision 和 cursor
  -> AgentViewBuilder.build(...)
  -> 事务外：构造 disposable PersonActAgent 并 decide
  -> WorldChangeValidator.plan(...)
  -> Agent observe_outcome(...)
  -> 一个写事务：WorldUpdater + PersonaStateStore + MemoryStore
  -> 返回 CharacterStepResult
```

CharacterStep 不是 Scheduler：它不决定下一角色、不循环、不 merge/split、不自动推进世界时间。M4 的 EventSessionRunner 只在外层决定角色并反复调用该入口。

最终 Fixture 固定为两步对话和一步对象操作：

1. Anon direct utter 给 Soyo，并明确 `expects_response=true`；
2. Soyo 的新 View 得到来源为 E1 的 mandatory Candidate，respond 显式引用 E1，request 原子 resolved；
3. 另一路 Anon 选择 World-issued metronome operation，Object state 与 ActionEntry 同事务提交；
4. 每条路径关闭 Engine 后重新加载；Entry 顺序、recipient、request、PersonaState、Memory 和 cursor 一致；
5. 在 CAS、Object、Entry、request、PersonaState、Memory 各 checkpoint 注入失败，验证无半提交；
6. 重放相同 decision/source 不重复 Entry，另一 Project及同 Project另一 World均不可见。

## 7. 预计文件树与开发量

以下是实现前预计。`NEW` 表示新增文件，`UPDATE` 表示完善现有文件；行数是新增/改写规模，不是承诺精确 diff。本节保留为设计基线，M3 开发后在“实现与验收记录”中另列实际文件树、`git diff --numstat` 统计及偏差原因，文档不计入实现量。

```text
generative_go_world/
├── agent_runtime/
│   ├── migrations/
│   │   ├── env.py
│   │   │   [UPDATE · M3.1 · +5～10]
│   │   │   — 注册四类 Entry ORM Row，保证 Alembic metadata drift 可见
│   │   └── versions/
│   │       └── 0002_m3_event_entries.py
│   │           [NEW · M3.1 · +350～500]
│   │           — 四张 Entry/Request 表；World control/scheduler 列；
│   │             Persona cursor/decision 列；Memory source_entry_id；约束与 trigger
│   ├── scenario.py
│   │   [UPDATE · M3.1 · +60～100]
│   │   — ObjectOperationSeed 及 operation 引用、唯一性和 canonical hash 校验
│   ├── bootstrap.py
│   │   [UPDATE · M3.1/M3.2 · +25～50]
│   │   — Genesis 默认 control/cursor；可信装配携带 pinned Scenario operation
│   ├── world/
│   │   ├── contracts.py
│   │   │   [UPDATE · M3.1/M3.3 · +110～170 / -20～40]
│   │   │   — CommitPosition、epoch/sequence、稳定 Affordance、reply/request 字段；
│   │   │     source_event_id/event_revision -> source_entry_id
│   │   ├── state.py
│   │   │   [UPDATE · M3.2 · +20～40]
│   │   │   — WorldState 增加 control_epoch/scheduler_seq，不增加 Snapshot 历史
│   │   ├── entries.py
│   │   │   [NEW · M3.1 · +230～340]
│   │   │   — DialogueEntry/ActionEntry 判别联合、Link、Recipient、Request contract
│   │   ├── entry_storage.py
│   │   │   [NEW · M3.1 · +380～540]
│   │   │   — WorldRef-bound ORM/Store；source/position/recipient/request 窄查询与写入
│   │   ├── affordances.py
│   │   │   [NEW · M3.1 · +120～190]
│   │   │   — Builder/Validator 共用的纯 operation 解析与确定性 ID；不是 Service 层
│   │   ├── storage.py
│   │   │   [UPDATE · M3.2 · +110～170]
│   │   │   — status/epoch/version/sequence CAS、精确 Object/Agent/Session 读写
│   │   ├── updater.py
│   │   │   [NEW · M3.1/M3.2 · +400～580]
│   │   │   — WorldChangeValidator、WorldUpdatePlan/Result、受限状态变化与原子写入
│   │   ├── view_builder.py
│   │   │   [NEW · M3.3 · +320～470]
│   │   │   — public rows + recipient/request -> AgentView；不接触 Agent Memory
│   │   └── __init__.py
│   │       [UPDATE · M3.1/M3.3 · +15～30]
│   ├── agent/
│   │   ├── memory/
│   │   │   ├── contracts.py
│   │   │   │   [UPDATE · M3.2 · +5～15]
│   │   │   │   — MemoryRecord 增加 nullable source_entry_id
│   │   │   └── storage.py
│   │   │       [UPDATE · M3.2 · +120～190]
│   │   │       — 单 Agent load、append 新 Memory、显式 touch；不整流重写/删除
│   │   └── personact/
│   │       ├── state.py
│   │       │   [UPDATE · M3.2 · +25～50 / -0～10]
│   │       │   — PersonActStateUpdate 与 PlanDisposition；保留现有普通 plan queue
│   │       ├── storage.py
│   │       │   [UPDATE · M3.2 · +120～180]
│   │       │   — 单 Agent load、state_revision CAS、cursor/last decision 持久化
│   │       ├── proposal.py
│   │       │   [UPDATE · M3.1 · +40～70 / -10～25]
│   │       │   — 由 kind+target 升级为完整 affordance/reply/channel 匹配
│   │       ├── loop.py
│   │       │   [UPDATE · M3.2/M3.3 · +35～70 / -5～15]
│   │       │   — 私有 plan disposition、MemoryTouch 输出、source_entry_id novelty
│   │       ├── agent.py
│   │       │   [UPDATE · M3.2/M3.4 · +50～100 / -5～20]
│   │       │   — disposable work-unit 语义与最小 observe_outcome；不造事务状态机
│   │       └── model_strategy.py
│   │           [UPDATE · M3.1/M3.2 · +10～25]
│   │           — 输出 schema/prompt 适配；ModelGateway 和 repair 机制不改
│   ├── event/
│   │   ├── character_step.py
│   │   │   [NEW · M3.4 · +230～350]
│   │   │   — 单步读/算/原子写协调；M4 Runner 的唯一底层入口
│   │   └── __init__.py
│   │       [UPDATE · M3.4 · +5～15]
│   └── model_smoke.py
│       [UPDATE · M3.1 · +10～25]
│       — 适配 Entry/affordance contract，仍只证明模型产出 Proposal
├── projects/
│   └── for-the-band/scenario.yaml
│       [UPDATE · M3.1 · +8～20 / -1～2]
│       — seed version + metronome 的受信状态转换
├── agent_runtime/tests/
│   ├── test_event_entries.py
│   │   [NEW · M3.1 · +550～800]
│   ├── test_world_updater.py
│   │   [NEW · M3.2 · +650～950]
│   ├── test_agent_view_builder.py
│   │   [NEW · M3.3 · +500～750]
│   ├── test_character_step.py
│   │   [NEW · M3.4 · +550～850]
│   ├── test_project_database.py
│   │   [UPDATE · M3.1 · +140～220]
│   ├── test_agent_storage.py
│   │   [UPDATE · M3.2 · +180～280]
│   ├── test_scenario.py
│   │   [UPDATE · M3.1 · +100～170]
│   ├── test_bootstrap.py
│   │   [UPDATE · M3.1/M3.2 · +60～110]
│   ├── test_personact.py
│   │   [UPDATE · M3.1/M3.2 · +100～180 / 适量替换旧 Fixture]
│   ├── test_personact_agent.py
│   │   [UPDATE · M3.2/M3.3 · +70～130 / 适量替换旧 Fixture]
│   ├── test_model_strategy.py
│   │   [UPDATE · M3.1/M3.2 · +40～80]
│   ├── test_trace_personact.py
│   │   [UPDATE · M3.1/M3.2 · +20～45]
│   ├── test_persona_state.py
│   │   [UPDATE · M3.2/M3.3 · +10～25]
│   └── test_model_smoke.py
│       [UPDATE · M3.1 · +10～25]
└── wiki/design/
    ├── dev_plan_MVP.md
    │   [UPDATE · M3.1–M3.4 · 状态与证据，不计实现行数]
    └── M3_dev_log.md
        [UPDATE · M3.1–M3.4 · 实际 diff/tests/Review，不计实现行数]
```

预计规模：生产 Python（不含 migration）约 **+2,300～3,300**，Alembic migration 约 **+350～500**，测试约 **+3,000～4,600**，Project Fixture 约 **+8～20**；删除/改写主要来自旧 `source_event_id/event_revision` Fixture 与提前发布语义。测试量高于生产量，是因为需要覆盖权限矩阵、两层 World 隔离、SQLite constraint/trigger、CAS 和逐 checkpoint 回滚。

本阶段不新增 Python 依赖，预计不修改 `pyproject.toml` 或 `uv.lock`。实际范围如有变化，必须在本节说明原因，不能把新增文件藏在模糊的模块描述中。

## 8. `origin/mvp` 复用边界

选择性复用以下机制和测试思路：

- Validator 先生成无副作用的 typed Plan，Writer 只提交已验证 Plan；
- caller-owned 单 transaction 与逐 checkpoint 故障注入；
- source provenance、显式 reply、append-only trigger 和稳定排序；
- decision progress 与 World commit 同事务；
- 跨进程恢复、隐私隔离和确定性 Fixture 的测试意图。

明确不复制：

- `WorldSegment / WorldVersionRow / EntityRevision / Snapshot` 等重复历史；
- 一 World 一 DB 假设和 `MAX(event_order)+1`；
- `commit_wave()` 自开 transaction 与 same-snapshot lockstep；
- 任意 `state_patch/payload`、Director arbitrary external event；
- 自动整组合并、successor Session 和旧 runnable queue；
- `PerceptionProjector` 读取所有 Agent Memory 并在 commit 时直接写 Observation；
- 没有实际领域职责的 `EventRecognizer` 空抽象。

旧分支和当前分支没有共同 Git 祖先，因此复用表示按当前 contract 重写机制并保留测试意图，不表示复制 package、schema 或整体 merge。

## 9. 开工前 Review 的六个冻结点

以下是当前推荐默认值。在用户 Review 通过前，它们仍是 M3 设计草案：

1. **一次一个 Entry。**M3 一个 Proposal 最多产生一条 committed Entry，因此 `source_index=0 / entry_index=0`；多 Entry 原子步骤等真实场景证明需要后再开放。
2. **Scenario operation。**Object operation 使用 Scenario 的 `operation_id/from_state/to_state/result_text`；不建 Affordance 表，不允许自由文本修改对象状态。
3. **首版可见性。**direct 对 commit 时 root 可见；whisper 只有 actor+target；Character 不获得跨 root 对话 affordance，跨 root 看见在场角色也不自动 merge。
4. **回应而非审批。**只有 `expects_response=true` 才创建 response request；任何有效 Respond 都置为 resolved。台词中的拒绝仍是一次已发生回应，不新增 `declined` Runtime 状态，也不承担入组审批。
5. **Disposable PersonAct。**每步从 committed DB 构造可丢弃的 PersonActAgent，以 DB 为唯一权威；不实现长期 live Agent 的 begin/commit/rollback 状态机。
6. **M3 不推进故事时间。**Entry 使用当前 `world_time`，允许多个决策共享同一事实时间；wait/no_op 也不推进。调度跳时与 Director 检查留到 M4/M5。

## 10. M3.1–M3.4 任务与状态

| ID | 交付范围 | 独立验收 / Review 重点 | 状态 / 证据 |
| --- | --- | --- | --- |
| M3.1 | strict EventEntry/link/recipient/request；稳定 operation/affordance；Alembic 0002 与 Store | 两层 World 隔离；source/position 唯一；复合 FK；append-only；reply DAG；Scenario operation 不接受任意 patch | TODO · DESIGN REVIEW |
| M3.2 | WorldChangeValidator/WorldUpdater；Agent private update；World/Agent/decision 同事务 | SQL CAS；applied/not_applied/wait/no_op；state revision；事务内不调用模型；逐 checkpoint 全回滚 | TODO · DESIGN REVIEW |
| M3.3 | AgentViewBuilder 与历史 recipient/request 可见性 | actor/target/旁听/另一 root/whisper 参数化矩阵；隐藏 link 不泄漏；pending request 不因 cursor 丢失；调用顺序无关 | TODO · DESIGN REVIEW |
| M3.4 | CharacterStep 与 utter→respond、Object operation 可重载 Fixture | 关闭连接重载一致；相同 decision/source 不重复；错 World、paused、stale 全丢弃；M4 可直接复用单步路径 | TODO · DESIGN REVIEW |

## 11. 阶段门禁

M3 只有在以下证据全部取得后才能由 DESIGN REVIEW/TODO 改为 DONE：

- M3 专项测试覆盖 Entry、Updater、AgentViewBuilder 和 CharacterStep；
- 完整 Python suite 通过；
- `uv run ruff format --check agent_runtime` 通过；
- `uv run ruff check agent_runtime` 通过；
- `uv run pyright` 为零错误、零警告；
- Alembic upgrade/downgrade、ORM metadata 对齐和迁移故障回滚通过；
- wheel build 包含 M3 migration 资源；
- 现有 Node 测试通过，证明制作层没有被 Runtime 改动破坏；
- 可重放 Fixture 覆盖 applied、not_applied、wait、no_op、stale/非法输入、重复 source、错 Project/World 和逐 checkpoint 回滚；
- 关闭 Engine 后重载，Entry 顺序、recipients、request、Object、PersonaState、Memory、cursor 和 revision 一致。

真实 Provider 的自然对话质量、持续多角色运行、Session 重组、Director 和 Broadcast 均不是 M3 门禁，不能将未执行的实网测试登记为成功。

## 12. 实现与验收记录

**尚未开始。**

Review 通过并进入实现后，本节原位记录：

- M3.1–M3.4 的实际完成状态与 commit/PR；
- 实际文件树、真实新增/删除行数及与预计范围的差异；
- 专项与全量测试命令、通过数量和耗时；
- Alembic、wheel、Node 与 `git diff --check` 结果；
- 独立 Review 发现、修复和仍未解决的问题；
- M4 接手 CharacterStep、调度字段与 EventSession 运行闭环时必须遵守的边界。
