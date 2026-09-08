# Generative MyGO World

本上下文描述由自主角色共同推进、经确定性世界提交后再投影为视觉小说演出的生成式世界。

## Language

**World**:
由 `world_id` 标识并跨进程持续存在的世界实例，拥有自己的 SQLite 数据库、World Ledger 和当前 World Version；启动或退出 Runtime 进程不会创建、结束或重置 World。
_Avoid_: Process、Generation Batch、Scenario Seed

**Scenario Seed**:
由人编写并纳入版本控制的初始世界条件，定义首批角色、地点、事实和 Event Session；它只在 `init` 时被物化为持久世界，之后不再是实体状态的权威来源，文件变更也不会修改既有 World。
_Avoid_: Fixture、Prompt、Snapshot

**Scenario Policy**:
与某个 Scenario 或 Event Session 绑定的创作策略，描述故事前提、软目标、节奏和自然收束条件；它不是角色人格，不能授予权限或绕过 Validator。MVP 尚未将其实现为独立持久模型。
_Avoid_: Scenario Seed、Runtime Skill、Agent Contract

**Event Session**:
一段具有持久身份和生命周期的互动执行容器；其固定参与者集合与 Interaction Scope 共同构成互动边界。一个 Event Session 可以跨越多轮 Generation Wave 并产生多个 World Event；参与者集合发生分区时，当前 Session 结束并产生新的后继 Session。
_Avoid_: Event、World Event、Event Channel、Interaction Group

**Interaction Scope**:
Location 内具有稳定 `scope_key` 的语义区域，表示角色能够直接相互行动或交流的可见边界；它由已提交世界状态确定，是 Location 拥有的值对象而不是独立 Entity。
_Avoid_: Interaction Group、Location、WebGAL Scene

**Location**:
具有稳定 `location_id` 和版本化状态的持久 Entity，可以拥有一个或多个 Interaction Scope，并定义 Scope 之间的可达关系；MVP 不使用坐标、距离或寻路计算。
_Avoid_: Interaction Scope、Event Session、WebGAL Scene

**World Event**:
经过校验并提交、已经成为世界客观事实的具体事件，可以属于某个 Event Session，也可以由环境或系统独立产生。
_Avoid_: Event、Event Session、Render

**World Segment**:
一次初始化或 Generation Wave 通过校验后形成的原子世界提交，包含对应实体变更及其证据；要么完整提交，要么完全不提交。
_Avoid_: Generation Wave、World Event、Snapshot

**Genesis Segment**:
`init` 将 Scenario Seed 物化时创建的首个 World Segment，用于建立 World Version 1、初始 Entity Revision 和 Event Session，但不暗示这些初始条件刚刚作为剧情事件发生。
_Avoid_: World Event、Generation Wave、Scenario Seed

**Entity Revision**:
角色、地点或对象在某次 World Segment 提交后形成的版本化状态记录，用于重建任意 World Version。
_Avoid_: Snapshot、World Event

**World Ledger**:
由 World Segment、Entity Revision 和 World Event 构成的 append-only 世界历史，是客观世界的唯一权威。
_Avoid_: Snapshot、Agent Memory、Generation Trace

**Generation Wave**:
一个 Event Session 的单轮原子决策周期；Turn Scheduler 从某个已提交 World Version 为一名 Character 授予 Decision Turn，该角色至多提出一个 Action Proposal，Director 返回创意 Resolution，Runtime 确定性组装并校验后原子提交。下一 Wave 因而能观察上一 Wave 已提交的行动。
_Avoid_: Fixed-duration Tick、Event Session、World Event

**Decision Turn**:
Turn Scheduler 在一个 Generation Wave 中授予单个 Character 的一次决策机会；它允许该角色提出一个行动或 `no_op`，但不强制角色发言，也不是持续固定时长的模拟回合。
_Avoid_: Generation Wave、Action Proposal、World Event

**Turn Scheduler**:
Runtime 内的确定性调度模块，不是 Agent。它先从当前 `pending_response_ids` 中选择被点名者；没有点名时接受 Director 对其余合法参与者的选择，多人 Session 默认排除上一位已完成 Decision Turn 的角色；Director 缺失或给出非法结果时，按稳定 participant ID 和持久游标 round-robin 回退。首期不实现 continuation 或 Turn Bid。
_Avoid_: Director、Runnable Session Queue、Presentation Order

**Decision Turn Record**:
记录每次 Decision Turn 的候选角色、选中角色、选择来源及最终状态的持久运行记录；它用于审计、恢复 round-robin 游标和重放调度，但不是客观世界事实，不属于 World Ledger。
_Avoid_: Generation Trace、World Event、World Segment

**Generation Batch**:
一次带唯一 `run_id` 的有界离线世界生成过程；Runtime 从指定 World 的当前版本取出 Runnable Session Queue 队首，并在确定性上限内只推进该 Session 的一条后继 lineage。它记录起止 World Version，但不决定 Broadcast 的增量边界。
_Avoid_: World、Process、Generation Wave、Render

**Runnable Session Queue**:
World 中所有 Runnable Event Session 按各自不可变单调序号形成的逻辑 FIFO 集合；它由 Session 生命周期派生而非独立持久实体，Runtime 而非模型决定顺序，一个 Generation Batch 只消费队首 lineage。
_Avoid_: Focus Character、Presentation Order、Model Priority

**World Version**:
一次成功原子提交后得到的世界状态版本，也是 Snapshot、感知输入和 Broadcast 读取范围的稳定边界。
_Avoid_: Generation Batch、Render Revision

**Snapshot**:
某个 World Version 的完整可读状态投影，供 Character、Director 和查询流程使用；它按版本持久化并带校验和，但只是可由 World Ledger 重建的缓存。
_Avoid_: World Ledger、Scenario Seed、Entity Revision

**World Time**:
由 Director 以受限相对时长提议、再由 Runtime 根据 Snapshot 绝对时间确定性组装并校验后提交的剧情语义时间，以 Scenario 起点后的整数毫秒表示；可选日历锚点只用于显示，模型调用的真实耗时只进入 Generation Trace。
_Avoid_: Wall-clock Time、Model Latency、Presentation Time

**Action Proposal**:
Character Agent 在一次 Generation Wave 中提出的唯一原子候选行动，包含简短意图摘要，类型限于 `utterance`、`move`、`interact`、`wait` 或 `no_op`；只有经过 Director 补全和 Runtime 校验提交后才成为世界事实。`utterance` 以 `expects_response` 显式声明是否要求收件人回应，并以 `response_to_event_id` 引用本次正在回应的已感知 World Event；`wait` 是有意等待并可推进 World Time，`no_op` 不产生行动或独立事件。
_Avoid_: World Event、World Segment

**Director Resolution**:
Director 对单个已接受 Action Proposal 返回的窄创意结果，只包含相对语义时长、可选结果摘要、Creative External Event、合法 Entity State Change 与 Event Session 收束意图；它不拥有 World Version、Session、绝对时间、Proposal Event、事件键、来源或角色动作 payload。
_Avoid_: Segment Draft、Validated Commit Plan、Action Proposal

**Player Event Request**:
玩家针对尚未提交的未来世界，以自然语言请求发生某个外部事件的版本绑定输入；它本身不是世界事实，由 Director 解释成候选后仍须通过 Runtime 校验与提交。
_Avoid_: Action Proposal、World Event、Narrative Thread

**External Event Candidate**:
不由 Character Action Proposal 拥有的结构化事件候选，可来自 Director 环境刺激、System/Tool 输入，或 Director 对 Player Event Request 的解释；它可以描述暴雨或背景人物冲突，但不能在没有对应 Action Proposal 时冒充持久 Character 的重要选择、台词或动机。
_Avoid_: Action Proposal、World Event、Validated Commit Plan

**Proposal Validator**:
Runtime 中针对单个 Action Proposal 的确定性门禁，依据该角色实际收到的 PerceptionFrame 检查行动唯一性、来源版本、角色所有权、目标可见性与目的地可达性；它只返回有效候选或稳定诊断，不修改提案、不调用模型也不持久化状态。
_Avoid_: Pydantic Schema、Director、Segment Validator

**Segment Assembler**:
Runtime 中的纯确定性深模块，以 Snapshot、当前 Event Session、已接受 Action Proposal、Director trace 身份和 Director Resolution 为小接口，集中组装完整 Segment Draft；它注入版本、Session 与绝对时间，逐动作复制角色字段，生成事件键、来源、因果引用及 `move` 位置变化，但不校验提交资格、不写入 World，也不事后覆盖模型原本有权输出的字段。
_Avoid_: Director、Segment Validator、World Committer

**Segment Validator**:
Runtime 中针对 Segment Assembler 产出的完整 Segment Draft（或测试中手工构造的恶意 Draft）的确定性门禁，依据共同 Snapshot、原始 Action Proposal 和 Session 规则检查意图保真、状态迁移、资源冲突、时间、因果与分区，并产出可提交计划或稳定诊断；它不能组装、创造结果、修复内容或写入 World。
_Avoid_: Proposal Validator、Segment Assembler、World Committer

**World Committer**:
权威世界状态的唯一事务写入模块；初始化时接收由已校验 Scenario Seed 构造的 Genesis Commit Plan，Generation Wave 时接收 Segment Validator 产出的 Validated Commit Plan。
_Avoid_: WorldInitializer、Ledger Repository、Director

**Incidental Prop**:
只在单个已提交 World Event 的 payload 中存在、没有全局 Entity ID 或后续状态生命周期的一次性普通道具；它带有 Event 内唯一 `prop_key`、类型和描述，可以丰富自然语言事实并为未来升格保留来源，但不能充当人物、地点、重要剧情物品或独立美术资源。
_Avoid_: Entity、Asset、Inventory Item

**Agent Memory**:
某一个 Agent 私有、append-only 且持久化的主观记忆，只能通过该 Agent 的身份与 namespace 访问；首期记录分为亲历的 `observation`、可能错误的 `belief` 和待履行的 `commitment`，旧认知通过新记录显式取代或解决，`reflection` 只保留 Schema 而不自动生成。它不构成世界事实，也不能被其他角色直接读取。
_Avoid_: World Ledger、Snapshot、Generation Trace

**Observation**:
由 PerceptionProjector 在 World 提交事务中，根据角色当时的时间、位置与字段权限，从已通过校验的待提交 World Event 确定性形成的亲历记忆；它只在事务成功后可见，只证明该角色可感知到相应内容，不改变世界事实。
_Avoid_: World Event、Belief、Attention Candidate

**Runtime Skill**:
Character、Director 或 Broadcast 使用的版本化、场景无关创作配置，以自然语言表达稳定的人格、动机、关系倾向或创作风格；具体轮次、实体 ID、输入输出字段和结束条件不属于 Runtime Skill。
_Avoid_: Codex Skill、Prompt、Agent Contract

**Character Skill**:
属于单个角色的不可变 Runtime Skill，以自然语言描述跨场景稳定的人格、长期驱动力、关系倾向、判断方式和表达风格；具体任务由 Scenario Policy 或运行上下文提供，经历造成的变化由 Agent Memory 承载。
_Avoid_: Character Memory、Asset Manifest、Codex Skill

**Character Presentation**:
Character Entity 拥有的公开可感知值对象，描述跨场景相对稳定的外貌、外显气质、声音和可观察行为倾向；它提供形成印象的线索，不代表观察者已经形成的主观判断。
_Avoid_: Character Skill、First Impression、Asset Manifest、Agent Memory

**Agent Contract**:
后端为一种 Agent 角色拥有并强制执行的接口，包括输入投影、工具白名单、结构化输出 Schema、确定性字段及校验规则；后端可以把契约说明组装成模型指令，但自然语言提示本身不构成权限或正确性保证。
_Avoid_: Runtime Skill、Prompt

**Acceptance Skill**:
只服务于显式 Fixture 或 Live 验收的 Runtime Skill，可以为覆盖验收路径而指定轮次、动作和结束条件；它必须以用途明确的 ID 与正式 Character Skill 分离，不用于长期 World。
_Avoid_: Character Skill、Scenario Policy

**Asset Manifest**:
仓库中经过人工确认的素材白名单，以稳定领域 ID 将角色、地点、表情和动作映射到 WebGAL 分类根下的相对路径；它不描述人格或决定剧情，未列出的文件不能由 Broadcast 使用。
_Avoid_: Character Skill、Render

**Generation Trace**:
一次模型生成所使用的 Agent 身份、Runtime Skill 版本与内容哈希、模型配置、完整输入、原始响应、结构化结果和校验诊断；它用于诊断与复现实验，不是世界事实，也绝不保存 API 密钥或认证信息。
_Avoid_: World Ledger、Agent Memory

**Event Recognizer**:
在 World 提交事务中，根据已经通过校验并进入待提交状态的 World Segment 与 Entity Revision，确定性识别零到多个适合查询和叙事引用的 World Event；它不能改变候选事实，Event 与 Segment 只在整个事务成功后一起对外可见。
_Avoid_: Director、World Committer、Broadcast

**Director**:
在没有待回应点名时可从 Scheduler 给出的合法候选中建议下一位行动者，并在 Character Agent 提案后以 Director Resolution 提出环境反应、对象结果、相对时长、因果关系与 Event Session 收束意图的 Agent；它可以提出无主体桥接事件，但不能构造或改写 Proposal Event 的版本、来源、绝对时间与角色 payload，不能替角色作出重要选择、发言或改变动机，也不能提交世界事实或编排演出。
_Avoid_: Broadcast、World Committer

**Broadcast**:
读取已提交世界历史并决定删减、排序和演出表达的 Agent；它只产生结构化 Broadcast Plan，不能新增或修改世界事实，Render 由确定性规划与编译流程产生。
_Avoid_: Director、Render Gateway

**Broadcast Run**:
一次显式 `render` 调用；它在固定 World Version 上处理尚无 Broadcast Disposition 的 World Event，发布零到多个不可变 Render，并在全部发布成功后记录对应 Disposition，与产生这些 Event 的 Generation Batch 边界无关。
_Avoid_: Generation Batch、Render、World Version

**Broadcast Disposition**:
Broadcast Run 对一个新 World Event 作出的不可变处理记录，结果为已纳入 Render 或带原因省略；只有全部 Render 成功发布后才写入。
_Avoid_: World Event、Beat、Generation Trace

**World Order**:
World Ledger 中由提交时间、世界时间和因果关系确定的客观事件顺序，是重放世界状态的权威顺序。
_Avoid_: Presentation Order、Render Order

**Presentation Order**:
Broadcast 为演出选择的叙事顺序，可以删减、压缩、跨 Event Session 交错或倒叙，但不能改写 World Order 或制造虚假因果。
_Avoid_: World Order、Commit Order

**Render**:
从已提交世界历史投影出的、自包含且发布后不可原地修改的 WebGAL 演出片段，由一组有序 Beat 构成。
_Avoid_: World Event、Event Session

**Beat**:
Render 中最小的语义演出步骤，例如切换背景、显示角色、播放音乐、台词或旁白；它不是视频帧，也不代表一次屏幕刷新。
_Avoid_: Frame、World Event、Action Proposal
