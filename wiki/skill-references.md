# 人物 / 导演 / 导播 Skill 外部参考

> 用途：为 Character Skill、Director Skill、Broadcast Skill 的字段设计、提炼流程和评测提供外部输入。
> 状态：调研笔记，不是已冻结决策。标注「已核对」的字段和结论来自论文/源码原文；标注「工艺常识」的来自写作与 TRPG 行业惯例，未逐条溯源。
> 更新：2026-08-25

## 0. 速览：谁对应我们的哪一层

| 我们的组件 | 最值得参考的外部工作 | 参考什么 |
| --- | --- | --- |
| Character Skill（人格与行为策略） | Character Profile Axes、BEYOND DIALOGUE、CharacterBox | 5 维 28 字段 schema；profile↔行为句级对齐；行为准则优于描述性速写 |
| Character Memory（主观认知） | REVERIEMEM、CoSER | 三层记忆 + 可见性标签；inner thought 显式化 |
| 人物提炼（JSON 人物定义的来源） | CoSER、BookNLP、Think Before you Write | extract-then-aggregate 流水线与可直接改写的 Prompt |
| Director Agent（补完与叙事约束） | IBSEN、Dramatron、StoryVerse、EvolvingWorld | plot objective 列表 + outline→script→instruction→check 闭环 |
| Broadcast Agent（观看投影） | FilmAgent、ShotBench、SEAM | 镜头语言枚举、Debate-Judge 选镜、跨镜头实体一致性 |
| Validator / Pydantic Evals | CoSER 罚分 rubric、PersonaGym、InCharacter | 可直接落成评测维度和缺陷类型表 |

## 1. 人物提炼：从长文本到结构化人物

### CoSER（ICML 2025，arXiv 2502.09082，[Neph0s/CoSER](https://github.com/Neph0s/CoSER) ★221）
**最值得直接抄的一份。** 从 771 本书提炼 29,798 段真实对话，采用 extract-then-aggregate 多阶段流水线。已核对的关键设计：

1. **动态按剧情分块**：抽取时同时判断本 chunk 内剧情是否 `truncated`，截断的剧情拼到下一 chunk，避免固定窗口切碎情节。
2. **一次抽取产出的字段**（已核对原 Prompt）：
   - `plots[]`：`chapter_title / first_sentence / last_sentence / prominence(1-100) / summary / key_characters[] / conversation[] / state(finished|truncated)`
   - `plots[].key_characters[]`：`name / description（本剧情之前的人物描述，≤20 词）/ summary（在本剧情中的角色、想法、行为与人物发展，≤30 词）`
   - `conversation[]`：`scenario / topic / key_characters[](name, motivation) / dialogues[](character, message)`
   - `next_chunk_start`：下一块的起始句
3. **消息三分**：`speech` / `(action)` / `[thought]`。thought 对他人不可见，构成信息不对称；环境信息作为 `character: "Environment"` 的一条 utterance 写入。**每条 utterance 必须以内心想法开头。**
4. **原文锚定技巧**：不让模型抽取整段原文，只抽首句和末句，再用词法相似度回匹配原始文本 —— 解决引号/标点无法逐字复现的问题。
5. **二次精修**：单独一次 LLM 调用 refine `scenario` 与每个角色的 `motivation`，因为模型在一次多字段抽取里倾向把信息分散而不重复，导致 scenario 上下文不足。这一条对我们的分阶段 Prompt 设计直接有用。
6. **人物档案生成**：叙事体而非字段体，要求串起 `background / physical description / personality traits / core motivations / notable attributes / relationships / key experiences / major plot involvement / key decisions / character arc`，并明确禁止编造不确定细节。
7. **消融结论**：显式给出 inner thoughts 和 motivations，推理期与训练期都稳定提升质量；去掉 inner thoughts 训练的模型一致更差。

### Character Profile Axes（arXiv 2601.04716）
**字段 schema 的最佳来源。** 统一层级 schema：5 个顶层维度 / 15 中层 / 28 叶子字段，字段选取参考 RisuAI 上 50 份用户手写卡并只保留「对话中可表达」的字段。已核对的完整结构：

- **Personal Attributes**：Name / Age / Gender / Origin / Appearance
- **Personality Traits**：Big5(Extraversion, Conscientiousness, Agreeableness, Neuroticism, Openness) / Preference(Like, Hate) / Character(Positive Traits, Negative Traits)
- **Interpersonal Relationships**：Social Interaction(In normal situations, In close relationships, In conflict situations) / Relationships(Positive, Negative, Neutral)
- **Motivations**：Goal / Morality / Worldview
- **Abilities**：Knowledge and Skills(Skills/Expertise, Education) / Emotional Abilities(Commonly felt emotions, Ability to regulate emotions, Way of expressing emotions)

**对我们最重要的三条实证结论：**
- **Structure 轴几乎无影响**：结构化 schema 与自由文本叙事的表现差异 ≤0.03 且多数不显著。也就是说 Character Skill 用 JSON 还是散文写，模型效果差别很小 —— 选结构化应该是为了工程可校验和可版本化，不要指望它本身提升质量。
- **Familiarity 轴影响可忽略**：模型是否「认识」这个角色不是主要瓶颈。对原创角色（MyGO 二创同理）是好消息。
- **Disposition 轴是主导瓶颈**：不道德/反派角色表现大幅一致下降（CoSER 上差 5.89–9.22 分）。退化集中在 `Motivations.Goal` 和 `Motivations.Morality` 等价值负载字段，`Personality Traits` 相对稳定。DPO 对齐会放大这个差距。写反派 Skill 时要预期这种"角色崩坏成助手"的失真。
- 论文给出的缓解手段 FACD 是解码期对比（把完整 profile 对比一份删掉道德敏感字段的 sanitized profile），依赖 logits，走 API 用不了，但"哪些字段扛得住、哪些字段容易被对齐冲掉"这份清单可以直接用于 Skill 设计与 Validator 关注点。

### 其他提炼路线
| 工作 | 路线 | 对我们的价值 |
| --- | --- | --- |
| [booknlp/booknlp](https://github.com/booknlp/booknlp) ★929 | 传统 NLP 流水线：人物共指、引语归属、事件 | 不依赖 LLM 的确定性预处理，可做 fixture 与交叉校验 |
| Think Before you Write（2604.11435） | 推理与生成解耦：推理模型产出结构化 QA trace，生成模型据此写人物描述 | 反直觉发现：人物描述生成任务上，内置 reasoning 关掉反而更好。适合我们做两段式提炼 |
| ChatHaruhi（2308.09597，★2107） | 32 个角色，人工整理 + 检索式记忆复现动漫角色 | 番剧类角色的语料整理方式最接近 MyGO 场景 |
| RoleLLM（2310.00746，★528） | 100 角色，Context-Instruct + RoleGPT 生成角色 profile 与指令数据 | 从剧本反推 role profile 的做法 |
| Character-LLM（2310.10158，★645） | "经验上传"：把人物经历写成场景再训练 | 如果以后要微调 Character 模型 |
| Dialogue-Based Multi-Dimensional Relationship Extraction（2507.04852） | 从小说对话抽多维人物关系 | 补 `Interpersonal Relationships` 的自动化 |
| [Neph0s/awesome-llm-role-playing-with-persona](https://github.com/Neph0s/awesome-llm-role-playing-with-persona) ★1065 | 综述列表 | 追踪入口 |
| [yingpengma/Awesome-Story-Generation](https://github.com/yingpengma/Awesome-Story-Generation) ★650 | 综述列表 | 导演侧追踪入口 |

### 工艺侧（工艺常识，未逐条溯源）
- **SillyTavern / Character Card V2·V3**（[spec](https://github.com/kwaroran/character-card-spec-v3)）：事实上的人物卡工业标准，已核对字段为 `name / description / personality / scenario / first_mes / mes_example / system_prompt / post_history_instructions / alternate_greetings / character_book(Lorebook) / assets / nickname / group_only_greetings`。`character_book` 就是按关键词触发的世界观条目 —— 与我们的 Location Info 披露机制思路同源。注意它把「人物」和「开场演出」混在一张卡里，我们应该拆开。
- **Egri《The Art of Dramatic Writing》三维人物**：生理 / 社会 / 心理 三层骨架。`Personal Attributes` 对应生理，`Origin/Education` 对应社会，其余对应心理 —— 可以用它检查 28 字段有没有漏层。
- **Stanislavski 的 given circumstances / objective / obstacle / tactics**：CoSER 的 "given-circumstance acting" 明确说是借自表演理论（已核对）。`scenario + motivation` 就是 given circumstances + objective。我们的 ActionProposal 可以显式带 obstacle 和 tactic 字段。
- **D&D 5e 的 Personality Traits / Ideals / Bonds / Flaws**：极紧凑的行为策略四元组，一句话一条，可直接触发行为。
- **Burning Wheel 的 Beliefs / Instincts / Traits**：`Instinct` 是「当 X 发生时我总是 Y」的条件规则，形式上就是可执行的行为策略。这是把 Character Skill 从"描述"写成"策略"最直接的范式参考。

## 2. Character Skill 与主观认知

### REVERIEMEM / Staying In Character（arXiv 2606.25632）
和我们 `model.md` 里「世界事实与角色主观认知必须分开」的设计高度重合，值得优先读。它命名了两种失败：
- **Factual Overreach**：共享检索或参数化记忆让角色用到自己视角之外的事实。
- **Stylistic Monotony**：profile 描述把角色压平成一个固定腔调。

三层记忆架构：
1. **Episodic**：第一人称场景记忆
2. **Semantic**：带**可见性标签**的事实
3. **Personality**：**情境相关**的说话与行为模式（不是一条固定风格描述）

配套 KBF-QA 基准（8 本小说 4,386 题）专测知识边界，Knowledge Boundary Fidelity 比最强基线高 34.6 个百分点。

**映射到我们：** 「可见性标签」正是 PerceptionProjector 的产物；第三层说明 Character Skill 里的语言风格不该是单值字段，应该按情境分支（这也和 28 字段里 `Social Interaction` 按 normal/close/conflict 分三条一致）。

### 其他
- **BEYOND DIALOGUE**（2408.10903，[BeyondDialogue](https://github.com/yuyouyu32/BeyondDialogue)）：指出用预定义 profile 去 prompt 特定场景的对话训练，会产生 profile 与对话互相冲突的训练偏差；提出场景级 profile↔dialogue 句级对齐。对我们的启示是 Skill 版本与实际提交行为之间需要一致性检查，而不是假定 Skill 一写就对。
- **CharacterBox**（2412.05631）：character agent + **narrator agent** 的沙盒，产出情境化细粒度行为轨迹而不是对话快照。narrator 协调角色间互动与环境变化 —— 职责边界和我们的 Director 很接近。同一作者线的结论：**行为准则（behavioral guidelines）优于描述性速写（descriptive sketches）**，这条直接支持把 Character Skill 写成规则而非散文。
- **BookWorld**（2504.14538）：从小说构建交互式 agent 社会做创意故事生成，是"番剧/小说 → 多智能体世界"这条路上最完整的开源系统形态参考。
- **EvolvingWorld**（2607.17250，[官方代码](https://github.com/HKUST-KnowComp/EvolvingWorld)）：**整体形态上最接近 MyGO-World。** 它用开放 schema 的 Character Agent + LLM World Model 维护全局状态、地点/重要实体状态和人物档案，定义 7 个可训练任务，数据规模为 57 本书 / 138,596 训练样本 / 222 测试快照，轨迹评测覆盖 10 维 20 指标。需要准确区分：论文时间仅是场景步 `t` 与场内轮次 `k`，没有持续时间、并发 Event、事务或重放；“实体级”是嵌入地点状态的重要非人物实体，不是稳定 ID + revision 的实体总账；官方代码由 LLM 返回完整状态后直接覆盖内存状态。论文还明确把“单一客观状态、没有角色各自主观世界”列为限制。因此最值得借的是七任务分解、全局/地点更新敏感度评测和 hidden tracker，不应照搬其状态所有权。完整核对见[世界、互动、时间与实体建模调研](world-interaction-time-entity-modeling-research.md)。

### 评测（可直接落成 Pydantic Evals 数据集）
CoSER 的**罚分式** LLM 评委（已核对缺陷类型表，比打分更适合做 Validator 诊断）：
- **Anthropomorphism**：Self-identity（缺主动性与目标 / 不独立决策 / 无明确好恶 / 表现得像"有帮助的 AI 助手"）、Emotional Depth（缺心理复杂度、把想法和感受直说而不用潜台词）、Persona Coherence、Social Interaction
- **Character Fidelity**：Character Language、Knowledge & Background（**出现超出角色当前阶段的未来信息** —— 正是我们要靠 PerceptionProjector 守住的）、Personality & Behavior、Relationship & Social Status
- **Storyline Quality**：Flow & Progression（冗长、重复他人观点、机械重复自己）、Logical Consistency
- **Storyline Consistency**：与原始对话的反应偏离

每条缺陷标 1–5 严重度，按严重度扣分。其他：PersonaGym 五维（Persona Consistency / Linguistic Habits / Expected Action / Action Justification / Toxicity Control）、InCharacter（心理量表访谈测人格保真）、CharacterEval（中文）、Can LLM Agents Stick to the Script（2608.08160，长程一致性）。

## 3. Director Skill

### IBSEN（ACL 2024，arXiv 2407.01093，[OpenDFM/ibsen](https://github.com/OpenDFM/ibsen)）
**和我们 Director 的职责最像。** director / actor / player 三类 agent，player 可选（人类介入）。已核对的循环：

1. Director 读 script settings 和**预定义 plot objective 列表**
2. 写一段延续当前 objective 的 story outline `O`
3. 把 outline 翻译成若干轮 script `S`，每轮含 `role` 和该角色的**预期发言**
4. **不把 script 直接当成台词**：只用它决定发言顺序和内容纲要，实际台词交给 actor 生成
5. 关键权衡：把原始 script 给 actor，actor 会照抄；完全放开，剧情会跑偏。于是 director 生成一条**"instruction"** 只透露必要的高层信息 —— 当前 story outline + 下一句 script 的简要梗概 + 若干**关键词**
6. Actor 发言后，director 用对话历史查询 LLM，**判断当前 objective 是否达成**；达成则推进到下一个 objective，否则继续用原 script 指导下一轮

Actor 侧记忆显式拆成 **Actor Profile / Memory Database / Character Database**（拒绝单一记忆模块，理由是条目增多后检索失效）；记忆文档用第一人称 **"monologue"** 改写 —— 检索仍用原文 embedding，喂给 actor 的是独白版本。profile 与 character database 随剧情动态更新。

### Dramatron（SIGCHI 2023，arXiv 2209.14958，[google-deepmind/dramatron](https://github.com/google-deepmind/dramatron) ★1112）
层级生成：logline → title → characters → scenes → places → dialogue。已核对的代码内实体（`colab/dramatron.ipynb`）：
- `Title(title)`
- `Character(name, description)` —— description 是**一句话**
- `Scene(place, plot_element, beat)`，`plot_element` 取值如 Beginning / Middle / Conclusion
- `Place(name, description)`
- 标记：`**Character:** / **Description:** / **Scenes:** / **Dialog:** / **Logline:**`；元素前缀 `Title: / Characters: / Description: / Place: / Plot element: / Previous beat: / Summary: / Beat: / Logline:`

**要吸取的教训（作者自己写在 README 里）**：15 位编剧的用户研究反馈是"不会用它写完整剧本"、输出"套路化"；他们真正用它做**世界构建**和替换人物/情节做探索。自顶向下的层级顺序也不符合所有人的写作流程。人物只有一句话描述是明显的薄弱环节 —— 我们的 Character Skill 要厚得多，这正是差异点。

### 其他
- **StoryVerse**（2405.13042）：用 abstract acts 做叙事规划 + LLM 角色模拟共同创作动态剧情。「作者意图与角色自治如何共存」这个问题的正面处理，和我们「Character 提案 / Director 补完」的分工同构。
- **Agents' Room**（2410.02603，DeepMind）：按叙事理论把小说创作分解成多个专职 agent 的多步协作。
- **HoLLMwood**（2406.11683）：writer / editor / actor 角色分工做剧本创作。
- **长文一致性与节奏**：DOC（2212.10077，Detailed Outline Control）、Improving Pacing in Long-Form Story Planning（2311.04459）、Dynamic Hierarchical Outlining with Memory-Enhancement（2412.13575）。节奏那篇对我们的「约 30 分钟领先库存」有直接参考价值 —— 现有系统的通病正是要么草草掠过重要事件、要么在无关细节上过度铺陈。
- **SNAP**（2601.11529）：plan-driven 可控交互叙事，明确处理 LLM 对话 agent 的时空错乱。
- **Drama Llama**（2501.09099）：LLM 驱动的 storylets 框架 —— storylet（前置条件 + 内容片段）是把作者控制与生成自由缝在一起的经典结构，和我们的 EventSession 可对照。

## 4. Broadcast / 编导 Skill

### FilmAgent（arXiv 2501.12909）
**导播分工与选镜流程的最佳参考。** 四个角色（已核对职责）：
- **Director**：设定人物档案、写场景大纲（明确每段的 where / what / who）、给剧本反馈、冲突时做最终裁决
- **Screenwriter**：写台词，并为**每句台词**指定站位和动作，按 Director 批评迭代
- **Actor**：只按自己的人物档案微调台词，保证对白贴合角色，把必要修改反馈给 Director
- **Cinematographer**：按镜头使用规范为每句台词选镜，与同行比较讨论

两个协作算法：**Critique-Correct-Verify** 用于剧本阶段，**Debate-Judge** 用于选镜阶段（两个摄影 agent 各自选镜并互相辩论，Director 作为 Judge 收尾裁决）。文中的辩论实例："Alex 没在移动，用 Tracking Shot 违反使用规范，应改用 Medium Shot 展示肢体语言" —— 这种**带规范依据的反驳**很适合做我们 Broadcast 的自检 Prompt。

环境侧的量化配置（可对照我们的素材与 Live2D 能力表）：15 个地点、65 个演员站位（32 站 + 33 坐，每个点位配人工描述，如"沙发旁、可坐、位于 A 与 C 之间、便于与这两处角色交流"）、9 类镜头（3 静 6 动）共 272 个、21 个动作、评测四维（剧情连贯性 / 对白与人物档案的一致性 / 镜头设置恰当性 / 动作准确性）。

### ShotBench（arXiv 2506.21356）
**镜头语言词表的权威来源。** 8 个维度（已核对）：`shot size / shot framing / camera angle / lens size / lighting type / lighting condition / composition / camera movement`，3.5k+ 专家标注 QA，来自 200+ 部（多为奥斯卡提名）影片。已核对的 shot size 定义示例：Close-up 贴近主体、通常含衣领，承载身份；Medium 含姿态与肢体动作；Long 含全身，交代主体所在位置。

**对我们的用法**：BroadcastPlan 的镜头字段直接用这 8 个维度做枚举，而不是自造词表。另一个实证提醒：即使最强 VLM 平均准确率也不到 60%，camera movement 维度过半模型不到 40% —— 说明**镜头术语的判定不可靠**，Broadcast 输出必须靠枚举 + Validator 约束，不能靠模型自由描述。相关：CineTechBench（2505.15145）。

### 其他
- **SEAM**（2608.22725）：Shot Entity-Attribute Memory，短剧规模化生成的跨镜头视觉一致性。现有 agent 框架逐镜头独立生成导致上下文漂移 —— 我们跨 RenderJob 的人物立绘/服装一致性是同一个问题。
- **Anim-Director / VideoDirectorGPT**：用 LLM 规划多场景视频，输出场景描述 + 实体位置与布局。
- **Agentic Aerial Cinematography**（2509.16176）：自然语言导演意图 → 具体镜头轨迹的映射，可参考"导演意图"到"可执行参数"的中间表示。

## 5. 可直接抄 schema / Prompt 的仓库

| 仓库 | ★ | 抄什么 |
| --- | --- | --- |
| [Neph0s/CoSER](https://github.com/Neph0s/CoSER) | 221 | 抽取 Prompt、人物档案生成 Prompt、罚分评委 rubric、`data/` 样例格式 |
| [google-deepmind/dramatron](https://github.com/google-deepmind/dramatron) | 1112 | 层级实体定义与 Prompt 前缀（在 `colab/dramatron.ipynb`） |
| [OpenDFM/ibsen](https://github.com/OpenDFM/ibsen) | — | director 的 outline/script/instruction/objective-check 四段 Prompt |
| [joonspk-research/generative_agents](https://github.com/joonspk-research/generative_agents) | 21986 | 已是我们的认知底座 |
| [kwaroran/character-card-spec-v3](https://github.com/kwaroran/character-card-spec-v3) | — | 人物卡字段与 Lorebook 触发机制 |
| [booknlp/booknlp](https://github.com/booknlp/booknlp) | 929 | 确定性人物/引语抽取 |
| [choosewhatulike/trainable-agents](https://github.com/choosewhatulike/trainable-agents) | 645 | Character-LLM 数据构造 |
| [LC1332/Chat-Haruhi-Suzumiya](https://github.com/LC1332/Chat-Haruhi-Suzumiya) | 2107 | 动漫角色语料整理 |
| [yuyouyu32/BeyondDialogue](https://github.com/yuyouyu32/BeyondDialogue) | — | profile↔dialogue 对齐任务构造 |
| [ExplosiveCoderflome/AI-Novel-Writing-Assistant](https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant) | 2625 | 中文长篇创作的世界观/写法引擎/RAG 工程组织 |
| [alfredxw/denova](https://github.com/alfredxw/denova) | 650 | Go 实现，内建 Skills / subagent workflow / 版本化，工程形态可对照 |

## 6. 对本项目的初步建议

未冻结，供 Skill 设计时讨论：

1. **Character Skill 的字段用 28 字段做骨架，但内容写成规则而不是描述。** 结构化本身不提升质量（Profile Axes 实证），所以结构化的收益要靠工程侧兑现：版本 hash、字段级 diff、Validator 可寻址。质量收益要靠"行为准则优于描述性速写"，即 Burning Wheel `Instinct` 式的「当 X 时我总是 Y」。
2. **语言风格不能是单值字段。** 按 normal / close / conflict 至少分三支（28 字段的 `Social Interaction` 和 REVERIEMEM 的 personality 层都指向这一点）。
3. **ActionProposal 显式带 thought / action / speech 三段。** CoSER 的消融证明 inner thought + motivation 对质量有稳定正贡献；三段式也天然对上我们「thought 属于 Character Memory、action/speech 才进 World Presence」的边界。
4. **Character Memory 的 semantic 条目带可见性标签。** 这是 PerceptionProjector 的落地形式，也是 REVERIEMEM 拿到 34.6 个百分点提升的地方。KBF-QA 的「知识边界」测法可以直接改造成我们的边界测试。
5. **Director 的补完以 objective 列表为锚。** IBSEN 的 objective-check 回路给了「补完何时收敛」一个可判定条件，比让 Director 自由补完更容易做确定性重放。
6. **Director 给 Character 的输入用"instruction + 关键词"而非成品台词。** 否则 Character 会退化成照抄，我们的 Proposal/补完分层就名存实亡了。
7. **BroadcastPlan 的镜头字段用 ShotBench 8 维枚举。** 并且因为模型在镜头术语上准确率很低，Validator 必须校验枚举合法性与使用规范（FilmAgent 的"Tracking Shot 要求主体在移动"就是一条可执行规范）。
8. **Validator 与 Pydantic Evals 用罚分制而不是打分制。** CoSER 的缺陷类型表可以几乎原样落成我们的诊断枚举，特别是「表现得像有帮助的 AI 助手」和「使用了角色当前阶段不该知道的未来信息」这两条 —— 后者正是我们权限边界的可观测指标。
9. **写反派或道德复杂角色时预留额外预算。** Disposition 是唯一被证实的大幅退化轴，且集中在 `Motivations.Goal / Morality`。对齐更强的模型这个差距更大。
10. **把 EvolvingWorld 拆成正反两部分参考。** 正向吸收七任务分解、开放语义 facet、hidden tracker 和轨迹级评测；反向对照其 LLM 完整状态覆盖、单一主观视图、无稳定实体 revision 与无事务时间模型。我们继续坚持 World Runtime 唯一权威，模型只产出待验证候选。
