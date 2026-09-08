# 核心持久化模型

本页暂时只记录已经讨论确认的核心模型。字段、表名和索引在实现前另行定义。

## 1. 存储边界

```text
JSON                运行前的人物与世界定义
Skill               人物、导演、导播可插拔的设定
SQLite              运行后实际发生的世界历史、角色记忆和执行记录
```

- 世界事实和角色主观认知必须分开。
- 运行历史采用追加和 revision，不直接覆盖旧记录。
- SQLite 可以建立当前状态 View，但 View 不是新的权威数据。
- Embedding、FTS 和 `sqlite-vec` 只是可重建索引。

## 2. 人物

人物由五部分组成，不放进一张通用 Entity 状态表。Character Skill 与 Character Presentation 的详细分工见[Character Skill 当前设计](character-skill-design.md)。

### Character Definition

人物稳定身份。

```text
character_id / name / aliases
skill_ref / asset_refs
```

- 源：JSON。
- SQLite 只登记版本和内容 hash，供历史运行引用。

### Character Skill

人物相对稳定的人格和行为策略，例如价值观、语言风格、关系策略、注意力与行为倾向。

- 源：版本化 Skill 文件。
- SQLite 记录每次生成实际使用的 Skill 版本和 hash。
- Skill 不是人物当前状态，也不是人物记忆。

### Character Presentation

人物跨场景相对稳定、可被周围角色感知的外部特征。

```text
appearance / demeanor
voice / observable_traits
```

- 源：Scenario Seed；提交后随 Character Entity Revision 持久化。
- PerceptionProjector 只向同一 Interaction Scope 内可感知该角色的参与者投影。
- Presentation 提供形成第一印象的线索；具体第一印象属于观察者自己的 Belief。
- 当前衣着、受伤和一次发言的声音变化属于版本化状态或 World Event。
- 具体素材文件仍由 Asset Manifest 持有。

### Character World Presence

世界需要统一判断的人物客观外部状态。

```text
character_id / revision
location_id / physical_state
observable_action / occupied_resources
active_event_session_ids
effective_world_version / source_world_segment_id
```

- 存储：SQLite revision。
- 来源：通过校验并提交的世界变化。
- 可由 World Ledger 重放和归约。
- 不包含私有情绪、目标、计划、信念或记忆。

### Character Memory

人物实际感知、记住、相信或反思的主观历史。

```text
memory_id / character_id / memory_type
content_text
source_observation_ids / source_world_event_ids
importance / confidence / emotion
status(active|resolved|superseded|expired)
created_world_version / valid_until_world_version?
```

- 存储：SQLite append-only Ledger。
- 文本记忆以 `content_text` 保存。
- 情绪、目标、计划、信念、承诺和主观关系也属于 MemoryRecord。
- “人物当前内部状态”是对当前有效 Memory 的查询结果，不另设 Cognitive Snapshot。
- Memory 是人物主观事实，不能充当客观世界事实。

## 3. 地点与物品

### Location

地点是独立世界实体：

```text
Location              稳定身份
LocationFactRevision  客观地点事实及其版本
LocationInfoRevision  有效期、周期和披露范围内的可发现信息
```

- 初始定义来自 JSON。
- 运行后的 Fact/Info revision 存入 SQLite。
- 地点规则属于 World Policy/Validator，不属于 Character Skill。

### Object / Resource

只有会移动、占用、消耗、损坏或参与因果关系的物品才建立独立实体。

```text
entity_id / entity_kind / revision
state / location_id / owner_id
effective_world_version / source_world_segment_id
```

- 存储：SQLite revision。
- 普通背景装饰可以只作为 LocationFact 或 Render 素材。

## 4. 互动与事件

### EventSession

持续互动的容器或 interaction boundary。

```text
event_session_id / interaction_mode
participants / location_id? / channel_id?
status / scheduler_cursor / next_wakeup_at
```

`interaction_mode` 可以是线下同场、线上同步、线上异步或混合互动。

### WorldEvent

已经提交的具体客观事件。

```text
world_event_id / event_session_id?
event_type / participants / location_id
world_time / facts / evidence_refs
source_world_segment_id
```

- 一个 EventSession 可以包含多个 WorldEvent。
- 环境或系统事件可以不属于任何 EventSession。
- Memory 中的 `source_world_event_ids` 指 WorldEvent，而不是 EventSession。

## 5. 世界总账

### Entity Revision

Character World Presence、Location Fact/Info 和 Object State 各自用 revision 保存已提交状态。

`StateDelta` 不作为独立持久化模型。提交前的候选变化可称为 `StateMutation`；提交后由各实体 revision 表达最终状态。

### WorldSegment

一次原子世界提交的事务边界。

```text
world_segment_id / world_version / commit_seq
world_time_start / world_time_end
revision_refs / evidence_refs
```

同一 WorldSegment 内的多个实体 revision 必须全部成功或全部失败。

### World Ledger

世界已提交历史的总称：

```text
World Ledger
├── WorldSegment / Transaction Ledger
├── Entity Revisions
└── WorldEvent Ledger
```

- WorldSegment 说明一次原子提交包含什么。
- Entity Revision 说明对象提交后的状态。
- WorldEvent 说明这次提交在剧情语义上发生了什么。
- World Ledger 是客观世界的唯一权威。

## 6. Agent 产出

```text
Character Agent -> ActionProposal
Director Agent  -> DirectorResolution
Assembler       -> SegmentDraft + deterministic StateMutation candidates
Validator       -> ValidatedCommitPlan / diagnostics
Committer       -> WorldSegment + Entity Revisions
Event Recognizer-> WorldEvent
Broadcast Agent -> BroadcastPlan
Render Planner  -> RenderJob
```

- Character 决定角色想做什么，不能替其他角色决定结果。
- Director 只补全相对时长、结果、对象变化和环境事件候选；Runtime 的 Assembler 注入权威字段并构造内部 Draft，二者都不能直接提交事实。
- Validator 是无状态规则组件；只持久化必要的校验结果和诊断。
- Committer 是世界事实的唯一写入入口。

## 7. 最小重放要求

- 世界重放：从 WorldSegment 和 Entity Revision 恢复指定 `world_version`。
- 事件重放：按 WorldEvent Ledger 恢复客观剧情历史。
- 角色认知重放：按 Observation 和 Character Memory 恢复角色主观历史。
- Agent 调试重放：保存实际输入引用、Skill 版本、检索出的 Memory IDs、结构化输出、耗时和校验结果；重新调用模型不算重放。
