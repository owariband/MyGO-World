# MyGO 五人正式 Character Skill 与大文本 Scenario Seed

Status: ready-for-agent

## Problem Statement

当前可运行的中文 Demo 只让千早爱音与长崎素世参与一个很短的验收场景，正式
Character Skill 也只覆盖这两名角色。用户因此无法观察高松灯、要乐奈、椎名立希加入
后，多 Character Agent 在同一 Event Session 中并行提案、形成较多 World Event 并被
Broadcast 编排为较长 WebGAL 文本时的实际效果。

现有 `-live` Acceptance Skill 为稳定覆盖两人验收路径而包含具体轮次、目标实体与收束
条件，不能作为长期 World 的人物设定。新增角色需要遵守已经确立的分层：Character
Skill 只保存跨场景稳定的人格、驱动力、关系倾向、判断方式和表达风格；当前排练议题、
对象状态和私有动机由 Scenario Seed 与 Agent Memory 初始化；输入输出 Schema、ID、
权限、来源和校验继续由后端 Agent Contract 负责。

动画官网只提供人物短简介和分集梗概，并不存在可直接复制成长 Prompt 的官方人物文本。
因此人物设定需要以一手短资料为事实边界，明确区分官方事实与低风险的创作性归纳，避免
把二手梗、口癖或夸张关系固化为角色规则。

## Solution

新增高松灯、要乐奈、椎名立希三份可长期复用的简体中文正式 Character Skill，格式、
层级和篇幅对齐现有千早爱音与长崎素世正式 Skill。三份 Skill 以动画/企划官方资料为
事实依据，只把能够跨场景成立的特征转化为创作倾向，不包含具体 Scenario、World
Version、实体 ID、固定行动、发言顺序、轮数或结构化输出说明。

新增一个独立的五人 Scenario Seed。Seed 将爱音、素世、灯、乐奈和立希作为 Character
Entity 放入 RiNG 的同一 Interaction Scope 和 Event Session，并通过客观的排练议题、
持久 Object 状态以及每名角色各自隔离的初始 Agent Memory，形成足以支持多轮自然互动
的共同处境。Seed 绑定五份正式 Character Skill 以及现有正式 Director/Broadcast
Skill，不使用 Acceptance Skill。

新增与该 Seed 配套、独立于两人 Live 验收的五人 Asset Manifest。Manifest 复用当前
外部 WebGAL 整合包中已经存在的休息室背景、BGM，以及五名角色各自的 `live_default`
Live2D 模型，从实际模型元数据中人工挑选少量可验证的常用 motion 和 expression。外部
素材仍不复制进仓库，Character Skill 仍不包含任何素材路径。

通过离线、确定性的高层行为测试证明新内容可以被现有 Runtime 加载、初始化并推进至少
一个五人 Generation Wave；真实 DeepSeek 调用和浏览器展示保留为用户显式授权后的
人工试跑，不作为默认测试或本规格执行的完成条件。

## User Stories

1. As a Character author, I want a formal Chinese Skill for 高松灯, so that she can participate in reusable long-running Worlds without relying on a test prompt.
2. As a Character author, I want a formal Chinese Skill for 要乐奈, so that her decisions can remain recognizable without reducing her to a catchphrase.
3. As a Character author, I want a formal Chinese Skill for 椎名立希, so that her high standards and directness can drive interaction without turning into indiscriminate hostility.
4. As a Character author, I want every new Skill to use stable ID, semantic version and `character` Agent kind metadata, so that Runtime binding remains auditable.
5. As a Character author, I want the three new Skills to follow the same five-section structure as the current formal Anon and Soyo Skills, so that character configuration stays consistent.
6. As a Character author, I want the Skill prose to be concise natural Chinese, so that model context is spent on actionable characterization rather than encyclopedic biography.
7. As a Character author, I want official facts distinguished from creative inference, so that unsupported fan interpretation is not presented as canon.
8. As a Character author, I want Tomori's sensitivity, awkward sincerity, persistence and poetic way of conveying difficult feelings represented as tendencies, so that she can still make choices instead of always remaining silent.
9. As a Character author, I want Raana's freedom, intuition, musical interest and concise expression represented as tendencies, so that she is not mechanically made to say “喵” or judge everything with one repeated word.
10. As a Character author, I want Taki's seriousness, high standards, impatience with avoidance and action-oriented concern represented as tendencies, so that she is not forced to attack teammates in every exchange.
11. As a Character author, I want each new Skill to describe relationships and continuity across the five-member band conservatively, so that later Agent Memory can refine trust and conflict from actual World history.
12. As a Runtime maintainer, I want Character Skills free of World Version, session IDs, entity IDs, output field copying rules and fixed-round scripts, so that the Agent Contract remains the sole technical authority.
13. As a Runtime maintainer, I want scenario-specific goals and current tensions outside Character Skills, so that the same character versions can be reused in other Worlds.
14. As a World operator, I want one versioned five-member Scenario Seed, so that I can initialize a fresh World for larger-volume experiments.
15. As a World operator, I want all five MyGO members represented by stable Character Entity IDs, so that their proposals, Memory and rendered models remain traceable.
16. As a World operator, I want all five characters to begin in one RiNG Interaction Scope and Event Session, so that one Wave exercises true five-character lockstep behavior.
17. As a World operator, I want the initial situation to contain a concrete rehearsal or arrangement decision with more than one reasonable answer, so that conversation can develop from character differences rather than a forced script.
18. As a World operator, I want persistent rehearsal objects to expose the shared issue as objective state, so that characters can legally reference and interact with them.
19. As a Character Agent, I want an initial personal Observation plus a private Belief or non-critical Commitment relevant to the shared issue, so that my first action has context without revealing another character's thoughts.
20. As a Character Agent, I want every private Memory tagged to relevant participants and the RiNG location, so that existing retrieval selects useful context without bypassing ownership isolation.
21. As a World operator, I want initial Commitments, when used, to avoid hard-blocking Session resolution merely to inflate output, so that longer interaction can still end naturally.
22. As a World operator, I want the five-member Seed to bind the formal Anon, Soyo, Tomori, Raana and Taki Skills plus the formal Director and Broadcast Skills, so that the run evaluates reusable production configuration rather than Acceptance Skills.
23. As a World operator, I want the existing two-person Live acceptance Seed and its fixed assertions preserved, so that the new experiment does not weaken ticket 09 regression coverage.
24. As an asset curator, I want a separate five-member Asset Manifest, so that all five rendered characters are explicitly whitelisted without broadening the two-person acceptance manifest implicitly.
25. As an asset curator, I want the Manifest to use only files and Live2D capabilities present in the current external WebGAL package, so that Broadcast cannot invent models, motions or expressions.
26. As an asset curator, I want a small curated capability set per character, so that generated performances are valid and predictable without exposing the entire raw model catalog.
27. As a repository maintainer, I want default tests to remain independent of the external WebGAL installation, credentials and network, so that CI stays deterministic.
28. As a test author, I want the new Seed loaded through the production Scenario Seed and Runtime Skill loaders, so that malformed references or mismatched bindings fail at the same boundary as real initialization.
29. As a test author, I want a fresh World initialized and reopened from the new Seed, so that the five Character Entities, one Event Session, private Memory and exact Skill bindings are observable in persisted state.
30. As a test author, I want one offline five-character Generation Wave to pass through the production projector, validators and committer, so that participant count and concurrency assumptions are tested without paid calls.
31. As a test author, I want all five Character requests in that Wave to share one starting World Version while containing only their own private Memory, so that larger input volume does not regress lockstep or isolation.
32. As a test author, I want five valid Action Proposals and their committed provenance to be observable after the offline Wave, so that the scenario is demonstrably runnable rather than merely schema-valid.
33. As a viewer, I want an explicitly documented manual command sequence for a fresh Provider World and Render, so that I can later generate and inspect a larger Chinese WebGAL scene intentionally.
34. As a viewer, I want generated narrative language to remain simplified Chinese through the existing formal Director and Broadcast Skills, so that adding characters does not return the Demo to English output.
35. As a cost-conscious operator, I want the documented Provider run to use existing bounded waves, concurrency and request budgets, so that a five-character experiment cannot become an unbounded paid loop.
36. As an auditor, I want exact Skill versions, Seed identity and asset IDs retained in existing receipts and traces, so that a large run can still be diagnosed.

## Implementation Decisions

- Create three new immutable formal Character Skill identities: `mygo.character.tomori`,
  `mygo.character.rana` and `mygo.character.taki`, each beginning at version `1.0.0` with
  `agent_kind: character`.
- Use the same headings and comparable length as the current formal Anon/Soyo standard: 核心气质、
  核心驱动力、判断与行动倾向、关系与连续性、表达风格. Do not create `-live` variants for
  this feature.
- Base the character content on the accompanying official-source research note. Official role and
  plot facts may inform the prose; personality and speech guidance derived from those facts must stay
  conservative and must not claim to be verbatim canon dialogue.
- Keep technical contracts out of all five formal Character Skills. In particular, no Skill may
  mention a particular World Version, `world_version`, `session_id`, `actor_id`, `source_kind`, a
  fixed entity ID, required proposal ID format, exact round count or forced Session closure.
- Add one new versioned example Scenario Seed rather than changing the semantics of the existing
  minimal Seed or two-person Live acceptance Seed.
- Use stable Character Entity IDs `character-anon`, `character-soyo`, `character-tomori`,
  `character-rana` and `character-taki`. Use the player-facing Chinese name “椎名立希”; do not retain
  the earlier typo “椎名利希”.
- Place all five Characters in one RiNG location/scope and one Event Session. Seed an objective
  rehearsal-arrangement problem and at least two persistent shared Objects with incomplete states,
  giving the characters several legitimate dimensions to discuss without prescribing their actions.
- Initialize at least two private Memory records per Character: one Observation and one Belief or
  Commitment. Memories must be character-owned, relevant to the shared entities or location, and
  must not expose another Character's private conclusion as objective fact.
- If a Commitment is initialized for narrative pressure, keep it below the Runtime's key-commitment
  resolution threshold unless the Seed and deterministic test also establish a valid way to complete
  it. Output volume must not be increased by making natural resolution impossible.
- Bind the five formal Character Skills and the existing formal Chinese Director/Broadcast Skills.
  The new Seed must not bind any Acceptance Skill.
- Add a dedicated five-member Asset Manifest. Reuse the existing RiNG lounge background and BGM,
  preserve existing Anon/Soyo asset identities, and add Tomori/Raana/Taki `live_default` model
  entries using only capabilities verified from their installed model metadata.
- Curate a minimal neutral set for each new model, including an idle motion, a default expression and
  a small number of character-appropriate common alternatives. Do not expose unrelated characters'
  capabilities even if the raw model metadata happens to contain a shared catalog.
- Keep all Manifest paths relative to their WebGAL asset-category roots. Do not copy external MyGO
  files into this repository and do not place paths in Character Skills.
- Document the existing generic `init → advance --gateway provider → render --gateway provider`
  command sequence for this Seed and Manifest. Use a fresh World ID, bounded `max_waves`, the existing
  request budget and an explicit ignored environment file; do not generalize or replace the specialized
  two-person `live-demo` acceptance command.
- Treat “large text” as structural load rather than a deterministic prose-length promise: one Wave has
  five independent Character calls plus one Director call, and a bounded multi-Wave run can accumulate
  many committed Events. Do not test exact wording, token count, character count or guaranteed number
  of Waves from a stochastic Provider.
- No Runtime schema, database migration, validation rule, concurrency default, global request budget,
  Render beat limit or Scenario Policy model is required for this content feature.

## Testing Decisions

- Use one primary high-level seam: load the repository Seed and Skill catalog, initialize a fresh World,
  reopen it, then advance one deterministic five-character Wave through the existing production Runtime
  with `FixtureGateway` replacing only model calls.
- The initialization assertions must observe five Character Entities, their exact five formal Skill
  bindings, a single five-member Event Session, the shared Objects, and at least two private Memory
  records per Character.
- The Wave assertions must observe five Character calls sharing the same starting World Version, each
  request containing its owner's tagged Memory and none of the other four private markers.
- The deterministic responses must yield one valid non-`no_op` Action Proposal per Character. The
  committed Ledger/Generation Trace evidence must preserve all five proposal sources through the same
  validated World Segment boundary.
- Extend the existing Runtime Skill content test to load the new versions and assert the common Chinese
  section structure. Apply the existing forbidden-token checks to the three new formal Character Skills
  rather than snapshotting their complete prose.
- Load the five-member Asset Manifest through the production manifest parser in default tests and assert
  unique asset IDs plus exact Character-to-model mappings. Default tests must use repository-local fake
  metadata or schema-only validation and must not read the external MyGO installation.
- Add an explicit, non-default asset preflight command that runs the existing Manifest file/Live2D
  capability validator against `/Users/yyu03/project/dev/MyGO_v3.1.1`; record success when that directory
  is available, but do not make its absence fail ordinary CI.
- Run the smallest focused tests while developing, then run the repository gates `uv run pytest`,
  `uv run ruff check .`, and `uv run ruff format --check .` because this change touches versioned
  production content consumed by initialization and generation tests.
- A real Provider run is manual evidence only. It must use a new World ID and explicit credentials,
  and requires separate user authorization because it incurs network access and cost.

## Out of Scope

- Implementing a persisted Scenario Policy type or moving scenario policy into Character Skills.
- Adding or changing Pydantic Agent contracts, Action Proposal types, Validators, database schema,
  migrations, scheduling, concurrency, request budgets or retry behavior.
- Creating new `-live` Acceptance Skills or altering the deterministic behavior of the existing Anon ×
  Soyo acceptance fixture.
- Rewriting the existing Anon, Soyo, Director or Broadcast formal Skill beyond preserving their current
  layered Chinese versions.
- Copying, renaming, generating or editing external WebGAL backgrounds, BGM, Live2D models, textures,
  motions or expressions.
- Automatically starting WebGAL, changing its entry scene, running DeepSeek, spending Provider quota or
  asserting subjective narrative quality during implementation.
- Reproducing anime dialogue, episode scripts or copyrighted long-form text; the new Skill prose is an
  original, concise behavioral abstraction.
- Guaranteeing a fixed output length, a fixed number of Waves or a particular generated plot from a
  stochastic model.
- Adding player input, tools, an internal Agent loop, multi-Provider routing or cross-Session parallelism.

## Further Notes

- The evidence companion is `research.md` in this tracker feature directory. It uses official animation
  pages as the source owner and archived indexes only because the original pages returned 404 on
  2026-09-01. It explicitly separates source facts from creative inference.
- The external WebGAL package was inspected during planning and contains `live_default` models for all
  five Characters, including Tomori-, Raana- and Taki-prefixed motions and expressions.
- The current branch already has unrelated or earlier uncommitted localization/layering work. Execution
  must preserve it, treat the current formal Anon/Soyo and Director/Broadcast format as the intended
  standard, and avoid absorbing unrelated generated WebGAL scenes into this feature.
- This is a bounded content-and-integration-test change and fits one fresh implementation context.
