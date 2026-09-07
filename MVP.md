# MVP 决策

> 状态：当前 MVP 实施基线  
> 更新：2026-09-08

若旧文档仍保留 Go + Eino 的技术栈描述，MVP 实现以本页为准；既有 World / Event / Agent 权限边界不因此改变。

## 技术栈

- **开发语言：**Python，便于接入模型、评测和 Agent 生态。
- **工程工具：**使用本机已有的 `uv`、Python 3.13、`pyproject.toml` 和锁文件管理 Python Runtime。
- **数据契约：**Pydantic v2，用于定义和校验结构化领域模型。
- **模型边界：**不使用 PydanticAI；项目定义显式 `ModelGateway`，分别提供确定性 Fixture 和一个真实 Provider SDK 适配器。
- **模型配置：**首个真实适配器使用 OpenAI-compatible API，由全局运行环境提供 Base URL、API Key、默认 model ID 与模型参数，Character、Director 和 Broadcast 共用；密钥不进入仓库，后续可以按 Agent 类型增加 Provider 或模型覆盖配置。
- **结构化输出：**Provider 支持时使用原生 JSON Schema；否则解析 JSON 文本并使用同一 Pydantic Schema 校验，失败时仍只允许一次带诊断的语义修复。
- **环境来源：**进程环境变量是模型配置与密钥的权威来源，并可选加载未纳入版本控制的本地 `.env`；仓库只提供不含秘密的 `.env.example`。
- **持久化存储：**SQLite，适合 MVP 的单 Runtime 与串行提交，也便于本地重放。
- **本地数据目录：**每个 World 默认使用 `.mygo/worlds/<world_id>/world.sqlite3`；Ledger、Memory、Trace、Batch 和 Render 使用同一文件中的不同表组，锁和临时产物也位于 `.mygo/`，不进入版本控制。
- **数据库访问：**SQLAlchemy 2.x + Alembic，负责数据访问和 Schema 迁移。
- **测试与评测：**pytest + Pydantic Evals，覆盖确定性测试与模型效果评估。
- **可观测性：**OpenTelemetry，统一记录模型调用和运行链路 Trace。

## 范围与非目标

- **首个里程碑：**完成一条 Fixture 驱动、零模型请求的 Vertical Slice。
- **模型接入：**Fixture 链路可重放后再接入真实模型。
- **一期非目标：**不启动播放器、不提供玩家选择，不实现异步角色时钟、多 Session 并发、向量检索、自动人格更新、动态持久 Entity 创建、多 Provider、通用 WebGAL DSL Parser、分布式部署或通用 Agent 平台。
- **二期兼容：**一期不接收 Player Event Request，但 Director 的 SegmentDraft、Segment Validator 与 Event Recognizer 不得假设每个候选事件都有 Character actor 或一一对应的 ActionProposal；无主体环境/桥接事件必须走通相同的候选、校验与提交链路。

## 所有权与权限

- **世界事实：**World Runtime 是唯一权威。
- **Agent 权限：**Character、Director 和 Broadcast 只产生 Proposal 或 Plan。
- **人物外显：**Character Presentation 是 Character Entity 拥有的公开可感知值对象，保存跨场景相对稳定的外貌、外显气质、声音和可观察行为倾向；同一 Interaction Scope 内的其他角色通过 PerceptionFrame 获得这些线索。由线索形成的“亲切”“虚伪”或“难以接近”等第一印象属于观察者自己的 Belief，不能作为共享事实写回 Presentation。
- **外部请求：**未来的玩家自然语言输入只是绑定 `based_on_world_version` 的 Player Event Request；Director 可以将其解释为带来源证据的 External Event Candidate，但不能提交事实。没有对应 ActionProposal 时，外部事件不得替持久 Character 作出重要选择、生成台词或改变动机。
- **Agent 工具：**首期模型不主动调用工具；后端先按权限投影结构化上下文，再一次性传给对应 Agent。
- **Prompt 职责分层：**模型输入在职责上分为后端 Agent Contract、版本化 Runtime Skill、场景或 Session 策略及当前 Runtime Context。Agent Contract 负责技术约束，Runtime Skill 只负责跨场景稳定的创作倾向，场景策略负责当前目标与节奏，Runtime Context 提供当前事实与私有记忆；当前 MVP 尚未实现独立 Scenario Policy 时，只组合其余三层。
- **契约执行：**输入裁剪、私有数据隔离、输出 Schema、稳定 ID、来源证明和权限校验由后端负责；自然语言只解释语义，不能作为安全或正确性的唯一保证。当前 MVP 仍要求模型回传部分上下文字段以保留完整 Trace，但 Runtime 将其视为不可信副本并逐项校验。
- **提案表达：**ActionProposal 只包含简短 `intent_summary`，不请求思维链；`utterance` 显式列出接收者，为空时对 Session 公开。
- **行动目标：**`move` 只能选择 PerceptionFrame 中的可达地点；`interact` 引用既有对象或角色时只能选择当前可见实体，但交互意图可以使用自然语言，由 Director 结算、Proposal Validator 与 Segment Validator 约束状态变更。
- **偶发道具：**自然语言可以包含只属于当前 Event payload 的普通 Incidental Prop；它保存 Event 内唯一 `prop_key`、类型和描述，但没有全局 Entity ID、后续状态或专属素材。MVP 不实现升格，未来 Entity 可通过 `origin_event_id + origin_prop_key` 追溯来源。
- **调用保护：**模型调用默认 120 秒超时；仅网络错误、限流和服务端错误最多重试两次。Schema 或语义错误不计入传输重试，最多进行一次带诊断的修复。
- **调用预算：**实际 Provider 请求（包括传输重试和语义修复）按 CLI 命令计数；`advance` 默认上限为 40，`render` 默认上限为 6，达到上限即失败退出，并允许显式覆盖。
- **持久副作用：**只有 World Committer 能改变权威 World、Session、Queue 和 Agent Memory 状态；`init` 向它提交由已校验 Scenario Seed 构造的 `GenesisCommitPlan`，Generation Wave 向它提交 `ValidatedCommitPlan`。只有 Render Gateway 能写外部 WebGAL 目录；Batch、Trace、Render 元数据和 Skill Binding 只能由各自 Repository 按既定契约持久化，Agent 本身不能执行副作用。
- **播放器边界：**WebGAL 只负责展示，不拥有或推进世界状态。

## 运行语义

- **生成链路：**`advance` 与 `render` 是两条分离的显式链路；前者生成并提交世界事实，后者只从固定 World Version 投影演出。

  ```text
  advance:

  World Snapshot + Agent Memory
  → TurnScheduler（点名 → Director 选择 → round-robin fallback）
  → DecisionTurnRecord
  → PerceptionProjector / 被选中角色的 PerceptionFrame
  → Character / ActionProposal
  → Proposal Validator
  → Director / SegmentDraft
  → Segment Validator / ValidatedCommitPlan
  → World Committer（单个 SQLite 事务）
      ├─ 暂存 WorldSegment + EntityRevision
      ├─ 应用 Session transition + Runnable Session Queue
      ├─ Event Recognizer → WorldEvent
      ├─ PerceptionProjector → Observation
      ├─ 接受的 Belief / Commitment changes
      └─ Snapshot + checksum
  → 提交事务 / 发布新 World Version

  显式 render:

  固定 World Version + 未处理 WorldEvent + Asset Manifest
  → Broadcast / BroadcastPlan
  → RenderPlanner（校验并解析素材）/ RenderJob
  → RenderCompiler / WebGAL DSL
  → Render Gateway / 不可变 WebGAL 文件
  → Render 元数据 + Broadcast Disposition
  ```

- **校验边界：**Pydantic/JSON Schema 只负责结构校验；纯确定性的 Proposal Validator 依据生成时的 PerceptionFrame 检查单行动、版本、角色所有权、可见目标和可达目的地，Segment Validator 再依据共同 Snapshot、原始提案与 Session 规则检查意图保真、资源冲突、状态迁移、语义时间、因果和 Session 分区，并输出唯一可交给 Committer 的 `ValidatedCommitPlan`。无主体事件还必须检查来源证据、事件类型、影响范围，并拒绝借环境事件伪造 Character 行动。Validator 不调用模型、不修改候选事实、不写数据库。
- **语义修复：**Validator 返回稳定诊断码与字段路径；Proposal 错误只交回对应 Character 修复一次，SegmentDraft 错误只交回 Director 修复一次。再次失败时终止 Batch，不能由 Runtime 静默改写或降级成 `no_op`。

- **CLI：**统一入口为 `mygo-world`，MVP 提供 `init`、`advance`、`render`、`demo` 和 `skill-bind`；`demo` 只为新 World 串联前三步，重名时安全失败，测试仍由 pytest 负责。默认输出人类摘要，`--json` 输出稳定 Receipt。

- **世界时间：**模型调用的墙钟耗时只进入 Generation Trace；World Time 由剧情语义决定。
- **时间表示：**权威 World Time 使用 Scenario 起点后的非负整数毫秒；可选 ISO 日历锚点只用于显示，World Event 保存开始和结束毫秒。
- **单角色决策：**同一 Generation Wave 只授予一名 Character 一个 Decision Turn，并只生成该角色的一个 Action Proposal。成功提交后，下一个 Wave 从新 World Version 重新投影感知；同一时间点不再向全体角色并发征集彼此不可见的提案。
- **调度规则：**`TurnScheduler` 是确定性 Runtime 模块而非 Agent。它按“`pending_response_ids` 中的点名角色优先 → 无点名时 Director 从其余合法参与者中选择 → Director 缺失或非法时稳定 round-robin fallback”决定角色。多人 Session 的普通候选默认排除上一位已完成 Decision Turn 的角色；多名待回应者在同一稳定 participant ID 环上选择；round-robin 游标由持久 `DecisionTurnRecord` 恢复。首期不实现 continuation 或 `TurnBid`。
- **Director 选择：**真实 Provider 使用独立 `turn_selection` 结构化调用；Fixture 使用 `DeterministicDirectorFixture` 固定选择，二者都不能选择 Runtime 给定候选集合以外的角色。选择只授予机会，不强制说话或行动。
- **空行动：**`wait` 是有意等待并可推进剧情时间；`no_op` 不产生角色行动或独立 Event。被选中角色 `no_op` 且 Session 不结束时只记录 Batch、Wave、Trace 与状态为 `no_op` 的 Decision Turn，不推进 World Version；若 Director 合法提议 `resolved`，或 Runtime 应用 `limit_reached`，则以不含 WorldEvent 的控制 Segment 原子提交 Session/Queue 变化并推进 World Version。
- **Session 队列：**Runnable Session Queue 是 `status=runnable` 的 Event Session 按其不可变 `queue_order` 排出的逻辑 FIFO 集合，不是独立物理表；每个 Batch 取队首并只跟随一条 lineage。分裂时全部后继按参与者 ID 确定性取得新序号，当前 Batch 继续队首后继，其余留给后续 Batch；不使用 focus character 或模型决定顺序。
- **空间模型：**Location 是持久 Entity，可拥有多个带稳定 `scope_key` 的 Interaction Scope，并用显式边描述可达性；Character 位置由 `location_id + scope_key` 表示，MVP 不实现坐标或寻路。
- **人物可感知性：**同一 Interaction Scope 中可见的 Character 会连同其 Character Presentation 一起进入 PerceptionFrame。MVP 将其中的稳定声音描述视为同场角色可获得的基础线索；具体一次发言的音量、颤抖或哽咽属于对应 utterance Event，观察者对此的解释进入自己的 Agent Memory。
- **话语可见性：**`utterance.addressee_ids` 标记主要接收者和待回应关系，但同一 Scope 内其他角色仍可听见；MVP 不支持耳语。
- **分裂时点：**移动在当前 Wave 内按事件时间参与感知；Runtime 在最终提交前依据 Wave 结束位置计算分区，并在同一事务中提交角色位置、关闭旧 Session、创建全部后继 Session 和确定性入队，只有事务完成后才对外可见。
- **Wave 内时间：**被选中 Character 只观察 Wave 起始时的已提交事实；Director 为该角色提案及合法环境后果安排语义时间，Runtime 校验时间、因果和角色所有权。
- **Wave 时长：**Scenario 可以覆盖单个 Wave 的最大剧情时长，默认上限为 5 分钟；超限输出按语义错误修复一次，仍失败则终止 Batch。
- **并发边界：**一个 Event Session 的单个 Wave 内没有 Character 并发；现有全局信号量只保留为不同 Runtime 调用共享 Provider 容量的兼容保护，不影响谁取得 Decision Turn。
- **World 锁：**同一 World 的 `init`、`advance` 和 `skill-bind` 获取世界变更独占文件锁；`render` 使用独立的每 World Render 锁，使两个 Render 串行，但可基于固定 World Version 与 `advance` 并存。Render 对 Ledger/Snapshot 只读，对 Trace、Render 元数据和 Broadcast Disposition 有写入，最终使用短 SQLite 事务与唯一约束避免重复消费；进程退出时文件锁自动释放。
- **失败规则：**重试后仍失败会回滚当前 Wave、将 Generation Batch 标记为失败并让当前 CLI 命令以非零状态退出；此前已提交的 World Version 保留。
- **空工作：**`advance` 没有 Runnable Session 或 `render` 没有新 Event 时返回成功的 `no_work`，不调用模型或生成空记录。
- **轮数上限：**正常达到 `max_waves` 时以 `limit_reached` 关闭当前 Session，Batch 成功结束并警告；Provider 请求预算耗尽仍作为失败处理。
- **长期演化：**长期 World 通过多个有界 Generation Batch 持续推进，而不是让单次调用无限运行。Character Skill 不按 World Version 编排固定动作；持续目标、承诺和认知变化进入 Agent Memory，客观后果进入 World Ledger。多人 Session 依靠 `utterance` 的待回应关系、`wait`/`no_op` 和 Session 生命周期控制发言与收束。
- **展示规则：**首期只生成并校验 WebGAL 演出脚本，不自动启动播放器。
- **发布规则：**Render 先在 `.mygo/renders/<render_id>/` 生成并校验规范产物，Render Gateway 再把脚本写入 WebGAL 目标目录内的临时文件并原子重命名为不可变 hash 路径，最后用短 SQLite 事务写 Render 元数据和 Broadcast Disposition。若进程在文件发布后、数据库提交前中断，重试必须校验已有文件 hash：相同则复用并补全记录，不同则安全失败；其他失败不得留下被视为已完成的可变目标。
- **编译 seam：**Python Runtime 提供窄 `RenderCompiler` 接口，仅将首期八种结构化 Beat 确定性编译为 WebGAL DSL；它负责文本转义、Manifest/真实文件/Live2D 能力校验和内容哈希，不依赖 Node，也不实现通用 DSL Parser。现有 Node 输出仅作为兼容性 Golden 样例。
- **素材选择：**Broadcast 只能使用输入中提供的 Asset Manifest 候选 ID，不得填写路径或虚构 Live2D 能力。
- **Render 独立性：**每个 Render 自行建立背景、出场角色与必要 BGM，不继承上一 Render 的舞台状态，并单独生成一个不可变 WebGAL `.txt` 文件。
- **发布文件：**外部脚本使用 `game/scene/generated/<world_id>/<render_id>-<content_hash>.txt`，不得覆盖 WebGAL 的 `start.txt` 或可变 `latest.txt`。
- **WebGAL 路径：**CLI 的 `--webgal-root` 覆盖环境变量中的默认根目录；Scenario 和代码不得保存本机绝对路径。
- **增量编排：**`render` 选择截至目标 World Version 尚无 Broadcast Disposition 的全部 World Event，与其来源 Batch 无关；无新 Event 时不调用模型。旧 Event 可以作为上下文或倒叙引用，但新 Render 必须处理至少一个新 Event。
- **触发规则：**只有显式 `render` 或 `demo` 才调用 Broadcast，`advance` 本身不自动生成演出。发布全部成功后，才原子记录每个候选 Event 的 `included` 或带原因的 `omitted` Disposition。
- **编排契约：**BroadcastPlan 只使用 Event/素材 ID；RenderPlanner 确定性检查每个新 Event 恰好 `included` 或带原因 `omitted`，解析 Manifest 并产生 RenderJob；RenderCompiler 只生成 DSL。
- **台词真实性：**`dialogue` 必须保留来源 utterance 原文，只能整句省略；`narration` 可以引用并概括已提交事实，不得新增事实。
- **Render 上限：**单个 Render 默认最多 40 个 Beat且预计播放不超过 8 分钟，超出时要求 Broadcast 拆分，限制可配置。

## 数据与重放

- **初始化输入：**`examples/scenarios/*.yaml` 自包含一个 World 所需的初始角色、地点、对象、记忆和 Event Session；`init` 将其一次性物化为 Genesis Segment、World Version 1 及相应持久记录，之后既有 World 不再读取 Seed 作为状态来源。
- **初始化提交：**WorldInitializer 负责 Seed 校验、临时数据库迁移与原子文件发布；它构造 `GenesisCommitPlan` 并调用同一个 World Committer。Committer 使用可注入 Clock 与 ID Generator 分配领域时间和 ID，临时文件名不属于领域重放数据。
- **初始化安全：**目标 `world_id` 已存在时 `init` 失败且不覆盖或合并；World 只保存 `seed_id`、声明版本和内容哈希，不复制完整 YAML。
- **版本化内容：**Runtime Skill 使用 `content/skills/**/*.md` 中的单个版本化 Markdown 文件，MVP 不引入 `SKILL.md + references/` Bundle；Asset Manifest 使用 `content/assets/manifest.yaml`，Scenario 只引用稳定内容 ID，不保存美术文件路径。
- **Skill 格式：**Runtime Skill 使用 YAML frontmatter 保存 ID、版本和 Agent kind，Markdown 正文保存人物或创作风格；模型、密钥、权限、工具、具体 World Version、固定实体 ID、输出字段复制规则和 Session 结束脚本不属于正式 Skill 内容。只有当人物资料出现可由 Runtime 确定性选择且多数轮次不需要加载的独立分支时，才重新评估整体版本化和整体哈希的 Skill Bundle。
- **正式与验收 Skill：**无 `-live` 后缀的正式 Character/Director/Broadcast Skill 必须保持场景无关，可供长期 World 重用。显式 Live 验收可以绑定 `-live` Acceptance Skill，以固定动作和收束条件换取稳定覆盖；该例外不得进入正式 World。
- **Scenario Policy：**当前故事前提、软目标、节奏和自然收束条件属于 Scenario Policy，不属于 Character Skill。MVP 尚未提供独立持久化的 Scenario Policy Schema；现阶段正式世界通过 Scenario 初始状态与 Agent Memory 表达当前动机，Acceptance Skill 仅作为测试专用过渡方案。
- **人格版本：**MVP 不实现 Character Profile Revision 或自动 Reflection；运行中的经历变化由 Agent Memory 承载。人工调整通过创建新的不可变 Character Skill 版本并显式重绑单个 World 完成，从下一 Batch 生效，Trace 记录实际版本和内容哈希。
- **Skill 绑定记录：**`skill-bind` 追加独立配置记录且不推进 World Version；World 独占锁保证它只能在两个 Batch 之间生效。
- **素材清单：**Asset Manifest 是人工确认的白名单，使用相对 WebGAL 分类根的路径；外部 `game` 目录由运行配置提供，未列出的素材不得进入 BroadcastPlan。
- **事实记录：**World Ledger append-only，是世界事实的唯一重放源。
- **Schema 形态：**参与身份、外键、查询、排序和世界不变量使用关系型列；可扩展叙事状态和类型化 payload 使用带 Schema 版本并经 Pydantic 校验的 JSON。角色位置和 Session 成员关系必须关系化。
- **状态快照：**每个 World Version 持久化 Snapshot JSON 和校验和；Snapshot 是可重建缓存，不替代 Ledger。
- **提交事务：**WorldSegment、Entity Revision、Session/Queue 变化、Event Recognizer 产出的 World Event、PerceptionProjector 产出的 Observation、已接受的 Belief/Commitment change、Snapshot 和新 World Version 在同一 SQLite 事务中原子提交。Segment 与 Revision 先进入事务内待提交状态，Recognizer 和 Projector 基于这些候选结果运行，任何一步失败都会整体回滚。
- **标识与顺序：**Scenario 内容使用稳定可读 ID；运行生成记录使用 UUID 文本。World Version、Segment/Event 顺序和 Entity Revision 使用显式单调整数，不依赖 UUID 或 SQLite `rowid` 排序；Fixture 注入确定性 ID 生成器。
- **数据隔离：**World Ledger、Agent Memory、Decision Turn Record 和 Generation Trace 分开存储。
- **候选与决策记录：**ActionProposal 和 SegmentDraft，以及真实 Provider 返回的 TurnSelection，以带 Schema 版本的 JSON 保存在 Generation Trace；每次实际调度另写 `DecisionTurnRecord`，保存候选、选中角色、来源与结果状态。Decision Turn 是运行记录而非事实，不进入 World Ledger。Segment/Event payload 使用通用 `source_kind + source_ref/evidence_refs` 表达来源，不要求来源一定是 Character Proposal。已提交 Segment 引用来源 Trace ID，不建立候选事实表；二期再增加 Player Event Request 的独立输入记录。
- **Trace 内容：**本地数据库保存完整模型输入、原始响应、结构化结果、重试和修复诊断；密钥与认证信息永不保存，终端默认输出脱敏摘要。
- **Ledger 防护：**World Segment、Entity Revision 和 World Event 除 Repository 禁止改写外，还使用 SQLite Trigger 拒绝 UPDATE/DELETE；派生 Snapshot 可以重建。
- **感知投影：**生成输入中，PerceptionProjector 将同一 Interaction Scope 内 Entity 的公开状态和 Character Presentation 投影到 PerceptionFrame；提交事务中，它再按事件时间、角色位置、可见范围和字段权限，将所有可感知的待提交 Event 确定性投影为对应角色的 Observation。它们只在事务整体成功后可见，首期不增加模型注意力筛选。
- **主观更新：**Character 输出可以在唯一 ActionProposal 外附带自己的 Belief/Commitment change；仅在 Wave 成功时提交。Memory append-only，使用 `supersedes_memory_id` 或新的 Commitment 状态记录表达变化，不原地覆盖。
- **框架状态：**Agent checkpoint 只恢复生成流程，不代表世界重放。
- **Schema 迁移：**`init` 创建最新 Alembic Schema；其他命令检测到旧 Schema 时失败，不在 `advance` 或 `render` 中自动迁移。MVP 使用显式 Alembic 操作，不额外实现 `mygo-world db-upgrade`；出现首个需长期兼容的旧 World 后再增加安全包装命令。

## 质量与验收

- **确定性：**相同 Fixture、时钟、ID 和随机种子应产生相同 Golden Trace。
- **Fixture 纵切：**固定 Seed、模型响应、时钟和 ID 必须产生完全一致的 Ledger、Snapshot、Memory、BroadcastPlan 与 WebGAL 脚本内容哈希；Fixture 只替换 ModelGateway，不绕过生产 Runtime。
- **Fixture 组织：**Fixture 使用版本化目录；场景与调用清单使用 YAML，模拟模型的结构化响应使用 JSON。响应按 `agent_type + agent_id + batch/wave + call_kind` 选择，并断言规范化输入内容哈希，避免只靠脆弱的调用顺序匹配。
- **Fixture 素材：**仓库内维护一套最小测试 Asset Manifest 与假素材树，使默认测试不依赖本机 MyGO 安装；显式 Live 测试和 `demo` 才读取配置提供的真实 MyGO 3.1.1 `game` 根目录。
- **Golden 比较：**“完全一致”指规范化领域导出、Snapshot checksum、BroadcastPlan、WebGAL 脚本文本及内容哈希一致；不要求 SQLite 数据库文件逐字节一致，因为其内部页布局不是领域契约。
- **测试分层：**默认测试只使用 Fixture；需要密钥和外部 WebGAL 资源的真实模型链路使用显式 `live` 测试及 `demo`，不会被普通测试隐式触发。
- **Live 验收：**至少显式运行一次双 Character 多轮决策、Director 世界提交、Broadcast 增量编排、真实素材校验和 WebGAL 脚本生成；沿用模型请求预算，不要求加入默认 CI。
- **主演示门槛：**Anon 与 Soyo 必须各产生至少一次有效行为，目标 Event Session 在默认 6 Wave 内自然 `resolved`，并生成至少一个通过真实素材校验的 Render；`limit_reached` 只证明故障边界有效，不算创作验收通过。
- **边界测试：**覆盖权限隔离、点名优先、Director 越权选择、round-robin 重放和提交原子性。
- **无主体事件测试：**首个 Fixture Wave 由 Director 产生至少一个合法环境或桥接事件，验证它不要求 Character actor、仍带来源证据并经 Segment Validator、Committer、Event Recognizer 与 PerceptionProjector；另验证无 ActionProposal 的 Character 台词或重要行动会被拒绝。
- **分裂测试：**确定性 Fixture 验证角色离开 Scope 后旧 Session 关闭、所有后继 Session 入队、感知隔离以及下一 Batch 从队首继续；不依赖真实模型随机触发。
- **故障测试：**覆盖超时、取消、重试和 Render 失败恢复。
- **模型评测：**真实模型接入后以固定 Eval 数据集比较质量、延迟和成本。
- **文本验收：**Live 流程以 Schema、权限、时间、因果、来源和素材约束作为硬门槛；人物表现与叙事质量使用人工检查清单，不以逐字 Golden 或额外 LLM Judge 作为 MVP 阻断条件。

## 风险与退出条件

- **Provider 适配：**若首个 Provider SDK 无法可靠支持结构化输出，保留领域 `ModelGateway` 与 Pydantic Schema，替换适配器而不修改 World Runtime。
- **数据库：**出现多实例并发写入、持续锁竞争或远程数据库需求时，再迁移 PostgreSQL。

## 附录 A：当前实体表结构与数据模型

本附录是截至 2026-09-08 的实现快照，覆盖当前 MVP Runtime 的全部 SQLite 业务表、Pydantic 数据契约、判别联合和公开运行时记录。数据库结构以 Alembic head `0009_decision_turns` 实际迁移结果为准，模型结构以 `src/mygo_world/contracts.py`、`scheduling.py`、`skills.py` 及对应运行时模块为准；历史 Wiki 中的 Go 草案不属于当前实现。

### A.1 记号与边界

- `PK`、`FK`、`UQ`、`IDX`、`CK` 分别表示主键、外键、唯一约束、索引和检查约束；`?` 表示可空或可选；`[]` 表示列表。
- `str[min..max]` 表示字符串长度约束，`int[a..b]` 表示数值范围，`Literal[...]` 表示封闭枚举，`object` 表示可扩展 JSON object。
- 除明确标出默认值的字段外，字段均为必填。Pydantic `StrictModel` 默认拒绝额外字段并裁剪字符串首尾空白；`CompiledRender` 例外地保留脚本文本空白。
- 每个 World 使用独立 SQLite 文件，因此部分表保留 `world_id` 便于审计，但并非所有 `world_id`、`session_id` 都建立数据库外键。以下只记录实际存在的 FK，不推断逻辑引用。
- SQLite 中的时间戳为带时区 ISO 8601 字符串；剧情时间统一为相对 Scenario 起点的整数毫秒。
- `*_json` 均为规范化 JSON 文本；其字段结构在 A.4 中单列。Runtime 的权威数据模型不包括 CLI 临时 Receipt、异常类、Repository/Service、私有 `_RequestBudget` 等执行辅助对象。

### A.2 表总览

| 表 | SQLAlchemy Row | 用途 |
| --- | --- | --- |
| `worlds` | `WorldRow` | World 根记录和当前版本指针 |
| `world_segments` | `WorldSegmentRow` | append-only 原子世界提交 |
| `world_versions` | `WorldVersionRow` | World Version 与语义时间 |
| `entity_revisions` | `EntityRevisionRow` | Character、Location、Object 的版本化状态 |
| `world_events` | `WorldEventRow` | append-only 已提交世界事件 |
| `snapshots` | `SnapshotRow` | 每个版本的可重建完整投影 |
| `event_sessions` | `EventSessionRow` | Event Session 生命周期 |
| `event_session_members` | `EventSessionMemberRow` | Session 固定参与者 |
| `event_session_pending_responses` | `EventSessionPendingResponseRow` | 待回应角色集合 |
| `agent_memory_records` | `AgentMemoryRow` | append-only 私有 Agent Memory |
| `skill_bindings` | `SkillBindingRow` | append-only Runtime Skill 绑定历史 |
| `generation_traces` | `GenerationTraceRow` | 模型请求、响应和校验来源 |
| `generation_batches` | `GenerationBatchRow` | 有界生成 Batch 状态 |
| `generation_waves` | `GenerationWaveRow` | Batch 内 Wave bookkeeping |
| `decision_turn_records` | `DecisionTurnRecordRow` | 单角色调度决定、游标与结果状态 |
| `broadcast_runs` | `BroadcastRunRow` | 固定版本上的一次 Broadcast Run |
| `renders` | `RenderRow` | 已发布不可变 Render 元数据 |
| `broadcast_dispositions` | `BroadcastDispositionRow` | Event 的已纳入/省略决定 |

### A.3 SQLite 表结构

#### `worlds`

```text
world_id          varchar(64)  PK
name              varchar(200)
seed_id           varchar(200)
seed_version      varchar(100)
seed_content_hash varchar(64)
current_version   integer
calendar_anchor   varchar(100)?
created_at        varchar(40)
```

`world_id` 由 CLI 额外约束为 `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`。`current_version` 是可更新指针；历史事实仍由 Ledger 决定。

#### `world_segments`

```text
segment_id      varchar(36) PK
segment_order   integer     UQ
world_version   integer     UQ
segment_type    varchar(32)
source_trace_id varchar(36)?
schema_version  integer
payload_json    text
committed_at    varchar(40)
```

`segment_type` 当前为 `genesis | generation_wave`。SQLite Trigger 拒绝所有 `UPDATE` 和 `DELETE`；`source_trace_id` 是逻辑 Trace 引用，当前没有 FK。

#### `world_versions`

```text
version       integer     PK
segment_id    varchar(36) FK -> world_segments.segment_id, UQ
world_time_ms integer
created_at    varchar(40)
```

一个 Segment 恰好发布一个 World Version。

#### `entity_revisions`

```text
entity_revision_id varchar(36)  PK
entity_id          varchar(200)
entity_type        varchar(32)
revision_order     integer
world_version      integer      FK -> world_versions.version
segment_id         varchar(36)  FK -> world_segments.segment_id
name               varchar(200)
location_id        varchar(200)?
scope_key          varchar(200)?
schema_version     integer
payload_json       text
UQ(entity_id, revision_order)
UQ(entity_id, world_version)
```

`entity_type` 当前为 `location | character | object`。应用层要求 Location 的自身位置为空、Character 的 `location_id + scope_key` 必填、Object 两者同时为空或同时有值。SQLite Trigger 拒绝所有 `UPDATE` 和 `DELETE`。

#### `world_events`

```text
event_id       varchar(36)  PK
event_order    integer      UQ
world_version  integer      FK -> world_versions.version
segment_id     varchar(36)  FK -> world_segments.segment_id
session_id     varchar(200)? FK -> event_sessions.session_id
start_time_ms  integer
end_time_ms    integer
event_type     varchar(64)
schema_version integer
payload_json   text
```

SQLite Trigger 拒绝所有 `UPDATE` 和 `DELETE`。`session_id` 可空，为未来不属于 Event Session 的环境或系统事件保留。

#### `snapshots`

```text
world_version integer     PK, FK -> world_versions.version
schema_version integer
snapshot_json text
checksum      varchar(64)
created_at    varchar(40)
```

`checksum` 是规范化 `snapshot_json` 的 SHA-256。Snapshot 是可重建缓存，不属于 append-only Ledger。

#### `event_sessions`

```text
session_id            varchar(200) PK
queue_order           integer      UQ, DEFAULT 0（迁移哨兵）
status                varchar(32)
location_id           varchar(200)
scope_key             varchar(200)
created_world_version integer      FK -> world_versions.version
closed_world_version  integer?     FK -> world_versions.version
closure_reason        varchar(32)?
```

`queue_order=0` 是 SQLite 为既有行新增非空列所需的迁移哨兵，插入/更新 Trigger 会拒绝它及所有负数，因此 Runtime 必须显式分配正数。该值按 Scenario Seed 顺序或后继 Session 创建顺序全局单调分配，每个 Session 只分配一次。插入时只允许 `status=runnable` 且关闭字段为空；更新时 Queue Order、Location、Scope 和创建版本不可变。状态只允许 `runnable -> closed`，关闭时 `closure_reason` 必须是 `partitioned | resolved | limit_reached` 并同时设置 `closed_world_version`，关闭后不可重新打开。

#### `event_session_members`

```text
session_id varchar(200) PK, FK -> event_sessions.session_id
agent_id   varchar(200) PK
```

复合主键为 `(session_id, agent_id)`。成员仅能在新 Session 的创建事务、该 Session 尚未进入已提交 World Version 时插入，提交后始终拒绝 `INSERT`、`UPDATE` 和 `DELETE`。

#### `event_session_pending_responses`

```text
session_id  varchar(200) PK, FK -> event_sessions.session_id
responder_id varchar(200) PK
```

复合主键为 `(session_id, responder_id)`；它是当前 Session 的可变运行状态，关闭 Session 时清空。

Session lineage 不建立独立物理表。后继 Session 的 `parent_session_ids` 保存在不可变 World Segment 的 `successor_sessions` 事实中；Snapshot 与 Canonical Export 的 `parents` 数组由这些 Ledger 事实稳定推导。

Runnable Session Queue 也不建立独立物理表。其当前队首由 `event_sessions.status = runnable` 后按 `queue_order` 升序选择；`created_world_version` 和 `closed_world_version` 分别表达兼容 Queue 视图中的入队和出队版本。Canonical Export 继续输出派生的 `queue` 数组。

#### `agent_memory_records`

```text
memory_id             varchar(200) PK
agent_id              varchar(200) IDX
namespace             varchar(100)
memory_type           varchar(32)
world_version         integer      FK -> world_versions.version
relative_time_ms      integer
importance            integer
schema_version        integer
payload_json          text
source                varchar(200) DEFAULT 'legacy'
status                varchar(32)?
supersedes_memory_id  varchar(200)? FK -> agent_memory_records.memory_id, UQ
entity_tags_json      text         DEFAULT '[]'
location_tags_json    text         DEFAULT '[]'
```

约束：`memory_type` 为 `observation | belief | commitment | reflection`；`status` 为空或 `active | completed | cancelled`，且仅 Commitment 可带状态；一个旧 Memory 最多被一条新记录 supersede。插入时前后记录的 `agent_id + namespace + memory_type` 必须一致，终态 Commitment 必须引用前序记录。SQLite Trigger 拒绝所有 `UPDATE` 和 `DELETE`。

#### `skill_bindings`

```text
binding_id                 varchar(200) PK
binding_order              integer
world_id                   varchar(64)  FK -> worlds.world_id, IDX
agent_kind                 varchar(32)
agent_id                   varchar(200)
previous_skill_id          varchar(200)?
previous_skill_version     varchar(100)?
previous_skill_content_hash varchar(64)?
skill_id                   varchar(200)
skill_version              varchar(100)
skill_content_hash         varchar(64)
operator                   varchar(200)
reason                     text
bound_at                   varchar(40)
UQ(world_id, binding_order)
```

`agent_kind` 为 `character | director | broadcast`。SQLite Trigger 拒绝所有 `UPDATE` 和 `DELETE`；重绑追加新记录，不推进 World Version。

#### `generation_traces`

```text
trace_id               varchar(36) PK
world_id               varchar(64) IDX
input_world_version    integer
session_id             varchar(200)
agent_type             varchar(32)
agent_id               varchar(200)
call_kind              varchar(64)
skill_id               varchar(200)
skill_version          varchar(100)
skill_content_hash     varchar(64)
model_id               varchar(200)
model_config_json      text
request_json           text
raw_response           text
structured_result_json text
validation_json        text
created_at             varchar(40)
```

Trace 持久化完整生成证据但拒绝凭据字段；当前迁移未为 `world_id`、`session_id` 建立 FK，也未用 Trigger 强制不可变。

#### `generation_batches`

```text
run_id              varchar(36) PK
world_id            varchar(64) IDX
session_id          varchar(200)?
status              varchar(32)
start_world_version integer
end_world_version   integer
wave_count          integer
request_count       integer
warnings_json       text
error_code          varchar(100)?
created_at          varchar(40)
updated_at          varchar(40)
```

`status` 当前持久值包括 `running | completed | failed | cancelled | interrupted`；`no_work` 只存在于 CLI Receipt，不创建 Batch 记录。数据库本身未用 CK 封闭该集合。

#### `generation_waves`

```text
wave_id             varchar(36)  PK
run_id              varchar(36)  FK -> generation_batches.run_id, IDX
wave_number         integer
session_id          varchar(200)
status              varchar(32)
start_world_version integer
end_world_version   integer
world_time_ms       integer
error_code          varchar(100)?
created_at          varchar(40)
updated_at          varchar(40)
UQ(run_id, wave_number)
```

`status` 当前运行值包括 `running | committed | no_op | failed | cancelled | interrupted`；数据库本身未用 CK 封闭该集合。

#### `decision_turn_records`

```text
decision_id             varchar(36)  PK
turn_order              integer      UQ, CK > 0
run_id                  varchar(36)  FK -> generation_batches.run_id, IDX
wave_id                 varchar(36)  FK -> generation_waves.wave_id, UQ
wave_number             integer
session_id              varchar(200) IDX
base_world_version      integer      FK -> world_versions.version
selected_actor_id       varchar(200)
selection_source        varchar(32)
candidate_ids_json      text
status                  varchar(32)
resulting_world_version integer?      FK -> world_versions.version
error_code              varchar(100)?
created_at              varchar(40)
updated_at              varchar(40)
```

`selection_source` 封闭为 `nominated | director | round_robin`，`status` 封闭为
`selected | no_op | committed | failed`。每个 Wave 至多一条记录；`turn_order`
提供跨 Batch、跨进程的稳定游标顺序；失败记录不推进游标。

#### `broadcast_runs`

```text
run_id               varchar(36) PK
world_id             varchar(64) IDX
target_world_version integer     FK -> world_versions.version
source_trace_id      varchar(36) FK -> generation_traces.trace_id
plan_json            text
created_at           varchar(40)
```

SQLite Trigger 拒绝所有 `UPDATE` 和 `DELETE`。

#### `renders`

```text
render_record_id     varchar(36)  PK
world_id             varchar(64)  IDX
render_id            varchar(200)
run_id               varchar(36)  FK -> broadcast_runs.run_id
target_world_version integer      FK -> world_versions.version
render_order         integer
content_hash         varchar(64)
artifact_path        text
scene_path           text
created_at           varchar(40)
UQ(world_id, render_id)
UQ(run_id, render_order)
```

插入时 `render_order >= 1`，且 `world_id + target_world_version` 必须与所属 Broadcast Run 一致。SQLite Trigger 拒绝所有 `UPDATE` 和 `DELETE`。

#### `broadcast_dispositions`

```text
event_id        varchar(36) PK, FK -> world_events.event_id
run_id          varchar(36) FK -> broadcast_runs.run_id
status          varchar(16)
reason          text?
render_ids_json text
created_at      varchar(40)
```

`status` 为 `included | omitted`。Included 必须无 `reason` 且 `render_ids_json` 是至少含一项的数组；Omitted 必须有非空 `reason` 且数组为空。Event 必须不晚于 Run 的 `target_world_version`。以 `event_id` 为 PK 保证一个 Event 只能获得一次最终 Disposition；SQLite Trigger 拒绝所有 `UPDATE` 和 `DELETE`。

### A.4 持久化 JSON 载荷

以下结构不是独立表，但属于当前持久化模型；`object` 内允许的叙事字段由对应 Schema 版本和 Validator 约束。

#### Entity Revision payload

```text
Location:  { state: object, scopes: InteractionScopeSeed[] }
Character: { state: object, presentation?: CharacterPresentation }
Object:    { state: object }
```

`entity_id`、`entity_type`、`name`、位置和版本信息位于关系型列，不在 payload 中重复。
`presentation` 是 Character 拥有的公开可感知值对象而非独立 Entity；临时情绪和可变场景状态仍保存在 `state`，美术文件映射仍由 Asset Manifest 持有。

#### World Segment payload

```text
GenesisSegmentPayload {
  schema_version: 1,
  kind: "genesis",
  session_initializations: EventSessionSeed[],
  queue_initializations: { queue_order: int, session_id: str }[]
}

GenerationWaveSegmentPayload {
  schema_version: 1,
  kind: "generation_wave",
  session_id: str,
  wave_started_at_ms: int,
  wave_ended_at_ms: int,
  proposal_ids: str[],
  events: (ProposalEventCandidate | ExternalEventCandidate)[],
  entity_changes: EntityStateChange[],
  session_intent: "keep_open" | "partitioned" | "resolved" | "limit_reached",
  closed_session_ids: str[],
  successor_sessions: SuccessorSession[],
  pending_response_ids: str[]
}
```

#### World Event payload

```text
WorldEventPayload {
  actor_id: str?,
  source_kind: str,
  source_ref: str?,
  evidence_refs: str[],
  cause_event_ids: str[],
  location_id: str,
  scope_key: str,
  fact: object
}
```

#### Snapshot payload

```text
SnapshotV1 {
  schema_version: 1,
  world_id: str,
  world_version: int,
  world_time_ms: int,
  entities: {
    entity_id: str,
    entity_type: "location" | "character" | "object",
    name: str,
    revision_order: int,
    location_id: str?,
    scope_key: str?,
    payload: EntityRevisionPayload
  }[],
  sessions: {
    session_id: str,
    status: "runnable" | "closed",
    location_id: str,
    scope_key: str,
    participant_ids: str[],
    parent_session_ids: str[],
    pending_response_ids: str[],
    closure_reason?: "partitioned" | "resolved" | "limit_reached",
    closed_world_version?: int
  }[],
  runnable_session_queue: { queue_order: int, session_id: str }[]
}
```

`sessions[].parent_session_ids` 来自已提交 World Segment 的后继创建事实；`runnable_session_queue` 是兼容投影，只包含当前可运行 Session，并按 `event_sessions.queue_order` 排序。

#### Agent Memory payload

```text
SeedMemoryPayload {
  content: str,
  entity_tags: str[],
  location_tags: str[],
  source: str
}

ObservationPayload {
  content: object,
  event_type: str,
  source_event_id: str,
  source_kind: str
}

BeliefOrCommitmentPayload {
  content: str,
  supersedes_memory_id?: str
}
```

Tags、来源、状态和 supersedes 关系同时关系化保存，以支持过滤和数据库不变量。

#### 其他 JSON 列

- `generation_traces.model_config_json`：模型参数 object；`request_json`：`ModelRequest.trace_payload()`；`structured_result_json`：对应 Pydantic 响应；`validation_json`：校验/重试诊断 object。
- `generation_batches.warnings_json`：`str[]`。
- `broadcast_runs.plan_json`：完整 `BroadcastPlan`。
- `broadcast_dispositions.render_ids_json`：去重后的 `render_id: str[]`。

### A.5 Pydantic 数据契约

当前共有 48 个具体 Pydantic 模型和 3 个判别联合。下面的 `={}`、`=[]` 表示通过 `default_factory` 创建空值，不表示共享可变默认对象。

#### Scenario Seed 与初始化

```text
ReachableDestination {
  location_id: str[1..],
  scope_key: str[1..]
}

InteractionScopeSeed {
  scope_key: str[1..],
  name: str[1..],
  reachable_destinations: ReachableDestination[] = []
}

LocationSeed {
  entity_type: Literal["location"],
  entity_id: str[1..],
  name: str[1..],
  scopes: InteractionScopeSeed[1..],
  state: object = {}
}

CharacterPresentation {
  appearance: str[1..2000]? = null,
  demeanor: str[1..2000]? = null,
  voice: str[1..2000]? = null,
  observable_traits: str[1..][0..20] = []
}

CharacterSeed {
  entity_type: Literal["character"],
  entity_id: str[1..],
  name: str[1..],
  location_id: str[1..],
  scope_key: str[1..],
  presentation: CharacterPresentation? = null,
  state: object = {}
}

ObjectSeed {
  entity_type: Literal["object"],
  entity_id: str[1..],
  name: str[1..],
  location_id: str? = null,
  scope_key: str? = null,
  state: object = {}
}

EntitySeed = discriminated union by entity_type(
  LocationSeed | CharacterSeed | ObjectSeed
)

EventSessionSeed {
  session_id: str[1..],
  location_id: str[1..],
  scope_key: str[1..],
  participant_ids: str[1..]
}

MemorySeed {
  memory_id: str[1..],
  agent_id: str[1..],
  namespace: str[1..] = "default",
  memory_type: Literal["observation", "belief", "commitment", "reflection"],
  content: str[1..],
  relative_time_ms: int[0..] = 0,
  importance: int[1..5] = 1,
  entity_tags: str[] = [],
  location_tags: str[] = [],
  source: str[1..] = "scenario_seed",
  status: Literal["active", "completed", "cancelled"]? = null,
  supersedes_memory_id: str? = null
}

SkillReference {
  skill_id: str(pattern="^[a-z][a-z0-9._:-]*$"),
  version: str[1..100]
}

ScenarioSkillBindings {
  characters: map[str, SkillReference],
  director: SkillReference,
  broadcast: SkillReference
}

ScenarioSeed {
  schema_version: Literal[1],
  seed_id: str[1..],
  version: str[1..],
  name: str[1..],
  world_time_ms: int[0..] = 0,
  calendar_anchor: ISO-8601 datetime with timezone? = null,
  entities: EntitySeed[1..],
  sessions: EventSessionSeed[1..],
  memories: MemorySeed[] = [],
  skill_bindings: ScenarioSkillBindings
}

LoadedSeed {
  seed: ScenarioSeed,
  content_hash: str
}
```

跨字段不变量：Character Presentation 一旦提供就必须至少包含一项可感知特征；Object 的 `location_id` 与 `scope_key` 必须成对出现；Commitment 缺省状态归一为 `active`，其他 Memory 不能有状态，只有 Belief/Commitment 可 supersede；Scenario 内 Entity、Session、Memory ID 唯一，所有 Location/Scope、参与者、Memory owner 和 Skill 绑定引用必须存在，参与者必须位于 Session Scope，Memory supersedes 图不得分叉、跨 owner/namespace/type 或成环，终态 Commitment 必须有前序记录。

#### 感知与 Generation Wave

```text
PerceivedEntity {
  entity_id: str[1..],
  entity_type: Literal["location", "character", "object"],
  name: str[1..],
  location_id: str? = null,
  scope_key: str? = null,
  presentation: CharacterPresentation? = null,
  state: object = {}
}

PerceivedMemory {
  memory_id: str[1..],
  agent_id: str[1..],
  namespace: str[1..],
  memory_type: Literal["observation", "belief", "commitment", "reflection"],
  relative_time_ms: int[0..],
  importance: int[1..5],
  source: str[1..],
  status: Literal["active", "completed", "cancelled"]? = null,
  supersedes_memory_id: str? = null,
  entity_tags: str[] = [],
  location_tags: str[] = [],
  payload: object
}

PerceptionFrame {
  schema_version: Literal[1] = 1,
  world_id: str[1..],
  world_version: int[1..],
  world_time_ms: int[0..],
  session_id: str[1..],
  character_id: str[1..],
  location_id: str[1..],
  scope_key: str[1..],
  participant_ids: str[],
  pending_response_ids: str[] = [],
  visible_entities: PerceivedEntity[],
  reachable_destinations: ReachableDestination[],
  memories: PerceivedMemory[]
}

TurnSelection {
  schema_version: Literal[1] = 1,
  world_version: int[1..],
  session_id: str[1..],
  actor_id: str[1..],
  reason: str[1..500]
}

UtteranceAction {
  kind: Literal["utterance"],
  text: str[1..2000],
  addressee_ids: str[] = [],       // 只允许当前可见 Character
  expects_response: bool,
  response_to_event_id: str?       // null 或已感知的 World Event
}

MoveAction {
  kind: Literal["move"],
  location_id: str[1..],
  scope_key: str[1..]
}

InteractAction {
  kind: Literal["interact"],
  target_id: str[1..],
  description: str[1..2000]
}

WaitAction {
  kind: Literal["wait"],
  duration_ms: int[1..300000],
  reason: str[1..500]
}

NoOpAction {
  kind: Literal["no_op"],
  reason: str[1..500]
}

Action = discriminated union by kind(
  UtteranceAction | MoveAction | InteractAction | WaitAction | NoOpAction
)

MemoryChangeCandidate {
  agent_id: str[1..],
  namespace: str[1..] = "default",
  memory_type: Literal["belief", "commitment"],
  content: str[1..2000],
  importance: int[1..5] = 1,
  supersedes_memory_id: str? = null,
  entity_tags: str[] = [],
  location_tags: str[] = [],
  source: str[1..] = "character",
  status: Literal["active", "completed", "cancelled"]? = null
}

ActionProposal {
  schema_version: Literal[1] = 1,
  proposal_id: str[1..],
  world_version: int[1..],
  session_id: str[1..],
  actor_id: str[1..],
  intent_summary: str[1..280],
  action: Action,
  memory_changes: MemoryChangeCandidate[] = []
}

EntityStateChange {
  entity_id: str[1..],
  state_patch: object = {},
  location_id: str? = null,
  scope_key: str? = null
}

CandidateEvent {
  event_key: str[1..],
  event_type: str[1..],
  actor_id: str? = null,
  start_time_ms: int[0..],
  end_time_ms: int[0..],
  cause_event_keys: str[] = [],
  source_kind: str[1..],
  source_ref: str? = null,
  evidence_refs: str[] = [],
  location_id: str[1..],
  scope_key: str[1..],
  payload: object = {}
}

ProposalEventCandidate extends CandidateEvent {
  source_kind: Literal["action_proposal"],
  source_ref: str[1..],
  payload: object containing non-empty intent_summary
}

ExternalEventCandidate extends CandidateEvent {
  source_kind: Literal["director", "environment", "system", "tool", "player_request"]
}

SegmentDraft {
  schema_version: Literal[1] = 1,
  world_version: int[1..],
  session_id: str[1..],
  wave_started_at_ms: int[0..],
  wave_ended_at_ms: int[0..],
  proposal_events: ProposalEventCandidate[],
  external_events: ExternalEventCandidate[] = [],
  entity_changes: EntityStateChange[] = [],
  session_intent: Literal["keep_open", "resolved"] = "keep_open"
}

SuccessorSession {
  session_id: str[1..],
  location_id: str[1..],
  scope_key: str[1..],
  participant_ids: str[1..],
  parent_session_ids: str[1..],
  pending_response_ids: str[] = []
}

ValidatedCommitPlan {
  schema_version: Literal[1] = 1,
  world_id: str[1..],
  base_world_version: int[1..],
  new_world_version: int[2..],
  session_id: str[1..],
  wave_started_at_ms: int[0..],
  wave_ended_at_ms: int[0..],
  events: (ProposalEventCandidate | ExternalEventCandidate)[],
  entity_changes: EntityStateChange[] = [],
  accepted_memory_changes: MemoryChangeCandidate[] = [],
  session_intent: Literal["keep_open", "partitioned", "resolved", "limit_reached"] = "keep_open",
  closed_session_ids: str[] = [],
  successor_sessions: SuccessorSession[] = [],
  pending_response_ids: str[] = [],
  proposal_ids: str[],
  source_trace_id: str[1..]
}

ValidationDiagnostic {
  code: str[1..],
  path: str[1..],
  message: str[1..]
}
```

跨字段不变量：Memory change 中仅 Commitment 可有状态，终态必须 supersede；Entity 位置字段成对出现；Candidate Event 必须至少有 `source_ref` 或 `evidence_refs`；Proposal Event 的 `source_ref` 必须指向原 Proposal，且 payload 必须保留非空 `intent_summary`。Pydantic 只保证这些结构规则，版本、所有权、可见性、可达性、时间、因果、冲突和 Session 分区仍由 Proposal/Segment Validator 校验。

#### 素材、Broadcast 与 Render

```text
AssetEntry {
  asset_id: str[1..],
  path: str[1..]
}

Live2DModelAsset extends AssetEntry {
  character_id: str[1..],
  display_name: str[1..],
  motions: str[] = [],
  expressions: str[] = [],
  entrance_effects: str[] = []
}

AssetManifest {
  schema_version: Literal[1],
  manifest_id: str[1..],
  version: str[1..],
  backgrounds: AssetEntry[1..],
  bgms: AssetEntry[] = [],
  live2d_models: Live2DModelAsset[] = []
}

BroadcastEvent {
  event_id: str[1..],
  event_order: int[1..],
  world_version: int[1..],
  session_id: str? = null,
  event_type: str[1..],
  actor_id: str? = null,
  start_time_ms: int[0..],
  end_time_ms: int[0..],
  location_id: str[1..],
  scope_key: str[1..],
  fact: object
}

ChapterBeat {
  beat_id: str[1..], type: Literal["chapter"],
  title: str[1..], subtitle: str? = null,
  source_event_ids: str[] = []
}

BgmBeat {
  beat_id: str[1..], type: Literal["bgm"], asset_id: str[1..],
  volume: int[0..100] = 70, fade_ms: int[0..60000] = 1000,
  source_event_ids: str[] = []
}

StopBgmBeat {
  beat_id: str[1..], type: Literal["stop_bgm"],
  fade_ms: int[0..60000] = 1000,
  source_event_ids: str[] = []
}

BackgroundBeat {
  beat_id: str[1..], type: Literal["background"], asset_id: str[1..],
  source_event_ids: str[] = []
}

ShowBeat {
  beat_id: str[1..], type: Literal["show"],
  character_id: str[1..], model_asset_id: str[1..],
  position: Literal["left", "center", "right"],
  motion: str? = null, expression: str? = null, entrance_effect: str? = null,
  source_event_ids: str[] = []
}

HideBeat {
  beat_id: str[1..], type: Literal["hide"],
  position: Literal["left", "center", "right"],
  source_event_ids: str[] = []
}

DialogueBeat {
  beat_id: str[1..], type: Literal["dialogue"],
  character_id: str[1..], text: str[1..], source_event_ids: str[1..],
  model_asset_id: str? = null, motion: str? = null,
  expression: str? = null, entrance_effect: str? = null
}

NarrationBeat {
  beat_id: str[1..], type: Literal["narration"],
  text: str[1..], source_event_ids: str[1..]
}

Beat = discriminated union by type(
  ChapterBeat | BgmBeat | StopBgmBeat | BackgroundBeat |
  ShowBeat | HideBeat | DialogueBeat | NarrationBeat
)

BroadcastRender {
  render_id: str(pattern="^[a-z][a-z0-9_-]*$"),
  title: str[1..],
  estimated_play_ms: int[1..],
  beats: Beat[1..]
}

BroadcastDisposition {
  event_id: str[1..],
  status: Literal["included", "omitted"],
  reason: str? = null
}

BroadcastPlan {
  schema_version: Literal[1] = 1,
  world_id: str[1..],
  target_world_version: int[1..],
  dispositions: BroadcastDisposition[1..],
  renders: BroadcastRender[] = []
}

RenderJob {
  schema_version: Literal[1] = 1,
  world_id: str[1..],
  target_world_version: int[1..],
  render_id: str(pattern="^[a-z][a-z0-9_-]*$"),
  title: str[1..],
  estimated_play_ms: int[1..],
  beats: object[1..]
}

CompiledRender {
  job: RenderJob,
  script: str[1..],
  content_hash: str(pattern="^[0-9a-f]{64}$")
}
```

跨字段不变量：Asset Manifest 中所有分类共享唯一 `asset_id`；Omitted Disposition 必须有原因，Included Disposition 不能有原因。RenderPlanner 继续校验 Event 覆盖、事实来源、自包含舞台、素材白名单和 Live2D 能力；这些语义约束不只依赖 Pydantic。

#### Runtime Skill 元数据

```text
RuntimeSkillMetadata {
  skill_id: str(pattern="^[a-z][a-z0-9._:-]*$"),
  version: str[1..100],
  agent_kind: Literal["character", "director", "broadcast"]
}
```

Markdown 正文不在 frontmatter 模型中，但加载后必须非空；完整内容的 SHA-256 成为 Skill 身份的一部分。

### A.6 公开运行时值对象与记录

这些对象不是 SQLite 表，也不直接生成 JSON Schema，但在模块边界上传递结构化数据。

```text
RuntimeSkill {
  skill_id: str,
  version: str,
  agent_kind: "character" | "director" | "broadcast",
  body: str,
  content_hash: str,
  path: Path
}

EffectiveSkill {
  agent_kind: "character" | "director" | "broadcast",
  agent_id: str,
  skill_id: str,
  version: str,
  content_hash: str,
  body: str
}

TurnContext {
  run_id: str,
  wave_id: str,
  wave_number: int,
  world_version: int,
  session_id: str,
  participant_ids: tuple[str, ...],
  pending_response_ids: tuple[str, ...] = (),
  last_selected_actor_id: str? = null
}

DecisionTurnRecord {
  decision_id: str,
  run_id: str,
  wave_id: str,
  wave_number: int,
  session_id: str,
  base_world_version: int,
  selected_actor_id: str,
  selection_source: "nominated" | "director" | "round_robin",
  candidate_ids: tuple[str, ...],
  status: "selected" | "no_op" | "committed" | "failed",
  resulting_world_version: int? = null,
  error_code: str? = null
}

ModelRequest {
  agent_type: str,
  agent_id: str,
  call_kind: str,
  model_id: str,
  skill_id: str,
  skill_version: str,
  skill_content_hash: str,
  input_payload: object,
  model_config: object,
  skill_body: str = ""
}

ModelGeneration[T] {
  request: ModelRequest,
  raw_response: str,
  structured: T,
  usage: map[str, int | float]? = null,
  latency_ms: float? = null,
  transport_attempts: int = 1
}

FixtureResponse {
  body: object,
  expected_input_hash: str? = null
}

ProviderSettings {
  base_url: str,
  api_key: str (repr 隐藏且禁止持久化),
  model_id: str,
  structured_output_mode: str = "json_schema",
  model_parameters: object? = null,
  timeout_seconds: float = 120.0
}

GenesisCommitPlan {
  world_id: str,
  loaded_seed: LoadedSeed,
  skill_bindings: tuple[EffectiveSkill, ...]
}

GenesisCommitResult {
  world_version: int,
  snapshot_checksum: str
}

GenerationTraceRecord {
  trace_id: str,
  world_id: str,
  input_world_version: int,
  session_id: str,
  agent_type: str,
  agent_id: str,
  call_kind: str,
  skill_id: str,
  skill_version: str,
  skill_content_hash: str,
  model_id: str,
  model_config: object,
  request: object,
  raw_response: str,
  structured_result: object,
  validation: object
}

WaveCommitResult {
  world_version: int,
  snapshot_checksum: str,
  world_event_count: int,
  observation_count: int,
  entity_revision_count: int,
  continuation_session_id: str?
}

RecognizedEvent {
  event_id: str,
  event_order: int,
  candidate: CandidateEvent
}

ProjectedObservation {
  agent_id: str,
  event_id: str,
  relative_time_ms: int,
  location_id: str?,
  payload: object
}

ValidationOutcome[T] {
  value: T?,
  diagnostics: tuple[ValidationDiagnostic, ...],
  derived ok: bool
}

DemoFixture {
  root: Path,
  fixture_id: str,
  version: str,
  seed_path: Path,
  skills_dir: Path,
  asset_manifest_path: Path,
  webgal_template: Path,
  clock_value: datetime,
  random_seed: int,
  steps: tuple[object, ...],
  gateway: FixtureGateway
}

WorldPaths {
  worlds_dir: Path,
  world_dir: Path,
  database: Path,
  mutation_lock: Path,
  render_lock: Path
}
```

`ProviderSettings.from_environment()` 将 `structured_output_mode` 限制为 `json_schema | json_text`，并校验 URL、有限 JSON 参数、保留字段、凭据字段及正数超时；dataclass 结构自身仍按源码声明为 `str`。`RuntimeSkill`、`EffectiveSkill`、Commit 记录、Gateway 记录、识别/投影结果、Validation Outcome 和 Demo Fixture 均使用 dataclass；`WorldPaths` 是只负责派生本地路径的运行时 holder。`ModelGateway` 与 `CancellableModelGateway` 是行为协议，不属于数据模型。

### A.7 非权威兼容/Authoring JSON 模型

以下结构仍存在于仓库并有测试，但不属于 Python MVP Runtime 的世界事实或模型契约：Node Authoring 用于人工剧本编译，Dynamic Render extension 是兼容性原型。它们允许额外字段，且没有 Pydantic/Alembic 版本保证。

#### Node Story 与 Project

```text
ProjectManifest {
  formatVersion: int (当前文件为 1),
  id: str,
  name: str,
  gameKey: str,
  titleImage: relative asset path,
  titleBgm: relative asset path,
  gameLogo: relative asset path,
  defaultLanguage: str,
  textSpeed: number[0..100],
  migrateTextSpeedAtOrBelow?: number,
  playerSettingsVersion: positive int,
  showPanic?: bool,
  enableAppreciation?: bool,
  legacyExpressionBlendMode?: bool,
  positioningType?: str,
  stageWidth?: number,
  stageHeight?: number,
  autoRotate?: bool
}

PlayerConfig {
  version: int,
  storageKey: str,
  textSpeed: number,
  migrateAtOrBelow: number
}

Story {
  formatVersion: int (当前文件为 1),
  title: str,
  subtitle?: str,
  entryScene: SafeId,
  characters: StoryCharacter[],
  scenes: StoryScene[]
}

StoryCharacter { id: SafeId, name: str, figure: relative game/figure asset }
StoryScene { id: SafeId, title: str, beats: AuthoringBeat[] }
SafeId = str(pattern="^[a-z][a-z0-9_-]*$")

AuthoringBeat =
  Chapter  { id, type:"chapter", title, subtitle? } |
  Bgm      { id, type:"bgm", asset, volume?:0..100, fadeMs?:0..60000 } |
  StopBgm  { id, type:"stop_bgm", fadeMs?:0..60000 } |
  Bg       { id, type:"background", asset } |
  Show     { id, type:"show", character, position, motion?, expression?, enter? } |
  Hide     { id, type:"hide", position } |
  Dialogue { id, type:"dialogue", character, text, motion?, expression? } |
  Narrate  { id, type:"narration", text } |
  Choice   { id, type:"choice", options:{ text, target }[2..] } |
  Jump     { id, type:"jump", target } |
  Ending   { id, type:"ending", title, text? }
```

Story 校验还要求 Character/Scene/Beat ID 唯一、Scene 引用存在、所有 Scene 从入口可达且能到达 Ending、位置为 `left | center | right`，并检查素材文件及 Live2D motion/expression 能力。

#### Dynamic Timeline extension

```text
DynamicTimelineSource {
  formatVersion: positive int,
  revision: positive int,
  activeEventId: SafeId,
  events: DynamicEvent[1..]
}

DynamicEvent {
  id: SafeId,
  title: str,
  location: str,
  characters: StoryCharacter[1..],
  renders: DynamicRender[]
}

DynamicRender {
  id: SafeId,
  title: str,
  estimatedPlayMs?: positive int,
  beats: (Chapter | Bgm | StopBgm | Bg | Show | Hide | Dialogue | Narrate)[1..]
}

CompiledDynamicRender {
  id: SafeId,
  title: str,
  estimatedPlayMs: positive int,
  contentHash: SHA-256 hex,
  script: str
}

TimelineSnapshot {
  type: Literal["timeline.snapshot"],
  revision: int,
  selectedEventId: SafeId,
  playback: {
    mode: "black" | "loading" | "playing",
    rendererConnected: bool,
    eventId: SafeId?,
    renderId: SafeId?
  },
  events: {
    id: SafeId,
    title: str,
    location: str,
    selected: bool,
    status: "live" | "ready" | "complete",
    readyCount: int,
    playedCount: int,
    renders: {
      id: SafeId,
      title: str,
      estimatedPlayMs: int,
      status: "loading" | "playing" | "played" | "ready"
    }[]
  }[]
}
```

Dynamic Event ID 和 Render ID 全局唯一，`activeEventId` 必须存在；每个 Dynamic Render 必须自行建立背景并包含 Dialogue 或 Narration。该 extension 的模型不能写入 World Ledger，也不能替代 `BroadcastPlan -> RenderJob -> CompiledRender` 的正式链路。
