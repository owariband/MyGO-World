# EventStaff Director 与 Broadcast

> 状态：Director 的权限边界已由 [D-044](decisions.md#d-044director-只管理由已提交事实触发的-eventstaff) 重定义。旧版“Segment Completion / Narrative Intervention / Narrative Thread”设计已经废止，不再作为实现目标。本文字段和唤醒策略是自由互动 MVP 的默认方案，仍需用 Golden Trace 验证。

## 1. 三类 Agent 的边界

| 角色 | 可以决定 | 明确不能决定 |
| --- | --- | --- |
| Character Agent | 自己说什么、做什么、回应谁、是否等待 | 其他角色的行动；未经提交的世界事实；后台环境过程的完成 |
| Director Agent | 已经客观启动的后台过程是否入队、继续等待、释放或取消 | Character 的台词、行动、意愿、关系、组队；剧情压力；任意 World patch |
| Broadcast Agent | 玩家看到哪些已提交 Event、以什么镜头和 WebGAL 表现 | 世界发生了什么；角色知道什么；后台过程是否完成 |

Director 不是故事里的“导演角色”，也不是上帝视角的剧情作者。它更接近一个受权限约束的**客观异步过程调度器**。

## 2. 为什么需要 EventStaff

有些世界变化不是 Character 一次动作提交后立刻完成的：

- Character 启动咖啡机，咖啡稍后煮好；
- Character 发起下载，文件稍后完成；
- 门被打开后，自动闭门器稍后关门；
- 已经确认发出的列车，在预定世界时间到站。

这些事情有三个共同点：

1. 必须先有已经提交的客观起因；
2. 中间需要跨越至少一次调度或世界时间；
3. 完成结果不再需要 Character 替环境作决定。

`EventStaff` 表示的正是“已被客观事件启动、尚未完成的环境过程”。它不是已经发生的 `WorldEvent`，也不是 Director 想让故事发生的愿望。

```text
Character ActionProposal
-> WorldChangeValidator
-> World.apply_change / WorldUpdater
-> committed WorldEvent
-> DirectorRunner 构造受限 DirectorView
-> EventStaffDecision
   enqueue / keep / no_op 只更新 Staff 与消费游标
   release / cancel 再进入 Validator + WorldUpdater
-> committed objective WorldEvent
-> AgentViewBuilder 投影给可见 Character
-> Broadcast Agent 只读选择玩家表现
```

Character Proposal 的提交不经过 Director。Director 永远位于第一次事实提交之后。

## 3. EventStaff 数据模型

MVP 使用以下持久化字段：

```text
EventStaff
  event_staff_id
  world_id
  session_id
  source_event_id
  staff_kind
  subject_type
  subject_id
  completion_event_type
  status
  created_world_version
  created_world_time
  next_check_at?
  release_event_id?
  details_json
```

字段语义：

- `session_id` 是 Character 的稳定 EventSession 节点，不是会随 merge/split 改变的 root；
- `source_event_id` 必须指向真正启动过程的 committed Event；
- `staff_kind` 标识受信过程类型，例如 `coffee_brewing_completion`；
- `subject_type + subject_id` 标识正在变化的客观对象；
- `completion_event_type` 在 enqueue 时已经由 World 规则确定，release 时不能临场改写；
- `status` 只允许 `pending / released / cancelled`；
- `next_check_at` 是下一次可检查时间，不等同于“保证完成时间”；
- `details_json` 只容纳该 staff kind 独有且经 strict schema 校验、无需单独查询的数据。

`event_staff` 表本身就是持久化队列，不再建立一张复制顺序的 queue 表。Runner 按类似以下键稳定查询：

```text
(world_id, status, next_check_at, created_world_time, event_staff_id)
```

至少需要以下约束：

```text
UNIQUE(source_event_id, staff_kind, subject_type, subject_id)
FOREIGN KEY(source_event_id) REFERENCES world_events(event_id)
FOREIGN KEY(release_event_id) REFERENCES world_events(event_id)
CHECK(status IN ('pending', 'released', 'cancelled'))
```

唯一键保证同一个 source Event 因重试被再次消费时不会重复排入同一个客观过程。

## 4. Director 到底能看见什么

Director 不使用 Character 的 `PerceptionFrame`。它没有“站在哪里、听见什么”的角色认知语义；Runtime 为它构造的是一个**操作授权视图** `DirectorView`。

这个 View 必须由 World-owned 的确定性函数构造，例如 `World.build_director_view(trigger)`。Director Agent 不能自己查询数据库、扩大因果窗口或选择想看的 Session。DirectorRunner 可以读取下一条 Event 的 ID/type 以推进 cursor；若 World 计算后没有任何 Staff affordance，也没有相关 pending Staff，Runner 直接以确定性 `no_op` 前移 cursor，不调用模型、更不必把整条台词交给 Director。

```text
DirectorView
  trigger
    kind                    committed_event | event_staff_check
    source_event_id?
    event_staff_id?

  world_id
  based_on_world_version
  world_time

  session_id                    opaque delivery anchor

  source_event_projection
  bounded_causal_events
  relevant_object_process_state
  relevant_location_process_state
  selected_pending_staff?
  conflicting_pending_staff

  event_staff_affordances
```

### 4.1 可以看

一次调用只允许看到：

- 当前尚未消费且确实产生 Staff affordance 的一个 committed source Event，或者当前被唤醒检查的一条 Staff；
- 与 source/staff 的同一 process/subject 直接相连的有限因果窗口，而不是全部世界历史；
- source/staff 明确引用且完成条件确实需要的 Object/Location 机器状态；
- Staff 的稳定 `session_id`，但只把它当不透明投递锚点；
- World 已判断为冲突或互斥的 pending Staff；
- World 根据事件类型、对象状态和规则预先计算的 `event_staff_affordances`；
- 判断客观完成条件所需的世界时间与过程状态。

Director 默认不读角色台词正文。只有某种受信 process contract 明确需要某个已提交字段时，World 才将该字段投影进 `source_event_projection`；咖啡流程只需要 `coffee_brewing_started` 的 typed process 数据，不需要“我要煮咖啡”的 quote。真正的操作范围仍由 affordance 决定。

### 4.2 不能看

Director 明确不能收到：

- 未提交、被拒绝或仍在生成中的 `ActionProposal`；
- Character 私有 Memory、Scratch、Goal、Plan、Reflection、检索结果或模型思维过程；
- 与当前触发源没有因果关系的其它 EventSession 正文；
- 当前 root、成员名单，以及哪些 Character 正在等待、观看或谈论该过程；
- `interaction_requests` 中用于角色自主回应的调度选择；
- BroadcastPlan、RenderJob、Viewer Cursor、热度或玩家观看反馈；
- secret WorldFact，除非它是当前客观过程自身的受信机器状态且不会作为自然语言泄露；
- 任意 SQL、任意 World patch、任意角色 Memory write 能力。

这不是单纯依靠 Prompt 约束。`DirectorView` 的构造器根本不查询这些数据，`EventStaffDecision` 的类型也不提供相应输出槽位。

### 4.3 Affordance 才是最终权限

World 先以确定性规则决定某次 Director 调用有哪些合法选项。例如：

```text
event_staff_affordances:
  - kind: enqueue
    staff_kind: coffee_brewing_completion
    source_event_id: E2
    subject_type: object
    subject_id: coffee-machine-1
    completion_event_type: coffee_ready
    earliest_release_at: T+120s
    invalidated_by:
      - coffee_brewing_cancelled
      - coffee_machine_broken
```

Director 只能在这些候选中选择，不能自行发明 `staff_kind`、subject 或完成事件。若没有 affordance，唯一合法输出就是 `no_op`。

当 Staff 尚未满足 World 计算的 release guard 时，只允许 `keep` 或在存在明确失效事实时 `cancel`。即使模型错误地请求 `release`，Validator 也必须拒绝；到达硬 deadline 时，Runtime 可以使用确定性 fallback，避免模型故障导致客观过程永久悬挂。

### 4.4 当前 Action contract 的前置缺口

当前源码中的 Character `Affordance` 只有 `kind + target`，`InteractAction` 只有 `target + description`。这还不足以把一次自然语言交互稳定解析为 `start_brewing`：同一个 coffee machine target 也可能是 inspect、start、stop 或取走咖啡。

EventStaff 开工前必须增加稳定操作引用，推荐：

```text
AgentView Affordance
  affordance_id
  kind=interact
  target=coffee-machine-1
  operation_id=start_brewing
  based_on_world_version

InteractAction
  kind=interact
  target=coffee-machine-1
  affordance_id
  description
```

Validator 以 `affordance_id` 解析受信 operation 和状态转换；`description` 只用于表达/Trace，不能单独触发 Object mutation 或 Staff enqueue。备选是为每类 Object operation 建 typed action union，但不能继续依赖自由文本猜测。

## 5. Director 的输出

```text
EventStaffDecision =
  EnqueueDecision {
    affordance_id
    next_check_at?
  }
  | KeepDecision {
      event_staff_id
      next_check_at
    }
  | ReleaseDecision {
      event_staff_id
    }
  | CancelDecision {
      event_staff_id
      caused_by_event_id
    }
  | NoOpDecision
```

权限含义：

- `enqueue`：接受一个 World 已提供的 affordance，建立 pending Staff；
- `keep`：当前不释放，只安排下一次检查；
- `release`：请求兑现既有 Staff 的固定 completion contract；
- `cancel`：引用一条已提交的客观失效事件，终止 Staff；
- `no_op`：当前 committed Event 不产生后台过程。

Director 不能输出 Character ActionProposal、Session merge/split、自由文本 WorldEvent 或自然语言补丁。解释文本即使保留在调用 Trace 中，也没有世界写入权。

## 6. 咖啡 Golden Trace

“Anon 说『我要煮个咖啡』”只证明一句话发生了：

```text
E1: utter(actor=anon, quote="我要煮个咖啡")
-> committed
-> World 计算不到 coffee completion affordance
-> DirectorRunner 确定性前移 cursor，不调用 Director Agent
```

若 Director 此时就排入“咖啡煮好”，它实际上替 Anon 走到咖啡机旁并启动了机器，违反 Character autonomy。

正确流程是：

```text
Anon 自己决定 interact(coffee-machine-1, start_brewing)
-> Validator 校验对象可达、机器空闲、Anon 有此 affordance
-> 同一 World transaction：
     coffee-machine-1.state = brewing
     append E2: coffee_brewing_started(actor=anon)
     world.current_version += 1

DirectorRunner 消费 E2
-> DirectorView 只暴露 coffee_brewing_completion affordance
-> enqueue S1(session_id=session-anon, source_event_id=E2)
-> S1.status = pending

S1 到达 next_check_at
-> World 重新读取机器状态与 release guard
-> Director keep(S1) 或 release(S1)

release(S1)
-> Validator 再次确认：
     S1 仍 pending
     coffee-machine-1 仍 brewing
     release window 已开放
     source/cause 仍有效
-> 同一 World transaction：
     coffee-machine-1.state = ready
     S1.status = released
     append E3: coffee_ready(caused_by_event_id=E2)
     S1.release_event_id = E3
     world.current_version += 1

AgentViewBuilder(E3)
-> 投影给此刻确实可见的 Character
```

如果有人在完成前关闭咖啡机：

```text
E4: coffee_brewing_cancelled
-> DirectorView(trigger=E4, selected_staff=S1)
-> cancel(S1, caused_by_event_id=E4)
-> 同一 transaction 提交取消所需的对象状态与 Staff 状态
```

取消本身是否需要独立 `WorldEvent`，取决于角色是否能客观观察到取消；但 Staff 状态变化和导致它的 committed Event 引用必须被持久化。

## 7. EventSession merge/split 后 Staff 如何归属

EventStaff 绑定稳定 `session_id`，不绑定可变 `root_session_id`。创建于 `session-anon` 的 Staff 在 UnionPart merge/split 时不搬迁、不复制。

释放时才解析当前互动边界：

```text
current_root = UnionPart.rootOf(staff.session_id)
candidate_member_ids = UnionPart.members(current_root)
recipients = AgentViewBuilder.filterVisible(
  released_event,
  candidate_member_ids,
)
```

因此：

- 后来 merge 进当前 root 的 Character，只有在地点和感知渠道上真的可见时才收到；
- 已经 split 离开的 Character 不会因为过去同组而自动收到；
- 历史 Event 仍保存 `root_session_id_at_commit`，不会被当前 root 反向改写；
- Director 和 Broadcast 都不是故事 Character，不拥有 UnionPart 节点。

若将来确实需要“同一地点内跨 Session 的广播”，应为 Staff/Event 增加显式 `delivery_scope=location`，并由 AgentViewBuilder 校验；不能偷偷把 `session` 语义扩大为地点广播。这个扩展不进入首轮 MVP。

## 8. 消费游标、事务与恢复

当前 MVP 假设每个 World 只有一个逻辑 Director consumer，在 `worlds` 保存：

```text
director_event_cursor
```

处理 committed Event 时：

```text
read next event after cursor
-> build DirectorView
-> decide enqueue or no_op
-> transaction:
     insert EventStaff if needed
     advance director_event_cursor
```

Staff 插入与 cursor 前移必须处于同一 SQLite transaction。否则：

- 先前移 cursor、后插入失败，会永久漏掉后台过程；
- 先插入、后前移失败，会在重试时重复入队。

唯一键提供第二层幂等保护。DirectorRunner 还要独立查询到期的 pending Staff；事件消费游标不能代替时间唤醒队列。

`release/cancel` 必须把 Staff 状态、对象当前状态、新 WorldEvent（若有）和 `worlds.current_version` 放进同一 transaction。进程崩溃后只会看到事务前或事务后，不会看到“咖啡已 ready 但 Staff 仍 pending”的半状态。

## 9. “释放给角色”与 Broadcast Agent 不是一回事

项目中容易混淆三个动作：

```text
Director release EventStaff
-> 让一个客观环境结果进入 World

AgentViewBuilder project WorldEvent
-> 决定哪些 Character 可以感知这个结果

Broadcast Agent project committed events
-> 决定玩家如何看到这些结果
```

所以“咖啡煮好了广播给当前 EventSession”在精确术语中是：

1. Director 请求 release；
2. WorldUpdater 提交 `coffee_ready`；
3. AgentViewBuilder 以当前 UnionPart 成员为候选并执行硬可见性过滤；
4. Character 的 CognitiveController 决定是否注意、是否写入自己的 Memory；
5. Broadcast Agent 稍后选择是否把该 Event 编进 WebGAL。

Director 不能直接把一句文本 push 到每个 Character 的 Prompt 或 Memory；否则会绕过 WorldEvent、可见性和 Character attention。

## 10. Broadcast 的只读输入与产物

Broadcast 只读取稳定到某个 `event_watermark` 的 committed `world_events`，通过以下关系组织多条 EventSession 流：

- `event_order + world_time`：客观先后；
- `root_session_id_at_commit`：事件发生时的互动分区；
- `in_reply_to_event_id / caused_by_event_id`：回应与因果；
- `SessionPartitionChanged`：merge/split 的历史连接。

输出至少保留：

```text
BroadcastPlan
  source_event_ids
  source_world_interval
  projection_mode
  viewpoint
  transition
  artistic_reason

RenderArtifact
  render_id
  source_event_ids
  based_on_world_version
  structured_beats[] {
    source_event_ids
    truth_kind: fact | quote | inference
  }
  compiled_webgal_script
  provenance_sidecar
  content_hash
```

Broadcast 可以省略、压缩、切镜和加入明确标记的演出推断，但不能：

- 制造 `coffee_ready`；
- 改写 Character 的原话；
- 改变 Event 顺序或因果；
- 将展示结果写回 World 或 Character Memory。

## 11. 当前实现与分支复用结论

当前主分支的 `agent_runtime/agent/director/` 仍只是边界占位，没有 EventStaff Runtime。`extensions/dynamic-render/` 仍消费 Fixture Timeline，尚未接入真实 WorldEvent feed。

`origin/mvp` 的 Director 会看到完整 Snapshot 和一轮未提交 Proposals，并负责 Segment Completion。这个输入范围和权力与 D-044 冲突，因此不复用其 Director request、strategy 或调度模型。

可以选择性复用的只有：

- strict/frozen contract 和判别联合写法；
- source/evidence/provenance 字段设计；
- SQLite transaction、唯一约束和 rollback 测试；
- committed Event 之后再做感知投影的原则；
- 重启恢复、重复消费和故障注入测试思路。

## 12. MVP 尚待验证

以下不是 Director 权限开放项，而是实现前必须用 Golden Trace 冻结的机制：

1. `world_time / next_check_at / earliest_release_at` 的具体时钟语义；
2. 每种 object process 如何生成 enqueue/release/cancel affordance；
3. 硬 deadline 的确定性 fallback；
4. 同一 subject 上互斥 Staff 的约束；
5. cancel 是否总生成可感知 WorldEvent；
6. 单 World 单 Director cursor 的并发和恢复测试；
7. merge/split 前后释放事件的收件人测试；
8. Director 模型失败、超时或输出越权时的 retry/keep 策略；
9. Broadcast 的真实 WorldEvent ingress、Viewer Cursor 和 Artifact 持久化。

首条验收 Trace 应至少覆盖：只说要煮咖啡不入队、实际启动后入队、未到时间拒绝 release、完成时原子提交、途中取消、Session merge、Session split、Director 重启和 Broadcast 来源追溯。
