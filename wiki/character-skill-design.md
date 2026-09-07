# Character Skill 当前设计

> 状态：当前推荐结构，与 `MVP.md`、ADR 0010、ADR 0026 和现有 Runtime 实现对齐。  
> 用途：编写、评审或提炼正式 Character Skill 时使用。外部论文、项目和字段调研见[Skill 外部参考](skill-references.md)。

## 1. 设计结论

MVP 为每个角色绑定一个简短、不可变、版本化的 Markdown Character Skill。Skill 描述角色跨场景稳定的心理与行为生成倾向；它不是人物百科、当前状态、任务脚本或感知权限配置。

正式 Character Skill 当前保持单文件：

```text
content/skills/characters/<character>-<version>.md
```

MVP 不采用 `SKILL.md + references/` Bundle。只有同时满足以下条件时才重新评估 Bundle：

1. 单个人物已经出现大量关系或情境分支；
2. 一轮生成通常只需要其中少数分支；
3. Runtime 能依据参与者和场景确定性选择分支；
4. Bundle 可以整体版本化、整体计算 hash，并在 Generation Trace 中记录实际加载内容；
5. 评测证明选择性加载改善成本或人物一致性。

## 2. 信息所有权

| 内容 | 权威位置 |
| --- | --- |
| 核心气质、长期驱动力、判断与行动倾向 | Character Skill |
| 外貌、外显气质、稳定声音、可观察行为倾向 | Character Presentation |
| 当前情绪、信任、误解、承诺和主观关系 | Agent Memory |
| 当前故事目标、节奏和收束条件 | Scenario Policy |
| 客观世界事实和临时外部状态 | World Ledger / Entity Revision |
| 输入裁剪、权限、输出 Schema 和校验规则 | Agent Contract / Validator |
| 立绘、Live2D、动作和声音文件路径 | Asset Manifest |

同一信息只保留一个权威来源。Character Skill 可以写“重视自己的形象”，因为这是影响行动的心理倾向；不能重复描述发色、服装或音色，因为这些属于 Character Presentation。

## 3. 推荐层级

```markdown
---
skill_id: mygo.character.example
version: 1.0.0
agent_kind: character
---

# 人物名

## 核心气质

角色跨场景保持的基本倾向，以及这些倾向在压力下如何表现。

## 核心驱动力

- 长期希望得到什么
- 害怕失去什么
- 愿意持续维护什么

## 核心矛盾

两种同时真实、但会将角色拉向不同方向的需要或价值。

## 注意与判断倾向

角色优先注意什么、怎样解释模糊线索、什么证据能改变其判断。

## 行动倾向

- 当出现某种情境线索时，通常倾向采取什么策略。
- 哪些反向压力会让角色改变策略。
- 多个行动都合理时，角色偏向怎样取舍。

## 关系与连续性

稳定的关系需要、敏感点和关系更新速度。具体信任、冲突和承诺由 Agent Memory 提供。

## 表达风格

普通、亲近和冲突情境中的措辞、节奏、直接程度及潜台词表达方式。
```

并非每个角色都必须机械保留七个标题。`核心矛盾` 可以在内容很短时并入 `核心气质`；`注意与判断倾向` 与 `行动倾向` 可以合并为当前文件使用的 `判断与行动倾向`。完成标准是每项必要信息都有唯一位置，且 Skill 足以在不同情境中产生一致但不机械重复的选择。

## 4. 编写规则

### 4.1 写概率倾向，不写固定动作

```text
弱：她很外向，所以总会主动发言。

强：陌生局面出现停顿时，她通常先接近、询问或提出具体建议；
若对方明确要求空间，则暂时等待并寻找后续可回应的机会。
```

人格用于改变合理行动的概率，不直接决定唯一行动。

### 4.2 写情境—解释—行动—例外

每条重要策略尽量回答四个问题：

```text
情境线索：发生了什么？
角色解释：她认为这意味着什么？
行动倾向：她通常怎样应对？
改变条件：什么证据或压力会使她换一种做法？
```

### 4.3 保留矛盾

可信人物通常同时拥有相互竞争的需要。例如“希望得到明确接纳”和“害怕显得过于需要他人”可以共同存在。核心矛盾使同一人格在不同压力下产生不同但仍可理解的行动。

### 4.4 表达风格按互动模式描述

语言风格不能退化成固定口癖。优先描述角色在普通、亲近、冲突等模式下如何改变直接程度、句长、节奏和信息披露。

### 4.5 连续性来自 Memory

Skill 只规定关系怎样形成和变化，例如“信任需要持续一致的行动建立”。“现在是否信任某人”必须从该角色自己的 Agent Memory 得出。

## 5. 理论来源

### 5.1 特质是概率分布

大五人格适合描述长期倾向，但不能直接生成单次行动。Fleeson 将人格特质理解为一个人在不同时间呈现的状态分布，支持“人格调整行动概率，而非机械决定行为”的写法。

- Fleeson, 2001, [Traits as Density Distributions of States](https://doi.org/10.1037/0022-3514.80.6.1011)

### 5.2 CAPS 与情境—行为签名

Mischel 与 Shoda 的认知—情感人格系统（CAPS）把人格描述为相对稳定的“如果处于情境 X，就倾向于行为 Y”模式。它是 `注意与判断倾向`、`行动倾向` 和 `核心矛盾` 的主要理论来源。

- Mischel & Shoda, 1995, [A Cognitive-Affective System Theory of Personality](https://doi.org/10.1037/0033-295X.102.2.246)

### 5.3 动机与 BDI 分层

自我决定理论帮助区分自主、胜任和关系等持续需要；BDI 将计算角色拆成 Belief、Desire 和 Intention：

- Character Skill 提供相对稳定的 Desire、价值和决策偏好；
- Agent Memory 与 PerceptionFrame 提供当前 Belief；
- 当前 Generation Wave 形成 Intention 和 Action Proposal。

- Deci & Ryan, 2000, [The “What” and “Why” of Goal Pursuits](https://doi.org/10.1207/S15327965PLI1104_01)

### 5.4 会话分析与社会行动

行动必须回应当前互动位置和接收者，而不是只表达人物标签。轮次组织、邻接对、回应义务和 recipient design 支持 `关系与连续性` 与 `表达风格` 中的互动规则。

- Sacks, Schegloff & Jefferson, 1974, [A Simplest Systematics for the Organization of Turn-Taking](https://doi.org/10.2307/412243)
- Stivers & Rossano, 2010, [Mobilizing Response](https://doi.org/10.1080/08351810903471258)

## 6. 荣格八维的使用边界

荣格原始理论用内倾/外倾两种态度与思维、情感、感觉、直觉四种功能组合出八种功能—态度。现代常见的 `Te/Ti/Fe/Fi/Se/Si/Ne/Ni` 分数和完整功能栈是后续类型学扩展，不是八个得到充分验证的连续人格维度。

Character Skill 不保存以下内容：

```text
Ni: 85
Fe: 70
Ti: 45
Se: 20
```

原因包括：

- 类别或功能栈难以稳定预测具体社会行动；
- 分数缺少统一的心理测量意义；
- 类型标签容易使角色退化成刻板印象；
- MBTI 部分维度能映射到大五人格，不等于认知功能栈得到验证。

- McCrae & Costa, 1989, [Reinterpreting the MBTI From the Five-Factor Model](https://doi.org/10.1111/j.1467-6494.1989.tb00759.x)
- Stein & Swan, 2019, [Evaluating the Validity of MBTI Theory](https://doi.org/10.1111/spc3.12441)

荣格功能可以作为作者提问工具：

```text
Se / Si / Ne / Ni → 角色容易注意哪类信息？
Te / Ti / Fe / Fi → 角色倾向依据什么评价和协调行动？
```

得到答案后，将标签翻译成可观察的情境规则，再写入 `注意与判断倾向` 或 `行动倾向`。如果“任务 Skill”指 Scenario Policy 或 Acceptance Skill，则不放入荣格八维，因为任务配置不拥有角色人格。

## 7. 是否增加外貌特质维度

结论：人物模型需要外貌维度，但 Character Skill 不增加 `外貌特质` 区块。外貌不是角色的私有决策策略，而是周围角色可以感知的世界信息；当前模型已由 Character Presentation 承载：

```yaml
presentation:
  appearance: 注重搭配、整体形象明快的少女。
  demeanor: 第一眼显得亲切、主动，习惯迅速缩短社交距离。
  voice: 声音明亮，语速和语调富于变化。
  observable_traits:
    - 常主动开启话题并观察对方是否回应
```

四类信息的归属如下：

| 信息 | 归属 |
| --- | --- |
| 发色、体型、日常穿着等稳定外观 | `CharacterPresentation.appearance` |
| 第一眼可见的姿态和外显气质 | `CharacterPresentation.demeanor` |
| 稳定音色和说话的听觉特征 | `CharacterPresentation.voice` |
| 可反复观察的外部行为倾向 | `CharacterPresentation.observable_traits` |
| 当前衣着、受伤、颤抖或音量变化 | Character 的版本化状态或 World Event |
| “她看起来亲切、虚伪或危险” | 观察者自己的 Belief |
| “她重视外表并希望获得认可” | Character Skill |
| 立绘、Live2D、动作与音频路径 | Asset Manifest |

PerceptionProjector 只把 Character Presentation 投影给同一 Interaction Scope 内能感知该角色的参与者。Presentation 提供形成印象的公开线索，不替观察者生成统一的第一印象。

## 8. 评审清单

正式 Character Skill 合格时应满足：

- 每项内容都跨场景稳定，不依赖某个 World Version；
- 至少包含长期驱动力和情境化行动倾向；
- 人格表现为概率偏好，存在改变策略的条件；
- 关系状态从 Memory 读取，Skill 只规定关系变化方式；
- 表达风格会随互动模式变化，不依赖固定口癖；
- 外貌与稳定声音位于 Character Presentation；
- 当前任务、固定台词、实体 ID、输出字段和结束脚本不进入正式 Skill；
- 修改正式 Skill 时提升版本，并由 Runtime 记录实际版本和内容 hash。
