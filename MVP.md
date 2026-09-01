# MVP 决策

> 状态：当前 MVP 实施基线  
> 更新：2026-08-31

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
  → PerceptionProjector / PerceptionFrame
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
- **并发规则：**同一 Generation Wave 的 Character 基于共同 Snapshot 并行提案，Director 在同步屏障后统一补全，Runtime 原子提交。
- **调度规则：**Generation Wave 是 lockstep 决策周期而非固定时长 tick；全部行动在统一屏障前完成，提前完成者隐式空闲，首期不允许行动跨 Wave。
- **空行动：**`wait` 是有意等待并可推进剧情时间；`no_op` 不产生角色行动或独立 Event。全员 `no_op` 且 Session 不结束时只记录 Batch/Wave 与 Trace，不推进 World Version；若 Director 合法提议 `resolved`，或 Runtime 应用 `limit_reached`，则以不含 WorldEvent 的控制 Segment 原子提交 Session/Queue 变化并推进 World Version。
- **Session 队列：**World 持久化按显式序号排序的 FIFO Runnable Session Queue；每个 Batch 取队首并只跟随一条 lineage。分裂时全部后继按参与者 ID 确定性入队，当前 Batch 继续队首后继，其余留给后续 Batch；不使用 focus character 或模型决定顺序。
- **空间模型：**Location 是持久 Entity，可拥有多个带稳定 `scope_key` 的 Interaction Scope，并用显式边描述可达性；Character 位置由 `location_id + scope_key` 表示，MVP 不实现坐标或寻路。
- **话语可见性：**`utterance.addressee_ids` 标记主要接收者和待回应关系，但同一 Scope 内其他角色仍可听见；MVP 不支持耳语。
- **分裂时点：**移动在当前 Wave 内按事件时间参与感知；Runtime 在最终提交前依据 Wave 结束位置计算分区，并在同一事务中提交角色位置、关闭旧 Session、创建全部后继 Session 和确定性入队，只有事务完成后才对外可见。
- **Wave 内时间：**所有 Character 共享观察与决策时刻；Director 为已有提案安排 Wave 内执行顺序和语义时间，Runtime 校验时间、因果和角色所有权。
- **Wave 时长：**Scenario 可以覆盖单个 Wave 的最大剧情时长，默认上限为 5 分钟；超限输出按语义错误修复一次，仍失败则终止 Batch。
- **并发上限：**Character 模型调用使用默认大小为 4 的全局信号量；等待信号量不改变其输入 Snapshot 或 World Time。
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
- **版本化内容：**Runtime Skill 使用 `content/skills/**/*.md`，Asset Manifest 使用 `content/assets/manifest.yaml`；Scenario 只引用稳定内容 ID，不保存美术文件路径。
- **Skill 格式：**Runtime Skill 使用 YAML frontmatter 保存 ID、版本和 Agent kind，Markdown 正文保存人物或创作风格；模型、密钥、权限、工具、具体 World Version、固定实体 ID、输出字段复制规则和 Session 结束脚本不属于正式 Skill 内容。
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
- **数据隔离：**World Ledger、Agent Memory 和生成 Trace 分开存储。
- **候选记录：**ActionProposal 和 SegmentDraft 以带 Schema 版本的 JSON 保存在 Generation Trace；Segment/Event payload 使用通用 `source_kind + source_ref/evidence_refs` 表达来源，不要求来源一定是 Character Proposal。已提交 Segment 引用来源 Trace ID，不建立候选事实表；二期再增加 Player Event Request 的独立输入记录。
- **Trace 内容：**本地数据库保存完整模型输入、原始响应、结构化结果、重试和修复诊断；密钥与认证信息永不保存，终端默认输出脱敏摘要。
- **Ledger 防护：**World Segment、Entity Revision 和 World Event 除 Repository 禁止改写外，还使用 SQLite Trigger 拒绝 UPDATE/DELETE；派生 Snapshot 可以重建。
- **感知投影：**提交事务中由 PerceptionProjector 按事件时间、角色位置、可见范围和字段权限，将所有可感知的待提交 Event 确定性投影为对应角色的 Observation；它们只在事务整体成功后可见，首期不增加模型注意力筛选。
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
- **边界测试：**覆盖权限隔离、非法输出、并发冲突和提交原子性。
- **无主体事件测试：**首个 Fixture Wave 由 Director 产生至少一个合法环境或桥接事件，验证它不要求 Character actor、仍带来源证据并经 Segment Validator、Committer、Event Recognizer 与 PerceptionProjector；另验证无 ActionProposal 的 Character 台词或重要行动会被拒绝。
- **分裂测试：**确定性 Fixture 验证角色离开 Scope 后旧 Session 关闭、所有后继 Session 入队、感知隔离以及下一 Batch 从队首继续；不依赖真实模型随机触发。
- **故障测试：**覆盖超时、取消、重试和 Render 失败恢复。
- **模型评测：**真实模型接入后以固定 Eval 数据集比较质量、延迟和成本。
- **文本验收：**Live 流程以 Schema、权限、时间、因果、来源和素材约束作为硬门槛；人物表现与叙事质量使用人工检查清单，不以逐字 Golden 或额外 LLM Judge 作为 MVP 阻断条件。

## 风险与退出条件

- **Provider 适配：**若首个 Provider SDK 无法可靠支持结构化输出，保留领域 `ModelGateway` 与 Pydantic Schema，替换适配器而不修改 World Runtime。
- **数据库：**出现多实例并发写入、持续锁竞争或远程数据库需求时，再迁移 PostgreSQL。
