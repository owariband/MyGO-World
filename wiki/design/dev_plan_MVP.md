# MVP 分阶段执行计划

> 状态：M1 开发与自动验收完成，待用户 Review；M2–M7 尚未执行。
> 更新日期：2026-09-08
> 开发基线：`master@fc1d402`，已推送；不是可运行 World Runtime 的验收版本。
> 设计依据：[MVP_dev.md](MVP_dev.md)。该文档解释模型与规则，本文负责执行顺序、review 单元、验收与进度追踪。

## 1. 执行方式与完成边界

前期按“基础能力 / 可验证闭环”交付，框架成型后按 feature 交付。不先搭一个没有消费者的完整框架，也不要求每个目录对应一个阶段。

- M1–M3：基础契约、存储、初始化、单步提交；主要 review 数据所有权和不变量。
- M4–M6：自由互动、导演、导播；主要 review 可运行场景和功能边界。
- M7：把已通过的闭环串起来做验收，不在最后首次补持久化、安全或恢复。
- 先用 Fixture 验证确定性机制，再单独评估真实模型行为。Fixture 的固定动作只服务测试，不能写进真实角色策略。
- 以本文的 M 编号作为后续开发顺序；原设计第 7 节 Phase 编号作为设计分组参考，不另维护一套完成状态。

工程 MVP 的完成边界：指定 Project 初始化独立 World → 角色互动与分合组 → 导演处理客观事项并定向通知 → 手动暂停、关闭进程、读档续跑 → 导播导出可读故事线 JSON。Worker 配装与 WebGAL 转译由进程外 Codex 承担。

不纳入本轮：通用 CognitiveController、长期 Reflection、复杂物理模拟、多进程并行生成、生产 Provider 体系、自动结局、程序内 Worker/素材配装/WebGALCompiler、实时动态播放自动接线。预算或空转上限导致 paused，不伪造 ended。

2026-09-08 实现：M1 已移除 daily 与旧时间冷却，接入普通 `plan_queue + active_plan_id`，known_place_ids 保留暂不用；空队列才规划，不按日期重置，不凭 Proposal 宣称完成。50 轮对话约束随 M4.1 调度落地；计划的等待/完成/取消和推进随 M3.2 outcome 落地。实际字段、接口与测试见 [M1 开发记录](M1_dev_log.md)。

### 基线与已知问题

- 已有 PersonAct 单次认知链、进程内 Memory、Character Skill、ModelGateway 和制作层工具；不要重做这些模块。
- `fc1d402` 的验证记录：Python 64 项、Node 14 项通过，Ruff 与 Pyright 通过；这是基线记录，不代替后续阶段测试。
- 基线曾有 Gateway 兼容问题：标准解析器将 JSON 数组变成 list，与 strict tuple 冲突。M1.1 已修复并加入标准 parser 回归；基线全绿不是实网 Provider 已验收。源码：[model_gateway.py](../../agent_runtime/model_gateway.py)、[test_model_strategy.py](../../agent_runtime/tests/test_model_strategy.py)。
- 复用分支以设计调研过的 `05c5404` 为参考版本；当前 `origin/mvp` 已是 `fe8fd22`，不将后续变化视为已审查。每次复用记录实际来源 commit，不直接整分支 merge。

## 2. 阶段总览

| 阶段 | 开发目标 | 阶段结束时能展示什么 | 前置依赖 |
| --- | --- | --- | --- |
| M1 基础契约与现有接入修正 | 修正 Gateway，建立 WorldRef 与通用 UnionPart | 严格 JSON 模型边界和分区结构有独立测试 | 当前基线 |
| M2 Project 世界创建与加载 | 独立数据库、Scenario、公开/私有初始状态 | 创建两个隔离作品，重启后加载同一初始存档 | M1 |
| M3 单步世界与认知提交 | Entry、可见性、World/Agent 原子提交 | 一个角色行动生效或未生效后，可从 DB 重建一致结果 | M2 |
| M4 自由互动与可恢复运行 | Runner、多组互动、StoryLine、暂停续跑 | 三人可对话、加入、离开，中途重启继续 | M3 |
| M5 导演客观事件闭环 | pending Entry、按回合检查、定向通知 | 角色继续聊天，导演只通知 Anon 咖啡好了 | M4 |
| M6 导播编排与 JSON 导出 | 多线选择、呈现对齐、Plan 持久化与导出 | 离开 DB 也能阅读并交给 Codex 制作的故事 JSON | M4；完整咖啡场景依赖 M5 |
| M7 MVP 联合验收 | 整合场景、故障恢复、隔离与制作交接 | 可复现的工程 MVP 证据包；模型质量另行标记 | M1–M6 |

M1.1 与 M1.3 可独立推进；M5 与 M6 的局部 Fixture 开发可在 M4 完成后并行。共享 contract/schema 的修改必须明确负责的任务，避免两个分支各自定义同一模型。

## 3. Review 与 Trace 规则

### 工作单元

使用稳定编号 `M1.1`、`M1.2` 等。每个编号对应一个可独立解释、测试和 review 的改动，不等于一次性写完整个阶段。

1. 开工时说明任务 ID、设计依据、改动落点、验收用例和本次不做什么。
2. 一个 PR/提交组围绕一个主要任务；相关实现、迁移和测试一起交付，不把不同 Agent/存储/制作层的大改混成一个包。
3. 若任务过大，保留原 ID 并拆 `M4.2a / M4.2b`；不要重排已有编号，使历史引用失效。
4. 每个任务先通过自己的测试，再进入 REVIEW；存在依赖未完成或验收证据缺失时不能标 DONE。
5. 只有当前阶段全部必需项 DONE，阶段门禁才算通过；不以“文件已经建好”或“模型能说一句话”判断完成。

表内状态统一为 `TODO / DOING / REVIEW / DONE / BLOCKED`。`BLOCKED` 必须写清依赖和解除条件。阶段状态由子任务推导，不再维护另一份易漂移的百分比。

### 开发计划的文件级清单（固定要求）

2026-09-08 用户确认：以后每次开发计划必须用**带改动标注的文件树**列明具体 Folder、File、操作类型及改动内容，不能只写模块名或“完善相关代码”，也不用重复目录路径的表格代替文件树。树从仓库根目录或明确标出的子目录展开，逐文件列出生产代码、测试及必要配置；文件行统一写为 `文件名 [NEW / UPDATE · 任务 ID] — 具体改动`，新增目录也要标明。

- `NEW（新增文件）`：当前不存在的文件；说明新增职责与必要性。
- `UPDATE（修改 / 完善现有文件）`：在已有文件中修复、补充或调整；尽量定位到类、函数或字段，并说明保留哪些现有行为。向现有文件增加类或函数仍是 UPDATE，不算新增文件。
- 涉及移动、重命名或删除时单独标明操作和原路径 / 新路径，不能藏在 UPDATE 中；计划不替代执行授权。
- 总览可以链接阶段明细；具体开发前必须补齐当前任务的逐文件清单。尚未确认的落点标为待调查，不把猜测写成已确定范围；实际范围变化时同步说明差异。
- 开发量只估算新增 / 改写行数，区分生产代码与测试，不使用人日；交付时按实际 diff 核对文件清单。

### 每个完成任务留下的证据

固定维护两种文档：本文 `dev_plan_MVP.md` 负责范围、阶段编号、验收与进度；阶段 `Mx_dev_log.md` 负责实际功能、带标注文件树、真实新增/删除行数、测试结果、Review 与后续交接。原 `M1_dev_plan.md` 已更名为 [M1_dev_log.md](M1_dev_log.md)，不并存两份同义阶段文档。

在该行“状态 / 证据”中登记提交或 PR、测试位置、执行结果；细节链接对应阶段开发记录。未经用户要求不自动 commit / push，尚未提交时明确写 worktree。提交标题可采用 `feat(M4.2): ...`、`fix(M1.1): ...`，无需为了编号改造程序日志系统。

```text
任务：M3.2
依据：MVP_dev.md §6.5
提交 / PR：实际 hash 或链接
验证：实际命令、测试名、通过/失败结果
演示：project_id、world_id、Fixture ID、有关 Entry/decision ID
限制：尚不支持什么；是否有需要后续任务接手的问题
```

开发追踪与运行追踪分开：前者是任务 → commit → test；后者沿已有 WorldRef、decision/source ID、Entry 提交位置和 StoryLine 关联。测试 DB、Trace、模型配置中的秘密与私有上下文不提交到 Git；只提交必要 Fixture、脱敏预期与可重建步骤。

## M1. 基础契约与现有接入修正

实际接口、文件清单与测试证据见 [M1_dev_log.md](M1_dev_log.md)；ModelGateway 保留单一 `generate` 入口。实现位于当前 worktree，等待用户 Review，不伪造提交记录。

**开发目标：** 保住当前 PersonAct 能力，为后续存储与运行建立最小公共基础，不提前定义 Director/Broadcast 全套模型。

**落点：** 现有 `agent_runtime/model_gateway.py`、`world/contracts.py`、相关 PersonAct/Memory 边界；新增 `agent_runtime/common/union_part.py`。

| ID | 交付范围 | 独立验收 / Review 重点 | 状态 / 证据 |
| --- | --- | --- | --- |
| M1.1 | 修复结构化 JSON 与 strict tuple 的适配；统一 Gateway 验证边界 | 标准 parser 路径与完整 Agent 调用链；合法 tuple 通过，错误输出失败，repair 有界与诊断脱敏 | REVIEW · 已实现，[测试证据](M1_dev_log.md#6-自动测试与验收证据) |
| M1.2 | daily → 普通 Plan queue；WorldRef 身份传播；PerceptionFrame → AgentView 一次迁移 | World/Agent/scope 错配先于 replay/认知；JSON 重建保持队列；不把 Proposal 当完成 | REVIEW · 已实现，[实现与测试](M1_dev_log.md#4-m12身份角色状态与兼容边界) |
| M1.3 | 不带业务语义的 UnionPart：merge/split 与只读查询 | 完整覆盖、无重复、root 自指、双索引一致；非法输入无半更新；480 步确定性序列 | REVIEW · 32 tests passed，[实现记录](M1_dev_log.md#5-m13unionpart) |

**阶段门禁：** 现有回归通过；新增基础能力有独立测试。M1 不要求 SQLite、调度器或真实付费 Provider；标准解析路径回归不是实网模型验收。

## M2. Project 世界创建与加载

**开发目标：** 有一份真正按 Project 隔离、能初始化、能加载的存档。角色私有状态在此阶段落库，不留到导演开发之后。

**落点：** 新增 `agent_runtime/sqlite.py`、`scenario.py`、`bootstrap.py`、`world/state.py`、`world/storage.py`、`world/initializer.py`；Agent State/Memory 存储留在 `agent/` 所属层；作品内容放 `projects/<id>/`。

| ID | 交付范围 | 独立验收 / Review 重点 | 状态 / 证据 |
| --- | --- | --- | --- |
| M2.1 | Project 数据库工厂与初始迁移；project_database/worlds、公共状态和稳定 Session 行 | `.runtime/<project-id>/world.sqlite` 路径受控；文件身份绑定、外键开启、World 内复合关联；两个 Project 及同 Project 两个 World 均不串读写 | TODO |
| M2.2 | ScenarioSeed/loader；正式 agents.json；初始地点、对象、分组与 knowledge assignments | project/manifest/scenario 身份一致，引用/接收者/hash 严格校验；公开知识和私密知识明确分流，不把完整 Scenario 交给每个 Agent | TODO |
| M2.3 | 一个初始化事务写公共状态、PersonaState 和完整 Memory；重建 UnionPart | 任一点失败不留半存档；每 Agent 一个稳定节点；重复创建不覆盖，初始分组可由 singleton 合并而来，Genesis 不伪造普通剧情 Entry | TODO |
| M2.4 | create/load 分流与最小可调用装配入口；加载默认 paused | 关闭连接/进程后重读得到同一状态；不存在的存档报错，seed/spec 不匹配不自动 bootstrap；加载不调用 Agent | TODO |

**复用：** 参考旧分支 Seed 校验、初始化事务、WorldRow 和迁移/失败测试。沿用当前 Agent contract，不搬旧私有状态所有权和 successor Session。

**阶段门禁：** 提供两个 Project、同名内部 ID 的隔离 Fixture 和重启加载证据。只为当前消费者增加 schema；Entry 完整行为在 M3，Director/Broadcast 专属字段随 M5/M6 迁移增加。

## M3. 单步世界与认知提交

**开发目标：** 先证明“一次决策”能安全生效、反馈、落库和恢复，再把它放进循环。

**落点：** `world/contracts.py`、新增 `world/entries.py`、`world/updater.py`、`world/view_builder.py`；PersonAct 的工作副本与 State/Memory Store；受信单步协调入口。

| ID | 交付范围 | 独立验收 / Review 重点 | 状态 / 证据 |
| --- | --- | --- | --- |
| M3.1 | EventEntry/link/recipients 与请求表；当前场景必要操作契约、WorldUpdatePlan/Result | commit_position/source_index 确定；source 幂等、正文不可变、link 同 World 且无环；稳定 affordance_id/operation 区分说话与真实操作，respond 显式引用来源 | TODO |
| M3.2 | 暂存 Proposal 与私有增量；World/Agent/Scheduler 共用事务；最小 observe_outcome 与 Plan queue 等待/完成/取消、推进 | 事务内不调用模型；版本/epoch/state revision 校验；SQL commit 后才发布内存；合法未生效不得完成计划；世界写入后失败整体回滚，commit 后崩溃能重建完整结果 | TODO |
| M3.3 | AgentViewBuilder；按现有 channel 分类冻结首版字段/recipient 可见性矩阵 | 私语不泄漏，跨组可见不自动加入，未提交/pending 不可见；Builder 不做 attention/novelty/Memory 写入；引用边不能泄漏隐藏正文 | TODO |
| M3.4 | 单步 Fixture 串联 view → decide → validate/outcome → 暂存 feedback → 原子 commit → publish/reload | 已生效、合法未生效、wait/no_op、stale/非法输入各有用例；重复决策不重复生成 Entry | TODO |

**必须在本阶段成立：**

- 合法操作未生效时，撤销依赖成功的私有增量，只保存适当反馈和进度；不写假成功 Entry。越界、过期或被暂停取消的工作副本不发布。
- no_op/wait 可以保存私有状态、决策完成位置和调度进度，不伪造 Entry 或世界时间推进。
- 当前计划/进度保存在 PersonaState，历史决定复用 PLAN Memory；读档不依赖 top-k 才想起未完安排，不另建第二套决策队列。
- observation cursor 表示处理过输入，不等于已经回应；未解决 request 不因游标前移而丢失。
- 公共状态由 World 计算，私有认知由 Agent 计算；共享事务不改变两者所有权。M4 将复用这一单步协调路径，不再写另一套提交实现。

**阶段门禁：** 有可重放单步 Fixture、权限矩阵测试与事务失败测试。仍不要求持续运行或自然对话品质。

## M4. 自由互动与可恢复运行

**开发目标：** 首个可运行的演员闭环；能连续互动、改变组合，并在任意完整提交点暂停和续跑。

**落点：** 新增 `event/runner.py`、`event/session.py`、`event/story_line.py`；扩展 bootstrap 控制入口、现有存储与必要契约。

| ID | 交付范围 | 独立验收 / Review 重点 | 状态 / 证据 |
| --- | --- | --- | --- |
| M4.1 | 同 World 串行 Runner；被点名优先、公平轮转；连续对话 50 轮后接行为 | 冻结轮数及重置语义，不用旧时间 cooldown；优先不强制回应；wait/no_op 有界，多 pending 不饥饿；决策额度在派发前持久扣减 | TODO |
| M4.2 | 自主 join/leave/transfer；把自身行为映射到 UnionPart | 无需组员批准；不欢迎只形成言行，不自动踢人；从另一组加入只转移本人；root 与 Join/Leave Entry 原子更新，五人始终五节点 | TODO |
| M4.3 | Entry → StoryLine/StageView；跨组与交汇历史 | line_key 使用提交时 root/topology；多线各自有序，merge/split 可回查父子关系，后来加入不扩大旧私语接收者 | TODO |
| M4.4 | pause_and_save/load_world/resume_world、Ctrl+C、运行锁、额度追加与恢复演示 | 加载保持 paused；暂停与提交串行裁决；旧 epoch 迟到结果不能落库；同 World 并发 resume 排他；重启不返还额度、不覆盖 Seed | TODO |

**执行顺序：** 先 M4.1，再 M4.2/M4.3，最后 M4.4 的完整操作验收；控制字段与持久进度必须随前面的功能一起写入，不等最后补。

**阶段门禁：** Anon/Soyo 交谈，Tomori 自主加入，其他人可表达不欢迎，角色自主 split；暂停、关闭进程、加载同一 World，继续原请求/计划/分组。未完成模型调用可重算，已提交 Entry 不重复，离线时间冻结。至少补一个“不加入或不回应也能继续”的反例。

**到此可称为：** 可恢复的多角色互动骨架。还没有导演咖啡闭环与最终导播输出。

## M5. 导演客观事件闭环

**开发目标：** 演员继续自由对话，导演根据故事进展处理客观待办，并控制通知对象。

**落点：** `agent/director/`、新增 `event/director_runner.py`；扩展 world Entry/对象状态与导演游标存储。依赖 M3 的稳定操作与原子提交、M4 的 StoryLine 和 Runner。

| ID | 交付范围 | 独立验收 / Review 重点 | 状态 / 证据 |
| --- | --- | --- | --- |
| M5.1 | DirectorView/Decision、允许的环境操作和合法 audience；先 Fixture 后模型 Strategy | 全局可读 StoryLine，但不读 Character 私有状态/未提交提案；不输出角色台词、动作或 Session 改组；在本任务明确 release 时如何携带告知方式/对象 | TODO |
| M5.2 | pending Entry 的 schedule/keep/release/cancel 与幂等消费 | 只读到“想煮咖啡”不能当成已开始；合法开始后才能登记相应完成事项；队列是同表查询，同一 ID 转 committed，source 与 cursor 同事务 | TODO |
| M5.3 | 按 Character 决策步间隔检查、idle 检查与进度恢复 | pending 不阻塞聊天；Director 调用不增加角色步数；轮数只触发检查，不代表世界分钟或强制完成；keep/release 与检查位置原子保存，空转有上限 | TODO |
| M5.4 | 定向通知、重组与跨进程恢复 | 只通知 Anon 时同组其他人不可见；Anon 转告形成新 Entry；Session 范围跟随稳定节点解析当前组，explicit 不扩大；重启不重复入队/发布 | TODO |

**接口收口：** 设计正文允许导演检查时决定告知方式，但示例 `release(entry_id)` 尚未表达参数。M5.1 必须把该能力落到受校验的契约中；不要默认在 schedule 时永久冻结所有告知选择，也不能通过任意 patch 改写待办 subject/completion 语义。

**阶段门禁：** “角色实际开始客观过程 → pending → 继续聊天 → 导演通知 Anon → Anon 自己选择回应/转告/行动”完整可重放；在 pending、release 前后注入重启仍不丢失、不重复。

## M6. 导播编排与 JSON 导出

**开发目标：** 导播读取多条可读故事线，选择片段并安排呈现顺序，输出可以独立阅读和交给 Codex 制作的 JSON。

**落点：** `agent/broadcast/`、`event/story_line.py` 的只读输入；Broadcast 所属的 Plan/Binding 存储与导出入口。不把这些表放进 World 事实所有权，不新增渲染运行时。

| ID | 交付范围 | 独立验收 / Review 重点 | 状态 / 证据 |
| --- | --- | --- | --- |
| M6.1 | BroadcastStageView/Plan/PresentationBinding；冻结首版编排范围与迟到 revision 规则 | 输入是分线正文、前情窗口与交汇关系，不是扁平全量日志；提交顺序、事实时间、呈现时间明确分离 | TODO |
| M6.2 | Fixture/Strategy 选材、切线、跨线时间对齐与因果检查 | 只能选 committed Entry；台词保真；推断有标记；独立线可对齐，因果倒叙必须显式表达，不能回写 World | TODO |
| M6.3 | Plan/Binding 持久化及 Project/World scoped JSON 导出 | 正文、source、line/link、呈现顺序、revision、运行状态/截止位置/未完事项完整；新编排创建新 revision；导出只读且不启动生成 | TODO |
| M6.4 | Codex 制作交接样例与现有 authoring 校验 | 限用本 Project 声明的素材；保留原 BroadcastPlan；手工转成现有 story.json/WebGAL 脚本并通过 check/compile，不新增 Worker API 或 Compiler | TODO |

**范围收口：** M6.1 可先采用“某次暂停时的固定提交范围 + 不可变 Plan revision”作为最小导出方式，但必须在 review 中明确：它只封闭本次选材范围，不证明某个事实时间之前永远不会再出现迟到 Entry。不能把最新 Entry ID 或暂停 cutoff 冒充事实时间 watermark；需要持续发布时再实现对应封口策略，不静默修改旧导出。

**阶段门禁：** 导出在不连接 SQLite 的情况下可逐句阅读，能追溯交汇、回复和因果；跨线对齐不改 source 事实；两个 Project 同名 ID 不混入同一 Plan。制作交接不要求“一条 Runtime 命令自动生成可播 WebGAL”。

## M7. MVP 联合验收与交付

**开发目标：** 交付一套别人能复现、能 review、能定位故障的 MVP，而不是再增加功能。

| ID | 交付范围 | 独立验收 / Review 重点 | 状态 / 证据 |
| --- | --- | --- | --- |
| M7.1 | 咖啡场景完整 Fixture、命令入口说明与干净环境重建步骤 | 从空 Project DB 初始化 → 多角色互动/重组 → 导演通知 → 暂停/重启继续 → 导播 JSON；各步有来源证据 | TODO |
| M7.2 | 累积隔离/故障/幂等测试和运行诊断关联 | 复用各阶段已有测试补跨模块组合；错库、迟到调用、SQL/内存边界崩溃、预算耗尽后追加续跑均有结果；脱敏 Trace 不改变事实 | TODO |
| M7.3 | 工程验收记录与制作交接回归 | 全部门禁通过，已知限制与未完成项列明；静态 WebGAL/动态 Fixture 能力不回归；不把未运行的 Provider 测试标成功 | TODO |

**真实模型试跑单独记录：** 有明确 Provider 配置与调用预算时，可运行若干条非固定动作轨迹，观察是否自然互动、是否重复或空转、角色是否违背可见信息、导演介入是否合适。这个记录不替代确定性权限测试，也不强迫每次 Tomori 都加入或都得到同一结局。未配置时标“未验收”，不为完成工程 MVP 临时扩建生产 Provider 体系。

**最终验收清单：**

- [ ] 两个 Project 使用独立数据库；同 Project 多个 World 隔离；身份错配在任何写入前失败。
- [ ] 初始化公开/私有知识准确分流；已有存档加载不重新初始化。
- [ ] 角色逐次决策与提交；加入无需审批，离开自主；节点数不随组合变化增长。
- [ ] 私语、跨组消息和后续转述的知识边界正确；对方不回应不会卡死整个世界。
- [ ] Entry 有稳定顺序、source 幂等与因果/交汇关系；合法未生效不产生假成功。
- [ ] 暂停/崩溃后世界、当前计划、Memory、请求、调度与导演进度一致恢复；迟到调用无权发布。
- [ ] 导演按对话进展处理 pending，只发布允许的客观事件，接收者由契约校验并固化。
- [ ] 导播选材与时间编排不改世界；导出的故事 JSON 可读、可追溯、不会串 Project。
- [ ] Codex 外置制作交接可执行；自动结局、实时渲染接线和真实模型品质的验收状态如实注明。

## 4. 通用验证与复用要求

Python Runtime 任务遵循现有工具链；先运行任务相关测试，交付时运行：

```bash
uv run ruff format --check agent_runtime
uv run ruff check agent_runtime
uv run pyright
uv run pytest
git diff --check
```

涉及 Node/制作层时运行 `npm test`；M6.4/M7 同时记录实际 Project 的 `check/compile` 命令与结果。Runtime 命令入口在 M2/M4 随实现确定，本文不把尚不存在的命令写成现在可运行。

复用 `origin/mvp` 内容时，提交说明至少包含：来源 commit/模块、保留机制、为当前 contract 做的改造、对应测试。M2 主要借 Seed/初始化，M3 借关系约束/事务/稳定 ID 与故障测试，M5 借有限可见性规则；不搬 lockstep、successor Session、重复快照表、旧 Proposal 或 Director 代替角色提交的权限模型。

诊断数据只保留定位必需的 WorldRef、decision/source/Entry ID、验证结果与重试计数，不默认落完整 Prompt/私有 Memory。若增加 Generation Trace 存储，在对应任务明确失败是否影响提交，不为“好 trace”创建另一套 World 真值。

## 5. 交付记录

2026-09-08：M1.1 / M1.2 / M1.3 已在 worktree 实现；Python **193 passed**、Ruff 格式/静态检查、Pyright、diff whitespace 检查全部通过；独立 Review 的诊断字段名泄漏问题已修复并回归。实际文件/行数、测试映射与命令结果统一登记在 [M1_dev_log.md](M1_dev_log.md)。未 commit / push；本文状态为 REVIEW，用户 Review 后再标 DONE。M1 不代表数据库隔离或多角色 World Runtime 已实现。

**下一工作单元：Review M1；通过后进入 M2.1。** 不在本次任务自动启动数据库或后续 Runtime 开发。
