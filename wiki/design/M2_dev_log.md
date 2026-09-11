# M2 开发记录：Project 世界创建与加载

> 状态：DONE；实现与自动验收完成，已提交并推送。
> 日期：2026-09-09
> 开发基线：`master@907a8a6`（M1 已推送）。
> 阶段提交：`5d2f496f6c76b2587e5aa39558c433fdb2a5354c`（`feat(M2): add project-isolated world bootstrap`，已推送至 `origin/master`）。
> 计划入口：[dev_plan_MVP.md](dev_plan_MVP.md#m2-project-世界创建与加载)

## 1. 交付结论

M2 已提供真正可持久化、可关闭进程后重读的 Project World 初始存档：

```text
projects/<project-id>/{project.json, agents.json, scenario.yaml}
        │ 严格加载、引用校验、canonical hash、Spec 编译
        ▼
create_world(project_id, world_id)
        │ 一个 World 初始化事务
        ▼
.runtime/<project-id>/world.sqlite
        │
        └── load_world(project_id, world_id) -> trusted LoadedWorld (paused)
```

- 一个 Project 独占一个 SQLite 文件；文件内允许多个独立 `world_id` 存档。
- 公共 World、每个 Agent 的私有 PersonaState、完整 Memory 及稳定 EventSession 节点在同一事务初始化。
- `load_world` 只加载已存在且为 `paused` 的 World，不会静默创建、恢复运行或调用模型。
- `for-the-band` 与 `rain-after` 是两份正式初始化配置；前者有五个稳定 Session 节点。
- M2 没有创建 EventEntry、InteractionRequest、Director、Broadcast 或 Runner 表/逻辑；这些仍属于 M3–M6。

## 2. 实际文件树与改动量

以下为实现代码写入完成、文档更新前的实际 diff。生产 Python（含迁移）为 **+2816 / -12 行**，依赖与迁移配置 **+247 行**，Project Fixture **+531 行**，测试 **+2183 行**，合计 **+5777 / -12 行**；文档行数不计入实现量。

```text
generative_go_world/
├── pyproject.toml                                      [UPDATE · M2.1 · +2]
│   └── 固定 sqlalchemy==2.0.52、alembic==1.19.2
├── uv.lock                                             [UPDATE · M2.1 · +183]
├── alembic.ini                                         [NEW · M2.1 · 37]
├── agent_runtime/
│   ├── sqlite.py                                       [NEW · M2.1 · 349]
│   │   └── Project 路径/身份/权限、连接策略、schema head 与原子首次建库
│   ├── scenario.py                                     [NEW · M2.2 · 472]
│   │   └── ScenarioSeed、YAML/JSON 严格加载、交叉引用与 canonical hash
│   ├── bootstrap.py                                    [NEW · M2.3/M2.4 · 565]
│   │   └── create/load 分流、跨域初始化事务、配置钉住与可信装配结果
│   ├── migrations/
│   │   ├── __init__.py                                 [NEW · M2.1 · 1]
│   │   ├── env.py                                      [NEW · M2.1 · 84]
│   │   ├── script.py.mako                              [NEW · M2.1 · 25]
│   │   └── versions/
│   │       ├── __init__.py                             [NEW · M2.1 · 1]
│   │       └── 0001_m2_world.py                        [NEW · M2.1 · 271]
│   ├── common/strict_json.py                           [NEW · M2.2 · 41]
│   │   └── JSON 重复 member、NaN/Infinity 与指数溢出的统一拒绝边界
│   ├── world/
│   │   ├── __init__.py                                 [UPDATE · M2.1 · +26/-5]
│   │   ├── state.py                                    [NEW · M2.1 · 170]
│   │   ├── storage.py                                  [NEW · M2.1 · 455]
│   │   └── initializer.py                              [NEW · M2.3 · 12]
│   ├── agent/
│   │   ├── personact/
│   │   │   ├── manifest.py                             [UPDATE · M2.2 · +12/-4]
│   │   │   │   └── Creator JSON 先严格解码再进入 Pydantic JSON mode
│   │   │   ├── state.py                                [UPDATE · M2.3 · +3/-3]
│   │   │   │   └── 持久化浮点拒绝 NaN/Infinity
│   │   │   └── storage.py                              [NEW · M2.3 · 117]
│   │   └── memory/storage.py                           [NEW · M2.3 · 237]
│   └── tests/
│       ├── test_project_database.py                    [NEW · M2.1 · 500]
│       ├── test_scenario.py                            [NEW · M2.2 · 646]
│       ├── test_agent_storage.py                       [NEW · M2.3 · 363]
│       ├── test_bootstrap.py                           [NEW · M2.3/M2.4 · 657]
│       └── test_persona_state.py                       [UPDATE · M2.3 · +17]
├── projects/
│   ├── for-the-band/
│   │   ├── agents.json                                 [NEW · M2.2 · 212]
│   │   └── scenario.yaml                               [NEW · M2.2 · 100]
│   └── rain-after/
│       ├── agents.json                                 [NEW · M2.2 · 129]
│       └── scenario.yaml                               [NEW · M2.2 · 90]
└── wiki/
    ├── index.md                                        [UPDATE · M2 文档导航]
    ├── log.md                                          [UPDATE · M2 维护记录]
    └── design/
        ├── dev_plan_MVP.md                             [UPDATE · M2 状态/证据]
        └── M2_dev_log.md                               [NEW · 本文]
```

实际代码量高于开工时估算，主要差额来自：ORM 与 Alembic migration 双重 schema 明示、严格 Scenario 负例、跨进程/并发/故障注入测试，以及首次建库的不可覆盖原子发布。没有为 M3 预建 Entry、Snapshot 或 Director 表。

## 3. M2.1：Project SQLite 与公共 World

### 数据库边界

```text
project_id = 选择作品配置，也是物理数据库边界
world_id   = 同一作品内的一次独立运行/存档

.runtime/
├── for-the-band/world.sqlite
└── rain-after/world.sqlite
```

路径只接受小写 slug；Runtime root、Project 目录和数据库文件均拒绝符号链接逃逸。目录权限收紧为 `0700`，文件为 `0600`。每个数据库有唯一 `project_database` 身份行；把 A Project 的文件复制到 B Project 目录会被拒绝。

首次创建不是直接修改最终文件：先在同目录构造临时数据库、迁移到 head、写入并验证 Project 身份、`fsync`，再用不可覆盖的同目录 hard-link 原子发布。并发创建者只会得到同一个完整文件；失败不会留下半迁移的 `world.sqlite` 或临时文件。已有数据库绝不在打开时静默升级，revision 不匹配直接报错；打开时还执行 `foreign_key_check`，损坏或离线篡改不会伪装成有效存档。

所有业务连接和 Alembic migration 都显式使用 Python 3.12 的现代 SQLite transaction control；因此一次 SQLAlchemy Session 的多表读取具有真实事务快照，迁移中途失败也会回滚 DDL。每条连接同时固定 `foreign_keys=ON` 与 `busy_timeout=5000`。

### 首版九张业务表

| 表 | 唯一职责 |
| --- | --- |
| `project_database` | 当前 SQLite 文件所属 Project 的不可歧义身份 |
| `worlds` | 存档头：Seed 身份、版本、世界时间、运行状态、创建时间 |
| `locations` | 当前公共地点 |
| `agent_world_states` | Agent 当前公开位置/状态，不含私有认知 |
| `objects` | 当前公共对象、位置、owner 与状态 |
| `world_facts` | 结构化公共事实；可绑定 World/Location/Object/Agent |
| `event_sessions` | 每 Agent 一个稳定节点及当前 `root_session_id` |
| `agent_runtime_states` | Agent 私有 PersonaState、revision 与编译 Spec digest |
| `agent_memory_records` | 每 Agent 的完整、按 `memory_seq` 有序 MemoryRecord |

另有 Alembic 自己的 `alembic_version`。M2 没有重复的 World JSON snapshot，也没有 `world_versions/entity_revisions/world_changes/world_events` 等尚无当前消费者的表。

## 4. M2.2：Scenario 与两份正式 Project 配置

`load_project_scenario()` 依次校验 Project 目录、`project.json`、`agents.json`、`scenario.yaml`，并要求四处 `project_id` 一致。JSON 顶层/嵌套重复 member、NaN/Infinity 与会溢出为非有限浮点的指数（如 `1e400`）在 Pydantic 前统一拒绝。YAML 仅作为易写格式，加载后必须能无损表示为 JSON；重复 key、YAML 日期对象、NaN/Infinity、未知字段、错误类型、悬空引用、重复 ID、重叠/缺失分区与非法私密接收者都会失败。

Scenario 只保存开场必要内容：

- 公共地点、对象、事实和 Agent 公开状态；
- 每个 Agent 的稳定 `sessionId` 及初始 UnionPart 分区；
- `recipients: all` 或 explicit Agent 列表的初始私有 knowledge。

canonical hash 按语义排序 locations、objects、facts、agents、partitions、tags 和 explicit recipients，并将 `-0.0` 规范为 `0.0`，不受 YAML key/列表的无业务顺序影响；内容或接收者变化一定改变 hash。`worldTime` 的显式 UTC offset 保留为故事本地时钟语义，不擅自归一为 UTC。本次正式值已固定进 golden test：

| Project | Scenario SHA-256 |
| --- | --- |
| `for-the-band` | `046fd7016734dcbe0e35ac7dae0a1d263f4237d89ac141a7fc04436275e06d59` |
| `rain-after` | `c4e3f46b9f9ce70b5e8ba14875bf1ab064208ad7ce1a53191df9673fc562d33c` |

`for-the-band` 固定五个节点：Anon/Soyo 初始同组，Tomori、Rana、Taki 各自 singleton；不会因为初始组合只创建两个或四个 Session。公共知识进入 World Fact；私密 knowledge 只展开到指定 Agent Memory。

## 5. M2.3/M2.4：原子初始化与可信加载

公共入口保持很少：

```python
create_world(repository_root, project_id, world_id, *, catalog, cognitive_config, clock)
load_world(repository_root, project_id, world_id, *, catalog)
```

`create_world` 在接触数据库前完成 Scenario 校验、Spec 编译、所有 Pydantic 状态构造和有限浮点验证；随后一个 caller-owned transaction 写入公共 World、PersonaState 与 Memory。首个 SQL 是 `worlds` INSERT，不用 SELECT 后升级写锁；SQLite 因而能串行化同 Project 的并发 Genesis。相同 `world_id` 稳定映射为 `WorldAlreadyExistsError`，不同 ID 均可成功。中途任一点失败都会回滚该 World 的全部行；重复创建不覆盖旧存档。Genesis 只建立初始状态，不伪造成普通剧情 Entry。

`load_world` 重新读取当前 Scenario/Manifest 并验证：

- Project 数据库身份；
- `seed_id + seed_version + seed_hash`；
- 完整 `agent_id -> CompiledPersonActSpec.digest`；
- Scenario 固定的 `agent_id -> session_id`；
- 每条私有 Memory 的 Agent scope；
- World 状态必须为 `paused`。

然后从稳定 Session 行重建 `UnionPart[str]`。`LoadedWorld` 是可信 Runtime bootstrap 的全角色装配对象，不是 AgentView、DirectorView 或 BroadcastView；聚合私有数据的方法显式命名为 `load_all_for_bootstrap`，这些 Store 也没有从 Agent package 根导出。M3 为单个角色构造视图时必须使用绑定 `agent_id + scope` 的读取路径，不能把该聚合对象传给任何模型。

## 6. 自动测试与独立 Review

执行结果：

```text
M2 四个新增测试文件                    95 passed
完整 Python suite                      367 passed in 9.16s
Ruff format --check                    59 files already formatted
Ruff lint                              All checks passed
Pyright strict                         0 errors, 0 warnings
Node 制作层回归                        14 passed
git diff --check                       passed
wheel migration 资源                   env.py / script.py.mako / 0001 均存在
```

M2 测试覆盖：两个 Project 与同 Project 两个 World 复用内部 ID 不串数据、复制 DB 身份与 FK 损坏拒绝、外键/文件权限/路径逃逸、ORM 与 migration 无漂移、首次建库及相同/不同 World 并发、迁移 DDL/Genesis 故障回滚、Scenario hash/重复 JSON member/非有限数值与有限数值兼容/引用/私密路由、非同序 Session ID 重启、重复创建、进程内重开与新 Python 进程重读、Seed/Spec/Session/scope 篡改拒绝，以及 bootstrap 不调用 Agent。

独立只读 Review 发现并已修复：

1. 非有限认知权重可能在 JSON 序列化后变成 `null`，造成失败语义不一致；现已在持久化前拒绝。
2. 首次建库原先会先暴露迁移中的最终文件；现改为完整临时文件后不可覆盖原子发布，并增加并发/故障测试。
3. Python 3.12 legacy SQLite 模式下 SELECT 不开启真实读事务；现显式启用 modern transaction control。
4. 加载时漏校验 Memory scope 与稳定 `agent_id -> session_id`；现已补齐篡改测试。
5. 全量私有状态读取入口容易被误用于模型视图；现明确限制为 trusted bootstrap assembly。
6. Alembic 自有 Engine 仍是 legacy transaction，DDL 无法可靠回滚；现与业务连接共用 modern transaction control，并强制 transactional DDL。
7. Genesis 先 SELECT 再写会在两个并发 World 间发生锁升级冲突；现改为先 INSERT 获取写锁，并稳定映射重复 ID。
8. 创建按 Agent ID、加载按 Session ID 排序，非同序 ID 重启后 tuple 会变化；现统一按 Session ID 并加入反例。
9. `project.json/agents.json` 曾静默接受重复 JSON member，且 `1e400` 可绕过常量检查变成 `inf`；现由共享严格 decoder 在进入领域模型前拒绝重复 member、非标准常量与浮点溢出。
10. `agents.json` 非法 UTF-8 曾泄漏底层异常；现统一映射为 Manifest/Scenario decode error 并有集成用例。
11. 打开 DB 只验身份、未发现离线造成的 FK 损坏；现增加 `foreign_key_check` 与损坏库拒绝测试。
12. canonical hash 曾区分语义相同的 `-0.0/0.0`；现规范化 signed zero，并为正式 Fixture 固定 golden hash。

第一轮修复后的最终只读复审未发现 High/Medium 阻塞项，并额外以多进程并发首次 open 验证最终只发布一个完整 Project DB；更深的 SQLite/Scenario 专项复审又发现第 6–12 项，均已按原始失败路径增加测试并修复。两位专项 reviewer 随后复跑真实 Alembic、并发 Genesis、strict JSON 与 Session restart 路径，确认全部 finding 闭合且无新增阻塞项。

## 7. M2 明确未声称完成的能力

- Agent 还不能连续自由互动；目前只创建/加载 paused World。
- 还没有 EventEntry、AgentViewBuilder、WorldUpdater 或一次决策的公共/私有原子提交；这是 M3。
- EventSession 目前只持久化五稳定节点和开场 root；merge/split 的运行时写入属于 M4。
- 没有 Director/Broadcast/Worker/WebGALCompiler。Worker 与 WebGAL 转译仍由外置 Codex 暂代。
- M2 不恢复 `running` World，也没有 pause/resume、运行锁、额度或未完成模型调用恢复；这是 M4.4。

因此本阶段准确结论是：**可创建、隔离、保存并确定性加载多 Project / 多 World 的开场状态；还不是可自由交互的 Agent Runtime。** 下一开发单元是 M3.1 EventEntry 与受限 World 操作契约。
