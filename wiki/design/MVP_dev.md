# MVP 完善开发计划

> 状态：执行基线（Working Plan）
> 最后更新：2026-09-08
> 当前实现基线：当前 worktree
> 可复用代码基线：`origin/mvp@05c5404`
> 相关文档：[Agent Runtime 一期落地方案](../agent-runtime-implementation.md)、[决策记录](../decisions.md)、[未决问题](../open-questions.md)、[难点账本](../difficulty-ledger.md)

本文用于把当前已经实现的 PersonAct 单角色认知链，补成一个可持久化、可恢复、支持多个 `EventSession` 及角色组合变化的可运行 MVP。本文只冻结足以开工的边界；标为“待讨论”的内容不是既成设计。

本文区分已确认决策、实施建议和待讨论问题。Project 独立 SQLite / WorldRef 模型、固定 EventSession 节点与 UnionPart、Director/Broadcast 分权及 Codex 外置制作已确认。第 6.5 节的“手动停止、保存、下次加载同一 World 继续”是明确需求，具体协议作为实施默认值；自动结局规则仍是待讨论建议。设计确认不代表代码已实现。

2026-09-08 M1 实现校准：Gateway 严格 JSON 解析、WorldRef 进程内归属、AgentView 一次性迁移、普通 Plan queue 和纯 UnionPart 已实现。执行进度以 [dev_plan_MVP.md](dev_plan_MVP.md) 为准，实际接口、文件与测试证据见 [M1_dev_log.md](M1_dev_log.md)。本页后续 Phase 仍是领域规划，不表示 SQLite、自由互动或存档续跑已经完成。

## 1. MVP 要证明什么

MVP 的关键不是“Agent 能生成一句话”，而是以下闭环可以连续运行：

1. 同一个 `EventSession` 中，角色 A 的发言先成为已提交、可读且可回查的 `DialogueEntry`（`EventEntry` 的一种）。
2. 角色 B 下一次决策时，通过自己的 `AgentView` 看见这条直接互动；B 可以回应、拒绝、延后或不行动。
3. B 的选择再次经过世界校验与原子更新，不能直接改写 A、其他 Agent 或世界事实。
4. 角色离开、加入、拆组或合组时，只更新固定 `EventSession` 节点的 `rootSessionId`；角色数量不变时，Session 节点数量也不增长。
5. root 不同的两个 EventSession 分区可以独立推进；通过 `UnionPart.merge/split` 改变组合后，调度仍能继续。
6. 进程重启后，可以从 SQLite 恢复同一个世界版本、root 分区、可运行互动组顺序和角色私有状态，并继续生成。
7. 玩家切换观看焦点只改变 Render/Broadcast 选择，不改变世界推进结果。
8. 不同 `project_id` 使用不同 SQLite 文件；即使两个项目复用相同的 `world_id / agent_id / session_id / entry_id`，World、Entry、Session 和 Memory 也不能交叉读取或写入。

首条验收场景固定为：

- `session-anon` 作为 root，其分区成员为 Anon/Soyo；Anon 向 Soyo 发言，Soyo 在后续 turn 中收到明确的待回应互动并作出选择；
- `session-tomori` 起初是 Tomori 的 singleton root，不会看见 Anon/Soyo 的私有互动；
- Tomori 自主选择加入后执行一次 merge，Anon 去买咖啡时执行一次 split，全程仍只有固定的角色 Session 节点；
- 重启 Runtime，验证 root mapping、成员集合、世界版本、Entry 历史和下一调度对象保持一致；
- 已提交事件最终能形成带来源关系的 `BroadcastPlan`；MVP 将它与当前项目素材一并交给进程外 Codex 完成素材配装和 WebGAL 脚本/JSON 转译，再由现有项目校验与构建工具消费。

### 1.1 当前起点

当前 worktree 已具备：

- strict/frozen `ActionProposal` 及六类 action union；
- `PersonActAgent -> PersonActLoop` 的 prepare/perceive/retrieve/plan/propose；
- 进程内 Memory、attention、novelty 与 Proposal 权限校验；
- Character Skill、Model Gateway、Fixture/模型 Cognition Strategy；
- M1 新增 WorldRef 归属校验、AgentView、普通 Plan queue 和无业务语义的 UnionPart；
- 现有动态 Render 制作与接入基础。

当前仍缺：

- `project_id -> 独立 SQLite 文件` 的硬隔离；WorldRef 进程内传播和拒绝测试已在 M1 实现，不代替数据库隔离；
- 面向作品的 `ScenarioSeed`、世界初始化与 Runtime 装配入口；
- 世界事实与 Agent Memory 的 SQLite 持久化；
- `WorldUpdater`、世界版本、原子事务和恢复协议；
- `AgentViewBuilder` 的完整硬可见性过滤；
- 可实际运行的 EventSessionRunner、互动回应和 Session 重组；
- pending `EventEntry` 的 Director 管理、commit feedback、必要 Trace 关联和完整端到端 Golden Trace；
- 世界运行的暂停/继续/终止、剧情目标与结局判断；现有 `wait/no_op` 只是角色动作，不能代替 Runtime 生命周期。

### 1.2 开场初始化与背景信息

Agent 自由互动需要一个明确的开场初始化环节，但它初始化的是“世界在 t=0 时是什么样”，不是预写后续台词和行动的线性剧本。当前 `authoring/story.json` 与 `projects/*/story.json` 属于 Render/制作层的静态故事，不是 Agent Runtime 的世界初始化输入；当前源码也还没有正式的 `ScenarioSeed` loader 或 bootstrap。

必须区分四类背景：

| 背景类型 | 示例 | 来源与归属 |
| --- | --- | --- |
| 模型常识 | 咖啡店通常可以买咖啡、人在对话时可以拒绝回应 | 主要依赖模型基础能力；会影响合法动作的条件仍必须由 World Fact/Affordance 明示，不能拿模型猜测当世界事实 |
| 世界客观背景 | 当前时间、地点、物体、公开发生的事件、初始 Session 分区 | `projects/<project-id>/scenario.yaml`，初始化为 World 公共事实 |
| 全员已知背景 | 五人共同经历过的历史、所有角色开场前都知道的公开设定 | Scenario 的 knowledge assignment 指向 all agents，bootstrap 展开成各 Agent 自己的初始 Memory |
| 角色专属信息 | 私人经历、秘密、误解、只对某角色成立的已知信息 | 角色稳定部分来自 `agents.json`；场景特有 assignment 显式列出接收者，只初始化到对应 Agent 的私有 Memory |

`ScenarioSeed` 至少包含：

```text
project_id / seed_id / version / world_time
locations / objects / public_facts
agent initial locations and public states
stable EventSession nodes and initial root partitions
scenario knowledge assignments: all agents or explicit recipients
agent manifest / Character Skill references
```

Scenario 可以声明“Anon 与 Soyo 开场正在咖啡店交谈”，但不能声明“Tomori 第三轮必须加入”或“Anon 第五轮必须去买咖啡”；后者必须由 Agent Proposal 和已提交世界状态共同产生。

### 1.3 已确认的主链路与剩余设计

以下按 2026-09-08 的讨论整理。六项均已有职责和数据方向，其中部分执行规则仍需冻结；源码尚未形成完整 Agent World Runtime。

| 能力 | 已确认设计 | 仍需完成的设计细节 |
| --- | --- | --- |
| 项目隔离 | 每 Project 独立 SQLite；单行 `project_database` 绑定文件身份，`WorldRef(project_id, world_id)` 贯穿运行边界 | 按第 4.0 节实现及验证；本轮用户已认可该模型 |
| Agent EventSession 隔离与合并/拆分 | 每 Agent 一个稳定节点，`UnionPart` 保存当前分区；自主加入不需要组员批准，不欢迎通过角色行为表达，退出由角色自己决定 | 行为到 join/leave 的稳定映射、重组后的调度公平性 |
| Agent 选择可见对象交互 | `AgentViewBuilder` 硬过滤可见性，World 提供 affordance，角色自行选择 ActionProposal | 稳定 operation/affordance ID、请求回应与唤醒规则 |
| 导演发布客观 Entry | Director 读 StoryLine，按互动进展检查 pending Entry 并选择发布时机、方式和 audience；角色期间可继续交谈 | 检查间隔、合法环境操作与恢复测试；不包含替角色决定行动或修改 committed Entry |
| 导播挑选与编排 Entry | Broadcast 读同 World 的多条 StoryLine，以 PresentationBinding 安排呈现顺序/对齐 | watermark、迟到 Entry 和选材策略 |
| 导出挑选后的故事线 JSON | 导出可读 `BroadcastPlan`，包含来源 Entry、Line 关系与呈现顺序，绑定 WorldRef | 最小导出 schema 与来源完整性校验；Worker/素材配装/WebGAL 转译由进程外 Codex 承担 |

在此链路之前还需要 Scenario bootstrap，在运行期间需要原子提交、Memory 恢复和有界调度。新增的暂停/终止与自然收尾问题见第 6.5 节，不能把上述六项理解为所有运行规则已经设计完毕。

## 2. 统一命名与职责

下面使用更贴近职责的名称。旧文档和当前代码中的历史命名先通过映射理解，不在本次文档维护中批量重命名源码。

| 历史名称 | 本计划名称 | 一句话职责 |
| --- | --- | --- |
| `World Ledger` | MVP 不保留独立同义对象 | 当前关系表保存权威状态，`event_entries(status=committed)` 保存已发生故事历史；不再复制通用 Change Log 和同义 Event 表 |
| `World Event Ledger` | `EventEntry` / `event_entries` | 从已校验世界变更中形成、可供 Character 感知和 Director/Broadcast 召回的统一 Entry |
| `EventStaff` | `EventEntry(status=pending)` | 尚未发生、等待 Director 管理的客观环境 Entry；发生后同一 `entry_id` 转为 committed |
| `World Committer` | `WorldUpdater` / `World.apply_change()` | 校验版本后，在一个数据库事务中更新所有世界权威状态 |
| `PerceptionProjector` | `AgentViewBuilder` / `World.build_agent_view()` | 从已提交世界状态中，为指定角色构建硬可见的局部视图 |
| `PerceptionFrame` | `AgentView` | 某角色基于某个世界版本可读取的不可变输入 |
| `World Snapshot` | MVP 不持久化逐版本 Snapshot | 重启直接读取关系型当前状态；出现历史版本查询或恢复性能证据后再增加 checkpoint |

### 2.1 通用分区结构 `UnionPart[T]`

`UnionPart[T]` 是不包含任何 Agent、EventSession、World、SQLite 或调度语义的通用数据结构，计划放在 `agent_runtime/common/union_part.py`。它表达一个有限集合当前被划分成哪些互不相交的部分。

```text
UnionPart[T]
- rootByItem: item -> root
- membersByRoot: root -> set[item]
```

唯一改变结构的操作是：

```text
merge(keepRoot, mergedRoots)
split(root, parts)
```

只读查询是：

```text
rootOf(item)
membersOf(root)
connected(left, right)
```

通用不变量：

1. 每个 item 恰好属于一个 part；
2. 每个 part 恰好有一个 root，且 root 必须属于该 part；
3. root 指向自己，其他 item 直接指向 root，不维护多层 parent tree；
4. `rootByItem` 与 `membersByRoot` 必须互相一致；
5. `merge` 显式指定保留哪个 root，底层结构不猜测业务优先级；
6. `split` 必须完整且无重复地覆盖原 part 的所有成员；
7. `membersOf` 对外返回只读集合，调用方不能绕过 `merge/split` 修改成员。

因此它不是标准的只增 Union-Find，而是支持显式重新分区的扁平结构。当前只有五个 Agent，merge/split 时重标受影响节点并同步维护一次成员集合即可，不需要 path compression 或动态连通算法。具体 EventSession 层采用继承还是组合，留到实现该层时再决定；`UnionPart[T]` 本身不提前加入业务 hook。

### 2.2 `PersonActLoop` 与 `CognitiveController`

概念关系如下：

```text
未来可能的 CognitiveController
├── PersonActLoop（当前已经实现）
│   ├── attention
│   ├── novelty filter
│   ├── memory write / retrieve
│   ├── plan
│   └── propose
├── observe_outcome / reflect（尚未完成）
└── 角色或 Agent 类型特异化策略（按实际重复出现后再抽取）
```

`CognitiveController` 的确比 `PersonActLoop` 更宽，也可能针对 Character、Director、Broadcast 或不同角色形成不同策略。但是 MVP **不新增** `CognitiveController` 基类、注册中心或按角色划分的 Controller 子类：

- 当前真实执行对象继续是 `PersonActAgent -> PersonActLoop`；
- 角色差异继续由 `CompiledPersonActSpec + Character Skill + CognitionStrategy` 注入；
- `AgentViewBuilder` 只裁剪硬可见信息，不做注意力、新颖性判断或 Memory 写入；
- 等 `observe_outcome / reflect` 落地，或至少两类 Agent 出现真实重复控制逻辑后，再决定是否抽取 `CognitiveController`。

### 2.3 初始化代码与内容的目录归属

初始化横跨 World 公共状态、Agent 私有状态和 EventSession，因此不能全部塞进 `world/`。目标目录按真实所有权组织：

```text
agent_runtime/
├── bootstrap.py                     # 启动编排：加载指定 Project 的 Scenario/Manifest，组装 World、Agents、Runner
├── scenario.py                      # 跨域 ScenarioSeed contract 与 loader；不实现 World/Agent 业务
├── sqlite.py                        # 按 project_id 创建独立 SQLite engine/session/transaction；禁止全局共享 Runtime DB
├── common/
│   └── union_part.py                # 无业务语义的 merge/split 分区结构
├── world/
│   ├── contracts.py                 # World-owned 输入/输出契约
│   ├── state.py                     # Location/Object/WorldFact/AgentWorldState 公共状态
│   ├── storage.py                   # World 关系表与查询；复用顶层 SQLite session
│   ├── initializer.py               # 只物化 Scenario 的公共 World 部分
│   ├── updater.py                   # 校验并原子更新当前状态、Session root 与 EventEntry
│   ├── entries.py                   # EventEntry pending/committed/cancelled 生命周期与查询
│   └── view_builder.py              # 公共事实 -> 指定 Agent 的硬可见 AgentView
├── event/
│   ├── session.py                   # EventSessionNode 与 UnionPart 的业务组合
│   ├── story_line.py                # committed EventEntry -> StoryLine/StageView 只读组织
│   ├── runner.py                    # 选择互动分区和下一决策角色
│   └── director_runner.py           # 消费 committed cursor/到期 pending Entry，协调 Director 与 World
└── agent/
    ├── personact/                   # Persona/Skill/决策与稳定角色配置
    ├── director/                    # DirectorStageView -> DirectorDecision；不含 World 写入
    ├── broadcast/                   # BroadcastStageView -> BroadcastPlan；只读 World
    └── memory/                      # 私有 Memory 及其存储；MVP 首先供 Character 使用

projects/<project-id>/
├── project.json                     # 现有作品/Render 配置
├── agents.json                      # 本作品的角色实例、Persona、Skill 引用和稳定记忆
├── scenario.yaml                    # 开场公共世界 + 全员/指定角色的知识分配
└── story.json                       # 现有静态 Render 故事；与 scenario 不混用

.runtime/<project-id>/
└── world.sqlite                     # 该 Project 独占；库内可保存多个 world_id

content/skills/characters/           # 可跨作品复用的版本化 Character Skill
```

`bootstrap.py` 负责首次创建与已有存档加载的分流。必须验证目录名、`project.json.id`、`agents.json.projectId` 和 `scenario.yaml.projectId` 完全一致，再打开由该 `project_id` 唯一推导出的数据库路径。以下初始化链只用于首次创建；已有 World 使用第 6.5 节的 load/resume 流程，不重新写入任何 Seed：

```text
resolve project_id -> projects/<project-id> + .runtime/<project-id>/world.sqlite
-> load ScenarioSeed + compile agents
-> 完整校验公共引用、Agent 接收者与 Memory ID
-> 在一个初始化 transaction 中：
     WorldInitializer 写入公共世界、初始位置和 Session root
     MemoryStore 写入各 Agent 的稳定 seeds 和分配给它的场景知识
-> 构造 EventSessionRunner 并开始第一轮
```

World 不读取 Character 私有 Memory，Agent 也不能直接读取完整 Scenario。Scenario 中的 knowledge assignment 由 bootstrap 按 `all agents` 或显式接收者展开到各自 `agent/memory`，公共客观部分才交给 `world/initializer.py`。两边共享初始化 transaction 只是为了避免半初始化存档，不改变领域所有权。同一内容分别进入多个 Agent Memory 是认知归属，不是重复的 World 权威事实。

## 3. 目标运行链路

```text
EventSessionRunner
  从 UnionPart roots 选择互动分区与下一决策角色
        |
        v
World.build_agent_view(agent_id, root_session_id, world_version)
        |
        v
AgentView
        |
        v
PersonActAgent.decide(...)
        |
        v
ActionProposal                 只是角色意图
        |
        v
WorldChangeValidator
        |
        v
World.apply_change(...)
        |
        +-- Location/Object/AgentWorldState 当前行
        +-- EventSession root binding merge / split
        +-- InteractionRequest / pending EventEntry current state
        +-- event_entries（status=committed 的公共历史）
        +-- worlds.current_version / world_time
        |
        v
Committed EventEntry
        |
        +--> Agent feedback / 下一轮 AgentView / Render
        |
        v
DirectorRunner -> DirectorView（只读 StageView + pending Entry 客观状态）
        |
        v
DirectorDecision              emit / schedule / keep / release / cancel / no_op
        |
        +--> emit：World 校验 audience 后直接提交 EnvironmentEntry + recipient snapshot
        |
        +--> schedule/keep：创建或更新 pending EventEntry 与 Director cursor
        |
        `--> release：WorldChangeValidator -> World.apply_change()
                     -> pending EnvironmentEntry 原子转为 committed -> AgentViewBuilder
```

必须保持的权威边界：

- Agent 只提交自然语言语义的 `ActionProposal`，不能填写原始 Session ID 来决定加入哪个组；
- Character Proposal 的校验与提交不经过 Director；Director 不能补写、重排或解释角色行为；
- Director 读取由 committed Entry 构成的全局 `StageView` 和 pending EventEntry 所需客观状态，并返回受限的 `DirectorDecision`；能看见所有故事线不代表能改写角色或直接写数据库；
- `AgentViewBuilder` 只读取已提交版本，未提交 Proposal 不能成为任何人的可见输入；
- 只有 `World.apply_change()` 能推进 `WorldVersion`、原子更新 Session root binding 并追加已提交 Entry；
- Render/Broadcast 只消费由 persisted Entry 组织得到的 `BroadcastStageView`，不能反向修改 World。

## 4. SQLite 与最小持久化模型

SQLite 是 Runtime 的本地持久化 Store，不是 `World` 领域对象自身携带的数据库。它的价值是为多张关系表提供 transaction、唯一约束、外键、版本检查、顺序查询和进程重启恢复；如果最终只保存一个完整 `snapshot_json`，则应直接使用原子 JSON 存档，而不引入关系数据库复杂度。

### 4.0 Project 数据库是硬隔离边界

已确认采用“一 Project 一 SQLite”：

```text
project_id = coffee-golden
project source = projects/coffee-golden/
runtime DB = .runtime/coffee-golden/world.sqlite

project_id = another-story
project source = projects/another-story/
runtime DB = .runtime/another-story/world.sqlite
```

`world_id` 不是 `project_id` 的替代品，而是同一 Project 数据库中的一份独立发展/存档。MVP 不把 `run_id` 引入领域模型；进程重启继续同一个存档时仍使用同一 `world_id`，未来如需调用链诊断再增加仅供 Trace 使用的 execution ID。

硬性不变量：

1. Runtime 的所有打开入口必须显式接收合法 `project_id`，数据库路径只能由受校验的 `project_id` 在配置的 runtime root 下推导，不能由调用者传入任意 DB 文件；
2. 每个 Project 创建独立 SQLAlchemy `Engine/SessionFactory`，禁止使用跨 Project 的全局 Engine、连接或 Repository singleton；
3. 每个数据库包含且只包含一条 `project_database` 身份行；其 `project_id` 和 `worlds.project_id` 必须与打开数据库时的 `project_id` 一致。复制或错配数据库文件时立即拒绝，而不是继续运行；
4. 库内所有状态表通过 `world_id` 外键归属于 `worlds`。因为 SQLite 文件本身已绑定 Project，子表不重复保存 `project_id`；跨组件数据则必须携带完整 `WorldRef`；
5. 同一 Project 可以有多个 `world_id`；不同 Project 可以安全复用相同的 `world_id / agent_id / session_id / entry_id`；
6. Repository/Store 在构造时绑定一个 `WorldRef`，查询 API 不允许调用方临时省略或替换 World 条件；
7. `.runtime/` 是生成状态并进入 `.gitignore`；测试必须使用临时 runtime root，不触碰开发者的正式存档。

只新增一个跨边界身份值对象，不为隔离制造额外业务层级：

```text
WorldRef
- project_id
- world_id
```

`WorldRef` 是 strict/frozen contract，进入 `AgentView`、`ActionProposal`、`WorldUpdatePlan/Result`、`StageView`、`DirectorView/Decision` 和 `BroadcastPlan`。`EventSessionRunner`、`WorldUpdater`、`AgentViewBuilder` 与 Memory Store 都由同一个 `WorldRef` 构造；收到不匹配的 Proposal/Decision 必须在任何数据库写入前拒绝。

此外只调整已有模型：

- `ScenarioSeed` 增加 `project_id`；`worlds` 增加 `project_id`；两者与 `agents.json.projectId`、目录名必须一致；
- 当前 `CompiledPersonActSpec.memory_scope = project/<project>/persona/<agent>` 不能承担 World 隔离。持久化 Memory 使用 `world_id + agent_id + memory_id` 关系约束，Store 再绑定 `WorldRef`；`namespace` 只表达记忆类别，不再充当安全边界；
- 不新增 `Run`、`ProjectWorld`、全局 `ProjectRegistry` 或每张表重复的 `project_id` 字段；Project 路径绑定、`worlds.project_id` 自检和 `WorldRef` 已足够。

`project_database` 不是新的业务聚合，只是 SQLite 文件的单行身份证，防止文件被复制到另一个 Project 路径后静默打开：

```text
project_database
- singleton_id = 1          // PK + CHECK，保证只有一个身份槽位
- project_id                // UNIQUE
- created_at

worlds.project_id -> project_database.project_id
```

首批 schema 采用明确的关系表：

- 需要独立校验、关联、过滤、排序或唯一约束的字段必须成为 SQL 列；
- 只有随事件类型变化、不会被独立查询的细节，才允许放入经 strict/frozen Pydantic 校验的 JSON payload；
- Persona、Character Skill 和 Prompt 正文继续作为版本控制中的配置资产，不复制到 World 表；World 只按需要保留稳定引用或 hash；
- 关系型当前状态是重启恢复的权威来源，`event_entries` 同时保存立即提交的互动 Entry 和待完成的客观 Entry；只有 `status=committed` 的行才构成可读、可召回的公共历史。MVP 不再另建内容重复的 Event/Queue 表，也不维护 change、revision 和逐版本完整 snapshot 三套重复历史。

MVP 最小表集：

| 表 | 最小用途 | 关键关系 |
| --- | --- | --- |
| `project_database` | 当前 SQLite 文件的单行 Project 身份 | `singleton_id=1`；打开文件时必须与请求的 `project_id` 相等 |
| `worlds` | 身份、世界版本/时间、Seed 来源、Director cursor；角色回合与导演检查进度；第 6.5 节的运行状态、epoch、调度序号和调用额度 | 库内每行必须属于该 SQLite 唯一绑定的 Project；作为世界更新和暂停保存的事务入口 |
| `locations` | 地点稳定 ID、名称和开场描述 | Agent/Object 的 `location_id` 外键目标 |
| `world_facts` | 作品内公开、客观的开场事实和当前事实 | 可选关联 Location/Agent/Object；只存 World 公共知识 |
| `agent_world_states` | Agent 当前地点和最小公开状态 | `agent_id -> location_id`；不包含 Persona 私有 State/Memory |
| `objects` | 物体身份、地点、所有者和当前状态 | 关联 Location/Agent；只为 Golden Trace 建必要字段 |
| `event_sessions` | 稳定节点与 root/topology；保存 `last_scheduled_seq / next_wakeup_world_time / consecutive_no_op` | root 自外键；成员由 root 派生，调度进展绑定稳定节点以支持读档和重组 |
| `event_entries` | Dialogue/Action/Environment/Join/Leave Entry 及其 `pending / committed / cancelled` 生命周期 | Character 结果直接 committed；未来客观结果可先 pending；只有 committed 对 Character/Broadcast 可见 |
| `event_entry_links` | Entry 间的 `previous / reply / cause` 有向边 | 用外键保存 StoryLine 顺序、对话回应、因果与 merge/split 交汇，不把多值关系塞进任意 JSON |
| `event_entry_recipients` | committed Entry 在发生时实际告知到的 Agent ID 快照 | Character 可见性的最终依据；Session 后续 merge/split 不能扩大旧 Entry 的接收者 |
| `interaction_requests` | 当前仍可由某个 Agent 处理的回应或邀请；不是入组审批 | 以发起 Entry 为来源，以解决 Entry 关闭；供 AgentView 与 Scheduler 重启恢复 |
| `agent_memory_records` | Agent 私有 Memory | `world_id + agent_id + memory_id` 隔离并引用 `source_entry_id`；`namespace` 只分类，由 Agent Memory 边界写入 |
| `agent_runtime_states` | 当前 PersonaState、state revision、角色 spec digest、观察游标、最近完成的决策 ID | `PK(world_id, agent_id)`；第 6.5 节恢复角色计划/冷却等所必需，Director/Broadcast 不可见 |

如首条 Golden Trace 证明一个 Entry 必须结构化关联多个参与者，再增加 `event_entry_participants(entry_id, agent_id, role)`；在此之前使用单 actor/target 和 Join/Leave 的 strict details 语义，不预建多对多表。

库内 World 隔离也必须依靠关系约束，而不是只靠每条查询记得加过滤条件：实体表使用 `(world_id, entity_id)` 复合主键，跨表引用同时携带 `world_id`。例如 Location 外键是 `(world_id, location_id)`，Entry link 两端是 `(world_id, entry_id)`，不能只引用裸 `location_id/entry_id`；因此相同内部 ID 可以在两个 World 中合法重复，却不能形成跨 World 外键。

一次 `World.apply_change()` 的公共部分包含以下原子动作；第 6.5 节的 Runner 以同一 transaction 协调 Agent-owned Store 和调度进度写入：

```text
校验 WorldRef、status=running、control_epoch、current_version == expected_world_version
-> 更新受影响的 agent/location/object 当前行
-> 更新受影响的 EventSession root binding
-> 新增 committed EventEntry，或将一个 pending EventEntry 原子转为 committed/cancelled
-> 追加相应 event_entry_links，并物化 committed Entry 的 recipient snapshot
-> 创建或解决相关 interaction_requests
-> worlds.current_version + 1 并推进 world_time
-> Agent Store 写暂存私有结果，Scheduler 写进度（第 6.5 节）
-> 整体 commit
```

进程重启直接从这些当前行恢复 World 和 `UnionPart`。MVP 暂不承诺“仅靠事件还原任意历史版本”；确实出现历史版本查询、长日志恢复性能或独立审计需求后，再增加 typed effect log 或周期 checkpoint，而不是默认每一步复制一份完整 World。

以下暂不进入首批 schema：

- `world_changes / world_versions / entity_revisions / world_snapshots`：与当前状态和 `event_entries` 重复；属于 `origin/mvp` 的重 Event Sourcing 设计，本 MVP 明确不搬；
- `generation_batches / generation_waves`：属于另一个分支的 lockstep 调度模型；
- `broadcast_runs / renders / broadcast_dispositions`：现有 Render 边界尚未需要这一整套数据库模型；
- `generation_traces`：先保留进程内有界 Trace，等隐私和恢复需求明确后再持久化；
- `event_session_members`：当前成员集合由 root binding 唯一决定，Runtime 使用 `UnionPart.membersByRoot` 缓存，不再持久化第二份成员事实；
- successor/lineage 表：merge/split 不创建新 Session；root mapping 的变化作为带 `world_version` 的 `JoinEntry/LeaveEntry` 及 Entry graph 边保存；
- 独立的 runnable queue projection 表：MVP 可从 `root_session_id = session_id` 的 root 节点集合派生并确定性排序；
- 仅以 `responder_id` 为键、无法指向发起 Entry 的 pending-response 表：不能回答“在回应哪件事”，明确不采用。

### 4.1 Agent 互动的持久化边界

互动不会按 EventSession 各写一份 conversation JSON，也不会把 Prompt、Proposal、Memory 和 WebGAL Render 混成同一条日志。MVP 将五类信息分开，但“已经发生”和“等待发生”的客观事件统一使用同一个 `EventEntry` 模型：

| 层 | 持久化内容 | 权威含义 |
| --- | --- | --- |
| 已发生故事历史 | `event_entries(status=committed) + event_entry_links` | 已经通过校验并发生的发言、回应、行动、对象变化和 Session merge/split；可直接组织成有来有回的 StoryLine |
| 待完成客观事件 | `event_entries(status=pending)` | 已由 committed Entry 触发、等待 Director 保持/释放/取消的环境结果；不另建第二种队列对象，也不对 Character/Broadcast 暴露 |
| 当前互动状态 | `interaction_requests` | 哪一个已提交 Entry 仍在等待哪位 Agent 回应、接受或拒绝；它是可恢复的当前状态，不是第二份正文 |
| 角色主观认知 | `agent_memory_records.source_entry_id` | 某个 Agent 是否注意到、如何理解这件事；不同 Agent 可以完全不同 |
| 观看与演出 | `BroadcastPlan + PresentationBinding` | 导播怎样选择、对齐和编排已提交 Entry；Worker、WebGAL 转译和 Render Artifact 在 MVP 中由进程外 Codex 手工完成，不进入 World DB |

`event_entries` 是统一持久化单位，不是请求时从任意日志 JSON 临时生成的文本。首轮最小字段冻结为：

```text
entry_id
world_id
status                      // pending | committed | cancelled
entry_kind                  // dialogue | action | environment | join | leave
source_kind / source_id / source_index
audience_mode               // world | session | location | explicit
delivery_channel            // public | direct | whisper | environment
delivery_session_id? / delivery_location_id?
actor_id? / target_id? / subject_id? / location_id?
world_version? / entry_index?
root_session_id_at_commit? / topology_version?
scheduled_for? / next_check_at?
occurred_at? / ended_at?
text?
details_json
created_at                  // 落库 wall-clock，仅用于诊断
```

- `commit_position = (world_version, entry_index)` 是不可变提交位置：`world_version` 由一次原子 World 更新分配，`entry_index` 对同事务内多条 Entry 从零稳定编号；不再使用 `SELECT MAX(event_order)+1`，也不依赖 SQLite row insertion order；
- `source_kind / source_id / source_index` 记录 Entry 来自哪个 Character Proposal、已提交 source Entry、system/tool/player 输入；同一来源原子产生多条 Entry 时用从零开始的稳定 `source_index` 区分，并共同承担幂等键。它不等于故事里的 actor；
- `actor_id / target_id / subject_id` 记录故事内谁实际行动、对象是谁。Director 不是故事 actor，不能被记成角色说话者；
- `audience_mode + delivery_channel` 表达告知规则与感知渠道；`explicit` 用于只告诉指定 Agent 的私语，`session/location/world` 在 commit/release 时由 World 解析；
- `delivery_session_id` 给 Session 范围或 pending 环境 Entry 使用，并始终绑定稳定 Agent Session 节点；实际 commit/release 时才解析当前 root，写入 `root_session_id_at_commit + topology_version`；
- `root_session_id_at_commit + topology_version` 保存 committed Entry 发生时的互动分区版本。root 后续可以 merge/split，因此历史查询不能拿“当前 root”反推旧 Entry；
- `occurred_at / ended_at` 是世界内事实时间，可以与提交位置不同；`created_at` 只是程序何时落库，不能拿来排列剧情；
- committed `text` 是经过对应 Entry contract 校验的公共可读正文：Dialogue 必须逐字保真，Action/Environment 只能描述已经提交的结果；pending Entry 不能提前伪装成已发生台词或环境广播；
- 常用关联、过滤和排序字段进入 SQL 列；只有 Entry kind 特有、不会独立查询的细节进入经 strict Pydantic 校验的 `details_json`。

生命周期约束是统一模型可用的前提：

- Character 的 Dialogue/Action/Join/Leave Entry 经 World 校验后直接以 `committed` 插入；
- 只有受 World affordance 约束的客观 `EnvironmentEntry` 可以先以 `pending` 插入；pending 必须有 `delivery_session_id`，但没有 `commit_position`、Line 归属或公共正文；
- `release` 在一次 World transaction 内应用对象状态变化，并给同一 Entry 填入提交位置、事实时间、Line 归属和公共正文，然后把状态转为 `committed`；`cancel` 只转为 `cancelled`；
- committed Entry 禁止 update/delete；pending Entry 只允许更新 `next_check_at` 或执行 `pending -> committed/cancelled`，不能被 Director 改写 actor、kind、subject 或 completion contract；
- Character、StoryLine 和 Broadcast 的所有查询都强制 `status=committed`。cancelled Entry 只保留审计，不进入故事线。

每条 committed Entry 都同时物化接收者快照：

```text
event_entry_recipients
  entry_id                 // FK -> event_entries.entry_id
  agent_id

PK(entry_id, agent_id)
```

`audience_mode` 说明 World 如何计算接收者，`event_entry_recipients` 才是之后构建 Character `AgentView` 的权威结果。即使原 Session 后续加入新人，旧私语也不会因此泄露。Director/Broadcast 使用全局生产视角读取所有 committed Entry，但仍看不到 Character 私有 Memory；Character 查询必须 join recipients，不能只凭当前 root/location 重算历史可见性。

Entry 的图关系使用一张规范化关系表，不把可变长度 ID 列表藏进正文 JSON：

```text
event_entry_links
  entry_id
  related_entry_id
  relation_kind             // previous | reply | cause
  relation_order

PK(entry_id, relation_kind, related_entry_id)
FK(entry_id) -> event_entries.entry_id
FK(related_entry_id) -> event_entries.entry_id
```

- `previous` 形成 StoryLine 的结构顺序：普通 Entry 通常一个前驱，merge 的首个 JoinEntry 可以有多个父 Line frontier，split 后多个子 Line 可以引用同一个 LeaveEntry；
- `reply` 表示这句回应针对哪条 Dialogue/Interaction Entry；MVP 限制最多一个；
- `cause` 表示客观因果，可多值，不能拿 StoryLine 相邻关系冒充因果；
- Validator 要求 link 两端存在且属于同一 World；`previous/reply` 只能连接 committed Entry，并拒绝指向更晚 `commit_position` 的 `previous`，因此 Entry graph 不会成环。pending EnvironmentEntry 可以先用 `cause` 指向触发它的 committed Entry，`previous` 和 Line 归属在 release 时补齐；跨 Line 的 `previous` 只能由已校验 merge/split 产生。

数据库至少建立：

```text
UNIQUE(world_id, world_version, entry_index)
UNIQUE(world_id, source_kind, source_id, source_index)
INDEX(root_session_id_at_commit, topology_version, world_version, entry_index)
INDEX(status, next_check_at, entry_id)
```

SQLite 的 UNIQUE 允许多条 pending 行在 nullable `world_version/entry_index` 上共存；进入 committed 状态时才必须得到唯一提交位置。同一 Session 一次只提交一个角色决策；过滤 `status=committed` 和同一 `line_key` 后按 `commit_position` 可得到稳定追加顺序，`previous` 边进一步保证正常延续、merge 和 split 的 StoryLine DAG。重试使用 source 幂等键，不得重复追加同一个角色结果或 pending Entry。

`interaction_requests` 不复制事件正文，只维护最小当前状态：

```text
request_entry_id          // PK/FK -> event_entries.entry_id
request_kind              // response | join
requester_agent_id
recipient_agent_id
status                    // pending | resolved | declined | cancelled | expired
resolution_entry_id?      // FK -> event_entries.entry_id
updated_world_version
```

只有确实要求对方作出选择的发言、邀请或交互才创建 request；普通可被听见的闲聊不自动制造回应义务。创建 request、追加发起 Entry 和推进 `worlds.current_version` 必须在同一事务；回应或拒绝时，追加新的解决 Entry 并更新 request，也必须在同一事务。纯 `no_op` 不伪造 EventEntry，request 保持 `pending`，由 Scheduler 的唤醒/退避规则决定何时再次提供机会。

因此直接互动的可恢复链路是：

```text
A utter(target=B, expects_response=true)
-> WorldUpdater 提交 DialogueEntry E1，并创建 interaction_requests(E1, A, B, pending)
-> AgentViewBuilder 为 B 生成 source_entry_id=E1 的 mandatory/direct_interaction candidate
-> B respond / decline / wait / no_op
-> respond 或 decline：提交 DialogueEntry/ActionEntry E2(reply->E1)，并以 E2 关闭 request
-> no_op：不写假 Entry，request 仍 pending
-> B 的私有 Memory 若选择记录，只保存带 source_entry_id=E1/E2 的主观 Observation
```

为避免 `evidence_ids` 兼任所有语义，`RespondAction` 在 Phase 0 必须增加显式 `in_reply_to_entry_id`；它必须指向当前 Agent 可见且仍可回应的 committed Entry。邀请可作为普通社交请求关联这一字段，但 request 的接受状态不是 join 的前置条件；没有邀请的自主加入也合法，不新增入组审批记录。

#### 4.1.1 从 committed EventEntry 组织可读 StoryLine

`event_entries(status=committed)` 本身就是已经通过世界校验、可供感知和召回的权威公共历史；MVP 不再先落一份宽泛 `WorldEvent`，再复制投影成第二份 Entry。`World.apply_change()` 在更新当前状态的同一事务内提交严格的 `EventEntry`，StoryLine Builder 只负责按既有 Entry 和 link 组织读取窗口：

```text
World.apply_change()
-> committed EventEntry       一句台词、一个动作或一个环境变化
-> StoryLine        一个互动分区内连续、可读的 Galgame 场景线
-> StageView        同一 World 中多条并行 StoryLine 的全局视图
```

原版 `generative_agents` 把一段双人对话称为 `chat/conversation`：`description` 保存摘要，`filling` 保存 `[[speaker, utterance], ...]` 形式的完整对话。MVP 复用“摘要 + 精确 Transcript”的机制价值，但不搬其一次生成整段双人 Chat、再分别写入角色 Memory 的实现；本项目还必须容纳多人互动、动作、环境事件和多线交汇。

`EventEntry` 是严格、可读且可回查的判别联合，首轮只支持：

```text
DialogueEntry       speaker_id / text / in_reply_to_entry_id?
ActionEntry         actor_id / operation_id / text
EnvironmentEntry    subject_id? / text
JoinEntry           joined_agent_ids
LeaveEntry          left_agent_ids

公共字段：entry_id / commit_position / occurred_at? / ended_at? / source_kind / source_id / source_index
```

- 已校验 `utter/respond` 在提交时形成逐字保真的 `DialogueEntry`；
- 已校验 `act/interact` 在提交时形成描述已经发生之结果的 `ActionEntry`，不能把 Proposal 意图当结果；
- pending `EnvironmentEntry` release 后进入 committed 公共历史；
- UnionPart merge/split 在提交时形成 `JoinEntry/LeaveEntry`，并用 `previous` link 连接 StoryLine 的父子 frontier；
- Entry 的公共正文和结构字段必须与状态变化在同一事务中校验并落库，之后的 StoryLine/摘要过程不能补写角色台词、动作结果或世界事实。

一条提供给模型的 StoryLine 不是全历史召回，而是：

```text
StoryLine
  line_key = (root_session_id_at_commit, topology_version)
  participant_ids
  parent_line_keys
  summary                       // 已稳定前缀的派生摘要，不是 World Fact
  summary_through_commit_position
  recent_entries                // 最近的精确、有来有回的 committed EventEntry
  open_processes                // 尚未完成的客观过程
```

精确 Entry 是权威记录；`summary` 只是可丢弃、可重建的读取加速层，必须标记覆盖到哪个 `commit_position`，并可由源 Entry 重建。若后续持久化摘要缓存，还必须保存输入 Entry 范围与内容 hash，不能把摘要当 World Fact。最近互动始终保留精确 Entry，避免摘要替换角色原话。merge 时新 Line 同时引用两条父 Line，split 时两个子 Line 引用同一父 Line，因此可以形成多线 Galgame 的 StoryLine DAG，但不会新增 EventSession 节点：

```text
Line A ----\
            +--> Line C --> Line D
Line B ----/             \-> Line E
```

不同 Line 的隔离由 `line_key` 和 World-owned Builder 强制执行，而不是交给 Prompt：单 Line 查询不得返回其它 Line 的 Entry；全局消费者收到 `StageView.story_lines[]`，也不能收到失去 Line 归属的扁平 Entry 列表。

```text
StageView
  world_ref / based_on_world_version / world_time
  story_lines[]
```

Character 是演员，仍只通过 `AgentViewBuilder` 获得 recipients 中包含自己的 committed Entry；Director 与 Broadcast 是全局制作视角，可以读取所有 committed StoryLine，包括只告知某个 Character 的私密 Entry，但不能读取 Character 的 Memory、Goal、Plan、Reflection、模型思维或未提交 Proposal。全局可见性不扩大写权限。

### 4.2 Director 发布客观 EventEntry，并管理 pending 生命周期

Director 不做 Character Proposal 的补全，也不能替 Character 说话、行动或直接写 Memory。它可以发布角色意愿之外的客观 `EnvironmentEntry`，明确决定何时发生、告知范围和感知渠道；需要延迟完成的 Entry 仍用同一个生命周期表达：

```text
EventEntry(status=pending)
    -- release --> EventEntry(status=committed)
    `-- cancel  --> EventEntry(status=cancelled)
```

2026-09-08 用户确认：角色不必为了咖啡等客观待办静态等待，可以继续对话，Director 在若干轮互动后选择合适方式通知指定的人。讨论中的两条路径是已发生的 chat（`DialogueEntry`）与交由 Director 处理的客观待办（`EnvironmentEntry(status=pending)`）；`entryQueue` 只是后者的查询视图，不新增队列表或第二套 ID。这里将用户所说的 `eventCommit` 理解为客观待办路径，不新增同名模型，也不表示待办已经发生或 Director 可以审批 Character 行动。真实发生的角色操作仍可形成 `ActionEntry`，不是每句 chat 都机械地生成一项待办。

#### 按互动进展检查，不阻塞角色

- 默认实施方式：每完成配置的 N 个 Character 决策步，若有 pending Entry，就给 Director 一次检查机会；新出现的合法客观过程也可立即触发登记。N 是调度配置，不是要求角色凑满 N 句台词。
- 在 `worlds` 保存 `character_turn_seq / director_checked_turn_seq`；Character 完整决策提交（包括合法 no_op）时递增前者，Director 完成检查时原子推进后者。Director 自身调用不增加角色回合数，暂停和重启不重置计数。分组变化不改变计数；它只决定检查机会，Director 仍须阅读待办对应的 StoryLine 和因果条件。
- Director 可以 `keep` 或 `release`，并选择允许的告知方式和接收者；轮数达到只允许检查，不证明咖啡已好，更不代表固定的世界分钟数。`next_check_at` 是需要显式世界时间约束时的补充，不是所有待办唯一的触发来源；若 Scenario 声明最早发生时间，轮数检查也不能绕过它。
- 普通 pending Entry 不使角色或分区休眠；角色仍可交谈、行动、离开或自行选择 wait。全部 no_op 时也能到达一次有界的 Director 检查机会；若已没有可运行角色但仍有 pending Entry，允许一次 idle 检查，不要求再凑够 N 步。检查后仍无进展则按空转/额度上限暂停，不强造台词或结局。精确的定时等待才需要世界时间推进规则。
- 例如 Director 发布「服务员轻声通知 Anon：你的咖啡好了」，使用 explicit audience `[anon]`；同组 Tomori/Soyo 不因此获知。服务员须是 Scenario 允许的环境角色，不能借此接管注册的 Character Agent。Anon 是否转告或去取咖啡仍由自己决定。

Director 每次接收“全局 StoryLine + 当前 pending Entry 操作窗口”。它可以理解各条已提交互动线，以决定立即发布、登记延迟事件、保持、释放或取消；所有结果仍须经过 World 校验：

```text
DirectorView
  stage_view                 全部 StoryLine；只含 committed Entry
  trigger                    committed_entry | pending_entry_check
  world_ref / based_on_world_version / world_time
  source_entry?              触发过程的精确 committed Entry
  selected_pending_entry?
  bounded_causal_entries
  relevant_object_process_state
  conflicting_pending_entries
  director_affordances       emit / schedule / keep / release / cancel / no_op

DirectorDecision
  emit(environment_entry, audience)
  schedule(environment_entry, audience, next_check_at)
  keep(entry_id, next_check_at)
  release(entry_id)
  cancel(entry_id, caused_by_entry_id)
  no_op
```

Director 可以读取所有 StoryLine、与 source/pending Entry 直接相关的有限因果窗口、必要的 Object/Location 状态及冲突 pending Entry。它不能读取未提交 Proposal、Character 私有 Memory/Goal/Plan/Reflection、BroadcastPlan 或玩家反馈，也不能输出 Character 的 Dialogue/Action、Session merge/split、任意 World patch 或 Memory write。

`emit/schedule` 只能创建客观 `EnvironmentEntry`，并受 `director_affordances`、World Fact、合法 subject/location 和 audience 约束。World 可以拒绝与事实冲突或冒充 Character agency 的内容；Director 返回的是决策，不直接 insert 数据库。`release` 只能选择既有 pending Entry：World 根据已经冻结的 completion contract 和当前状态，在同一 transaction 内更新对象状态、分配 Entry 的 `commit_position`、Line 与 recipients 并转成 committed，不能临场把 `coffee_ready` 改成另一件事。

告知对象由 Director 明确选择，World 在 commit 时物化成 recipient snapshot。例如：

```text
DirectorDecision.emit(
  environment_entry = 「有人在 Anon 耳边低声说……」,
  audience = {mode: explicit, agent_ids: [anon], channel: whisper},
)
-> committed E4.recipients = {anon}
```

即使 Anon 当时正与 Tomori、Soyo 处于同一 root，二人也不能在自己的 `AgentView` 中看到 E4。Anon 后续主动说出这件事时会产生新的 DialogueEntry；Tomori/Soyo 只能看到 Anon 的新发言，不能沿 `cause/reply` link 反向读取原本无权看到的 E4。Director/Broadcast 的全局制作视角仍能看见两条 Entry 及其关系。

#### 前置阻塞：当前 Object interaction 还不能表达稳定操作

当前源码的 `Affordance` 只有 `kind + target`，`InteractAction` 只有 `target + description`，只能证明“Anon 与 coffee machine 发生了某种交互”，不能确定性证明操作是 `start_brewing`、`inspect` 还是 `turn_off`。Phase 0 推荐增加稳定操作引用：

```text
AgentView.affordances[]:
  affordance_id / kind=interact / target / operation_id=start_brewing

InteractAction:
  kind=interact / target / affordance_id / description
```

Validator 必须确认 `affordance_id` 来自同一 AgentView/world version、actor 有权使用、target/operation 一致且尚未失效，再由 World 将它解析为 `coffee_brewing_started`。在这个契约落地前，咖啡 pending Entry 只能算设计，不能算现有 `InteractAction` 已支持。

#### 咖啡因果链

“Anon 说『我要煮个咖啡』”只是一条 committed quote，不能证明冲煮已经开始。正确触发点必须是 Character 自己产生并已提交的客观开始 Entry：

```text
E1 = DialogueEntry(committed, Anon,「我要煮个咖啡」)
-> 没有 completion affordance；DirectorRunner 只前移 cursor

Anon 决定 interact(coffee_machine, start_brewing)
-> E2 = ActionEntry(committed, coffee_brewing_started)
-> coffee.state = brewing

DirectorDecision.schedule(affordance from E2)
-> E3 = EnvironmentEntry(pending, source/cause=E2,
                         delivery_session_id=session-anon)

角色继续交谈若干轮，达到 Director 检查间隔（或显式 next_check_at 到期）
-> DirectorDecision.keep(E3) 或 release(E3)

release(E3)
-> 同一 transaction：coffee.state = ready
                     E3 pending -> committed
                     分配 commit_position、occurred_at、Line 和 text=「咖啡煮好了」
-> AgentViewBuilder 只把 committed E3 提供给当前确实可见的 Character
```

这里没有第二个队列项 ID，`E3` 从排队到发生始终是同一个 `entry_id`。Director 只决定 pending Entry 的生命周期；World 提交事实，`AgentViewBuilder` 决定哪些 Character 可见，Broadcast 决定玩家如何看到。

#### merge/split 时 pending Entry 跟谁走

pending Entry 的 `delivery_session_id` 绑定稳定 Session 节点，不能绑定可变 root。merge/split 时不搬迁、不复制；release 时才解析当前分区：

```text
current_root = UnionPart.rootOf(E3.delivery_session_id)
候选接收者 = UnionPart.members(current_root)
最终接收者 = AgentViewBuilder.filterVisible(E3, 候选接收者)
```

因此新加入当前 root 的角色只有在地点/渠道上确实可见时才收到；已经 split 离开的角色不会因为曾经同组而自动收到。若未来需要“地点内所有 Session 都能听见”，应新增明确的 location delivery scope。

`worlds.director_entry_cursor` 让 DirectorRunner 依次消费 committed Entry；`schedule/no_op` 与 cursor 前移必须同事务，防止崩溃后漏处理或重复排队。没有任何 affordance/相关 pending Entry 时，Runner 直接前移 cursor 而不调用 Director Agent。回合间隔检查直接查询同 World 的 pending Entry，按来源提交位置与 entry_id 稳定排序；显式时间检查另按 `next_check_at` 选出到期项。两者操作的是同一批持久化 Entry，不另建队列；检查进度与 keep/release/cancel 同事务保存。

### 4.3 Broadcast 如何跨多个 EventSession 组织 WebGAL

Broadcast 不读取未提交 Proposal、Director 调用过程或 Character 私有 Memory。它以全局制作视角读取所有 committed Entry，包括角色不可见的定向私语，同时保留每条 Entry 的 recipients/channel，绝不能因为向玩家展示而改变 Character 的认知。它不直接接收扁平、全量的 Entry，而是消费稳定到 `story_watermark` 的 `BroadcastStageView`：每条 StoryLine 提供已稳定前缀摘要、最近精确 committed EventEntry、父子 Line 关系和等待编排的 Entry。

这里必须并存三条时间轴，不能合并成一个含义模糊的 `event_order`：

| 时间轴 | 字段 | 谁写入 | 是否可被 Broadcast 改写 | 用途 |
| --- | --- | --- | --- | --- |
| 数据库提交顺序 | `commit_position = (world_version, entry_index)` | `World.apply_change()` | 否 | 全 World 唯一稳定排序、增量 cursor、幂等恢复 |
| 世界事实时间 | `occurred_at / ended_at` | World Validator/Updater | 否 | 表示事情在故事世界中何时发生；迟到信息允许出现“提交较晚、发生较早” |
| 演出呈现时间 | `PresentationBinding.presentation_order / presentation_time_ms` | Broadcast | 是，只改 BroadcastPlan 的新 revision | 决定玩家先看到什么、何时切镜，以及跨 StoryLine 的同时发生/插叙 |

因此“Entry 在数据库中后提交，但导播把它与另一条 Session 线较早的时点对齐”是合法的演出操作；它新增 `PresentationBinding`，不 update `event_entries` 的 `commit_position` 或 `occurred_at`。EventSession 的 root 是**当前互动边界**，不是永不变化的剧情章节 ID。过去的 Line 归属始终由 `(root_session_id_at_commit, topology_version)` 保留，Broadcast 不能用当前 root 重写过去。

Broadcast 通过以下信息理解多条互动流：

- `commit_position + occurred_at/ended_at`：区分提交顺序与客观发生时间；
- `line_key = (root_session_id_at_commit, topology_version)`：隔离 Entry 所属故事线；
- `event_entry_links(previous/reply/cause)`：理解 Line 延续、merge/split 交汇、对话回应和客观因果；
- `PresentationBinding`：只表达跨 Line 的演出排列和时间对齐。

其输出必须显式列出来源和绑定：

```text
BroadcastPlan
  plan_id / revision
  world_ref                  // project_id + world_id
  based_on_world_version / story_watermark
  source_entry_ids
  source_world_interval
  entry_bindings[]
  structured_beats[]          // 可读 text、source_entry_ids、truth_kind 与对应的 PresentationBinding
  projection_mode / viewpoint / transition / artistic_reason

PresentationBinding
  entry_id
  presentation_order
  presentation_time_ms?
  aligned_with_entry_id?
  relation                    // before | after | same_time | overlap | cutaway | flashback
  offset_ms?
```

`PresentationBinding` 属于 Broadcast 的持久化数据，不属于 World Fact，也不是 `EventEntry` 的字段。Phase 7 落库时应作为 `BroadcastPlan` 的规范化子记录（例如 `broadcast_entry_bindings(plan_id, entry_id, presentation_order, presentation_time_ms, aligned_with_entry_id, relation, offset_ms)`），以外键指向既有 Entry；重编排创建新 `plan revision`，不原地改写旧 Plan。它可以重排或对齐彼此独立的 Line，但不得悄悄颠倒 `reply/cause` 的语义；若刻意先给结果、后揭示原因，必须标记 `flashback/cutaway`，让后续制作和溯源都知道这是演出手法。

例如 Line B 的 `B1` 在执行和提交上都晚于 Line A 的 `A1`，导播仍可生成 `B1(aligned_with=A1, relation=same_time, offset_ms=0)`，把两个镜头剪成同一演出时点；`B1.commit_position`、`B1.occurred_at` 和所属 Line 均保持原值。这里的 `same_time` 只表示呈现轴对齐，不宣称两件事在 World Fact 上同时发生。

`story_watermark` 是“当前可稳定编排到哪里”，不是最后一条 Entry ID。World/Broadcast 只有在确认某个事实时间之前不会再正常补入更早 Entry 时，才封住该前缀；watermark 之后仍到达的迟到 Entry 若属于已发布区间，必须生成新的 BroadcastPlan revision，并以补叙/插叙表达，不能静默篡改旧 Artifact。

`BroadcastPlan` 中的台词、旁白或推断必须通过 `source_entry_ids` 回查到精确 Entry，或明确标注为 Broadcast inference。进程外 Codex 生成的 WebGAL DSL/JSON 未必能承载这些元数据，因此手工制作产物必须同时保留原始 BroadcastPlan；演出文本和镜头命令不写回 `event_entries`。World 侧不建立 Broadcast 表，MVP 只为 Plan/Binding 提供必要的 Broadcast 侧持久化，不新增 RenderJob/RenderArtifact 数据库模型。

故事线 JSON 必须能够离开数据库直接阅读：导出包含 `world_ref`、Plan revision、按呈现顺序排列的 beats、选中 Entry 的精确正文与 `line_key`、必要的 previous/reply/cause 引用及 Line 交汇关系。跳过的前情可附带标明覆盖范围的摘要，不能只导出一串 Entry ID。源 Entry 快照是这次导出的依据，SQLite 仍是权威记录；按第 6.5 节另附运行状态、暂停原因、提交截止位置和未完事项，自动结局方案采用后再附 ending_id。

MVP 的程序内边界固定为：

```text
committed EventEntry
-> StoryLineBuilder（分线、摘要、recent window、交汇关系）
-> BroadcastStageView（全局多线输入）
-> Broadcast Agent（选材、切线、视角、文字组织、PresentationBinding）
-> persisted/exported BroadcastPlan
```

随后采用明确的进程外人工开发步骤：

```text
BroadcastPlan + 当前 Project 的素材信息
-> Codex 临时承担 Worker Agent：选择背景、立绘、表情、动作、站位、BGM 与转场
-> Codex 同时完成 BroadcastPlan 到现有 story.json / WebGAL DSL/JSON 的转译
-> 现有 authoring validation / build 校验并构建
```

Worker Agent 是已保留的未来架构角色，但 **MVP 不接入、不实现 Worker 接口、不调用模型服务**。新增的 `BroadcastPlan -> WebGALCompiler` 也暂不实现；Codex 在仓库外编排过程中临时代替 Worker 与转译层。Codex 不能修改 source Entry、World Fact、Character 可见性和因果关系，并且一次只能使用 `WorldRef` 指定 Project 的素材。将来程序化时再把这一步拆成受约束的 Worker 输出与确定性 Compiler，不能反向改变现在的 World/Broadcast contract。

当前 `extensions/dynamic-render/` 仍是 Fixture 原型：`timeline.json` 提供手写 Event/Render，`played Set`、当前 active Render 和选择状态都只在 Node 进程内存中，重启会丢失。它尚未接收真实 `EventEntry -> BroadcastStageView -> BroadcastPlan`。本轮只要求已有 Fixture/静态项目能力不回归，不把 Dynamic Render 的持久化和自动接线列为 Agent World MVP 的完成条件。

## 5. 从 `origin/mvp` 选择性复用

`origin/mvp@05c5404` 与当前分支没有共同 Git 祖先，不能做整分支 merge。这里的“复用”分为模型/约束移植、实现改造和测试思路复用，不等于复制整个 package。它是对 D-041“只做选择性融合”的继续细化，不是重新接纳另一个分支的整套 World DB Runtime。

### 5.1 可以直接或轻量改造复用

| 分支内容 | 本分支落点 | 复用方式 |
| --- | --- | --- |
| `ScenarioSeed / load_seed` | `agent_runtime/scenario.py` | 保留严格解析、ID/引用校验、版本与内容 hash；将旧 Session participants 改成固定节点初始 root 分区 |
| `initialize_world()` 主流程 | `bootstrap.py + world/initializer.py` | 保留“校验后一次初始化、已有存档不静默覆盖、失败不留半成品”的行为；不照搬 World 包读取 Agent 私有信息的依赖 |
| `WorldRow` | `worlds` 当前元数据 | 保留 seed 来源、current version 和乐观版本检查；不搬独立 `WorldVersionRow` |
| `WorldEventRow` | `EventEntry` 持久化模型 | 只复用稳定 ID、source/causal 引用和顺序约束思路；改成带明确生命周期的可读 typed Entry，不复制旧宽泛 payload |
| SQLAlchemy transaction/constraint 写法 | `world/storage.py` | 复用同事务提交、外键/唯一约束和 rollback 测试方式，改写为当前明确业务表 |
| 初始化与故障测试 | `tests/` | 复用重复初始化、无效 Seed、写入中途失败、重启恢复和私有 Memory 隔离的测试思路 |

### 5.2 复用约束和测试，不原样复制类

- 一次 `World.apply_change()` 在同一 SQLite transaction 内更新明确的当前状态行、Session root binding、`event_entries + event_entry_links` 和 `worlds.current_version`；任何一步失败必须全部回滚；
- 使用 `expected_world_version` 对 `worlds.current_version` 做 CAS，拒绝 stale update；
- committed `event_entries` 和既有 `event_entry_links` 禁止 update/delete；pending Entry 只允许受控生命周期转换，其他当前状态表只能经 `WorldUpdater` 更新；
- Session root mapping、对应的 `JoinEntry/LeaveEntry` 和 world version 必须同一事务更新，失败时全部回滚；
- 角色 Proposal 只能代表自己的意图，Director 不能替角色说话或接受互动；
- Entry 必须能通过 source 幂等键追溯到 Proposal、source Entry、外部输入或已提交 evidence；
- 复用 Seed 引用校验、故障注入、事务回滚、跨进程恢复和 schema migration 测试思路。

### 5.3 明确不复用

- 分支旧 `PerceptionFrame`：它把可见实体、Memory 和 pending responder 混在一起；当前 `PerceptCandidate / channel / attention_tier / visible_fields / evidence / affordance` 更适合作为 `AgentView` 基础；
- 分支旧 `ActionProposal`：包含 `move`、`memory_changes` 和不同的 actor/action 契约，与当前六类 action union 冲突；
- `GenerationBatch / GenerationWave` 及同 snapshot 收齐全体 Proposal 再提交的 lockstep；当前目标是同一互动分区内逐角色决策、逐步提交，隔离分区可并行计算；
- 原样的 `ValidatedCommitPlan` 和 `commit_wave()`：改成单个 world step 的 `WorldUpdatePlan` 与 `World.apply_change()`；
- `WorldSegmentRow / WorldVersionRow / EntityRevisionRow / SnapshotRow` 及同一状态在 segment、revision、event、snapshot 中重复保存的布局；
- `EventSessionRow / EventSessionMemberRow / SuccessorSession` 的关闭父 Session、创建后继 Session 模型：本项目改用每 Agent 一个稳定 Session 节点，并通过 `UnionPart` 更新 root binding；
- 自动 Session 合并策略：不能因为 A 对另一 Session 的 C 说话，就自动把双方 Session 的全部成员合并；
- 只按 `location_id / scope_key` 自动拆组的策略：物理同地不等于同一社交互动组；
- 分支 `PerceptionProjector` 类本身：只借鉴私有字段裁剪、scope 隔离和提交后才可见的规则；
- 分支 `EventRecognizer`：当前基本是一对一改写 Candidate，并没有解决事件聚合和边界识别，不值得为了名字搬一层空抽象；
- World transaction 内直接替 Agent 选择并写入 Memory：这会越过 `PersonActLoop` 的 attention/novelty 权限。

## 6. EventSession runtime model

### 6.1 已确认采用：固定节点 + root 分区

每个 Agent 拥有一个生命周期稳定的 `EventSession` 节点。五个 Agent 默认只有五个节点；角色组合反复变化也不创建第六个 Session。

```text
EventSessionNode
- session_id              // 稳定节点 ID
- agent_id                // 与 Agent 一对一
- root_session_id         // 当前互动分区的 root
- topology_version        // 当前 part 的形成版本；只有该 part 成员变化时更新
- updated_world_version   // 最近一次 root binding 生效版本
```

初始未显式分组的节点以自己为 root。场景 Seed 可以直接声明初始分区，不需要为了形式先制造 singleton merge 历史：

```text
session-anon    -> root session-anon
session-soyo    -> root session-anon
session-tomori  -> root session-tomori
session-taki    -> root session-taki
session-rana    -> root session-rana
```

当前互动组由 root 等价类定义：

```text
sameEventSession(A, B)
    = rootOf(A.sessionId) == rootOf(B.sessionId)
```

Runtime 从 SQLite 的五条 root binding 构建 `UnionPart[SessionId]`。`rootByItem` 是当前持久化投影，`membersByRoot` 是由它派生并只在 merge/split 时同步维护的内存索引，不额外写一份 `event_session_members` 事实。`topology_version` 不是全 World 统一递增后强迫所有 Line 换代：只有 merge/split 影响到的 part 才以本次提交的 world version 作为新 topology version；未受影响的 part 保持原值。这样相同 root 后续再次组成相同成员时，也不会与旧 StoryLine 混淆。

### 6.2 Merge 与 split

Tomori 加入 Anon/Soyo：

```text
before
session-anon    -> session-anon
session-soyo    -> session-anon
session-tomori  -> session-tomori

after UnionPart.merge(keepRoot=session-anon, mergedRoots={session-tomori})
session-anon    -> session-anon
session-soyo    -> session-anon
session-tomori  -> session-anon
```

Anon 去买咖啡并离开当前互动：

```text
before
session-anon    -> session-anon
session-soyo    -> session-anon
session-tomori  -> session-anon

after UnionPart.split(
    root=session-anon,
    parts={
        session-anon: {session-anon},
        session-soyo: {session-soyo, session-tomori},
    },
)
session-anon    -> session-anon
session-soyo    -> session-soyo
session-tomori  -> session-soyo
```

规则：

1. root 是数据结构代表，不表示角色领导权、叙事主视角或关系地位；
2. merge/split 只改变受影响节点的 root binding 和 topology version，不创建、关闭或删除 Session 节点；
3. root binding、描述这次变化的 `JoinEntry/LeaveEntry` 和 `worlds.current_version` 必须在一个 `World.apply_change()` 事务中更新；
4. 当前 `ActionProposal.eventSessionId` 使用决策时的 `rootSessionId`，并与已有 `basedOnWorldVersion` 一起标识互动上下文；root 已变化的 stale Proposal 必须被拒绝；
5. 历史 Entry 的 root 引用必须同时带 `topology_version` 和自身 `commit_position`，因为同一个 root key 在不同时期可能代表不同成员集合；
6. Agent 不能直接提交 root key、成员集合或调用 `UnionPart`；它只能表达加入、离开、回应、拒绝等角色行为，受信 Runtime 才能生成并应用 merge/split；
7. 新 Agent 出现时才新增稳定节点；普通互动变化不会让 `event_sessions` 表增长。

root 分区历史直接作为结构化 `JoinEntry/LeaveEntry` 保存，不再另写一份 `SessionPartitionChanged` Event：

```text
JoinEntry / LeaveEntry details
- reason: merge | split | transfer
- affected_session_ids
- before_root_by_session
- after_root_by_session
- topology_version

link
- cause -> trigger_entry_id
- previous -> one or more parent Line frontiers
```

这保留了当前需要的审计与 StoryLine 交汇信息，同时避免 successor Session、宽泛 Event 和独立 Change Log 膨胀；仅靠 Entry 重建任意历史 World 状态不属于首轮 MVP 承诺。

### 6.3 Agent 自主参与互动

`UnionPart` 让“参与/退出互动”的结果具备稳定、简单的底层表达，但它本身不替 Agent 做选择。自主参与链路是：

```text
AgentView 暴露可见互动与 join / leave affordance
-> Agent 自己提出 interact / act / respond / wait / no_op
-> Validator 校验可见性、角色权限和客观可达条件，不要求其他成员批准
-> World.apply_change()
-> 需要改变互动组时才调用 UnionPart.merge/split
```

MVP 优先复用现有 action union：主动接近/加入可先由 `interact` 表达，主动离开可先由 `act` 表达，不为了 Session 数据结构新增 `join_session / leave_session` action kind。只有 Golden/Badcase 证明自然语言 intent 无法稳定校验时再扩展 contract。

必须区分：

- root 相同：处在同一个同步直接互动上下文；
- root 不同但 `same_scene` 可见：可以观察或提出加入，不自动 merge；
- `targeted_message`：可以跨 root 传递已提交消息，不必 merge；
- 收到邀请：获得一次明确的决策机会，不等于被系统强制加入，也不是自主加入的必需凭证。

2026-09-08 已确认：Tomori 自主接近并加入可达互动组，不需要 Anon/Soyo 接受，也不等待投票或审批。其他角色可以通过发言、态度或自己的行动表达不欢迎；这不会自动撤销 join、踢走 Tomori 或强制 split，Tomori 自己决定留下或离开。若加入者原本在另一组，默认只转移她自己的稳定节点，不把原组所有成员一起拖入。root 代表没有批准加入或驱逐成员的权限。

因此入组审批不是待设计项；剩余只是角色行为到自身 join/leave/transfer 的稳定映射，以及客观可达性校验。社交上的拒绝是一段已发生的角色互动，不是 Runtime 拒绝加入。

### 6.4 同一 Session 的互动闭环

MVP 的直接互动必须具备 Entry 级来源：

```text
A utter(target=B)
-> committed DialogueEntry E1
-> AgentViewBuilder 为 B 生成 mandatory/direct_interaction candidate(E1)
-> EventSessionRunner 优先给 B 一个决策机会
-> B respond(target=A, in_reply_to_entry_id=E1) / wait / no_op / 其它合法动作
-> 新的 world update
```

“优先给回应机会”不等于“强迫 B 回答”。Scheduler 决定谁获得决策机会，角色仍决定做什么。纯 `no_op` 不产生假的 `EventEntry`；其私有状态、调度进度和唤醒条件仍按第 6.5 节持久化。

### 6.5 手动保存续跑，以及独立的剧情结局规则

2026-09-08 需求补充：用户的“手动结束故事”表示结束这次运行，下一次继续同一份故事。因此本节将手动停止统一定义为 `pause_and_save`，状态变为 `paused`，保留原 `project_id + world_id`。这替代上一版把手动终止映射到不可继续 `ended` 的建议。自动结局与 EndingRule 单独讨论，不阻塞保存/读档实现。以下接口和状态均为待实现设计。

#### 用户操作与保存位置

```text
create_world(project_id, world_id)               首次创建才执行 Scenario bootstrap
pause_and_save(project_id, world_id)             停止生成，确认保存完成
load_world(project_id, world_id)                 加载已有存档，保持 paused
resume_world(project_id, world_id, additional_decisions?)
                                                明确继续生成
```

存档始终在 `.runtime/<project-id>/world.sqlite` 中。`world_id` 对应一个可反复保存和加载的存档；每次暂停不会创建新 World，也不复制一份全量 Snapshot。读取不存在的 `world_id` 必须报错，不能偷偷改为新建世界。列表/查看/导出是只读操作，不启动 Agent。

手动停止按钮和正常处理的 Ctrl+C 都走 `pause_and_save`。只有暂停事务提交成功才向用户确认“已保存，可以关闭”；本次暂停不要求角色说再见、不等待咖啡完成，也不调用 Director/Broadcast 来宣布结局。崩溃/强杀时只能承诺恢复最后一次已经提交的完整决策。

#### 保存哪些状态，才能真的继续

| 保存内容 | 位置/字段 | 恢复后保证什么 |
| --- | --- | --- |
| 世界与存档身份 | `project_database`、`worlds` 的 WorldRef、seed/config 校验信息、`current_version / world_time` | 仍是原作品的原存档、原世界时刻 |
| 地点、物体、角色位置与互动组 | 公共关系表、`event_sessions.root_session_id / topology_version` | 咖啡仍在原进度，原分组不变，重建同一 UnionPart |
| 已发生故事与未完事项 | Entry/link/recipients、`interaction_requests`、pending Entry 的 `next_check_at` | 保留原对白、接收者、未回应邀请和待发生客观事件 |
| 每个角色的认知状态 | 新增 `agent_runtime_states`，保存 M1 的 `PersonaState` | 保留普通 Plan queue / active_plan_id、已知地点和反思累计；对话轮数随后续 Scheduler 保存，不再保存 daily / 旧时间 cooldown |
| 每个角色的 Memory | `agent_memory_records` 完整记录及访问/novelty/过期信息 | 继续原 MemoryStream，不能只恢复一份剧情摘要 |
| 下一决策与唤醒位置 | `worlds.scheduler_seq`；稳定 Session 节点上的 `last_scheduled_seq / next_wakeup_world_time / consecutive_no_op` | 根据未回应请求与保存的进度重新算出下一角色，不每次回到 Anon |
| 导演进度 | `worlds.director_entry_cursor / character_turn_seq / director_checked_turn_seq` 与 pending Entry 状态同事务保存 | 不重复处理已消费的 Entry，不重复登记咖啡完成事件，保留对话进展检查间隔 |
| 暂停与调用额度 | `worlds.status / control_epoch / stop_reason / stopped_at_world_version / decision_count / decision_limit_at` | 拒绝旧调用，记录暂停原因；继续时可以获得新额度 |

此前只保存 Memory 会遗漏 [PersonaState](../../agent_runtime/agent/personact/state.py) 中的运行状态。新增表只保存当前私有状态，不保存逐版本副本：

```text
agent_runtime_states
  world_id, agent_id                 PK + 同 World 的 Agent FK
  state_revision                    私有状态 CAS，与世界版本分开
  spec_digest                       检查加载的角色配置/Skill 是否仍匹配
  persona_state_json                现有 strict PersonaState 的完整当前值
  observation_world_version, observation_entry_index
                                    上次已处理的输入范围，防止重复消费
  last_decision_id                   最近完成的决策来源
```

私有计划作为严格类型的 Agent 状态整体保存；它与可查询的公共 `agent_world_states` 分开，Director/Broadcast 无权读取。M1 的 PlanItem 仅含 plan_id / description，active_plan_id 引用队列；不按日期重置，也不靠预计耗时判完成。等待/完成/取消和推进将在 M3 outcome 接入，对话 50 轮后接行为将在 M4 调度接入。调度字段放在稳定 Session 节点上，merge/split 时随角色保留；当前 root 的优先级由成员进度派生，平局按稳定 ID 排序。具体邀请优先级另按第 8 节冻结，但恢复必须使用同一算法和这些持久字段。

当前安排及执行进度由 PersonaState 保存，重要决定的历史复用已有 `MemoryKind.PLAN`；不新增并行 DecisionMemory/决策队列。Reflect 可以提供计划参考，但当前有效安排由 planning 管理，读档时直接加载而非依赖 top-k Memory 召回。完成标记只能对应已确认生效的结果；没有生效的安排保留为待处理或由角色调整，不能凭原 Proposal 写成已完成。

观察游标只表示“处理过这批输入”，不表示“已经回应”。角色看过邀请后选择 wait/no_op，`interaction_requests` 仍保持 pending；AgentViewBuilder 必须独立查询这些未解决请求，即使来源 Entry 在 observation cursor 之前也仍可作为 mandatory candidate 返回。

#### 每次完整决策就是一个可恢复点

首轮 MVP 每个 World 在当前 epoch 同时只派发一个 Character 或 Director 决策；不同 root 轮流推进。调用前用短 transaction 检查 running、epoch 和剩余额度，并将 `decision_count += 1`，用这个单调计数生成 decision ID；取消、失败和崩溃都不返还额度。一次模型决策先在工作副本中计算，受信 Runner 协调同一个 SQLite transaction：

```text
事务外：Agent 计算 Proposal + 暂存 PersonaState/Memory 增量
       Validator 生成合法世界更新（可为空）与确定性的 outcome
       Agent 自己计算最小 outcome feedback；首轮 Reflection 为 no-op
事务内：检查 WorldRef、status=running、control_epoch、world/state revision
       WorldUpdater 写公共状态、Session、Entry、请求与世界版本
       Agent 自己的 Store 写预先算好的 PersonaState、Memory 与观察游标
       保存决策完成 ID、scheduler_seq 和节点调度进度
       COMMIT
提交后：才把工作副本发布为当前内存状态，才公开新 Entry
```

Agent 私有状态的计算权仍属于 Agent；World 不解释 Attention 或 Memory，也不接收 Proposal 中的 `memory_changes`。协调器共享 transaction 是为了全部成功或全部回滚，事务内不调用模型。当前 [PersonActAgent.decide](../../agent_runtime/agent/personact/agent.py) 会在生成 Proposal 后立即替换 `_PrivateSnapshot`，实现时必须改为受信协调器可接管的暂存结果，或使用可丢弃的独立实例；只加一个 `paused` 判断无法解决提前发布问题。

`no_op/wait` 即使不产生世界变化，也要提交私有状态、决策完成标记和调度进度；不能伪造 Entry 或推进故事时间。因此保存结果同时包含 `world_version` 和 `scheduler_seq`。已完成 Entry 通过 source 幂等键防重；调用 ID 由持久化递增的派发序号生成，恢复后不会复用。

这里区分两种未生效：合法角色尝试因客观条件不满足而没有产生目标效果，是普通 outcome，不是审批拒绝；先撤销依赖成功的状态/Memory 增量，再由 Agent 依据确定性 outcome 计算最小反馈，与观察游标、角色回合和调度进度一起提交，不写虚假的成功 Entry。角色下次可以调整或继续原计划。跨 World、过期版本/epoch、非法契约以及暂停取消则是执行边界失败，整份工作副本不发布；已消耗的调用额度保留。这两类不能混成“所有未生效都当角色被拒绝”，也不能无条件发布原工作副本。

#### 点击“停止并保存”的精确顺序

1. Runner 停止派发新任务，暂停操作进入与提交相同的写入通道。
2. 在短 transaction 中设置 `status=paused / stop_reason=user_pause`，递增 `control_epoch`，记录当前世界版本；现有 `scheduler_seq` 标明同时保存到的调度位置。已经 paused 的重复请求直接返回当前保存结果。
3. 若角色决策的完整事务先提交，就保存包含它的状态；若暂停先提交，旧决策的 epoch/status 校验失败，整份结果丢弃。不会出现台词已存而 Memory/下一角色没存的中间态。
4. 暂停事务成功后返回保存结果，再 best effort 取消在途模型任务。取消失败不影响保存，迟到任务没有提交权限；关闭进程的耗时仍受模型传输取消/超时约束。

例如 Anon 刚提交 E42「你要不要一起喝咖啡？」，Soyo 正在生成回答，此时暂停先提交：

```json
{
  "worldRef": {"projectId": "coffee-golden", "worldId": "save-001"},
  "status": "paused",
  "worldVersion": 42,
  "schedulerSeq": 57,
  "worldTime": "2026-09-08T10:15:00+08:00",
  "stopReason": "user_pause",
  "nextAgentId": "soyo"
}
```

`nextAgentId` 是依据保存状态派生的预览，不是第二个调度真值。Soyo 尚未提交的回答没有进入故事；下次重新给她决策机会，已提交的 E42 不重复生成。若她先完成了完整提交，返回的则是包含回答的新版本与相应下一角色。

#### 下次读档与继续的精确顺序

1. 根据 Project ID 打开原 SQLite，检查文件身份、目标 World、schema、Scenario seed hash 与各角色 `spec_digest`。配置不匹配时明确报错，不能重跑 bootstrap 覆盖存档。
2. 每个运行 World 必须持有本地排他锁；并发 resume 同一 World 时只能一个成功。发现数据库残留 `running`，必须先取得锁证明旧进程已不再持有它，才能按异常中断恢复为 paused 并作废旧 epoch，不能抢占仍在运行的 Runner。
3. 在一致的读取视图中恢复公共状态、UnionPart、私有 PersonaState/Memory、节点调度进度、未回应请求和 Director cursor。加载本身不派发任务；只有明确 resume 才切换到 running。
4. resume 取得运行所有权后必须重新读取数据库，不能直接启动先前 load 得到的旧内存对象；在同一控制 transaction 中检查状态、处理额度并递增 epoch、切换 running。累计 `decision_count` 不清零；例如已用 200 次后请求再运行 100 次，将 `decision_limit_at` 设为 300。没有追加额度时沿用剩余额度，已耗尽则保持 paused。重启不能免费重置预算；单次调用/重试另设上限，离线时间不计运行耗时。旧 epoch 调用若尚未取消，其工作副本完全隔离，永无发布权限。
5. 用保存的请求优先级、调度序号和唤醒状态选择下一决策者。上述例子仍先让 Soyo 看见 E42，成功回应后追加新 Entry；Director 从原 cursor 继续。所有新写入仍归属同一 `world_id`。

保存时世界时间为 10:15，现实中隔一天加载也仍从 10:15 开始。咖啡还需两分钟，则需要世界继续推进两分钟；离线不会让咖啡自动完成、请求自动过期或冷却自动流逝。暂停期间不跳过任何故事互动。恢复保证已提交状态连续；未完成的模型调用会重新计算，不承诺生成相同的未提交文字。

故事线 JSON 用于阅读和交给 Codex 制作，SQLite 才包含续跑所需的完整私有状态与调度信息。暂停后仍可导出截至该保存点的 BroadcastPlan；导出不把 paused 改成 ended。首轮只支持从当前保存点继续，恢复任意旧版本/另开分支存档不是这套接口的隐含能力。

#### 约束剧情前提、发展目标与结局（仍为待讨论建议）

建议直接扩展现有 `ScenarioSeed`，三个配置块分别承担：

```text
story_goal       Director 可读取的创作目标；不给角色新增知识或强制行动
ending_rules[]   有序的结局规则：ending_id / condition / closure_scope
run_limits       最大决策次数、运行耗时和连续空转阈值
```

其中只有 `EndingRule` 需要一个小的 strict/frozen 嵌套 contract。`condition` 根据同 World 的 committed Entry、明确 World Fact、位置或世界时间判断；按规则顺序匹配，多条同时满足时结果确定。先为 Golden Trace 写少量 typed predicates，不开放任意 SQL/Python 表达式，也不靠一句 LLM 评价直接宣布成功。`closure_scope` 指定本幕结束前必须解决的互动/客观过程；背景长线可以保留为导出中的未完事项。

剧本前提由 bootstrap 物化为世界事实或按接收者分配的角色知识；持续成立的硬规则由 World 校验。例如预先声明的营业截止时间，应由世界时间规则经 WorldUpdater 生效，不能靠 Director 是否愿意触发决定硬规则是否成立。Director 依据 `story_goal`，只在已经允许的客观事件、触发条件和 audience 范围内选择时机；目标不扩大其行动权限，也不能成为反复施加环境事件迫使角色接受的理由。

“本幕提供一次邀请的机会”可以通过开场设定和合法外部事件支持；“Tomori 必须接受邀请”依赖 Character 自己的选择，不能由 Director 保证。若作者坚持指定角色必须执行某动作，应另行明确剧本对角色的控制权限，这与当前自由互动约定不同，不能默认为 Director 新权限。对于“关系修复”之类主观目标，需先约定可验证证据，否则只作为创作偏好。

#### 自然收尾与兜底

咖啡店一幕可以定义：初始 Anon/Soyo 在一起，Tomori 独立；创作目标是让三人有机会讨论下一次排练。导演可以在合法时间告知“咖啡好了”“即将打烊”，角色自己选择回应和去留。结局至少覆盖：邀请被接受、邀请被拒绝、营业结束时事情仍未谈妥；后者是允许的开放结局。

Runner 在每次世界提交后及下一轮调度前检查结局规则。自然收尾应先让本幕关键回应与客观过程完成，或有已记录的拒绝/取消/过期结果；不能一出现关键台词就截断仍待回应的互动。未来唤醒、其它 root 的待办和 Director 尚未消费的 Entry 必须纳入检查；全员暂时 `wait/no_op` 只表示空闲，不等于故事完成。连续无进展或预算耗尽则暂停并导出“未完成”，不伪造结局。

`ended` 固定的是这份 World 的故事截止位置；它不清空历史、不删除稳定 Session，也不把所有 pending Entry 自动改成 cancelled。未解决事项按原状态保留并在导出中说明；`ending_id` 是否允许这些未完事项由规则判断。Broadcast 可以在这个已封闭前缀上继续编排和导出；已经停止的 World 不因导出而重新启动。

这套规则能保证计算有界、能够中断，并让正常路径有明确收尾；结局是否自然好看仍需用实际角色轨迹评估。MVP 接受多个合法结果，不能同时承诺自由选择和每次都命中同一个指定结局。

## 7. 分阶段开发计划

### Phase 0：冻结命名和最小 contract

交付：

- 在新增代码中使用 `AgentViewBuilder / AgentView / WorldUpdater / EventEntry`，不新增与 `event_entries` 同义的 History、Event Log、Change Log 或 Queue 领域对象；
- 决定当前 `PerceptionFrame` 到 `AgentView` 是一次性重命名还是短期兼容迁移；MVP 不长期保留两个同义领域模型；
- 新增 strict/frozen `WorldRef(project_id, world_id)`，让所有跨 Runtime 边界的 View、Proposal、Update、Director 与 Broadcast contract 显式携带相同身份；不新增 `Run` 领域模型；
- 新增不带业务语义的 `UnionPart[T]`，只实现 merge/split、root/members/connected 查询及通用分区不变量；
- 新增 strict/frozen `ScenarioSeed`、`WorldUpdatePlan`、`WorldUpdateResult`、`EventEntry` 判别联合、`EventEntryLink`、稳定 `EventSessionNode`、`DirectorView`、`DirectorDecision` 和 `PresentationBinding` contract；
- `ActionProposal` 保持当前六类 union，envelope 增加 `WorldRef`；不把 branch 的 `move / memory_changes` 搬回来。为 `RespondAction` 增加明确的 `in_reply_to_entry_id`，并为 Object `InteractAction` 增加引用 World-issued 操作的 `affordance_id`（或经讨论确认的等价 typed operation）；
- 按第 6.5 节补手动保存/读档接口与运行状态、调用额度字段；Scenario 的 `story_goal / ending_rules` 与 `EndingRule` 仍为独立待讨论建议。

完成条件：`UnionPart` 的 merge/split/非法分区测试，以及 WorldRef/Scenario/World contracts 的 JSON/YAML round-trip、unknown field 拒绝、frozen、引用完整性和 semantic validation 测试通过；所有边界拒绝不匹配的 WorldRef；自然语言 `description` 不能单独触发 Object state change 或 pending EnvironmentEntry。

### Phase 1：Scenario 初始化与 SQLite 世界事实底座

交付：

- 引入 SQLAlchemy + Alembic，建立第 4 节首批 schema；
- 实现 `.runtime/<project-id>/world.sqlite` 的 Project 数据库工厂：校验 project ID 与路径 containment，每个 Project 独立创建 Engine/SessionFactory，创建/打开时检查单行 `project_database` 和 `worlds.project_id`，禁止调用者直接注入任意数据库路径；
- 实现 `agent_runtime/scenario.py`，严格加载作品的 `scenario.yaml + agents.json`，校验公共引用、knowledge assignment 接收者和 Seed hash；
- 实现 `bootstrap.py` 与 `WorldInitializer`：预先校验全部输入，再在一个初始化 transaction 内将公共部分写入 World、将知识 assignment 按 Agent 路由到 `agent/memory`；Genesis 不伪造普通 `EventEntry`；
- 从关系型当前状态恢复地点、Agent、Object、公开 Fact 和稳定 Session root mapping，并派生 root queue；
- 建立 `event_entries + event_entry_links + event_entry_recipients`、Entry 生命周期、committed 不可变、提交位置唯一、source 幂等、link 外键/DAG 校验，以及 root 必须引用现有 Session、root 指向自己等约束；
- 建立以已提交 Entry 为来源的 `interaction_requests` 当前状态表；创建/解决 request 必须与相应 Entry 和 world version 同事务提交；
- 为 pending EnvironmentEntry 建立 source/subject 幂等约束和到期索引，并在 `worlds` 保存单一 `director_entry_cursor`；不另建 queue 表；
- 提供 repository 接口，但不在 repository 中夹带调度/认知策略。

保存/续跑还需同批建立 `agent_runtime_states` 与稳定节点调度列；`create_world`、`load_world` 明确分流，后者恢复已有状态且默认 paused。Scenario/角色配置校验失败不得退回重新初始化。

完成条件：公共背景与私密背景正确分流；两个 Project 使用不同 SQLite 文件并可复用相同内部 ID；跨 Project 数据库错配和 WorldRef 写入均被拒绝；同一 Project 的多个 World 互不污染；初始化、迁移、重启恢复、重复初始化、非法 Entry/link update/delete、无效 root、重复/遗漏成员和固定 Session 节点数量均有测试。

### Phase 2：`World.apply_change()` 原子更新

交付：

- `WorldChangeValidator` 作为无副作用校验层；
- `WorldUpdater` 校验 WorldRef、运行状态、control epoch 和 expected world version；Agent Store 校验自己的 state revision；
- Runner 协调一个 transaction，更新世界状态/Entry、Agent 已计算好的私有结果、消费游标与调度进度；领域计算仍由各自 owner 完成，事务内不调用模型；
- 完成结果返回 `WorldUpdateResult`，失败不留下半条世界事实；
- 使用 failure checkpoints 覆盖每一个写入阶段。

完成条件：stale version 被拒绝；任一 checkpoint 失败后当前状态、root、Entry/link 和 version 全部保持原值；相同 Fixture 输入得到相同 Entry ID、提交位置和关系型最终状态。

### Phase 3：`AgentViewBuilder`

交付：

- 基于指定 committed world version 构建一个角色的 `AgentView`；
- 复用当前 channel、attention tier、visible fields、evidence 和 affordance contract；
- 实现 self、direct interaction、same scene、targeted message、whisper、commitment update 的最小可见性矩阵；历史 Entry 必须以持久化 recipient snapshot 过滤；
- direct interaction 产生 mandatory candidate，但不在 Builder 内做 novelty、Memory 写入或主观解释；
- 私有 Entity 字段、其他 Agent Memory、未提交 Proposal 和其他隔离 Session 信息不可见。

完成条件：以角色身份参数化的权限测试通过；交换 Agent 调用顺序不会改变同一 committed version 的视图。

### Phase 4：EventSessionRunner 与互动/转换

交付：

- 从数据库恢复 `UnionPart[SessionId]`，由 root 集合派生可运行互动组顺序；
- 同一互动分区内一次只调一个 `PersonActAgent.decide`，成功提交后下一角色才读取新版本；
- 实现被直接互动角色优先、round-robin fallback、`wait/no_op` 唤醒和连续空转上限；普通 pending 客观事项不阻塞角色继续交谈；
- 将业务上的 join/merge/leave/transfer 收敛为 `UnionPart.merge/split`，不创建 successor Session；
- 读取 `World.apply_change()` 已持久化的 committed `EventEntry`，按 `(root_session_id_at_commit, topology_version)` 组织 `StoryLine`；merge/split 只增加父子 Line 关系，不新增 EventSession；
- 构建 `StageView.story_lines[]`，单 Line 读取强制隔离，其它 Session 的 Entry 不得混入；
- 角色行为到 root partition change 的策略先只支持 Golden Trace 必需的最小规则，Agent 不接触底层 root API。

本阶段同时实现第 6.5 节的 `pause_and_save / load_world / resume_world`、运行所有权、提交前状态/epoch 门禁和有界调度；保存恢复不依赖 Director 模型主动停止。自动结局检查在 EndingRule 确认后增加。

完成条件：同 Session 发言—回应闭环可以读成逐句 StoryLine；两个隔离 root 分区交错运行时 Entry 不串线；一次 merge、一次 split/transfer 后 StoryLine 父子关系可重建；无审批自主加入、他人表达不欢迎不强制踢人、角色自主退出和重启继续调度全部通过；重复变化后 Session 节点数仍等于 Agent 数。

### Phase 5：Director 的定向 Entry 发布与 pending 闭环

交付：

- Character Proposal 直接经 Validator/WorldUpdater 形成 committed `DialogueEntry/ActionEntry`，不调用 Director 补写角色结果；
- 实现 `DirectorStageView`：读取所有 committed StoryLine 的摘要、最近精确 EventEntry、交汇关系和开放过程，同时附带当前 pending Entry、相关 Object/Location 状态、冲突项与允许操作；
- 先用 deterministic fixture 验证 `emit / schedule / keep / release / cancel / no_op`；Director 可以选择 audience/channel，但不能读取 Character 私有状态、输出 Character 行动、任意 World patch 或 Session 变更；
- emit/schedule/no_op 与 `director_entry_cursor` 原子推进；commit 时同时物化 recipient snapshot；release/cancel 与 Entry 生命周期、对象当前状态和 world version 原子提交；
- pending Entry 的 Session 范围使用稳定 `delivery_session_id`，explicit audience 使用稳定 Agent ID；release 时解析动态范围并固化最终 recipients；
- 按已完成 Character 决策步的配置间隔检查 pending Entry，持久化角色回合与导演检查位置；角色继续聊天时可定向释放通知，不要求先进入 wait；
- 将 commit outcome 反馈给发起 Agent，补上最小 `observe_outcome`；Reflection 可以先 No-op。

若采用第 6.5 节的创作目标，DirectorView 只额外接收 Scenario 中的目标与基于已提交证据的进度；现有 emit/schedule/release 权限不扩张，不能为达标强制 Character 互动。

完成条件：咖啡开始—pending—release—可见 Agent 获知的 Golden Trace 可重放；“只向 Anon 私语”的 Entry 在同 root 的 Tomori/Soyo 视图中不可见，直到 Anon 自己产生新发言；重复消费 source Entry 不重复 schedule；split/merge 后 pending Entry 不丢失、不复制；越权读取 Memory、冒充 Character 行动和非法 audience 均被拒绝。

### Phase 6：Agent Memory、Trace 与恢复一致性

交付：

- 持久化当前通用 `MemoryRecord`，以绑定的 `WorldRef` 和 `world_id + agent_id + memory_id` 关系键隔离；`namespace` 只表达记忆类别；
- 将 M1 的 `PersonaState` 完整保存到 `agent_runtime_states`，恢复普通 Plan queue / 当前计划引用、观察与反思累计；对话轮数与调度进度按 M4 的归属保存，不恢复已删除的 daily / 旧时间 cooldown；
- 实现第 6.5 节工作副本与整体提交协议：World、Agent State/Memory、决策幂等信息与调度位置同事务，提交成功后才发布内存状态；
- 在隐私字段和失败语义冻结后，再决定是否增加 `generation_traces` 表；若增加，只关联 proposal、event entry、validation 和 model repair，不保存 credential 或完整私密上下文；
- 注入 World SQL 写入后、Agent SQL 写入前以及 commit 后/内存发布前的崩溃；前者整体回滚，后者从完整持久化结果重建。MVP 的最小 outcome feedback 不调用模型，不保留未定义的跨事务 Memory 缺口。

完成条件：在关键边界注入崩溃后重启，世界版本、Memory、待互动状态和下一调度角色保持确定。

### Phase 7：Broadcast 导出、Codex 制作交接与 MVP 验收

交付：

- 用最小 Broadcast Fixture 读取 `story_watermark` 前的 `BroadcastStageView`，从彼此隔离但通过父子边连接的 StoryLine 中选择切镜；不得把扁平全量 Entry payload 直接交给模型；
- Broadcast 只能编排 committed `EventEntry`，生成的每个对白、旁白和动作 Beat 都必须携带 `source_entry_ids + PresentationBinding`；允许跨独立 Line 对齐/重排，但不能改写 Entry 的提交位置和世界事实时间；
- 持久化并导出带 `WorldRef`、revision、精确 source Entry 正文、Line 关系与 PresentationBinding 的可读故事线 JSON；素材引用限于当前 Project 声明的集合（可包含显式共享素材），不能读取其它 Project 的运行结果；
- Worker Agent 保留为未来架构角色，但本阶段不实现、不接入。由进程外 Codex 临时完成素材配装、人物动作和镜头工作，并同时把 BroadcastPlan 转成现有 `story.json` 或 WebGAL 可解析脚本/JSON；
- 不新增 `RenderPlan / RenderJob / RenderArtifact / WebGALCompiler` Runtime contract 或数据库表，不要求动态 Render 自动接线；Codex 产物通过现有 authoring validation/build 校验，原始 BroadcastPlan 作为事实来源与溯源依据保留；
- 完整运行 Anon/Soyo + Tomori 场景，保存 deterministic fixture、数据库和 Trace；
- 跑现有 Python 质量门禁与新增集成测试。

完成条件：从指定 Project 的空数据库启动、连续互动、Session 重组、暂停保存、关闭进程并加载同一 World 继续；产生可读、来源完整且不会混入其它 Project 数据的 BroadcastPlan。验证预算耗尽后的追加额度续跑；命中合法结局在 EndingRule 确认后验收。Codex 制作与 WebGAL 转译是人工交付步骤，不要求一条 Runtime 命令自动完成。

## 8. 存疑、必须重新设计讨论的内容

### P0：进入 Phase 3-4 前必须冻结

1. **互动参与映射。** 自主加入无需批准、社交拒绝不自动 split 已按第 6.3 节确认；仍需用 Golden Trace 落实哪些明确行为表示自身加入/离开，哪些只发送跨 root 消息。单个角色加入只转移自身，不能自动合并原组全体成员；客观可达性不是入组审批。
2. **待回应关系的创建和失效策略。** 最小持久化形态已经冻结为 `interaction_requests(request_entry_id, request_kind, requester, recipient, status, resolution_entry_id, updated_world_version)`，`RespondAction` 增加 `in_reply_to_entry_id`。仍需用 Golden/Badcase 决定哪些 utter/interact 会创建社交 request、多个 pending request 的优先级及延期/取消条件；request 不承担入组批准，也不要求闲聊每句都被回答。
3. **Object interaction 的稳定 operation。** 当前 `InteractAction.target + description` 无法证明 `start_brewing`；需冻结 `affordance_id` 或 typed operation union，并定义 world-version 失效、参数、幂等和重放语义。
4. **硬可见性矩阵。** 五种 channel 各能暴露哪些字段，direct interaction、旁听、耳语、跨地点消息和 commitment update 如何区分；Character Skill 只能改变注意力和解释，不能扩大硬可见范围。
5. **调度优先级与空转。** 角色继续交谈，Director 按回合进展检查客观 pending Entry 已确认；按第 4.2 节默认方案实现检查间隔与恢复。被点名优先与 round-robin 仍需验证不饥饿，角色主动 wait/no_op 仍需有界退避；不能因咖啡 pending 就让整个分区静态等待。
6. **World update 的最小提交边界。** 一次 action 同时导致对象变化和几条 `EventEntry` 时，需用 Golden Trace 固定 `source_index` 和原子性。合法提案可以未生效，不是审批拒绝；未生效时只提交合适的私有反馈和调度进度，不宣称成功。Director 不参与 Character commit；首版不默认开放部分生效的复杂语义。
7. **Agent 暂存结果与最小 outcome feedback。** 保存/续跑采用第 6.5 节的协调事务：Agent 自己计算认知状态，World 与私有状态一起落库。仍需在实现中明确暂存接口、Observation cursor 和合法未生效反馈；PLAN 历史与当前计划不重复作为执行真值。首轮不把模型 Reflection 放进这个原子步骤。

独立待确认：首个 Scenario 接受哪些自动结局、结束前必须处理哪些互动、世界截止时间与未来唤醒如何配合。手动保存/续跑不等待上述讨论，按第 6.5 节实现；角色指定结局不能由 Director 强制达成。

### P1：底座可先实现，但完整 MVP 前要有结论

8. **关系型当前状态与 `EventEntry` 的边界。** 一次 Proposal 可以追加几个 Entry？Entry 的 typed fields/details 是否足以解释同事务应用的公共状态变化？持续状态和因果桥接如何表示？MVP 不为此新增同义 `WorldEventRecord`、`WorldChangeRecord` 或独立 Recognizer，先用 Golden Trace 冻结最小 Entry union。
9. **重组后的调度优先级。** 第 6.5 节将调度进度绑定稳定 Session 节点，随角色保留；历史 Line 引用仍使用 `(root_session_id_at_commit, topology_version)`。还需用 Golden Trace 冻结 root 的成员进度聚合、邀请优先和退避规则。
10. **串行运行验证。** 第 6.5 节已收敛为同 World 同时只派发一个 Character/Director 决策；首版不设计跨 root 并行模型计算或部分合并提交，验证版本门禁与恢复即可。
11. **Director 回合检查与显式世界时间。** 主路径按第 4.2 节让角色继续聊天、Director 按回合检查待办，不要求咖啡实时倒计时。回合不等同世界分钟；显式 `next_check_at / wait.next_wakeup` 仍使用世界时间，离线冻结。只有首个 Scenario 确实需要精确定时约束时，才补充世界时间推进与跳时规则，不能用对话轮数绕过已声明的时间前提。
12. **Entity/Location 的 MVP 边界。** 首条 Trace 必须结构化哪些角色、地点、对象和资源事实；既不能回退到任意 dict patch，也不应提前重建 Maze/物理模拟。
13. **Generation Trace 的隐私与事务关联。** Prompt、模型输出和 repair 信息哪些允许持久化，如何去除 credential/私密 Memory；Trace 写入失败是否影响 world commit。
14. **重命名迁移已在 M1 收口。** 已一次性采用 `AgentView / view` 并要求 WorldRef；无 PerceptionFrame/frame alias 或旧 Runtime 格式自动兜底。Manifest 配置版本不变。
15. **Story watermark 与迟到 Entry 策略。** 三条时间轴和 PresentationBinding 已冻结；仍需决定由谁声明“某个世界事实时间前不再正常补入迟到 Entry”、已发布区间的 revision 保留多久，以及补叙默认使用 `flashback` 还是生成新的连续片段。

### 延后，不阻塞本轮 MVP

- 抽取通用 `CognitiveController`、按角色建立 Controller 子类或注册中心；
- Director/Broadcast 的完整长期 Memory 与 Reflection；Director 代替 Character 决定行为始终不允许，第 6.5 节的创作目标建议也不改变这一边界；
- 生产模型 Provider、复杂 Tool、LangGraph 或持久 Graph checkpoint；
- 通用 Event Recognizer、完整物理/空间模拟、复杂资源争用；
- 多进程公平调度、高可用、长期 30 分钟领先库存和成本优化；
- Worker Agent 的程序内接入、自动素材配装、`BroadcastPlan -> WebGAL` 转译/编译层、RenderJob/Artifact/Cursor 持久化、Dynamic Render 自动接线；当前由 Codex 进程外完成素材与脚本制作；
- 高级 Broadcast 推荐策略、长期 Plan 历史压缩和复杂观看个性化；Phase 7 只保留最小 BroadcastPlan/Binding 持久化与导出。

## 9. 建议的首批测试清单

```text
test_same_session_utter_then_response
test_join_requires_no_member_approval
test_social_rejection_does_not_force_split
test_agent_can_choose_to_leave_after_social_rejection
test_join_transfers_only_the_acting_agent
test_pending_environment_does_not_block_conversation
test_director_checks_pending_after_character_turns
test_director_check_cadence_survives_pause_resume
test_ineffective_action_saves_feedback_without_success_entry
test_response_requires_visible_committed_source_entry
test_response_explicitly_links_request_entry
test_interaction_request_and_entry_commit_atomically
test_restart_recovers_pending_interaction_request
test_uttered_intent_cannot_schedule_completion_entry
test_committed_process_start_can_schedule_pending_entry
test_pending_entry_source_is_idempotent
test_pending_entry_follows_stable_session_across_split_merge
test_pending_entry_release_uses_current_root_and_visibility
test_director_view_excludes_private_agent_state_and_uncommitted_proposals
test_director_cannot_release_unafforded_environment_entry
test_release_updates_pending_entry_object_and_version_atomically
test_uncommitted_proposal_is_never_visible
test_agent_view_hides_private_fields_and_other_sessions
test_explicit_whisper_is_hidden_from_same_session_non_recipient
test_entry_recipient_snapshot_survives_later_session_merge
test_hidden_source_entry_does_not_leak_through_reply_or_cause_link
test_director_and_broadcast_views_include_restricted_committed_entries
test_story_line_reads_persisted_dialogue_action_and_environment_entries
test_story_line_query_never_returns_entries_from_another_line
test_story_line_merge_has_both_parent_lines
test_story_line_split_creates_two_child_lines_without_new_sessions
test_stage_view_exposes_all_committed_lines_but_not_agent_private_state
test_event_entry_commit_position_is_unique_and_stable
test_event_entry_source_key_is_idempotent
test_event_entry_graph_rejects_cycles_and_invalid_cross_line_edges
test_pending_event_entry_is_hidden_until_committed
test_committed_event_entry_is_immutable
test_broadcast_can_align_independent_cross_line_entries
test_broadcast_cannot_hide_causal_inversion_without_flashback
test_presentation_binding_does_not_mutate_entry_time
test_world_ref_mismatch_is_rejected_before_write
test_each_project_uses_a_distinct_sqlite_file
test_project_database_has_exactly_one_matching_identity
test_project_database_rejects_copied_database_from_another_project
test_two_projects_can_reuse_internal_ids_without_state_leak
test_two_worlds_in_one_project_do_not_share_entries_sessions_or_memory
test_broadcast_export_is_bound_to_one_world_ref
test_broadcast_export_cannot_reference_another_project_asset
test_union_part_merge_updates_roots_and_members
test_union_part_split_exactly_repartitions_members
test_union_part_rejects_overlap_missing_member_and_foreign_item
test_session_node_count_stays_equal_to_agent_count
test_session_merge_does_not_pull_unaccepted_members
test_agent_can_decline_invitation_without_root_change
test_agent_leave_splits_root_partition
test_stale_world_version_is_rejected
test_world_update_rolls_back_at_each_checkpoint
test_restart_recovers_world_sessions_memory_and_next_turn
test_scenario_public_and_private_background_are_isolated
test_scenario_seed_initializes_expected_root_partitions
test_viewer_event_switch_does_not_change_world_history
test_same_seed_and_fixture_produce_same_relational_state
```

第 6.5 节保存/续跑验收（自动结局相关测试待 EndingRule 确认）：

```text
test_pause_resume_preserves_world_sessions_and_pending_entries
test_load_world_never_reinitializes_seed_or_starts_generation
test_restart_restores_persona_plan_memory_cooldowns_and_next_actor
test_world_private_state_and_scheduler_commit_or_rollback_together
test_pause_racing_decision_commit_preserves_one_complete_boundary
test_late_decision_from_before_pause_cannot_commit_after_resume
test_second_resume_cannot_take_over_a_live_world_runner
test_offline_time_does_not_advance_world_time
test_resume_with_additional_budget_continues_exhausted_world
test_idle_agents_with_future_wakeup_do_not_count_as_story_ended
test_decision_budget_counts_noop_and_survives_restart
test_ending_requires_committed_evidence_and_declared_closure
test_character_rejection_can_reach_an_alternative_ending
test_director_story_goal_cannot_override_character_choice
test_export_includes_readable_lines_sources_and_stop_outcome
```

## 10. 实施顺序结论

最短路径不是先扩展 Agent 认知抽象，而是：

```text
ScenarioSeed contract
-> WorldRef + per-Project SQLite factory
-> UnionPart
-> SQLite models
-> Runtime bootstrap / WorldInitializer
-> World.apply_change()
-> AgentViewBuilder
-> EventSessionRunner
-> 同 root 互动与 merge/split
-> EventEntry + recipient snapshot / StoryLine / StageView
-> Director emit + pending EventEntry lifecycle
-> Memory/Trace 恢复协议
-> BroadcastPlan / PresentationBinding
-> project/world-scoped BroadcastPlan export
-> Codex 进程外承担 Worker + WebGAL 转译（非 Runtime 实现）
```

可以从另一个分支搬来的是 Scenario Seed 校验、World 初始化事务、旧 `WorldEventRow` 的稳定 ID/source/因果/顺序约束思路和故障测试；不再搬通用 Segment、Version、Entity Revision、逐版本 Snapshot、successor Session，也不搬其“Director 读取完整 Snapshot + 未提交 Proposals 后统一补完”的权限模型。每个 `project_id` 必须独占 `.runtime/<project-id>/world.sqlite`，库内以 `world_id` 保存同一作品的不同发展副本；`WorldRef(project_id, world_id)` 是所有跨 Runtime 边界的硬身份，不能只靠 Memory namespace 或调用约定隔离。EventSession 改为每 Agent 一个稳定节点，`UnionPart` 负责当前 root 分区的 merge/split，关系型当前状态负责重启恢复。`event_entries + event_entry_links + event_entry_recipients` 是唯一 Entry 持久化模型：角色结果直接 committed，客观异步结果先 pending；Director 可 emit/schedule 客观 Entry 并选择 audience，但不能替 Character 行动。`EventEntry -> StoryLine -> StageView` 将 committed Entry 组织为多线 Galgame 可读结构：Character 只看 recipient snapshot 允许的内容，Director/Broadcast 读取全部 committed StoryLine；Broadcast 用 PresentationBinding 跨线选材和安排演出时间，不修改 World 事实。Worker Agent 与 `BroadcastPlan -> WebGAL` 转译层均不进入本轮 Runtime，由 Codex 进程外完成素材配装、动作设计和可解析脚本/JSON 制作。剩余需要实现的是 Project 数据库硬隔离、WorldRef、Scenario bootstrap、AgentView、WorldUpdater、Entry 生命周期与 recipients、StoryLine 组织与隔离、互动参与协议、Director 定向发布、pending Entry 唤醒/释放、待回应语义、调度规则和最小 BroadcastPlan 导出。`PersonActLoop` 当前保持原状，等运行闭环证明出现更大的共同认知控制职责后，再把它纳入真正的 `CognitiveController`。
