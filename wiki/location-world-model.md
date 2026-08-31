# Location World Model

## 0. 状态与目标

- **状态：核心边界已确认，代码未实现；当前 NPC DIY PoC 只包含最小 World-owned 输入/输出类型。**
- **确认日期：2026-08-22。**
- **目标：**让地点成为可按世界版本查询的一等 World Model，维护地点的稳定身份、已提交事实、当前有效信息和发生于此的 Event 引用，避免 Agent 临场杜撰环境并造成前后口径漂移。

这里的 `LocationModel` 是确定性的世界数据模型与只读聚合视图，不是另一个 LLM Agent。只有 World Committer 能改变它；Character 和 Director 都只能查询或提出待校验变更。

证据边界：Stanford 原型中的 [`spatial_memory.py`](../../generative_agents/reverie/backend_server/persona/memory_structures/spatial_memory.py) 是每个 Persona 私有、从经历中学习出的地点层级，并不是全局权威的地点事实库；[`maze.py`](../../generative_agents/reverie/backend_server/maze.py) 则把地点语义硬绑在 tile 数据上。本文的 `LocationModel / LocationFact / LocationInfo` 是 MyGO 为无 Maze 世界新增的 **Decision**，不能表述成论文或原仓库已有实现。

## 1. 核心模型

```text
Location identity
  + append-only LocationFact revisions
  + append-only LocationInfo revisions
  + WorldEvent Ledger 的 location_id 索引
  = LocationView @ world_version / world_time
```

四类数据不能混在一起：

| 类型 | 含义 | 示例 | 权威来源 |
|---|---|---|---|
| `Location` | 地点稳定身份 | 羽丘高中、RiNG | 世界初始配置或正式创建事务 |
| `LocationFact` | 当前客观成立的地点事实 | 羽丘高中存在钢琴 | 已提交 Fact revision |
| `LocationInfo` | 与地点关联、具有有效期或周期的可发现信息 | Tomori 每周六在 RiNG 独自 Live | 已提交 Info revision |
| `LocationEventRef` | 当前或历史上发生于该地点的 Event 引用 | 本周六的实际 Live 已开始 | 从唯一 WorldEvent Ledger 按 `location_id` 派生 |

`LocationModel` 可以呈现 `active_event_refs / recent_event_refs`，但不能复制一份可独立修改的 Event。Event 正文、revision 和生命周期仍只存在于 WorldEvent Ledger。

## 2. 一期最小数据契约

```text
Location
  location_id / canonical_name / aliases / kind
  parent_location_id / timezone / created_world_version

LocationFactRevision
  fact_id / revision / location_id / key / value / operation
  previous_revision / effective_world_version / disclosure / discovery_channels
  source_kind / source_ref / evidence_ids / commit_seq

LocationInfoRevision
  info_id / revision / location_id / kind / subject_ids / predicate
  object_refs / summary / valid_from / valid_until / recurrence
  disclosure / discovery_channels / operation / previous_revision
  effective_world_version / source_kind / source_ref / evidence_ids / commit_seq

LocationEventRef
  event_id / event_revision / location_id / status / participant_ids
  started_at / ended_at / commit_seq

LocationView
  based_on_world_version / at_world_time / location
  active_facts / active_info / active_event_refs / recent_event_refs
```

实现时这些值使用统一 strict/frozen Pydantic Model：未知字段、隐式 coercion 和原地修改均被拒绝；pyright strict 检查静态引用。Pydantic 只能保证结构，Fact revision、disclosure 和 world-version 一致性仍由 World 领域逻辑校验。

一期 `FactValue` 只支持经过注册的 `bool / string / integer / entity_ref`，Fact key 也必须进入 registry。例如：

```text
location_id = haneoka_high_school
fact_id     = haneoka.facility.piano
key         = facility.piano.present
value       = true
revision    = 1
disclosure  = public
channels    = same_scene | told_by_character
```

没有该 Fact 表示 `unknown`，不能被解释成 `false`。只有正式提交“钢琴被搬走或已不可用”等变更后，新的 world version 才能返回 `false`；旧版本查询仍返回 `true`。

## 3. LocationInfo 与实际 Event

Tomori 决定每周六去 RiNG 独自 Live 时，角色的私有想法或单次模型输出首先只是 Proposal，并不自动成为地点信息。只有该计划被确认为世界中的承诺、安排或公告，并通过 Director Completion、Validator 和 Committer 后，才能追加：

```text
LocationInfoRevision
  info_id       = ring.tomori.solo_live
  location_id   = ring
  kind          = recurring_commitment
  subject_ids   = [tomori]
  predicate     = perform_solo_live
  object_refs   = []
  summary       = Tomori 每周六在 RiNG 独自 Live
  recurrence    = weekly / saturday / world_timezone
  disclosure    = public | restricted | private
  channels      = public_schedule | poster | word_of_mouth | targeted_message
  operation     = publish
```

这条 Info 表达“当前存在该周期安排”，并通过 `source_kind + source_ref` 关联世界初始配置或最初的承诺/公告 Transaction；它不是 Tomori 私有 Plan 的副本，也不代表每个周六的演出已经发生。

在 Tomori 还只是私下打算去 RiNG 时，该内容只能存在于其私有计划中；当她正式承诺、预约或公告后，LocationInfo 才成为公开世界中的 canonical record，Persona 计划只保留对 `info_id + revision` 的引用，不能再维护一份可独立修改的副本。每次实际发生仍要形成独立的 committed WorldEvent，例如：

```text
tomori_arrived(ring)
solo_live_started(tomori, ring)
solo_live_completed(tomori, ring)
```

修改或取消安排必须追加新的 Info revision，不能覆盖历史；`current / expired / historical` 是按 world version 和 world time 折叠出的查询结果。

## 4. 地点事实与角色认知必须分离

`LocationFact` 是客观世界事实，`KnownFact / Belief / Memory` 是某个角色的认知。二者不能因为共享了同一句自然语言而合并。

- 羽丘高中存在钢琴后，Agent 不能在需要客观成立的 Proposal 中无证据声称“学校没有钢琴”；Validator 应返回地点事实冲突。
- 尚未获知该事实的角色，其认知应是 `unknown`，而不是自动得到 `false`。
- 若剧情明确需要撒谎、误解或谣言，“没有钢琴”可以作为 `utterance / HeardClaim / Belief` 提交，但不能覆盖 `LocationFact`。
- Character 的 Proposal 引用地点事实时必须携带 `location_fact_ref` 或可追溯的 Observation/Memory evidence。
- LocationModel 不直接写入角色 Memory；角色只有经过合法的信息传播与感知，才获得自己的认知记录。

## 5. Director 的到访前查询与信息获知

每次角色形成前往某地点的有效意图后、该角色下一次基于目的地信息做决策前，Runtime 为 Director 提供只读查询：

```text
QueryLocationContext(
  location_id,
  visitor_agent_id,
  as_of_world_version,
  at_world_time
) -> LocationView + visitor_known_refs
```

完整链路：

```text
Character 提出 go_to(RiNG)
-> Validator 接受目的地与移动前提
-> Director Pipeline/strategy 的 mandatory LoadLocationContext 步骤以同一 world version 查询 RiNG LocationView
-> FilterDiscoveryCandidates 节点确定性去掉无效、已知和不可披露的 Info
-> 有候选时 Director 输出 NoOp 或 DiscoveryPlan
-> Validator 检查 Info revision、DisclosurePolicy、时间和传播渠道
-> 纯发现：Projector 从已提交 Info revision 生成 PerceptCandidate
   或因果传播：Committer 先提交 message/announcement 等 WorldEvent，再投影 Candidate
-> PersonAct.perceive 决定是否注意，返回 MemoryWriteIntent
-> 后续 PersonAct Decision 才能引用该信息
```

`DiscoveryPlan` 至少包含：

```text
source_location_id / source_info_refs / recipient_ids
delivery_timing / channel / visible_fields / evidence_ids
```

合法方式可以是公开日程、场所海报、朋友告知、定向消息、到场后听见演出或工作人员说明。Director 决定的是**传播机制和机会**，不是直接宣告“角色已经知道”，也不能代替一个有自主性的 Character 说话或发送消息。

`LoadLocationContext` 是 Director 内部必经的 typed Runnable/strategy 步骤，由 Runtime 注入 strict/frozen `LocationQuery`；它不是让模型自行决定要不要调用的开放 Tool。这样可以保证每次相关到访都查询同一版本的地点状态，同时无候选时在进入模型节点前直接返回 `NoOp`。当前 Director 尚未实现，这里描述的是一期契约。

Director 只能从该 Info 声明的 `DiscoveryChannels` 中选择传播方式，不能临场杜撰一个并不存在的海报、广播或知情人。若需要新建传播媒介，它本身先作为 World change 提交。

- 若公开网页或既有海报已经由 LocationInfo revision 证明存在，Projector 可直接生成引用该 revision 的 Candidate；角色是否注意属于私有 Observation，不必制造新的客观 Event；
- `targeted_message / friend_told` 若以具体 Character 为发送者，必须先获得该 Character 的 ActionProposal 或互动接受；Director 不能替其行动；
- `system_announcement_created` 等允许由世界规则或 Director Stimulus 产生的外部变化，也必须先提交 WorldEvent；
- `same_scene / poster_noticed / performance_heard` 等渠道必须等角色到场；可以预先规划，但只能在条件满足后投影；
- `DisclosurePolicy=private/restricted` 时，Director 不能为了剧情方便强行公开；
- 若角色已经拥有同一 Info revision，默认 `NoOp`，避免每次到访重复灌入；
- 如果确定性过滤后没有候选 Info，不调用 Director，直接继续到访流程。

地点的基础可见事实和可发现信息也要分开：角色到达羽丘高中后，钢琴是否在其可感知区域由 Fact 的 `DiscoveryChannels` 和 Projector 确定，不需要 Director 创造“钢琴存在”；Director 只在存在多种合理传播方式、叙事时机或披露选择时介入。

## 6. 写入与冲突规则

地点状态只有一条正式写入路径：

```text
Character / Director / System Proposal
-> LocationFactChange / LocationInfoChange
-> Validator
-> World Committer 原子提交 Transaction + revision
-> world_version / commit_seq 前移
-> LocationView 在新版本可见
```

一期至少守住：

1. 每个 `(location_id, fact_key)` 在任一 world version 最多一个 active revision。
2. `replace / retire / cancel` 必须携带当前 expected revision；并发修改只有一个成功。
3. 普通台词、Event summary、Director 草稿和 Agent Memory 都不能覆盖 LocationFact。
4. 同一 mutation ID、相同内容重复提交幂等；相同 ID、不同内容拒绝。
5. Director 查询与 Proposal 都绑定 `based_on_world_version`；版本已变化时重新查询，不能静默 last-write-wins。
6. Info 的 recurrence 只生成到期候选或唤醒点，不自动证明 occurrence 已发生。
7. Event 挂载只存引用；Location 与 Event Ledger 对同一事件不形成双写真相。
8. `perceptual_footprint` 是 Director 建议，Validator/Projector 可以收窄，不能扩大硬权限。
9. Prompt 中出现的地点描述只是输入或输出文本；只有合法的 Location change 经 Commit 后才能改变事实。
10. 只有造成外部状态变化的信息传播才新增 WorldEvent；对既有 LocationFact/Info 的个人注意写 Observation/Memory，避免 Event spam。
11. DiscoveryPlan 不得伪造具体 Character 的行为；需要某角色告知时，先创建可回应机会并由该 Character 提案。
12. 已公开的周期承诺由 LocationInfo 作为 canonical record；Persona 的计划、Scheduler wakeup 和 UI 展示只引用其 ID/revision，不维护可独立漂移的副本。

建议诊断码：

```text
LOCATION_FACT_CONFLICT
LOCATION_FACT_STALE_REVISION
LOCATION_INFO_STALE_REVISION
LOCATION_INFO_NOT_EFFECTIVE
LOCATION_DISCLOSURE_FORBIDDEN
LOCATION_DELIVERY_CHANNEL_INVALID
WORLD_VERSION_MISMATCH
```

## 7. 查询与存储边界

一期至少提供：

```text
GetLocation(location_id)
GetLocationView(location_id, as_of_world_version, at_world_time)
ListLocationEvents(location_id, before_commit_seq, limit)
ListLocationHistory(location_id, before_commit_seq, limit)
```

`GetLocationView` 返回不可变快照，供 Validator、Projector 和 Director 使用。Director 默认只读取 active facts、active info、active event refs 以及与当前 visitor 相关的 known refs；历史必须显式分页，不能把地点全部历史塞进 Prompt。

物理存储复用 World Ledger 的事务和索引能力。Location 不自建向量库；未来需要按自然语言搜索 LocationInfo 时，可通过 LangChain Retriever/VectorStore 的窄 adapter 适配现有索引，但权威结果仍回到稳定 `fact_id / info_id / event_id + revision`。

## 8. 一期范围与验收

一期实现：

- Location 稳定身份；
- 注册过的标量 LocationFact；
- `recurring_commitment` LocationInfo，只支持 weekly recurrence；
- WorldEvent 按 `location_id + commit_seq` 查询 current/history；
- `QueryLocationContext` 与 Director `NoOp / DiscoveryPlan`；
- append-only revision、world-version CAS、Fact/Info revision CAS；
- Fixture，不接真实模型。

明确延期：坐标、寻路、碰撞、任意 RRULE、节假日、模糊地点检索、自然语言矛盾检测、自动把周期 Info 当成已发生 Event，以及复杂地点 ACL/容量调度。

最小 Golden Trace：

```text
v0  羽丘高中 facility.piano.present=true
    RiNG 挂载 Tomori 每周六独自 Live 的 active LocationInfo
v1  Director 在 Anon 前往 RiNG 前查询 LocationView，提出合法通知渠道
    提交前 Anon 不知道该安排
v2  若 Director 选择 targeted_message，则先提交该 WorldEvent，Projector 只向 Anon 投影
    若选择读取已存在的公开日程，则 Candidate 直接引用 LocationInfo revision
    Anon 注意后才写入自己的 Memory
v3  某角色声称“羽丘高中没有钢琴”
    该 Claim 可以存在，但 LocationFact 仍为 true
v4  正式 piano_removed Transaction + fact replace
    当前 Fact=false；查询 v0-v3 仍为 true
```

验收还包括：重复到访不重复写同一 Info revision、restricted Info 不泄漏、`same_scene` 在到达前被拒绝、周期到点不自动生成 Event、并发 fact replace 只有一个成功，以及 Ledger 重放得到相同 LocationView。
