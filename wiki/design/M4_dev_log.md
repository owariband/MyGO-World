# M4 开发记录：自由互动与可恢复运行

> 状态：**ENGINEERING READY / REAL ARK RUN REVIEW**；M4.0–M4.5 已落地，真实 Ark 十人运行与审片产物已取得；但 `0 split` 与真实 SIGINT/SIGKILL 子进程证据仍未关闭，因此 M4 不标 DONE。
> 日期：2026-09-11；状态更新：2026-09-12
> 开发基线：`master@ecc29c0`（M3 已提交并推送）。
> 总计划：[dev_plan_MVP.md](dev_plan_MVP.md#m4-自由互动与可恢复运行)
> 领域设计：[MVP_dev.md](MVP_dev.md)
> 真实群演内容设计：[M4_hogwarts_demo.md](M4_hogwarts_demo.md)

本文是 M4 唯一阶段日志；设计基线、实际 diff、测试结果、Review 修复与阻塞均维护在本页，不另建 `M4_dev_plan.md`。第 10–14 节保留开工前合同，第 15 节记录实际实现，不把预计数量当成完成证据。

## 1. 阶段结果与产品边界

M4 是第一个真正可运行的产品 Demo。阶段完成时必须能演示：

```text
Project Scenario + paused World save
                │
                ▼
resume_world(additional_decisions=N)
                │
                ▼
同一 World 串行 EventSessionRunner
├── 多个角色连续作出决策（Fixture 证明机制，十人真实模型运行是交付硬门禁）
├── 同组对话 / 回应
├── 自主加入、离开、转组
├── 多条隔离 StoryLine 与交汇 DAG
└── 每一步完整结果原子写入 Project SQLite
                │
       Ctrl+C / 显式 pause
                ▼
pause_and_save → 关闭进程 → load_world → resume_world
                │
                ▼
恢复同一 pending request、计划、互动分组和调度位置；未提交模型工作重新派发
```

这里的“自由”有明确边界：角色可以从 World 发出的 affordance 中自由选择说话、回应、行为、等待、加入或离开；模型不能直接填写 root、成员集合、World patch 或 SQL。Runtime 只裁决客观可达性和提交不变量，不审批角色是否“受欢迎”，也不强迫角色回复。

M4 的完成定义不是“存在一个 `while` 循环”，而是以下四件事同时成立：

1. 连续运行：调度公平、有额度、失败不返还额度，wait/no-op 不会无限空转。
2. 自由组队：始终一名角色一个稳定 Session 节点，merge/split/transfer 只更新分区；五人 Fixture 恒为五节点，十人验收恒为十节点。
3. 可读历史：隔离互动不串线，交汇和分裂可从 committed Entry 重建为 StoryLine DAG。
4. 可恢复：暂停或崩溃后只损失尚未提交的模型计算，SQLite 中不存在半个角色结果。
5. 可审片：交付一条由真实 Provider 运行得到、确实包含 add 与 split 的 StoryLine JSON，以及由该 JSON 确定性生成的离线 HTML。

## 2. 本阶段明确不做什么

以下能力不属于 M4，不能为了“Demo 看起来完整”偷偷塞进本阶段：

- M5 Director、客观 pending Environment Entry、咖啡完成通知和 Director 检查节奏；
- M6 Broadcast 选材、PresentationBinding、正式发布故事 JSON 和 WebGAL 转译；M4 只导出未经导播重排的 raw StageView JSON 和开发用 HTML；
- M7 咖啡场景联合验收、30 分钟预生成库存和产品级剧情质量评分；
- 自动结局、EndingRule、任意旧版本恢复、分叉存档、云端多机 Runner；
- root 间并行模型调用、租约/心跳、通用任务队列和 LangGraph checkpoint；
- Worker Agent、素材配装和 WebGALCompiler；这些仍由进程外 Codex 临时代替；
- 新建 CognitiveController、长期 Reflection、复杂地点移动或物理模拟。

确定性 Fixture 继续负责证明权限、事务、故障和可重放机制，CI 也保持离线；但这已经不足以把 M4 标成 DONE。M4 还必须以现有 DeepSeek OpenAI-compatible Gateway 完成一次十人真实群演，接受的 World 中必须由模型 Proposal 实际选择并成功提交至少一次 add/merge/transfer 和一次 split/leave。若某次真实运行没有产生这些行为，应保留尝试记录、调整内容动机并创建新 World 重跑，不能用 Fixture、手写 Entry 或直接改 Session 表补结果。

## 3. 当前 M3 基线与必须复用的接缝

M3 已有唯一单步链：

```text
AgentViewBuilder
→ disposable PersonActAgent.decide()
→ WorldChangeValidator / WorldUpdater
→ 一个 transaction 提交 World + Entry + Request + PersonaState + Memory
```

M4 必须扩展 [character_step.py](../../agent_runtime/event/character_step.py)，不能在它外面实现第二套提交协议。当前 `CharacterStep.run()` 的模型调用已经在事务外，公共与私有结果已经在一个事务内；M4 需要补上的只是可信调度凭证、Session 变化和调度完成状态。

两个事务边界必须区分清楚：

```text
短 dispatch transaction（模型前）
├── 检查 running / owner / epoch
├── 确定一个可运行角色
├── 预扣一次模型派发额度
└── 发出不可变 CharacterDispatch

模型调用（绝不持有 SQLite transaction）

CharacterStep outcome transaction（模型后）
├── 再校验 dispatch / epoch / World fence
├── 提交 World / Entry / Request / Session
├── 提交 PersonaState / Memory
└── 提交该角色的完成、wakeup 和连续行为计数
```

模型传输或解析失败时，预扣额度不返还，也不会伪造 `EventEntry`；下一次派发可以选择其他角色。模型结果若晚于 pause 或新的派发 fence 返回，必须整体拒绝。

## 4. M4.1：EventSessionRunner 与有界调度

### 4.1 调度输入与输出

Runner 每次只选择一个角色，首版同一 World 最多一个在途模型调用。`CharacterDispatch` 是 Runtime 内部 strict/frozen 值，至少携带：

```text
world_ref
run_owner_id
control_epoch
dispatch_count
agent_id
expected_world_version
expected_decision_seq
decision_id
```

`decision_seq` 继续表示已经完整提交的 Character 决策顺序；新增 `dispatch_count` 表示已经在模型调用前消耗的尝试数。失败调用只增加后者。`dispatch_limit_at` 是绝对上限：已经派发 20 次后追加 10 次额度，新的上限为 30，重启不会清零或退款。

M4 的 `decision_id` 必须增加 `dispatch_count`，至少由 `(WorldRef, control_epoch, dispatch_count, agent_id)` 确定性生成；同一 World fence 上的两次失败重试也不能共用 source ID。`worlds.active_dispatch_count` 是 nullable 的当前派发 token，不是累计计数：dispatch 时设为本次 count，outcome 只接受完全匹配的 token并在提交时清空；模型失败由 Runner 做一次短事务清空，崩溃恢复则在新 epoch 中作废旧 token，均不回退 `dispatch_count`。

```text
begin_dispatch:
  CAS active_dispatch IS NULL + owner/epoch/status/quota
  dispatch_count += 1
  active_dispatch = (dispatch_count, agent_id)

finish_success:
  CAS active_dispatch/owner/epoch/world version/decision_seq 全匹配
  原子提交完整 outcome；active_dispatch = NULL

finish_failure:
  CAS active_dispatch/owner/epoch 仍匹配
  active_dispatch = NULL；decision_seq 不增加；额度不返还

resume_after_crash:
  取得 OS 锁；递增 epoch；清除旧 active_dispatch；额度不返还
```

若 `dispatch_count >= dispatch_limit_at`，Runner 不再创建 token，而是原子切回 `paused(stop_reason=budget_exhausted)`；该状态不是故事 ended。只有显式 resume 追加正数额度后才继续。

### 4.2 确定性选择顺序

同一 committed snapshot 下，候选角色按以下稳定规则选择：

1. 每个 recipient 只拿自己最早、尚未获得优先机会的 pending response 作为候选；先比较 recipient 的 `last_dispatch_count`，再比较 request 的 `CommitPosition` 和稳定 ID。
2. dispatch 携带唯一的 `priority_request_entry_id`；只有 CharacterStep 成功提交任意 outcome 后，才在同一 outcome transaction 把该 request 标记为已提供优先机会。同一角色的其他 pending request 不会被一次性吞掉，模型失败或 pause 丢弃的派发也不会假装角色已经获得机会。
3. 没有新 pending priority 时，root 的调度 frontier 定义为其所有成员 `last_dispatch_count` 的最大值；frontier 最小的 root 先运行，平局按 root ID。
4. 选定 root 后，从其中可运行成员选择 `last_dispatch_count` 最小者，平局按 agent ID。
5. pending 只提供一次调度优先权，不等于强制回应。角色可以发言、行动、等待或不回应；原 request 仍可见，直到显式回应或因分组不可达而取消。

例：root A 有 `anon/soyo`，root B 有 `tomori`，三人初始进度都是 0，且没有 pending；稳定 ID 平局先 A 时，前六次精确顺序应为 `anon → tomori → soyo → tomori → anon → tomori`。这体现“root 间轮转，root 内成员轮转”，并为 Golden scheduler test 提供唯一预期。

dispatch transaction 同时写入被选节点的 `last_dispatch_count`，因此模型失败也不会让一个坏角色永久霸占队首。选择算法只读取关系型当前状态和 committed request，不依赖 Python 进程里的临时 round-robin 游标。

每次 dispatch、模型失败、outcome、Session transition 和 pause 都写入现有 `LocalTrace`，至少关联 `world_ref / agent_id / dispatch_count / decision_id / control_epoch`；私有 Prompt/输出仍只在显式 debug 时记录。Trace 用于诊断，不参与恢复，也不能替代 SQLite 状态。

### 4.3 wait、no-op 与空转

M4 不解析模型给出的自然语言时间。`WaitAction.next_wakeup` / `NoOpAction.next_wakeup` 只保留角色意图说明；Runtime 的确定性结果是把该稳定节点标记为“等待下一条**该角色可见**的 committed Entry”。只有 `event_entry_recipients` 包含该角色、且 Entry 位置晚于等待阈值时，它才重新可运行；另一个隔离 EventSession 的不可见台词绝不能唤醒它。

wait、no-op 和合法 `NOT_APPLIED` outcome 都把节点的 `wait_for_visible_entry_after_version` 写成当前 version。nullable 表示立即可运行；非空则只有查询到版本更大且 recipient 命中的 Entry 才重新入选，并在下一次 dispatch 时清空。若没有任何立即可运行或被新可见 Entry 唤醒的节点，Runner 自动 `pause_and_save(stop_reason=idle)`，不烧光额度制造空日志。扫描到一半重启时，已尝试节点保留等待阈值，未尝试节点仍为 runnable，因此不会跳过或从头空转。

这是 M4 对早期 `next_wakeup_world_time` 草案的明确收敛：Character wait 首版按可见 Entry 唤醒，保护 Session 隔离；M5 的客观 process `next_check_at` 仍使用可冻结的世界时间。只有真实 Scenario 证明 Character 必须按世界时钟自行醒来时，才为 Character 增加 typed world-time wakeup 和最早时间跳转规则。M4 不偷偷跳时间或伪造“咖啡好了”。

### 4.4 50 次连续对话后必须发生行为

规则按**角色稳定 Session 节点**计数，不按 root、墙钟时间或旧 daily cooldown 计数：

- 角色提交一个 `DialogueEntry`：`consecutive_dialogue_turns += 1`；
- 提交自身 `BehaviorEntry`、对象 `ActionEntry` 或 `SessionTransitionEntry`：计数清零；
- wait、no-op、合法但未生效和模型失败：既不增加也不清零；
- merge/split 后计数随角色稳定节点保留。

达到 50 后，`AgentViewBuilder` 暂时不再给该角色发出 utter/respond affordance，直到它提交一次行为。为了避免没有对象可操作时死锁，每个角色始终拥有少量 World-issued、有限枚举的 self-behavior affordance；`ActAction` 必须选择其中一个，成功后落一条 committed `BehaviorEntry`，不再像 M3 那样固定 `NOT_APPLIED`。角色仍可 wait/no-op，Runner 的 idle/额度门禁负责最终收口。

这些首版 operation 只表示角色自身、非物质状态的表演动作，例如 `collect_thoughts`、`observe_surroundings`；每个 operation 的 committed result text 由受信 Runtime 模板生成，模型 `ActAction.description` 只作为私有意图/诊断输入，绝不直接成为权威 Entry 正文。它们不能更新 Object、Location、Session 或其他角色；任何会改变这些事实的动作仍必须选择对应的 typed affordance。`BehaviorEntry` 属于有界的可读表演历史，不是绕过 Scenario operation 的万能 World patch。

## 5. M4.2：自主加入、离开与转组

### 5.1 保持六类 Action，不让模型操作分区结构

M4 优先保留现有 `act / interact / utter / respond / wait / no_op` 六类 Action：

- `interact(character)` 选择 World 发出的 `operation_id=join_target_session` affordance，表示角色加入目标当前 root；
- `act` 增加必填 `affordance_id`，可选择 `operation_id=self_behavior.*` 或 `leave_current_session`；
- 模型只选择 affordance 和目标，不能提交 root ID、成员集合、keep-root 或 topology version；
- 不新增 `join_session / leave_session` ProposalKind，也不修改 Creator Manifest 的 allowed action kind 数量。

`Affordance.operation_id` 在 M4 允许用于 Character interact 和 act；它来自受信 Resolver，并以有限 enum/catalog 解释，不能靠解析 opaque affordance ID 或模型 description 猜语义。若真实模型 Golden/Badcase 证明同一个 `interact` 语义无法稳定生成，才在独立 Review 后扩展 Action union；不能在实现途中无证据地添加 JoinAction/LeaveAction。

`AgentView.event_session_id / ActionProposal.event_session_id` 保持当前源码语义：它们永远是行动者自己的**稳定 Session 节点 ID**，不是会随分组变化的 root。当前 root/topology 只存在于受信 World snapshot/affordance 内，stale transition 由 World version、dispatch 和 Session CAS 共同拒绝。

### 5.2 业务规则

- 加入不需要成员投票或批准；别人的不欢迎只能由其后续言行体现。
- singleton 加入目标组：保留目标 root。
- 已在另一组的角色加入目标组：只把行动者本人 transfer，原组其他成员不被拖走。
- 离开：行动者恢复以自己的稳定 `session_id` 为 root。
- 若离开者原本就是多人组 root，剩余组以稳定 Session ID 字典序最小者重新选 root。
- 任何 merge/split/transfer 后仍是一人一个稳定节点；`event_sessions` 行数永远恰好等于该 Project 的 Agent 数。
- 只有同地点且 AgentView 明确给出 affordance 时才能加入；自由文本“我过去了”不能改 root。

`event/session.py` 组合纯 [UnionPart](../../agent_runtime/common/union_part.py) 计算新分区，随后由 World Store 对所有受影响 Session 行做 CAS。`UnionPart` 本身不继承业务类型、不接触 SQL，并在 M4 保持 **NO CHANGE**。

每次 transition planning 都从本次 committed `event_sessions` 行重建一个临时 UnionPart，再生成不可变 plan；不能长期持有并原地修改 `LoadedWorld.session_partition`。这样 SQL rollback、stale CAS 或进程崩溃后不会留下与数据库分叉的内存分区。

### 5.3 transition Entry 与 pending request

一次分区变化与以下内容在 CharacterStep outcome transaction 内同时提交：

```text
受影响 EventSession root/topology 更新
+ 一条 SessionTransitionEntry
+ transition 前后 part/member 快照
+ 所有 affected StoryLine frontier links
+ 已变得不可达的 pending response cancellation
+ 行动者 PersonaState / Memory / scheduler completion
```

`SessionTransitionEntry` 是现有 `EventEntry` union 的一种，不是第二份事件日志；它正式取代早期草案中分开的 `JoinEntry / LeaveEntry` 名字。它记录 `merge / split / transfer`、行动者和选中的目标；完整多组变化使用关系型 `event_session_transition_parts` 行保存：

```text
(transition_entry_id, side=before|after,
 part_order, root_session_id, topology_version,
 member_session_id, member_order)
```

之所以需要这张窄表，是因为一次 transfer 可能同时改变两个旧 part 和两个新 part；单个 `from_root/to_root` 列无法表达完整 DAG，而把成员数组塞进 JSON 又无法建立外键和唯一性约束。

表级约束至少包括 World/transition Entry/root/member 的复合外键、`side in (before, after)`、非负且 canonical 的 part/member order、同一 side 每个 member 只出现一次、同一 part 的 root 必须在成员内。Store 写前和读取后还要整体验证：每个 side 都是无重叠非空分区，before/after 完整覆盖同一组受影响稳定节点，part 内 root/topology 一致，父 Entry 确为 `session_transition`；明细行 append-only。

分组变化后，requester 与 recipient 不再同 root 的 pending response 在同一事务改为 `cancelled`，并引用该 transition Entry。旧 DialogueEntry 仍是不可变历史；以后重新组队也不会复活旧 request。若仍可达，request 原样保留。

transition 的 recipient snapshot 精确定义为所有 before parts 稳定 Session 成员映射到 Agent 后的去重集合；对合法 merge/split/transfer，它必须与所有 after parts 的 Agent 集合相同。也就是说所有成员变化的直接当事人都能看见这次变化，同地点但未受影响的旁观者不会自动收到，后来加入者也不能回看。Store 必须同时拒绝漏收、多收和 before/after 人员总集不一致。

## 6. M4.3：StoryLine 与 StageView

M4 的 StoryLine 是确定性读取模型，不调用 LLM，不复制正文，也不持久化摘要：

```text
StoryLineKey = (root_session_id_at_commit, topology_version)

StoryLine
├── key
├── member_agent_ids
├── committed entries（按 CommitPosition）
├── parent_line_keys
└── child_line_keys

StageView
├── world_ref
├── built_through
└── 多条 StoryLine（稳定拓扑顺序）
```

只用 root 查询是错误的，因为同一稳定 root 后续可能再次组成另一批成员。普通 Entry 在同一 `StoryLineKey` 内沿 `previous` 链追加；transition Entry 连接所有 before frontier，`event_session_transition_parts` 产生 before/after Line 边。没有后续台词的刚分裂支线也必须出现在 StageView，不能等下一条 Entry 才“存在”。

transition Entry 只出现一次，并规范归属于**行动者 after part 的 StoryLineKey**，作为该 Line 的首个 Entry；它的 `previous` links 指向所有实际存在 Entry 的 before Line frontier，空的 before Line 则只由 transition parts 建边。其他 after Line 即使暂时为空，也由 transition parts 立即成为 child，其第一条后续普通 Entry 再以 transition Entry 为 previous。这样 split/transfer 不复制文本，又有唯一的展示位置。

一个 before Line 到一个 after Line 存在 lineage edge，当且仅当两边的稳定 Session member 集合有交集；part order 只负责确定性输出，不改变语义。这一规则让 merge 得到多父一子、split 得到一父多子、transfer 得到最多二父二子，且无需额外持久化同义 line-edge 表。

历史 `member_agent_ids` 优先从匹配该 key 的 transition before/after member 行恢复；从未发生 transition 且仍为当前 line 的 genesis/unaffected part 才从当前 `event_sessions` 读取。绝不从 Entry recipients（尤其 whisper recipients）反推完整成员。

`built_through` 的类型是 `CommitPosition | None`，表示一次一致 SQLite read transaction 中实际包含的最大 committed Entry；wait/no-op 只推进 decision sequence，不改变该位置。Builder 可接受 `as_of: CommitPosition | None`，所有 Entry、links 和 transition parts 必须在同一读取快照内截断，不能先取 max 再跨事务读取未来行。M6 才定义 publication/story watermark，M4 不占用那个名字。

必须保持：

- 只读取 `status=committed`；未提交 Proposal、Persona Memory 和 Trace 不进入故事线；
- 每条线按 `(world_version, entry_index)` 稳定排序，跨线不伪造全局戏剧时间；
- recipient snapshot 保持历史可见性，后来加入者不能获得旧私语；
- merge/split/transfer 不复制旧 Entry；同一 Entry ID 在一个 StageView 中只出现一次；
- Builder 校验 previous/reply/transition DAG 无环，并拒绝断裂或跨 World 引用。

M4 的 StageView 是 M5 Director 和 M6 Broadcast 的可靠输入底座，但本阶段不会让它们开始决策，也不输出最终 Broadcast JSON。

## 7. M4.4：暂停、读档、续跑与运行锁

### 7.1 对外生命周期

```text
create_world(...)                         -> paused
load_world(...)                           -> 只读加载，保持 paused
resume_world(..., additional_decisions=N) -> running + 新 owner/epoch
pause_and_save(..., reason=user_pause)    -> paused + 保存点
```

- 首次运行必须给正数额度；续跑追加到绝对 `dispatch_limit_at`，不覆盖已使用量。
- `load_world` 永远不调用模型、不自动 resume、不重做 Scenario bootstrap；若 DB 仍为 running，它返回可恢复的 `WorldStatusError`，也不擅自获取锁或改为 paused。只有 `resume_world` 能在取得空闲 OS 锁后恢复 stale-running。
- `pause_and_save` 不要求角色告别或剧情自然结束；它只保证恢复到一个完整提交边界。
- paused 不等于 ended。M4 不产生自然结局，`ended` 保留给后续 EndingRule。
- world_time 在离线期间冻结；M4 不使用墙钟时间推进故事。

### 7.2 进程排他与异常恢复

首版本地 SQLite 使用每 World 一个 POSIX 文件锁，锁文件名由 `world_id` 的 hash 派生并固定在该 Project 的 `.runtime` 目录下；同时在 `worlds.run_owner_id` 保存诊断身份。数据库 owner 字段不能单独证明旧进程已经死亡，因此不能替代 OS 锁。

resume 顺序是：先非阻塞取得锁，再重读数据库，再在一个控制事务中处理遗留 `running`、递增 `control_epoch`、登记 owner、追加额度并切换为 running。仍持锁的 live Runner 使第二个 resume 明确失败；锁已释放但 DB 残留 running 时，允许把旧 epoch 作废后恢复。

Ctrl+C 或 owner 内部的 `pause_and_save` 与在途 CharacterStep 通过 `status + control_epoch + dispatch_count` 串行裁决：

- outcome 先提交：保存点包含完整该步，然后 pause；
- pause 先提交：递增 epoch，迟到 outcome 整体拒绝；
- 任一结果都不能出现“Entry 已写、Memory 未写”或相反的半状态。

暂停后停止新 dispatch，并对在途模型做 best-effort cancel；取消是否成功不影响数据库安全。强杀只承诺恢复最后一个已提交边界，已经预扣但未提交的 dispatch 额度不会返还。

外部 `pause` CLI 是唯一允许的非 owner 控制写：它不取得 owner lock，而是在短事务中 CAS `running → paused`、递增 epoch、清空 active dispatch 并记录停止原因，使在途结果立即失去发布权限；随后等待 owner 锁释放再报告 Runner 是否已完全退出。已经 paused 时幂等返回。Ctrl+C 发生在 owner 进程内，也走同一个 fencing 操作。MVP 不强杀仍卡在 Provider 内的进程，但即使等待超时，SQLite 保存点已经安全，后续 resume 仍须等 OS 锁真正释放。

### 7.3 最小操作入口

新增 `agent_runtime/world_cli.py`，只做薄装配，不复制 Runtime 逻辑。至少提供：

```text
create PROJECT WORLD
run PROJECT WORLD --additional-decisions N [--fixture | --model MODEL_ID]
pause PROJECT WORLD
status PROJECT WORLD
story PROJECT WORLD
```

Fixture 模式用于可复现演示；model 模式复用现有 `create_deepseek_gateway()` 和本地 Trace。生产侧 Fixture/Catalog/strategy/empty-embedding 的具体装配放在 `world_cli.py` 私有 helper，不允许 import `agent_runtime/tests/runtime_fixtures.py`；Runner 本身只接收窄的 strategy factory。CLI 不读取或打印 API key，不将私有 Memory 混入 `story` 输出。

各命令 stdout 只输出一个 strict JSON 结果，stderr 只给脱敏诊断；exit code `0` 表示完成或 pause request 已接受，`2` 表示参数/配置错误，`3` 表示 live owner/状态冲突，`1` 保留给其他 Runtime 失败。`status/story` 是公共只读命令，不加载 PersonaState/Memory，也不创建 Provider。

## 8. 0003 Schema 预计变化

只扩展有真实消费者的现有表，并新增一张不可替代的 transition 明细表：

| 表 | 预计字段 / 约束 | 作用 |
| --- | --- | --- |
| `worlds` | `dispatch_count`、`dispatch_limit_at`、nullable `active_dispatch_count`、nullable `active_dispatch_agent_id`、`run_owner_id`、`stop_reason`、`stopped_at_world_version`；保留 `decision_seq` | 区分预扣模型尝试与完整提交位置；把 active token 绑定唯一 Agent；保存运行所有权和暂停点 |
| `event_sessions` | `last_dispatch_count`、`wait_for_visible_entry_after_version`、`consecutive_dialogue_turns` | root/成员公平轮转、跨重启可见 Entry 唤醒、50 次规则，全部绑定稳定角色节点 |
| `event_entries` | union 扩展 `behavior / session_transition` 及对应 nullable typed columns/checks | 记录真正发生的自身行为和分组变化，不建第二份历史 |
| `event_session_transition_parts` | transition 的 before/after part、root、topology 和 member 行 | 完整保存 merge/split/transfer 的 StoryLine lineage |
| `interaction_requests` | `cancelled`、`cancellation_entry_id`、一次性 priority marker | split 后关闭不可达请求；未回应不能永久霸占调度 |

不新增 `scheduler` 表、decision history 表、`event_session_members` 当前态表、World Snapshot、StoryLine 摘要表或宽 JSON 状态列。`decision_seq` 就是完整 Character 决策序号，不再增加同义 `scheduler_seq/character_turn_seq/last_completed_decision_seq`；M5 若需要区分 Director cursor，单独在 M5 增加 Director 自己的检查位置。

`0003` 会重建带 CHECK 约束的 SQLite 表，migration 与其测试在总量中只计一次；它在 M4.1 开始、M4.2 完成前随同一集成分支补齐，不能在 schema 尚无消费者时单独宣称 M4.2 已完成。必须验证 `0002 → 0003 → 0002 → 0003`、Foreign Key check 和中途故障整体回滚。

“往返保持”只承诺 0002-compatible 的旧数据。若数据库已经包含 Behavior/SessionTransition、cancelled request、非零 dispatch 或其他 M4-only 状态，downgrade 必须安全拒绝并保持原库不变，不能静默删掉新历史后宣称成功。

## 9. 预计修改文件树

以下是开工前按当前 `ecc29c0` 源码核准的落点。实际实现若越出此树，必须先在本日志解释原因和规模差异。

```text
generative_go_world/
├── agent_runtime/
│   ├── migrations/
│   │   ├── env.py
│   │   │   [UPDATE · M4.2] — 显式注册 EventSessionTransitionPartRow，保持 metadata 审计完整
│   │   └── versions/
│   │       └── 0003_m4_runtime_sessions.py
│   │           [NEW · M4.1/M4.2/M4.4]
│   │           — 运行额度/锁、Session 调度列、Entry union、request cancellation、transition parts
│   ├── common/
│   │   └── union_part.py
│   │       [NO CHANGE]
│   │       — 继续保持无业务语义的纯 merge/split 数据结构
│   ├── event/
│   │   ├── __init__.py
│   │   │   [UPDATE · M4.1–M4.4] — 只导出稳定 Runtime/StoryLine 公共入口
│   │   ├── character_step.py
│   │   │   [UPDATE · M4.1/M4.2]
│   │   │   — 定义/接收内部 CharacterDispatch；同一 outcome transaction 提交调度完成与 Session 变化
│   │   ├── runner.py
│   │   │   [NEW · M4.1/M4.4]
│   │   │   — 派发选择、预扣额度、连续循环、idle 收口、运行锁和 pause/resume 控制
│   │   ├── session.py
│   │   │   [NEW · M4.2]
│   │   │   — join/leave/transfer 规则、UnionPart 组合、确定性 reroot 与 transition plan
│   │   └── story_line.py
│   │       [NEW · M4.3]
│   │       — StoryLineKey、StoryLine、StageView 和 committed DAG Builder
│   ├── world/
│   │   ├── contracts.py
│   │   │   [UPDATE · M4.1/M4.2] — Act 选择 affordance；ACT/character-interact 的受信 operation 约束
│   │   ├── state.py
│   │   │   [UPDATE · M4.1/M4.4] — World 运行额度/owner/stop 与稳定 Session 调度状态
│   │   ├── storage.py
│   │   │   [UPDATE · M4.1/M4.2/M4.4] — dispatch/control CAS、Session 批量 CAS、生命周期读写
│   │   ├── entries.py
│   │   │   [UPDATE · M4.1/M4.2] — Behavior/SessionTransition Entry、cancelled request、transition part
│   │   ├── entry_storage.py
│   │   │   [UPDATE · M4.2/M4.3] — transition/lineage 持久化、latest_for_line、StageView 查询
│   │   ├── affordances.py
│   │   │   [UPDATE · M4.1/M4.2] — self behavior、join target、leave current 的稳定 affordance
│   │   ├── updater.py
│   │   │   [UPDATE · M4.1/M4.2] — act/character interact 生效并形成原子 WorldUpdatePlan
│   │   └── view_builder.py
│   │       [UPDATE · M4.1/M4.2] — 50 次限制、跨 root 可达 join、leave 与 cancellation 后视图
│   ├── agent/
│   │   └── personact/
│   │       ├── proposal.py
│   │       │   [UPDATE · M4.2] — ActAction affordance 选择与语义权限校验
│   │       └── model_strategy.py
│   │           [UPDATE · M4.1/M4.2] — 最小提示修正；解释受信 affordance 与等待边界
│   ├── bootstrap.py
│   │   [UPDATE · M4.4] — 保持 create/load 分权；装配 resume/pause 所需可信对象
│   ├── world_cli.py
│   │   [NEW · M4.4/M4.5] — create/run/pause/status/story；装配生产 Strategy/Trace，并把 StageView 确定性导出为 JSON
│   └── tests/
│       ├── runtime_fixtures.py
│       │   [NEW · M4 shared test support] — 三角色确定性策略、临时 Project/World 与重开 DB helper
│       ├── test_event_runner.py
│       │   [NEW · M4.1] — 调度、额度、pending priority、公平性、idle 与失败恢复
│       ├── test_event_session.py
│       │   [NEW · M4.2] — join/leave/transfer、reroot、request cancellation、原子失败
│       ├── test_story_line.py
│       │   [NEW · M4.3] — 隔离 Line、交汇 DAG、排序、recipient 保持与坏图拒绝
│       ├── test_world_lifecycle.py
│       │   [NEW · M4.4] — pause/load/resume、额度追加、epoch、并发锁、跨进程恢复
│       ├── test_runtime_process.py
│       │   [NEW · M4.4] — 真实子进程 Ctrl+C、dispose/reopen 和 stale owner 恢复
│       ├── test_character_step.py
│       │   [UPDATE · M4.1/M4.2] — dispatch fence 与 scheduler/session 同事务 checkpoint
│       ├── test_world_updater.py
│       │   [UPDATE · M4.1/M4.2] — Behavior/transition trusted plan 与伪造拒绝
│       ├── test_event_entries.py
│       │   [UPDATE · M4.2/M4.3] — 新 Entry/transition part/request lifecycle 约束
│       ├── test_agent_view_builder.py
│       │   [UPDATE · M4.1/M4.2] — 50 次限制、join/leave affordance 和跨组历史隔离
│       ├── test_project_database.py
│       │   [UPDATE · M4 migration] — 0003 round-trip、旧数据保持、注错回滚、wheel 资源
│       ├── test_bootstrap.py
│       │   [UPDATE · M4.4] — paused load、恢复后配置/分区/调度一致
│       ├── test_model_strategy.py
│       │   [UPDATE · M4.1/M4.2] — 结构化 Act affordance 与 wait/no-op 提示回归
│       └── test_personact.py
│           [UPDATE · M4.2] — 六类 Action 保持、Act affordance Schema/权限
├── content/
│   └── skills/
│       └── characters/
│           ├── uika-1.0.0.md
│           ├── sakiko-1.0.0.md
│           ├── mutsumi-1.0.0.md
│           ├── umiri-1.0.0.md
│           └── nyamu-1.0.0.md
│               [NEW · M4.0] — 补齐 Ave Mujica 五份稳定 Character Skill；学院与本次课程不写入 Skill
├── projects/
│   └── mygo-hogwarts/
│       ├── project.json
│       │   [NEW · M4.0] — Project 身份；复用现有制作层的最小 manifest
│       ├── agents.json
│       │   [NEW · M4.0] — 十人 Persona、关系、目标、Skill pin 与相同六类 Action 权限
│       └── scenario.yaml
│           [NEW · M4.0] — Hogwarts AU、Room of Requirement、四年级课程任务、对象、私有知识和初始分区
├── tools/
│   └── storyline-to-html.mjs
│       [NEW · M4.5] — 读取 raw StoryLine JSON，验证后生成无外部依赖的单文件 HTML
├── tests/
│   └── storyline-to-html.test.mjs
│       [NEW · M4.5] — JSON 拒绝路径、DAG/过滤、HTML escape、确定性字节与 CLI 测试
├── artifacts/
│   └── m4-hogwarts/
│       └── <accepted-world-id>/
│           ├── run_manifest.json
│           ├── storyline.json
│           ├── storyline.html
│           └── review.md
│               [GENERATED + COMMITTED · M4.6] — 一次真实十人群演的脱敏、可审阅证据
└── README.md
    [UPDATE · M4.4–M4.6] — Demo/导出/Viewer 命令、存档位置、真实群演复现条件与能力边界
```

预计 **NO CHANGE**：`agent/personact/manifest.py`、`projects/for-the-band/agents.json`（仍是六类 action，避免改变已有 World 的 spec digest）；既有 MyGO 五份 Skill（Hogwarts overlay 属于新 Project，不污染稳定人格）；`agent/memory/` 和 `agent/personact/storage.py`（调度状态不塞进 Persona JSON）；`pyproject.toml`（使用 `python -m agent_runtime.world_cli`，POSIX 锁不引入依赖）；Director/Broadcast package。Viewer 使用 Node 标准库与内联 CSS/JS，不增加 npm 依赖或修改 `package.json`。

## 10. 预计修改规模

只按新增/改写代码行估算，不使用人日：

| 子阶段 | 生产代码（含 migration） | 测试代码 | 主要规模来源 |
| --- | ---: | ---: | --- |
| M4.0 Content | 800–1,300 | 120–220 | 五份新 Skill、十人 manifest、Hogwarts Scenario 与严格加载/隔离测试 |
| M4.1 Runner | 900–1,350 | 800–1,250 | dispatch 事务、选择算法、50 次规则、idle/failure |
| M4.2 Session | 950–1,450 | 900–1,400 | transition plan、批量 CAS、新 Entry/明细表、权限矩阵 |
| M4.3 StoryLine | 450–700 | 450–750 | Line key、DAG Builder、StageView、坏图检查 |
| M4.4 Lifecycle | 750–1,150 | 750–1,200 | pause/resume、文件锁、CLI、跨进程与 race 测试 |
| M4.5 Viewer/real-run support | 450–750 | 350–600 | JSON 导出、单文件 HTML、escape/CLI 测试、run manifest 生成 |
| **分项合计** | **4,300–6,700** | **3,370–5,420** | 不重复计算 migration；真实生成 artifact 不计源码行 |

正式 Review envelope 各留少量跨阶段修复余量：生产代码与版本化内容 **4.5–6.9k**、测试 **3.5–5.6k**，因此预计 M4 非 Wiki、非生成 artifact 的总 diff 约 **8.0–12.5k 行**。`runtime_fixtures.py`、现有测试 UPDATE 和 migration 都已包含，不能在交付时重复加一次。超过上限时先检查是否误建了通用 Scheduler/Repository/StoryLine 缓存或前端框架；低于下限不代表失败，但必须仍覆盖下面全部行为门禁。

## 11. 测试目标与预期结果

### 11.1 新增测试目标

当前基线为 Python **442 passed**、Node **14 passed**。补齐内容加载、并发、恢复、lineage 和失败反例后，M4 预计新增约 108 个 pytest collected case，并新增约 8–12 个 Node Viewer case：

| 子阶段 | 预计新增 | 必须证明 |
| --- | ---: | --- |
| M4.0 | 12 | 十人 Skill/Manifest/Scenario 可编译；学院/私有知识正确分流；Project DB 不串；内容 hash 稳定 |
| M4.1 | 24 | active dispatch fence；pending 一次优先但不强制；root/成员公平；失败扣额度；可见唤醒/idle；50 次后行为；Trace |
| M4.2 | 26 | 无审批 join；只转移本人；root actor 离开；节点数恒等于 Agent 数；不可达 request 取消；CAS/重放/全事务回滚 |
| M4.3 | 20 | 两条隔离 Line 不串；空 child Line；多父子 DAG；一致 as-of 读取；root 复用、稳定排序和隐私 |
| M4.4 | 26 | pause/commit 竞态；迟到结果；并发 resume；SIGKILL/崩溃恢复；额度不返还；CLI/Project lock 隔离 |
| **Python 合计** | **约 108** | 完整产品 Demo 机制与十人内容输入 |
| M4.5 Node | 8–12 | raw JSON 验证；泳道/transition 渲染；XSS escape；无 CDN；相同输入字节稳定；CLI 错误码 |

最终完整 Python 预期约 **550 passed**；由于参数化拆分，允许落在 **546–554**，但完成判断只看行为覆盖，不为凑数字拆测试。Node 预期由 **14 passed** 增至约 **22–26 passed**。真实 Provider 群演不是 CI pytest：它以提交的 manifest、raw JSON、HTML 和 World 数据一致性审计作为独立硬门禁。

### 11.2 关键命名用例

至少出现并通过以下可读测试：

```text
test_pending_response_gets_one_priority_turn_but_does_not_force_reply
test_multiple_pending_recipients_do_not_starve_each_other
test_same_recipient_multiple_requests_consume_one_priority_marker_at_a_time
test_failed_dispatch_does_not_consume_request_priority_opportunity
test_transport_failure_consumes_dispatch_budget_and_yields_fairly
test_dispatch_limit_pauses_world_as_budget_exhausted_without_ending_story
test_failed_dispatch_and_next_dispatch_have_distinct_decision_ids
test_outcome_must_match_active_dispatch_token
test_restart_invalidates_active_dispatch_without_refunding_budget
test_all_agents_waiting_pauses_world_without_fake_entry
test_restart_mid_no_progress_scan_resumes_without_skipping_or_spinning
test_invisible_entry_from_another_root_does_not_wake_waiting_agent
test_visible_recipient_entry_wakes_waiting_agent
test_fiftieth_dialogue_hides_dialogue_until_behavior_commits
test_pending_response_returns_after_required_behavior_resets_dialogue_count
test_free_text_behavior_cannot_mutate_object_location_or_session

test_join_requires_no_member_approval
test_join_transfers_only_the_acting_agent
test_root_actor_leave_reroots_remaining_members_deterministically
test_repeated_merge_split_keeps_exactly_one_session_node_per_agent
test_split_cancels_only_requests_that_became_unreachable
test_transition_recipients_equal_all_and_only_affected_members
test_concurrent_stale_transitions_have_one_complete_winner
test_replayed_transition_does_not_change_topology_twice
test_transition_checkpoint_failure_rolls_back_entry_sessions_request_and_memory

test_isolated_roots_build_separate_story_lines
test_transfer_connects_all_before_and_after_line_keys
test_split_exposes_empty_child_line_before_its_first_dialogue
test_transition_entry_appears_once_in_actor_after_line
test_genesis_and_historical_line_members_are_reconstructed_without_recipients
test_reused_root_with_new_topology_never_reopens_old_story_line
test_later_join_does_not_reveal_old_whisper
test_stage_view_uses_one_consistent_read_snapshot_during_commit
test_built_through_excludes_future_entries

test_pause_racing_commit_chooses_one_complete_boundary
test_late_result_from_old_epoch_cannot_commit_after_resume
test_second_resume_cannot_take_over_live_world
test_external_pause_fences_live_owner_before_reporting_saved
test_stale_running_row_recovers_only_after_os_lock_is_free
test_resume_adds_budget_without_refunding_failed_dispatch
test_process_restart_restores_partition_request_plan_and_scheduler_position
test_sigkill_after_dispatch_preserves_last_commit_and_consumes_budget
test_same_world_id_in_different_projects_uses_distinct_locks
test_status_and_story_never_load_private_state_or_construct_provider
test_0003_downgrade_refuses_m4_only_rows_without_data_loss

test_mygo_hogwarts_compiles_exactly_ten_agents_and_ten_stable_sessions
test_mygo_hogwarts_private_relationship_hooks_do_not_become_public_facts
test_stage_view_json_round_trips_without_private_memory_or_trace
test_storyline_html_escapes_model_supplied_markup
test_storyline_html_is_byte_stable_and_has_no_network_dependency
test_storyline_html_renders_merge_and_split_membership_deltas
test_storyline_viewer_rejects_broken_or_cross_world_links
```

并发测试使用 `threading.Barrier/Event` 或受控 checkpoint，不用 `sleep()` 猜时序。跨进程恢复必须使用文件 SQLite，显式 dispose 后由新 Python 进程 reopen，不能用同一个 Session 冒充重启。

### 11.3 每个子阶段门禁

每个 M4.x 完成时都运行：

```text
对应专项 pytest
完整 uv run pytest
uv run ruff format --check agent_runtime
uv run ruff check agent_runtime
uv run pyright
git diff --check
```

涉及 0003 后额外运行 migration round-trip、ORM metadata empty、注错回滚、`PRAGMA foreign_key_check` 和 wheel migration 资源检查。M4.5 运行完整 Node suite，既证明新增 Viewer，也证明 Agent Runtime 改动没有破坏 Dynamic Render 基线。

### 11.4 Fixture Demo 与十人真实模型验收

最终同时保存两套不同性质的证据：

1. **确定性 Fixture（机制硬门禁）**：Anon/Soyo 连续对话，Tomori 加入，Soyo 表达不欢迎但 Runtime 不自动踢人，Tomori 自主离开；中途 pause、dispose、load、resume。相同 seed 跑两次应得到相同 dispatch 顺序、Entry ID、分区版本和 StoryLine DAG。它证明权限、事务、拓扑和恢复，不冒充真实剧情质量。
2. **DeepSeek flash 十人群演（产品交付硬门禁）**：使用 [MyGO × Hogwarts 内容设计](M4_hogwarts_demo.md) 创建独立 World，十个 Agent 都必须由真实 Gateway 至少成功派发一次。接受的运行至少包含十二条非 transition 的 committed Entry、一条由模型 Proposal 选择并成功提交的 add/merge/transfer、一条由模型 Proposal 选择并成功提交的 split/leave，以及一次关闭连接或进程后的 load/resume。

每次实网尝试预先设置 `dispatch_limit_at` 和进程级 provider generate 上限；默认模型使用仓库配置的 `deepseek-v4-flash`，实际 model ID、调用数、repair 数与停止原因写入 `run_manifest.json`。模型没有自然产生 add/split 时，本次 World 记为未通过但仍保留简要 attempt record；只允许调整 Character/Scenario 动机后创建新 World 重跑，不得改写真实台词、向数据库补 Entry 或让测试策略替角色选 transition。

M4 DONE 前必须提交：

```text
artifacts/m4-hogwarts/<accepted-world-id>/run_manifest.json
artifacts/m4-hogwarts/<accepted-world-id>/storyline.json
artifacts/m4-hogwarts/<accepted-world-id>/storyline.html
artifacts/m4-hogwarts/<accepted-world-id>/review.md
```

`storyline.json` 必须能在不连接 SQLite、不调用模型的情况下完整阅读；HTML 必须由 `node tools/storyline-to-html.mjs` 从该 JSON 确定性生成，展示 StoryLine 泳道、Entry、recipient、parent/child links 和 add/split 成员变化。HTML 不使用 CDN，所有模型文本必须 escape，脚本对坏 DAG、跨 World link 与缺字段返回非零退出码。

CI/普通测试必须清除 `DEEPSEEK_API_KEY`，Fixture Runner 断言 Provider factory 从未构造；Provider wire 测试只使用 `MockTransport`。这保证自动测试离线且不消费用户额度。反过来，真实 Provider 因网络、余额或模型不可用而未成功时，M4 必须如实保持 REVIEW/BLOCKED，不能再把它降格为“可选 smoke”后标 DONE。

## 12. 开工冻结点

下面是本设计给出的推荐答案；用户 Review 后即冻结，开发中不随意改语义：

1. **身份与 Action：** `event_session_id` 是稳定节点；保持六类 Action；character `interact` 和 `act` 只选择带 typed operation 的 join/self/leave affordance。
2. **dispatch fence：** decision ID 绑定 dispatch count；active token 同时绑定 count+agent；失败清 token、不推进 decision seq、不退款。
3. **50 次规则：** 只数该角色 committed DialogueEntry；Behavior/Object/Transition 清零；稳定节点携带计数。
4. **pending 未回应：** 每个 request 只享受一次优先派发，不强迫回应；分组后不可达则原子 cancelled。
5. **wakeup：** M4 只由角色 recipient 中的新 committed Entry 唤醒；隔离 root 的不可见变化无效，无候选时 paused/idle，不推进虚构时间。
6. **StoryLine lineage：** transition 只归行动者 after Line；窄的 transition part/member 表表达所有父子线，不用单个 from/to 字段或 JSON 数组丢信息。
7. **运行排他：** 本地 POSIX 文件锁 + DB owner/epoch；外部 pause 是明确的 fencing CAS；MVP 不做 lease/heartbeat 和多机接管。
8. **迁移降级：** 0002-compatible 数据可往返；存在 M4-only 状态时 downgrade 安全拒绝，不牺牲故事历史换绿灯。
9. **验收内容：** 使用 [MyGO × Hogwarts](M4_hogwarts_demo.md) 十人四年级 AU；学院属于 Scenario overlay，不写死进稳定 Character Skill。用户已确认第十人为八幡海铃，归入 Hufflepuff。
10. **真实结果：** Fixture 与实网各有职责，但都是硬门禁；M4 必须交真实群演 raw StoryLine JSON 和离线 HTML，Viewer 不承担 M6 导播选材。

若只需要调整命名，可在 M4.1 开工前一次性改；若要改变其中任一行为，必须同步修改本页测试合同和预计范围。

## 13. M4 完成门禁

只有以下清单全部满足，M4 才能标 DONE：

- [x] Fixture 角色连续互动，不靠测试直接写 Entry 冒充 Agent 决策；
- [x] pending 优先、公平轮转、额度预扣、模型失败和 idle 都有确定性持久化语义；
- [x] 对话 50 次后行为规则真实生效且不存在无合法行为 affordance 的死锁；
- [x] 自主 join/leave/transfer 使用 World-issued affordance，无审批、只移动行动者；
- [x] merge/split 多次后 Session 行数恒等于 Agent 数，root/topology 与 UnionPart 重建一致；
- [x] Session、transition Entry、request cancellation、PersonaState、Memory 和调度完成同事务；
- [x] StoryLine 按 root-at-commit + topology 隔离，并能表达 merge/split/transfer 完整父子关系；
- [ ] pause、旧 epoch 迟到结果、运行锁与重新加载已通过；真实 Ctrl+C/SIGKILL 子进程证据仍待补；
- [ ] 关闭连接后已恢复同一 plan/request/partition/scheduler 位置且 Entry 不重复；关闭独立进程后的同等证据仍待补；
- [x] `mygo-hogwarts` 十人 Manifest/Scenario/Skill 全部严格编译，十个稳定 Session node 与 Project DB 身份一致；
- [ ] 真实 Ark World 中十人均已成功派发，模型 Proposal 实际产生 1 merge + 5 transfer；仍缺至少一次 split/leave；
- [ ] 当前真实运行有 72 条非 transition committed Entry，add 前后可读；仍缺 split 前后内容；
- [x] 已保留脱敏 `run_manifest.json`、三份 attempt、raw `storyline.json`、确定性 `storyline.html` 与 `review.md`，并明确标注本次运行未被接受；
- [x] Viewer 无网络依赖，正确显示分线、交汇、recipient 与成员变化，模型文本 escape，坏 JSON/DAG 非零退出；
- [x] 完整 Python/Node、Ruff、Pyright、migration、wheel 和 diff 门禁全绿；
- [ ] 本页已补实际文件、真实增删行、测试结果与 Review 修复；M4 尚未 commit，因此没有提交 hash。

达成后可以准确称为：

> 一个按 Project 隔离、可暂停续跑、支持多个 Character 连续互动和自主分合组，并能提供多线可读 committed StoryLine 的 Agent World Runtime Demo。

它仍不是完整工程 MVP：Director 咖啡闭环属于 M5，Broadcast 选材/正式发布 JSON 属于 M6，联合产品验收属于 M7。M4 的 raw StoryLine JSON/HTML 只是演员 Runtime 的真实输出与开发审片工具。

## 14. 新增交付执行计划

用户在 2026-09-11 把 M4 的交付范围从“五人 Fixture + 小额度 Provider smoke”提高为“十人真实群演可审片”。开发顺序据此扩展，但不改变 M4.1–M4.4 已冻结的 Runtime 事务设计：

```text
M4.0  十人 Skill / Manifest / Hogwarts Scenario
  ↓
M4.1  有界 Runner 与持久 dispatch
  ↓
M4.2  模型选择 affordance 后的 add/split 原子提交
  ↓
M4.3  committed Entry → StoryLine DAG / raw JSON
  ↓
M4.4  pause / close / load / resume
  ↓
M4.5  storyline.json → standalone storyline.html
  ↓
M4.6  DeepSeek flash 十人真实运行、保留全部 attempt manifest
  ↓
M4.7  提交第一条满足硬门禁的 JSON / HTML / review
```

Review 分界：

- M4.0 Review 人物事实、AU 边界、私有知识和初始分组；
- M4.1–M4.4 Review 代码、不变量、Schema 和离线测试；
- M4.5 Review JSON contract、HTML 安全性和可读性；
- M4.6–M4.7 Review 真实角色行为、实际 add/split 来源、故事是否能读以及未通过尝试。

真实群演发生在所有自动门禁通过以后，避免把昂贵 Provider 调用用作调试事务 bug 的手段。接受运行不做台词润色；若故事质量不够，保存当前结果与问题，再用新的 `world_id` 重跑，不能修改已提交 World 历史。

## 15. 实现与验收记录

### 15.1 实际落地范围

截至 2026-09-11，M4.0–M4.5 已在当前 worktree 落地：

```text
agent_runtime/
├── event/
│   ├── runner.py                 [NEW] 串行派发、持久额度、公平选择、idle/pause 与 World 锁
│   ├── session.py                [NEW] 基于 UnionPart 规划 merge/transfer/split
│   ├── story_line.py             [NEW] committed Entry → StageView/StoryLine DAG
│   ├── character_step.py         [UPDATE] 可信 dispatch token 与 transition outcome
│   └── __init__.py               [UPDATE] M4 稳定公开入口的 lazy export
├── world/
│   ├── affordances.py            [UPDATE] self behavior、join、leave
│   ├── entries.py                [UPDATE] Behavior/SessionTransition Entry
│   ├── entry_storage.py          [UPDATE] Entry、link、request 与 transition part 持久化
│   ├── state.py                  [UPDATE] dispatch/owner/stop 与稳定节点调度状态
│   ├── storage.py                [UPDATE] 生命周期 fence、dispatch CAS 与批量 Session 更新
│   ├── updater.py                [UPDATE] 一次 outcome 原子发布公共/私有/调度结果
│   └── view_builder.py           [UPDATE] join/leave、自身行为、50 轮限制与 request 优先
├── migrations/versions/
│   └── 0003_m4_runtime_sessions.py [NEW] M4 关系型状态与安全 downgrade
├── model_budget.py               [NEW] 全体 Agent 共享的逻辑 Gateway 调用上限
├── world_cli.py                  [NEW] create/run/pause/status/story 与 attempt manifest
└── bootstrap.py                  [UPDATE] 只读 load-for-resume

projects/mygo-hogwarts/           [NEW] 十人 Project、Scenario、Agent 配置
content/skills/characters/        [NEW] Ave Mujica 五份稳定 Character Skill
tools/storyline-to-html.mjs       [NEW] 无 CDN 的单文件审片 Viewer
tests/storyline-to-html.test.mjs  [NEW] Viewer contract、安全与确定性测试
README.md                         [UPDATE] M4 CLI 和能力边界
pyproject.toml / uv.lock          [UPDATE] 增加 socksio 以支持当前 SOCKS proxy 环境
```

实际还新增 `test_event_session.py`、`test_event_session_runner.py`、`test_world_lifecycle.py`、`test_story_line.py`、`test_world_cli.py`、`test_model_budget.py` 和 `test_hogwarts_scenario.py`，并扩展既有 CharacterStep、Updater、ViewBuilder、Migration 测试。计划中的 `runtime_fixtures.py`、`test_event_runner.py` 和 `test_runtime_process.py` 没有机械照建：Fixture 放在对应专项测试中，Runner 集成测试统一收敛为 `test_event_session_runner.py`；真正的信号/强杀子进程测试仍是 M4.4 的待补证据。

非 Wiki、非生成 artifact 的实际 diff 为 **10,305 additions / 122 deletions，共 10,427 touched lines**，位于开工前 8.0–12.5k 的 Review envelope 内；其中包括版本化 Scenario/Skill 与完整测试。这组统计在 2026-09-11 记录时尚未 commit；2026-09-12 已作为 M4 engineering checkpoint 提交。

### 15.2 已验证的行为

- `test_real_character_steps_survive_pause_reopen_and_dynamic_session_changes` 通过唯一 `CharacterStep` 提交链完成 `utter/request → respond → merge → behavior → transfer → pause/close → load-for-resume → reopen/resume → behavior → split`；最终同时校验 Entry/links/transition parts、pending request、Persona/Memory、分区、调度位置和 StoryLine DAG。
- join 不需要批准；从已有小组加入只 transfer 行动者；root 成员离开时确定性 reroot；任意 transition 后稳定 Session 行数不变。
- 50 条连续 Dialogue 后只隐藏 utter/respond，self behavior/leave 仍可选择；一次 committed behavior 后 dialogue 和 pending respond 恢复。
- dispatch 在模型前持久预扣；失败不返还额度也不吞 pending priority；owner/epoch/count/agent 任一不匹配都不能发布迟到结果。
- StoryLine 以 `(rootSessionIdAtCommit, topologyVersion)` 分线，并从 transition before/after parts 重建 merge/split/transfer DAG；whisper audience 不被后来加入者扩大。
- Viewer 对 StoryLine/Entry/recipient/transition 做与 Python contract 对齐的校验，拒绝坏 DAG、跨 World link、phantom lineage、同 inode/hardlink 和危险 symlink；模型文本被 escape，输出使用 `0600 + fsync + atomic replace`。
- 独立 Review 修复了 transition `relation_order` 在缺失 frontier 时产生空洞，以及直接调用 CharacterStep 绕过 Runner owner/active token 的风险；真实运行入口另增加所有角色共享、线程安全且预扣式的逻辑 Gateway budget。

### 15.3 实际门禁结果

2026-09-11 首轮工程门禁：

```text
.venv/bin/pytest                    487 passed in 44.13s
npm test                            30 passed
.venv/bin/ruff check .              passed
.venv/bin/ruff format --check .     114 files already formatted
.venv/bin/pyright                   0 errors, 0 warnings
git diff --check                    passed
uv build                            sdist + wheel built successfully
wheel resource check                0003 migration、Runner、Session、StoryLine、CLI、budget 均存在
```

2026-09-12 push 前复验：Python 全量 **505 passed**，Node 全量 **31 passed**；Ruff check、117 文件 format check、Pyright（0 errors / 0 warnings）、`git diff --check` 全部通过，`uv build` 再次成功生成 sdist 与 wheel。

一次额外的十人离线 CLI 验证使用 World `m4-final-fixture-a93e`：先运行 8 次决策，关闭并重新装配后追加 7 次，最终 `dispatchCount=15 / decisionSeq=15 / worldVersion=16 / status=paused`，十名角色都至少提交一次 BehaviorEntry；导出的 StageView 有 4 条 Line、15 条 Entry，standalone HTML 转换成功。该结果只证明真实 Project 装配、Project SQLite、续跑、导出与 Viewer，不证明模型质量或模型自主 add/split。

另一个机制级集成 Fixture 导出了 9 条 Line、8 条 Entry、3 次 transition，覆盖 merge、transfer 与 split；它同样不是 accepted Provider StoryLine。

### 15.4 当前阻塞与完成判定

2026-09-12 已改用 Ark OpenAI-compatible Provider，在 World `ark-review-20260912-01` 上完成三段有界真实运行。合计 80 次持久 dispatch、79 个成功角色步骤、176 次逻辑 Provider 调用、0 个应用级失败 dispatch；最终保存为 `paused / budget_exhausted`，`decisionSeq=79 / worldVersion=79`。十名角色都至少成功派发一次。

公开 StoryLine 有 78 条 committed Entry：58 dialogue、5 action、9 behavior、6 session transition；真实模型选择形成 1 merge + 5 transfer，但没有选择 `leave_current_session`，因此是 `0 split`。这证明真实 Provider、连续运行、暂停续跑、分组变化和审片链路已工作，但仍不满足用户冻结的 M4 完成合同。

当前结论：

- M4.0–M4.3、M4.5 的工程依赖已经足够支持 M5 开工；
- M4.4 的数据库 fence、文件锁、pause/load/resume 与迟到结果已完成，仍缺真实 SIGINT/SIGKILL 子进程证据；
- M4.6/M4.7 从 Provider `BLOCKED` 转为 **REVIEW**：真实运行和审片四件套存在，但因 `0 split` 明确不接受；
- `artifacts/m4-hogwarts/ark-review-20260912-01/` 必须作为失败也真实的证据保留，不改台词、不补 Entry、不伪造 split；
- Scenario 已升级到 v2，爱音 Skill 升级为 3.1.0，增加暗恋爽世的目标/私有记忆及魔咒课、魔药课可操作内容。旧 World 绑定 v1 seed/spec，不能拿新配置继续恢复；下一次实网验收必须创建新 World。

所以 M4 整体仍不标 `DONE`，但这已经不再是 M5 的代码依赖阻塞。M5.1 可以开始；M4 的真实 split 与进程信号证据继续作为显式债务跟踪。

### 15.5 Viewer 与自动化修正

真实审片暴露了旧 HTML 的阅读问题，Viewer 已改为横向 worktree：join/merge 汇线，leave/split 分线；每个点直接展示中文角色名、“谁对谁说”及完整台词，动作/行为和 transition 原因同样展开，runtime ID 收进折叠技术信息。固定高度的内部限制框已移除，图谱按完整高度撑开；页面打开时清空浏览器恢复的旧筛选。

转换仍由同一确定性实现负责，没有第二套 renderer。日常入口为：

```bash
npm run story:html -- artifacts/m4-hogwarts/<world-id>
```

命令自动读取目录内 `storyline.json` 并原子更新同目录 `storyline.html`；显式 `<json> --out <html>` 形式继续兼容。当前真实 HTML 与 review 记录的 SHA-256 一致。
