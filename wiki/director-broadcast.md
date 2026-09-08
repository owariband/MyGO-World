# 导演层与导播层：Generative Agents 之上的待研究算法

与本页相关的历史卡点、否决方案和交换代价见[难点、卡点与代价账本](difficulty-ledger.md)。

## 1. 已确认的宏观起点

角色底座可以高度复用 Generative Agents 的思想：

```text
Character Agent
  Persona / Goal
  Perception
  Memory Retrieval
  Planning / Reacting
  Reflection
  Action
        |
        v
Sandbox / World Action Loop
```

这里的“几乎原封不动”指**认知循环可以作为 baseline**，不代表复制其全部工程实现。当前仍明确改造：

- 每轮 Agent 真实响应耗时进入 generation trace，响应后再对该段世界做时间/因果补完；
- 用 Snapshot/Version/Validator 防止顺序执行污染；
- 增加客观 WorldTransaction / WorldEvent Ledger；
- 把观察、信念和世界事实分离；
- MyGO/WebGAL 只作为 Render Backend。

在这一底座上，本项目新增两个职责不同的 Agent 层：

```text
Director Agent  导演层：世界应该获得什么叙事压力与机会？
Broadcast Agent 导播层：已经发生的世界应该怎样被玩家看见？
```

三类 Agent 共享认知阶段协议，但不共享 Persona 的具体实现：

```text
observe/perceive -> retrieve -> plan -> propose
                     |
                     v
           Runtime validate / commit
                     |
                     v
          observe_outcome -> reflect
```

Persona、Director、Broadcast 对外使用同一个 Eino `adk.Agent` 协议、Runner、事件与模型基础设施；Character 以自定义 `PersonActAgent` 封装内部 Compose Graph。三类 Agent 的阶段输入、Graph/strategy、Prompt、输出类型、触发频率和 Memory namespace 分别隔离，不要求 Director/Broadcast 机械复制 PersonAct 图。`execute` 不属于通用 Agent 能力：Agent 只提案，World Committer 或 Render Gateway 才产生副作用。

2026-08-21 的修正进一步把 Director 拆成两种运行频率：

```text
每个 generation wave 必经
  Segment Completion：在角色响应后补齐该段时间、因果、对象结果和 Event 边界

低频、可 NoOp
  Narrative Intervention：管理长期线程、机会、压力和剧情节奏
```

它们可以先由同一个 Director Agent 完成，但契约和评测必须分开。前者是无 Maze 的 Character Runtime 能闭合世界段的基础能力；后者才是可延后的高层导演优化。

## 2. 为什么需要导演层

纯 Generative Agents 擅长产生 believable everyday behavior，但不保证：

- 一条重要线索会在可接受时间内被触发；
- 角色弧会产生推进而不是日常循环；
- 冲突、缓和和高潮具有节奏；
- 多条 Event 不会长期停滞或互相稀释；
- 一个 Galgame 体验具有可辨识的主题和段落。

导演层的目标不是代替 Character Agent，而是在不破坏角色自治的前提下，调节世界的**叙事势能**。

因此更准确的运行时名称建议是 `NarrativeDirector`：它区别于制作期的脚本 Director、Skill 工作流和只负责画面效果的 Stage Director。

### 2.1 每轮必需的 Segment Completion

Character Agent 返回的是局部角色表演、意图、台词或对环境的反应，不足以单独回答“这一段客观上发生了什么、花了多久”。Director 读取：

```text
起始 committed snapshot
当前 active Event / 未完成动作 / 对象状态
各 Character 的局部输出与可知信息
本轮 Character / tool measured latency
跨 Event 的共享角色与因果约束
```

输出待校验的：

```text
DirectorResolution
  elapsed_ms               受限的相对语义时长
  outcome_summary          可观察结果摘要
  creative_external_events 环境反应及局部因果引用
  entity_state_changes     合法的对象状态结果
  session_intent           keep_open / resolved
```

Runtime 的 Segment Assembler 再从 Snapshot、当前 Session、已接受 ActionProposal、Director trace 身份与 Resolution 确定性构造内部 SegmentDraft，注入版本、绝对时间、Proposal Event、事件键、来源、角色 payload 和 `move` 位置结果。

具体持续时间不能由程序的通用 `duration=120s` 表替剧情作答。Director 可以把排队或冲煮保持为 `ACTIVE` 跨越多个 generation wave；当后续实际运行时间和新输出足以支持完成时，再在后续 Segment 中关闭。因此 Event duration 可以由 `ended_at - started_at` 事后得到，而不是在开始时硬编码。

Director 只提出 DirectorResolution；它不拥有 Proposal Event 的版本、来源或角色内容。Assembler 负责按权限构造，单调时间、角色双占用、知识边界、对象前后状态和因果引用仍由 Validator 检查。

### 2.1.1 地点到访前的信息发现

当 Character 的 `go_to(location)` Proposal 已通过移动前提校验时，Runtime 在该角色下一次基于目的地信息决策前，为 Director Graph 注入同一 `world_version` 的 `LocationView`。这是 mandatory typed input，不是由模型自行选择是否调用的开放 Tool。

Runtime 先确定性过滤已失效、角色已知或 disclosure 不允许的信息；只有仍存在多种合理传播方式或叙事时机时才调用 Director。Director 输出 `NoOp / DiscoveryPlan`，并只能引用 LocationInfo 声明的 discovery channel：

```text
公开日程 / 已存在海报 -> Projector 可直接形成引用 Info revision 的 Candidate
新消息 / 他人告知 / 新公告 -> 先提交 WorldEvent，再由 Projector 投影
到场声音 / 现场表演       -> 等 arrival 已提交且满足 same_scene 后投影
```

Director 不得因为 LocationInfo 存在就声称角色已经知道，也不得临场杜撰不存在的海报、广播或知情人。Location 的权威事实、Info revision 和完整规则见[地点 World Model](location-world-model.md)。

### 2.2 高层 Narrative Director 可以控制什么

```text
Narrative Thread
  当前未解决的承诺、秘密、误会、关系张力和目标冲突

Narrative Constraint
  某线索在时间窗内获得暴露机会；某角色弧不能无原因跳变

World Stimulus
  电话、天气、偶遇条件、消息、公共事件、资源变化

Priority / Budget
  哪条线程更值得获得世界机会和计算预算

Guardrail
  禁止角色越权获知、禁止无因果情绪反转、保护硬设定
```

### 2.3 导演不能控制什么

- 不能直接替角色选择行动；
- 不能绕过 Validator/Ledger，把补完草稿直接宣称为已提交世界事实；
- 不能绕过 Perception 把秘密塞进角色记忆；
- 不能为了剧情推进强制所有 Agent 接受互动；
- 不能直接输出 WebGAL DSL；
- 不能把“观众更爱看”作为修改过去事实的理由。

一句话边界：

> Character Agent 控制角色选择和反应；Director 补齐世界段的时间与因果，并低频控制机会、压力和约束；Validator/Committer 控制什么可以成为已提交事实。

### 2.4 只有高层叙事干预的默认动作必须是 `NoOp`

`NoOp` 约束的是 Narrative Intervention，不是每轮必需的 Segment Completion。导演对长期剧情应是低频、最小干预的滚动控制器；只有角色行为长期没有推进 Narrative Thread、硬约束即将失效或场景不可达时才介入。

候选刺激先过硬约束，再按下式做相对评分：

```text
DirectorScore =
  scene_goal_progress
+ causal_coherence
+ persona_consistency
+ tension_and_pacing
+ character_coverage
- intervention_cost
- repetition_and_contrivance
- future_unreachability_risk
```

`NoOp` 永远参与比较；世界自己能推进时，不干预就是最优动作。

导演介入可按强度分级：

```text
L0  观察 / NoOp
L1  轻量世界刺激、开放 affordance
L2  带 deadline 的剧情锚点，但完成路径自由
L3  停滞救场、要求重新规划
L4  固定 canon/cutscene，明确退出 Agent 自治模式
```

连续停滞才升级；恢复推进后立即降级并进入冷却。

## 3. 为什么需要导播层

Director Completion 和 Committer 先产生连续、多地点、并发且包含 generation span 的世界历史；玩家一次只能观看有限窗口。导播层解决的是**观看投影**而不是世界生成。

### 3.1 导播读取什么

- 已提交 WorldEvent 与 evidence；
- Event 的参与者、地点、时间区间、因果关系和显著性；
- 当前 Viewer Cursor 与未读历史；
- 各 Event 的 Ready Render 水位；
- 当前镜头的角色/场景连续性；
- 导演标记的主题、伏笔和优先线程；
- 玩家明确选择的 Event 和观看模式。

### 3.2 导播输出什么

```text
BroadcastPlan
  selected_event_id
  source_world_interval
  projection_mode
  target_render_duration
  camera/viewpoint
  reveal_scope
  transition
  artistic_reason
  evidence_ids
```

`projection_mode` 至少包括：

```text
continuous      连续演出
omit            省略纯技术空档
compress        压缩低信息时间
cutaway         切至另一并发 Event
montage         聚合赶路/重复劳动等长动作
summarize       用旁白/摘要跨越历史
dramatic_pause  明确艺术理由的停顿或沉默
replay          从历史 Event Log 回放
```

### 3.3 导播不能做什么

- 不能改变 WorldEvent 参与者、时序和结果；
- 不能把没发生的台词补成“更好看的剧情”；
- 不能未经 Director Completion 就自行把纯推理 latency 翻译成角色沉默或咖啡完成；
- 不能为了 Render Buffer 方便而宣称世界停止；
- 不能把相机没拍到等同于 Event 没发生。

一句话边界：

> 导播控制选择、压缩和表达；它不控制事实。

## 4. 两层不是上下级流水线

最危险的误解是：

```text
Director 写剧情 -> Character 照演 -> Broadcast 拍出来
```

这会退化为传统脚本生成，角色没有真正自治。更合理的闭环是：

```text
Character generation 返回局部角色输出
       |
       v
Director Segment Completion -> Validator / Committer -> World Segment
       |
       +--> 下一轮 Character Agents 在局部认知下自主响应
       +--> Narrative Director 低频观察线程并向未来施加 Constraint/Stimulus
       +--> Broadcast 从已提交历史选择观看方式 -> Render / Viewer
```

Narrative Intervention 只能进入**未来世界条件**；Segment Completion 只能补完尚未提交的当前 generation wave；Broadcast 只能进入**展示计划**。三者都不能回写已经提交的 WorldEvent。

### 4.1 Director 与 Broadcast 之间设置反馈防火墙

```text
在线允许
  Runtime 拒绝原因 -> Director 重新选择未来机会
  缺少 Ready Render / 播放故障 -> Broadcast 重新调度展示

在线禁止
  热度 / 弹幕 / 高潮评分 -> 当前局 Director 或 Character
  Broadcast 精选片段 -> Character Memory

局后可选
  聚合观看指标 -> 下一局策略评估
  但必须同时约束事实准确、角色自主、连贯性与多样性
```

否则系统会形成“观众爱看争吵 → 导演继续制造争吵 → 导播继续只拍争吵”的 spectacle feedback loop，最终世界不再自然，只剩注意力优化。

若玩家投票或弹幕确实要影响世界，必须由 Runtime 把它提交为带来源、可感知、可拒绝的世界事件，而不是隐藏 Reward。

### 4.2 Watermark 与事实成熟度

Broadcast 只消费 Runtime 声明稳定到某一 `event_watermark` 的事实。面对迟到 Agent 结果或仍在 ACTIVE 的 Event，导播必须标记：

```text
fact       已提交客观事实
quote      角色明确说过的话
inference  导播/叙事解释，不能伪装为角色内心事实
unknown    世界尚未确认
```

这样可以避免“先解说成真，后面 Runtime 又提交相反事实”。

## 5. Timeline 是世界执行语义，不是 UI

Timeline 同时承载：

- `ActionInterval`：动作何时开始、完成或中断；
- `InteractionSession`：邀请、接受、会话和结束；
- `EpistemicTime`：角色何时知道某条信息；
- `Plan/Commitment`：计划和承诺何时建立、冲突、失效；
- `WorldTransaction`：客观事实与因果链；
- `WorldEvent`：可被叙事理解的事件区间；
- `BroadcastProjection`：世界区间怎样投影成演出；
- `ViewerCursor`：玩家看到了哪里。

它还必须显式保留两个来源不同的时间：

- `MeasuredGenerationSpan`：Agent/工具真实花费的时间；
- `DirectorCompletedInterval`：这段时间在世界语义中被怎样解释、哪些动作仍然开放。

程序可以校验区间，却不通过通用硬编码表替 Director 决定“咖啡应在第几秒煮好”。

因此核心研究对象是 Timeline-Driven Multi-Agent World Runtime；`WorldEvent -> RenderArtifact -> WebGAL DSL` 只是它的一个 Projection Adapter。

## 6. 两层各自的宏观算法问题

### Director Agent

1. **Temporal Completion：**怎样结合真实响应耗时，补出自然的 start/continue/interrupt/complete，而不靠固定 duration？
2. **Causal Completion：**多个 Character 的局部输出之间缺少哪些桥接事实和对象变化？
3. **Cross-event Reconciliation：**多 Event 异步返回时，怎样形成一个没有共享角色冲突的全局 Segment？
4. **Thread State Estimation：**哪些剧情线程活跃、停滞、过早解决或即将失效？
5. **Narrative Constraint Planning：**怎样给未来施加时间窗和软目标，而不直接决定角色动作？
6. **Stimulus Selection：**注入哪个可感知世界刺激，既能创造机会又不显得“作者之手”？
7. **Autonomy/Control Trade-off：**剧情推进与角色一致性冲突时如何权衡？
8. **Minimal Intervention：**怎样以最少刺激让场景保持可达，而不是不断重试直到角色配合？

### Broadcast Agent

1. **Event Selection：**并行世界中现在应该看哪一条？
2. **Temporal Projection：**哪些时间连续展示，哪些省略、压缩或切镜？
3. **Information Control：**如何避免通过镜头把角色不知道的秘密错误传给玩家或反过来？
4. **Continuity：**切换地点/角色后如何保持因果、情绪和视觉连续性？
5. **Buffer-Aware Planning：**在 Ready Render 不均衡时怎样维持体验，又不扭曲世界事实？
6. **Truthful Narration：**如何把 fact、quote、inference 和 unknown 分开呈现？

## 7. 当前建议的最小研究版本

### Director MVP

- 每个 generation wave 先生成一个可校验 DirectorResolution，再由 Segment Assembler 构造内部 SegmentDraft；
- 只支持 `start / continue / interrupt / complete` 四类动作迁移和 Event `open / continue / close`；
- 输入必须包含各 Agent latency 和当前 active Event，允许事件跨多 wave 保持 ACTIVE；
- 用 Anon/Soyo 排队、冲咖啡和被打断的 trace 检查是否存在硬编码 duration；
- Director 自有字段的 Assembler/Validator 失败返回上一版输出与结构化冲突，再让 Director 修补一次；权威上下文失败直接作为 Runtime 缺陷；
- Director 自身 latency 的绑定策略先作为显式实验项，禁止静默忽略；
- 以下才属于低频 Narrative Intervention：
- 维护 3–5 条 Narrative Thread；
- 每条只有 `state / tension / deadline / involved_characters / evidence`；
- 动作只允许 `boost_thread / suppress_thread / inject_stimulus / set_guardrail / do_nothing`；
- 不允许生成角色台词；
- 每 1–5 个 WorldEvent 或线程停滞时决策一次，不逐 tick 控制；
- Thread 结果允许 `completed / transformed / missed / expired`；角色拒绝配合不是 Runtime Failure。
- 每场最多 2 次主动刺激，设置干预算与冷却；LLM 提候选，确定性策略过滤和选择。

### Broadcast MVP

- 只处理已关闭或已冻结 revision 的 WorldEvent；
- 每次从 3 条 Event 中选 1 条；
- 只允许 `continuous / omit / cutaway / summarize / dramatic_pause`；
- 每个选择必须带 evidence 和 `artistic_reason`；
- 不生成新世界事实，只生成结构化 BroadcastPlan。
- 只消费 `event_watermark` 之前的事实；caption 明确区分 fact/quote/inference。
- 相同 World Event Cassette 下，开启或关闭 Broadcast 不得改变世界事件序列。

## 8. 仍未确定的研究问题

- Director 的目标函数由作者配置、人工偏好、Judge 还是学习策略定义？
- 硬剧情锚点应约束世界状态、Event 出现，还是只约束“创造机会”？
- Character Agent 连续拒绝导演创造的机会时，导演如何升级压力而不操偶？
- Broadcast 是否可以把玩家兴趣作为未来 Director 的弱反馈？怎样避免世界为了观众注意力退化成持续狗血？
- Director Completion 应依据哪些 Character 输出、行为 evidence 和 latency trace，把区间标注为 `generation_gap / character_hesitation / environment_wait / narrative_action`？Broadcast 如何在不重新解释世界语义的前提下消费该分类？
- Director 的补完调用本身也消耗时间，怎样避免为解释自身 latency 而递归调用？
- Director 输出相对区间后由 Binder 拉伸到 measured span，是否会扭曲自然动作持续时间？
- 一个 Event 应跨多少 generation wave 才能自然完成；如何避免 Director 每轮都急于闭合？
- 多玩家同时观看不同 Event 时，是否共享一个世界、一个 Director，但各自拥有独立 Broadcast Agent？
- Director 与 Broadcast 是否应共享基础模型但使用不同 Memory/Tool/Reward，还是彻底隔离？
- 如何量化“剧情连贯性提高但角色自主性没有下降”：计划保留率、拒绝率、行动分布熵和反事实差异是否足够？
- 训练与评估是否必须使用完整 Event Log，而不是 Broadcast 精选片段，以避免 spectacle selection bias？
- 隐形观众镜头与世界内摄像/直播会造成不同 observer effect，是否需要 broadcast/no-broadcast 成对回放？

这些问题是本项目高层算法研究的核心，当前不应提前冻结成实现细节。
