# 世界、互动、时间与实体建模调研

> 状态：研究结论与一期建模建议，不替代 `decisions.md` 中已经冻结的决策。
> 范围：从 `skill-references.md` 中选取与世界运行时最直接相关的 6 项工作，重点精读 EvolvingWorld。
> 核对日期：2026-08-30。

## 1. 结论先行

EvolvingWorld 最值得借鉴的不是“让 LLM 保存世界”，而是三件更具体的事：

1. 把长期模拟拆成可独立训练和评测的七个任务；
2. 把世界分成全局状态与地点/重要实体状态，并按不同节奏更新；
3. 用 hidden tracker 累积弱证据，避免一次互动立刻改写人物的长期特征。

它不能直接解决 MyGO-World 的核心运行时问题。论文中的时间只是场景序号 `t` 与场内轮次 `k`，没有持续时间、并发 Event、资源争用、原子事务和重放；实体是地点状态里的“重要非人物实体”，没有稳定 ID、revision 或所有权协议；交互生成后由 LLM World Model 直接改写完整状态。论文自己也承认所有角色共享单一客观世界、没有角色各自的主观感知与错误记忆。

因此建议采用：

```text
EvolvingWorld 的开放语义与任务分解
                +
MyGO-World 的 typed invariant core / Ledger / Perception boundary
                =
LLM 提议开放内容，确定性 Runtime 裁决并提交版本化事实
```

不要把 EvolvingWorld 当作 MyGO-World 的状态存储方案；把它当作 Character/Director 的训练任务设计、语义 facet 生成和轨迹评测方案。

## 2. 文献核对范围

引用数取自 OpenAlex，属于 2026-08-30 的动态快照，不同平台可能不同。所有条目均有合法开放全文。

| 工作 | 年份 / Venue | OpenAlex 引用 | 本次核对重点 | 开放全文 |
| --- | --- | ---: | --- | --- |
| EvolvingWorld | 2026 / arXiv preprint | 0 | 开放 schema、世界/人物共演化、7 个任务 | [arXiv HTML](https://arxiv.org/html/2607.17250) / [代码](https://github.com/HKUST-KnowComp/EvolvingWorld) |
| BookWorld | 2025 / ACL | 1 | World Agent、地点、场景和移动轮次 | [ACL Anthology](https://aclanthology.org/2025.acl-long.773/) |
| CharacterBox | 2025 / NAACL | 3 | Narrator 作为世界裁判、环境与 BDI 状态 | [ACL Anthology](https://aclanthology.org/2025.naacl-long.323/) |
| IBSEN | 2024 / ACL | 9 | objective → outline → instruction → check | [ACL Anthology](https://aclanthology.org/2024.acl-long.88/) |
| StoryVerse | 2024 / FDG | 22 | abstract act、可执行 action schema、Game Environment | [arXiv HTML](https://arxiv.org/html/2405.13042) |
| Generative Agents | 2023 / UIST | 1763 | 地点/对象环境、计划时间、感知—反应 loop | [ACM DOI](https://doi.org/10.1145/3586183.3606763) / [arXiv HTML](https://arxiv.org/html/2304.03442v2) |

这是针对“世界、互动、时间、实体”问题的核心样本，不是对 `skill-references.md` 中人物卡、镜头语言和视频生成论文的逐篇复审。

## 3. EvolvingWorld 实际怎样建模

### 3.1 状态

在场景步 `t`，论文把某地点的世界状态和某人物状态写为：

```text
WorldState(location=l, t) = (GlobalState(t), LocationState(l, t))
CharacterState(i, t)     = (Profile(i, t), HiddenTracker(i, t), Motivation(i, t))
```

- `GlobalState`：开放 schema 的全局背景，例如历史、社会制度、阶级秩序与文化价值；
- `LocationState`：地点描述及其中“重要非人物实体”的状态；地点允许父子嵌套；
- `Profile`：开放 schema 的人物档案；
- `HiddenTracker`：尚不足以改变档案的弱信号和累积证据；
- `Motivation`：当前场景级动机。

原文公式和观测定义见 [§3.2 States and observations](https://arxiv.org/html/2607.17250#S3.SS2.SSS0.Px1)，全局/地点设计见 [§3.1.2](https://arxiv.org/html/2607.17250#S3.SS1.SSS2)。开放 schema 不是完全无结构：它仍然有固定容器，只允许容器内部的维度由模型按作品决定。

### 3.2 交互

一次场景按七个任务推进：

1. `scene_cast`：World Model 选下一场人物集合；
2. `location_scenario`：World Model 选地点并写场景设定；
3. `motivation_update`：Character Agent 为每位参与者生成入场动机；
4. `next_character`：World Model 选择下一位行动者或结束场景；
5. `interaction_gen`：Character Agent 生成一个互动单元；
6. `world_update`：每个互动后判断是否更新全局/地点状态；
7. `character_update`：场景结束后更新参与者档案和 hidden tracker。

详见 [§3.2 Task sequence](https://arxiv.org/html/2607.17250#S3.SS2)。互动单元把 `[...]` 内心、自然语言台词和 `(...)` 动作混在同一内容字段中；行动者还可以是 `Environment` 或人物组。官方代码会遮掉其他人物的 `[...]`，但角色仍直接收到当前地点世界状态与自身状态，见 [`mask_interactions_for_character`](https://github.com/HKUST-KnowComp/EvolvingWorld/blob/main/simulation/utils.py) 与 [`run_scene_execution`](https://github.com/HKUST-KnowComp/EvolvingWorld/blob/main/simulation/simulator.py)。

这一模型的优点是循环清楚、监督样本易构造；缺点是“说了/做了什么”和“客观上成功发生了什么”之间没有独立的 resolution 契约。动作文本一旦生成，下一步就是 world update。

### 3.3 时间

EvolvingWorld 的时间是两层离散顺序：

```text
t = 第几个 scene
k = scene 内第几个 interaction
```

`world_update` 在每个 `k` 后运行，`character_update` 在整个 `t` 结束后运行，未参与人物和非当前地点原样带到 `t+1`。这提供了清楚的更新节奏，却没有：

- 世界时钟或时区；
- 动作开始/结束与持续时间；
- 同时发生的多个 Event；
- happens-before、因果前驱或资源占用区间；
- generation latency 与故事时间的映射；
- 版本冲突、原子提交和历史重放。

所以它证明的是“跨场景状态持续性”，不是 MyGO-World 所需的时间模型。

### 3.4 实体与地点

论文明确支持嵌套地点，并在每个地点中保存重要实体及其状态，例如窗边的圣诞树，见 [§3.1.2](https://arxiv.org/html/2607.17250#S3.SS1.SSS2)。但“实体级”主要意味着实体信息被写进地点状态，并不等于存在独立实体总账。官方 `world_update` 的行为是：

- 模型返回是否更新 global/location；
- 如更新，则返回完整新状态而不是 delta；
- Python 代码直接替换内存中的 `global_state` 或 `location_states[location]`；
- 容错逻辑还会尝试修补截断 JSON。

见 [`update_world_state`](https://github.com/HKUST-KnowComp/EvolvingWorld/blob/main/simulation/simulator.py#L1100-L1142) 和 [`extract_json_fragment`](https://github.com/HKUST-KnowComp/EvolvingWorld/blob/main/simulation/utils.py)。这对研究型文本模拟足够，但不能提供实体稳定身份、CAS、revision、provenance、跨地点移动或事务一致性。

### 3.5 可见性与主观世界

论文做对了一半：其他人物的私有 thoughts 会从互动历史中移除。但它的 limitation 明确说明系统只有一个共享客观状态，没有为每个角色维护不同感知、错误记忆或主观世界，见 [Limitations](https://arxiv.org/html/2607.17250#Sx1)。

这意味着 MyGO-World 现有的 `WorldEvent -> PerceptionProjector -> Observation -> Character Memory` 不能被 EvolvingWorld 的 observation 简化掉；反而是我们已经比论文更严格的部分。

### 3.6 评测

EvolvingWorld 的 WORLD 分数包含：

- 场景规划：cast、地点/场景合理性、跨场景连续性；
- 说话者管理：选角、环境描写时机、群体行动、覆盖平衡、结束时机；
- 世界状态维护：global/location 的更新敏感度与准确性；
- 指令遵循。

其中最值得直接移植的是四个错误导向指标：

- `GUS`：全局更新不过度、不遗漏；
- `GSA`：全局事实准确、过期信息及时退役、表达不膨胀；
- `LUS`：暂时变化不持久化，持久变化不遗漏；
- `LSA`：空间一致、实体准确、跨场景连续。

定义见 [World State Maintenance](https://arxiv.org/html/2607.17250#A5.SS5.SSS3)。这些指标应改成 MyGO-World 的 Validator/Golden Trace 缺陷标签，而不是只保留一个总分。

## 4. 其他工作的互补结论

| 工作 | 可借鉴 | 不能直接照搬 |
| --- | --- | --- |
| Generative Agents | 计划有地点、起始时间、持续时间；环境包含层级地点、对象与 affordance；每个 time step 感知并决定继续或反应 | 单一沙盒、固定步进、手工地图；对象自然语言状态不是事务总账 |
| StoryVerse | `Story Domain = characters + locations + executable action schemas`；Game Environment 执行动作并更新状态，LLM plan 还要经过可执行性反馈 | Director 生成具体人物 action sequence，容易越过角色自治；世界变量和 memory 混在 World State 描述中 |
| IBSEN | objective 列表、outline、仅给演员 synopsis/keywords、逐轮 objective check | 主要生成剧本文本，不维护完整世界与实体状态 |
| BookWorld | World Agent 处理环境响应、全局事件、场景选角；人物需同地点；移动按 travel rounds | 世界状态能力较薄，LLM World Agent 同时承担叙事刺激与环境裁决 |
| CharacterBox | 把 action influence、interaction result、character update、environment update 分开；角色有 self-belief / environment-belief | Narrator 同时裁决事实、更新人物心理和环境，职责过宽；时间只是环境文本字段 |

一手依据：[Generative Agents §3.2](https://arxiv.org/html/2304.03442v2#S3.SS2) 与 [§4.3](https://arxiv.org/html/2304.03442v2#S4.SS3)、[StoryVerse §2.1](https://arxiv.org/html/2405.13042#S2.SS1) 与 [§2.3](https://arxiv.org/html/2405.13042#S2.SS3)、[IBSEN §3.1](https://arxiv.org/html/2407.01093#S3.SS1)、[BookWorld §3.2.2](https://arxiv.org/html/2504.14538#S3.SS2.SSS2) 与 [§3.3](https://arxiv.org/html/2504.14538#S3.SS3)、[CharacterBox §3.2](https://arxiv.org/html/2412.05631#S3.SS2)。

## 5. 与 MyGO-World 当前设计的对照

### 5.1 当前设计已经更强的部分

仓库当前只有 Dynamic Render MVP，Go Agent Runtime 尚未实现；以下是 Wiki 已冻结、仍待代码兑现的设计优势：

- World Ledger 是客观事实唯一权威，Agent 只输出 Proposal/Plan；
- Character World Presence 与 Character Memory 分离；
- `WorldSegment + Entity Revision + WorldEvent` 支持原子提交、历史与重放；
- `PerceptionProjector` 做硬可见性裁剪，角色只处理局部 Observation；
- `EventSession` 与 `WorldEvent` 分开；
- World Time、Render Time、Viewer Time 分开；
- Location Fact、Location Info、实际发生的 Event 分开。

这些边界不应为了复刻 EvolvingWorld 而回退。

### 5.2 当前设计仍缺的部分

结合 EvolvingWorld，当前文档还需要在实现前钉死以下细节：

1. `ActionProposal` 与客观 interaction outcome 的中间契约仍偏薄；
2. Object/Resource 只有概念字段，尚无“何时从 LocationFact 提升为实体”的机械判据；
3. 开放的叙事语义放在哪里尚不清楚，若全塞进固定字段会僵硬，全塞进 JSON/text 又失去校验；
4. 人物长期变化如何进入行为生成尚未确定；当前 Character Skill 不应被一次场景直接覆盖；
5. Scene/Event 的叙事规划与 Scheduler 的资源/并发裁决仍可能互相越权；
6. Actual Runtime 只有原则，缺少可直接测试的区间与因果约束结构。

## 6. 建模建议

### 6.1 采用“强类型内核 + 开放语义 facet”

不要在“固定 schema”和“任意文本”之间二选一：

```text
Typed Core
  id / kind / location / containment / lifecycle / ownership
  world_version / valid time / source segment / evidence
  resource occupancy / disclosure / causal refs

Open Semantic Facets
  namespace / key / value_json
  confidence / evidence_refs
  change_policy / status / revision
```

规则：

- 硬约束只读取 Typed Core；facet 不能绕过 Validator 改位置、所有权、存活状态或资源占用；
- EvolvingWorld 风格的社会秩序、气氛、关系阶段、象征意义适合放 facet；
- 高频 facet 可通过 registry 晋升为 typed field；
- 每个 facet 有稳定 key、revision 和 evidence，禁止模型整块覆盖全局文本。

这保留 open-schema 的跨作品适应性，又不牺牲重放和冲突检测。

### 6.2 把实体提升规则写成判定函数

建议在一期使用以下判定：只要某对象满足任一条件，就必须有稳定 `entity_id`。

- 可跨地点移动；
- 可被拥有、转交、占用、消耗、损坏或修复；
- 会被两个 Event 同时引用；
- 其状态会成为动作前置条件或因果证据；
- 后续角色必须能无歧义地再次指称它。

否则它可以继续作为 `LocationFact` 或 Render asset。EvolvingWorld 的“重要实体”判断可用作候选发现器，但最终提升应由上述规则决定。

建议的最小实体 revision：

```text
EntityDefinition
  entity_id / entity_kind / canonical_name / aliases

EntityStateRevision
  entity_id / revision / lifecycle
  location_id / container_entity_id?
  owner_id? / occupied_by[] / quantity?
  physical_state / capabilities[]
  semantic_facets[]
  effective_world_version / valid_time
  source_world_segment_id / evidence_refs
```

### 6.3 明确“尝试—裁决—提交”，不要把文本动作当事实

把 EvolvingWorld 的 `interaction_gen -> world_update` 扩展成：

```text
ActionProposal
  -> InteractionAttempt
  -> Resolver / Director completion
  -> StateMutation candidates + OutcomeDraft
  -> Validator
  -> atomic WorldSegment
  -> WorldEvent
  -> per-character Observation
```

`ActionProposal` 最少应区分：

```text
private_thought       只进角色私有 trace/memory
speech                可产生可听见的候选事件
physical_action       需要 affordance、目标与前置条件
communicative_action  需要 channel、recipient 与披露范围
```

并携带 `actor_id / target_ids / based_on_world_version / precondition_refs / intent / requested_effects / evidence_refs`。成功、部分成功、失败和被打断是 Resolver 的输出，不能由发起角色自行宣布。

### 6.4 时间同时保留“顺序轴”和“语义区间轴”

可以借 EvolvingWorld 的 `scene_seq / turn_seq`，但不能用它替代 World Time：

```text
Ordering
  event_session_id / scene_seq / turn_seq / commit_seq

Semantic Time
  earliest_start / latest_start?
  duration_min / duration_max?
  actual_start / actual_end
  relation: before | meets | overlaps | during
  causal_predecessor_ids[]

Generation Evidence
  started_at / returned_at / measured_elapsed_ms
```

Temporal Binder 只能把 `measured_elapsed_ms` 作为证据绑定到语义区间，不能让 wall clock 直接提交剧情。跨 Event 冲突先由角色/实体占用区间检测，再决定合并、重基或拒绝。

### 6.5 把 hidden tracker 映射成证据累积，不改写 Character Skill

EvolvingWorld 的多时间尺度思路值得采用，但 MyGO-World 不应让一次 LLM 调用覆盖版本化 Character Skill。建议：

- 短期情绪、目标、计划：普通 Character Memory；
- 尚未成熟的长期变化：`trait_evidence` MemoryRecord，引用 Observation/WorldEvent；
- 查询时由 reducer 得到当前 trait hypothesis；
- 只有证据阈值、方向一致性和回归检查通过，才形成 versioned `CharacterSkillOverlay` 候选；
- base Skill 永不原地覆盖，overlay 可撤销、可重放，并记录生成模型与证据。

这样保留“情绪快、人格慢”，也避免人物在一次争吵后永久换人格。

### 6.6 把七任务映射为代理任务，不映射为写权限

| EvolvingWorld task | MyGO-World 建议归属 | 输出性质 |
| --- | --- | --- |
| `scene_cast` | Director / Event Planner | 候选 EventSession 或 cast suggestion |
| `location_scenario` | Director | NarrativeConstraint / scene opportunity |
| `motivation_update` | PersonAct 私有 planning | 临时 scene objective，不是客观事实 |
| `next_character` | Event Scheduler + Director hint | 调度建议，需满足参与者可用性 |
| `interaction_gen` | Character Agent | ActionProposal |
| `world_update` | Resolver/Director | StateMutation candidates |
| `character_update` | Character feedback/reducer | Memory writes / SkillOverlay candidate |

这一映射可以直接用于以后收集 SFT 数据，但所有模型输出仍要经过现有 Runtime 边界。

### 6.7 评测采用“双层门禁”

确定性门禁：

- revision/CAS、单调时间、实体不双占用；
- location/containment/ownership 合法；
- knowledge boundary、thought 不泄漏；
- 同一输入 Golden Trace 可重放；
- Render 失败不回滚 World。

语义评测：

- 采用 EvolvingWorld 的 GUS/GSA/LUS/LSA、Scene Continuity、Turn Orchestration；
- 采用 IBSEN 的 objective completion；
- 采用 CharacterBox 的 action influence / interaction result 分项诊断；
- 采用现有 CoSER 罚分式角色一致性维度。

LLM Judge 只评价语义质量，不能替代确定性门禁。

## 7. 一期建议的最小落地顺序

1. 在 `world/models.go` 先冻结 `EntityDefinition`、`EntityStateRevision`、`ActionProposal`、`OutcomeDraft`、`StateMutation` 与时间区间字段；
2. 用咖啡场景写三个反例：同杯咖啡被重复创建、两人同时占用咖啡机、角色宣称动作成功但 Resolver 判失败；
3. 实现 typed core 的单写者 commit 与重放，再加入 `semantic_facets`；
4. 用 Fixture Agent 跑 `proposal -> resolution -> commit -> observation`，不要先接 LLM；
5. 接入 EvolvingWorld 风格的七任务 prompt 时，每个任务只返回对应候选类型；
6. 最后增加 `trait_evidence -> CharacterSkillOverlay candidate`，先离线评估，不进入实时主链路。

一期完成标准应包括：相同 Ledger 重放得到完全相同的 WorldSnapshot；并发 Event 不能让角色/实体双占用；任何角色都看不到其他角色的 thought；模型输出无法直接覆盖 global/location/entity state；同一场景的暂时变化不会错误持久化。

## 8. 建议形成的决策

建议后续在 `decisions.md` 冻结三条新决策：

1. **开放 schema 仅作为版本化 semantic facet，不作为世界硬约束的存储格式。**
2. **互动采用 Proposal/Attempt/Outcome/Commit 四段语义，生成文本不等于事件成功。**
3. **人物长期演化通过 evidence accumulator 与 Skill overlay 表达，base Character Skill 不被运行时原地改写。**

这三条是 EvolvingWorld 对本项目最有价值的正向吸收，也是避免其研究原型边界进入生产 Runtime 的关键防线。

## 9. 资料与可复现性说明

- EvolvingWorld 元数据由 [arXiv API](https://export.arxiv.org/api/query?id_list=2607.17250) 核对；论文 v1 发布于 2026-07-19。
- EvolvingWorld 的状态、七任务、限制与评测均来自[论文原文](https://arxiv.org/html/2607.17250)；直接写回行为来自[官方仓库](https://github.com/HKUST-KnowComp/EvolvingWorld)。
- 其余论文的题名、年份、venue 与开放全文由 arXiv、ACL Anthology、ACM DOI 和 OpenAlex 交叉核对。
- Semantic Scholar API 本次返回 HTTP 429，因此引用数改用 OpenAlex；报告没有把平台引用数用于论证模型优劣。
- EvolvingWorld 官方数据声明为仅限科研用途；如未来使用其数据训练商用模型，需先重新核对许可证和源作品限制，而不能只依据代码仓库可访问性。
