# MyGO-World 持久世界到 WebGAL 演出的 MVP

Status: ready-for-agent

## Problem Statement

项目已经拥有 WebGAL 打包版本、MyGO 美术资源和一套 Node.js 创作/编译原型，但还没有一条可持续推进、可验证、可重放的生成式世界链路。用户需要让多个 Character Agent 在同一个持久 World 中产生结构化行动，由 Director 将这些行动结算为可信世界事实，再由独立 Broadcast 在事实稳定后增量剪辑并生成可发布的 WebGAL 演出脚本。

该链路必须解决模型输出不可信、角色并发观察不一致、进程退出后状态丢失、Session 分裂、角色私有 Memory、事实与演出相互污染、素材幻觉，以及模型随机性导致系统无法自动测试等问题。MVP 的目的不是完成完整游戏，而是以一个确定性 Fixture 纵切和一次真实模型主演示，证明从 Scenario Seed 到持久数据、再到 WebGAL 脚本的端到端架构成立。

## Solution

构建一个 Python 3.13 本地 CLI Runtime。每个 World 使用独立 SQLite 数据库，并通过 append-only World Ledger、版本化 Entity Revision、World Version 和 Snapshot 跨多次进程运行持续存在。`init` 物化 Scenario Seed，`advance` 以有界 Generation Batch 推进 Runnable Session Queue 队首的一条 Event Session lineage，`render` 在固定 World Version 上增量编排尚未处理的 World Event，`demo` 串联新 World 的完整验收路径，`skill-bind` 在 Batch 之间切换不可变 Runtime Skill 版本。

每个 Generation Wave 中，所有 Character 从同一 Snapshot、World Time 和各自隔离的 Agent Memory 并行生成一个 Action Proposal。Proposal Validator 先验证每个角色的权限与行动边界；Director 再补全客观结果、语义时间、因果关系和 Session 生命周期；Segment Validator 将 Segment Draft 转换为唯一可提交的 Validated Commit Plan。World Committer 在一个事务内写入 Segment、Revision、Session/Queue 变化、World Event、Observation、被接受的 Memory 变化、Snapshot 和新 World Version。

世界推进与演出生成严格分离。显式 `render` 调用由 Broadcast 对新增 Event 作出纳入或省略决定，并产生结构化 Broadcast Plan；确定性的 Render Planner 校验来源与素材，Render Compiler 生成 WebGAL DSL，Render Gateway 使用不可变内容 hash 和可恢复发布协议写入 WebGAL 项目。WebGAL 只负责展示，不能拥有或推进 World。

首个里程碑完全使用 Fixture Gateway、固定时钟、确定性 ID 和仓库内最小素材运行真实生产链路。该纵切稳定后，再使用一个 OpenAI-compatible Provider、Anon 与 Soyo 以及真实 MyGO 素材完成显式 Live 验收。

玩家自由文本介入仍属于第二阶段。MVP 只冻结兼容 seam：未来的 Player Event Request 是绑定输入 World Version 的自然语言请求，Director 只能将其解释为 External Event Candidate；Segment Validator 与 World Committer 仍是成为事实的唯一通路。第一阶段通过 Director 产生的无主体环境/桥接事件证明该链路不与 Character Action Proposal 强耦合，但不实现玩家入口、请求存储或解析。

## User Stories

1. As a World operator, I want to initialize a named World from a versioned Scenario Seed, so that I have a reproducible starting state.
2. As a World operator, I want initialization to fail safely when the World already exists, so that prior history is never overwritten or merged accidentally.
3. As a World operator, I want the World to survive CLI process exits, so that later invocations continue the same history.
4. As a World operator, I want to advance a World through an explicit bounded command, so that generation cannot run indefinitely.
5. As a World operator, I want each advance invocation to have a Generation Batch receipt, so that I can identify its starting version, ending version and outcome.
6. As a World operator, I want a successful no-work result when no runnable Event Session exists, so that automation can distinguish idle state from failure.
7. As a World operator, I want interrupted work to resume from the last complete World Version, so that a partial Wave never corrupts the World.
8. As an automation author, I want stable JSON receipts as well as concise human output, so that CLI commands are scriptable.
9. As a Character author, I want each Character to have a versioned natural-language Character Skill, so that personality, motivation, relationships, appearance and speech style remain explicit.
10. As a Character author, I want runtime experiences stored in Agent Memory rather than mutating the Character Skill, so that authored identity and learned state remain distinct.
11. As a World operator, I want to bind a new immutable Character Skill version between Batches, so that manual personality changes take effect predictably from the next Batch.
12. As a Character Agent, I want to receive only my permitted PerceptionFrame and private Memory, so that I cannot use hidden world state or another character's memory.
13. As a Character Agent, I want all participants in a Wave to decide from the same World Version and World Time, so that model call order cannot change what they know.
14. As a Character Agent, I want to propose exactly one atomic action per Wave, so that concurrent proposals remain resolvable.
15. As a Character Agent, I want to speak to zero or more primary addressees while remaining audible within the Interaction Scope, so that ordinary group conversation is representable without implementing whispers.
16. As a Character Agent, I want movement choices limited to destinations exposed by my PerceptionFrame, so that I cannot teleport or invent routes.
17. As a Character Agent, I want interactions with persistent targets limited to visible entities, so that natural-language intent cannot bypass perception permissions.
18. As a Character Agent, I want to mention ordinary one-use Incidental Props in natural language, so that scenes can remain expressive without turning every object into a persistent Entity.
19. As a Character Agent, I want `wait` and `no_op` to have different semantics, so that intentional passage of time is distinct from taking no action.
20. As a Character Agent, I want to propose private belief and commitment changes alongside my action, so that accepted experiences can affect later decisions.
21. As a Director, I want to receive all valid proposals after the Character synchronization barrier, so that I can resolve conflicts without privileging request order.
22. As a Director, I want to assign semantic start/end times and causal relationships within a Wave, so that actions may be ordered or overlap while remaining deterministic after validation.
23. As a Director, I want to describe environment reactions and object outcomes, so that Character intent can become complete objective consequences.
24. As a Character author, I want the Director prohibited from inventing a character's important choice, dialogue or motivation, so that character agency remains with the Character Agent.
25. As a Runtime maintainer, I want invalid Character proposals rejected before Director invocation, so that inaccessible targets and ownership violations do not enter later stages.
26. As a Runtime maintainer, I want complete Segment Drafts checked against the original proposals and Snapshot, so that time, causality, state transitions and Session changes remain valid.
27. As a Runtime maintainer, I want validators to return stable diagnostics without silently editing facts, so that a single targeted model repair is observable and testable.
28. As a Runtime maintainer, I want a second invalid response to fail the Batch without committing the Wave, so that malformed model output never becomes world truth.
29. As a World reader, I want one new World Version per successful atomic World Segment rather than per individual World Event, so that every readable version is complete and internally consistent.
30. As a World reader, I want multiple events and revisions from one Wave to share one World Version, so that I can reason about their common decision boundary.
31. As a World reader, I want Snapshot to be a checksum-protected rebuildable projection of the Ledger, so that fast reads do not create a second source of truth.
32. As an auditor, I want World Segment, Entity Revision and World Event history to be append-only, so that committed facts cannot be silently rewritten.
33. As an auditor, I want every model request, raw response, structured result, retry, repair, Skill version and model configuration recorded without credentials, so that generation can be diagnosed.
34. As a Character Agent, I want every perceptible committed Event projected into my Observation memory, so that I can respond to later-in-Wave events only in the following Wave.
35. As a World operator, I want Session members to remain fixed during an Event Session, so that its interaction boundary is stable.
36. As a World operator, I want movement across Interaction Scopes to atomically close the old Session and enqueue all successor Sessions, so that character position and scheduling never disagree.
37. As a World operator, I want successor Sessions processed by a deterministic FIFO queue, so that multiple invocations continue reproducibly without a model-selected focus character.
38. As a World operator, I want one Generation Batch to follow only one successor lineage, so that the MVP avoids hidden multi-Session concurrency.
39. As a World operator, I want a naturally resolved Session distinguished from a `limit_reached` Session, so that creative success is not confused with a safety cutoff.
40. As a World operator, I want a pure all-`no_op` Wave to leave no new World Version, so that versions represent meaningful authoritative changes rather than attempts.
41. As a World operator, I want Session-only closure represented by a control Segment, so that queue and lifecycle changes remain atomic even without a World Event.
42. As an editor, I want rendering to occur only when explicitly requested, so that generated facts can be reviewed or accumulated before presentation.
43. As an editor, I want a render invocation to process every Event that lacks a Broadcast Disposition regardless of its source Batch, so that presentation follows story history rather than generation command boundaries.
44. As an editor, I want Broadcast to include, omit with a reason, reorder, interleave or flash back to committed material, so that presentation order can differ from World Order without changing facts.
45. As an editor, I want every newly considered Event assigned exactly one immutable disposition, so that incremental rendering has an unambiguous frontier.
46. As a viewer, I want spoken dialogue to preserve the exact committed utterance, so that the rendered character never says something that did not happen in the World.
47. As an editor, I want narration allowed to summarize sourced facts, so that long histories can be compressed without inventing events.
48. As an asset curator, I want Broadcast restricted to stable Asset Manifest IDs, so that models cannot invent paths, motions or expressions.
49. As a WebGAL author, I want generated Render segments to be self-contained, so that each one establishes its own background, visible characters and necessary BGM.
50. As a WebGAL author, I want a deterministic Python compiler for the supported Beat set, so that production rendering does not require the existing Node runtime.
51. As a WebGAL author, I want Live2D model, motion and expression references checked against the actual asset metadata, so that generated scripts do not reference impossible performances.
52. As a World operator, I want generated scripts published under immutable world/render/content-hash names without overwriting the entry scene, so that retries and history are safe.
53. As a World operator, I want a render interrupted between filesystem publication and database finalization to reuse an identical published hash on retry, so that the cross-medium crash window is recoverable.
54. As a test author, I want a Fixture Gateway to replace only model calls while keeping the production Runtime, validators, database and compiler, so that tests exercise the real system.
55. As a test author, I want fixed responses matched by semantic call identity and checked against an input hash, so that prompt or context regressions fail clearly.
56. As a test author, I want repository-local minimal assets for default tests, so that CI does not depend on a developer's MyGO installation.
57. As a test author, I want two clean Fixture runs to produce identical canonical domain exports, checksums, plans, scripts and hashes, so that nondeterminism is detected.
58. As a test author, I want a deterministic Session-split scenario, so that closure, successor queueing, perception isolation and next-Batch continuation are verified.
59. As a developer, I want real Provider tests excluded from normal CI, so that routine tests remain fast, deterministic and credential-free.
60. As a product owner, I want an explicit Live demo in which Anon and Soyo both act, the Session resolves naturally within six Waves, and a valid real-asset Render is produced, so that the architecture is proven beyond canned responses.
61. As a Runtime maintainer, I want Director-authored unowned environmental or bridge events to use the same validated commit path without requiring a Character actor, so that a later Player Event Request can be added without bypassing World authority or redesigning the commit pipeline.

## Implementation Decisions

### Runtime and dependency boundaries

- Implement the new Runtime in Python 3.13, managed by `uv` with a project manifest and lock file.
- Use Pydantic v2 for structured contracts, SQLAlchemy 2.x for persistence, Alembic for schema migration, pytest for automated tests, Pydantic Evals for later model-quality evaluation and OpenTelemetry for generation/runtime traces.
- Do not use PydanticAI in the MVP. Keep domain request and response models independent of any Provider SDK.
- Define one narrow `ModelGateway` used by Character, Director and Broadcast runners. Provide `FixtureGateway` and one OpenAI-compatible real Provider adapter.
- Configure the real adapter through process environment values for base URL, API key, model ID and model parameters, with optional loading of an ignored local environment file. No credential may enter source control, Generation Trace or CLI output.
- Use native JSON Schema structured output where supported; otherwise parse JSON text and validate it with the same Pydantic model.
- Models receive projected context in a single request and have no tool calling or internal Agent loop in the MVP.

### CLI and World lifecycle

- Provide `init`, `advance`, `render`, `demo` and `skill-bind` commands under one CLI application. All commands support concise human output and stable JSON receipts.
- `init` validates and atomically materializes a Scenario Seed into a new World. A duplicate `world_id` fails without overwrite or merge.
- `demo` is a convenience command for a new World that runs initialization, advancement and rendering. It is not the test runner and does not resume an existing World.
- `advance` is a bounded foreground Generation Batch. There is no daemon-style `start` or `stop` command in the MVP.
- SIGINT/SIGTERM cancels in-flight requests, rolls back the current uncommitted Wave, records cancellation when possible and exits nonzero. On the next invocation, stale `running` Batch records become `interrupted`.
- `advance` with no runnable Session and `render` with no new Event return successful `no_work` receipts and make no model calls or empty facts/renders.
- `init`, `advance` and `skill-bind` use a per-World mutation file lock. `render` uses a separate per-World Render lock, so Render runs are serialized while a Render may read a fixed version concurrently with a later `advance`.
- Database schemas are created at the latest revision during initialization. Other commands reject an outdated schema; the MVP exposes Alembic operations directly and does not add a database-upgrade wrapper command.

### Persistent domain model

- Each `world_id` owns one durable SQLite database in a project-local ignored runtime area. Ledger, Snapshot, Session queue, Agent Memory, Generation Trace, Batch, Skill binding and Render records use distinct table groups within that database.
- Scenario Seed is version-controlled initialization input, not a runtime entity store. After initialization, existing Worlds never reread Seed content as authoritative state; they retain only stable seed identity, declared version and content hash for provenance.
- World Ledger is the sole objective source of truth and comprises append-only World Segment, Entity Revision and World Event records. Repository APIs expose append/read operations only, and database triggers reject updates and deletes to Ledger tables.
- Use relational columns for identity, foreign keys, ordering, location, Session membership and other invariants. Use schema-versioned, Pydantic-validated JSON for extensible narrative payloads.
- Use UUID text for generated identities and explicit monotonically increasing integers for World Version, Segment order, Event order, queue order and Entity Revision order. Never infer domain order from UUID or SQLite row identifiers.
- One successful World Segment transaction advances World Version exactly once, regardless of how many Entity Revisions or World Events it contains. Failed or pure no-op Waves do not advance it.
- Initialization creates Genesis Segment and World Version 1 without fabricating a normal story Event.
- Persist one complete Snapshot JSON plus checksum per World Version. Snapshot is a rebuildable read projection and never replaces Ledger authority.
- Action Proposal and Segment Draft candidates are stored as schema-versioned JSON in Generation Trace. A committed Segment references the originating Trace; candidates do not become a second fact table.
- Candidate facts and recognized Events carry generic `source_kind` plus `source_ref` or `evidence_refs` in their schema-versioned payloads. Provenance must not require a Character Proposal; the MVP does not add a persistent Player Event Request table.

### Character, perception and Memory

- At the start of each Wave, PerceptionProjector creates each Character's PerceptionFrame from the common Snapshot, the Character's private Memory, current Session/Scope and permission rules.
- PerceptionFrame exposes visible persistent entities, reachable destination/affordance IDs and sufficient typed context for legal choices. A destination ID resolves to a known Location and Interaction Scope; no coordinates or pathfinding are introduced.
- Every Character emits exactly one discriminated Action Proposal: `utterance`, `move`, `interact`, `wait` or `no_op`, plus a short `intent_summary` rather than chain-of-thought.
- `utterance` carries zero or more primary addressee IDs. An empty set means a Session-public statement; all characters in the Interaction Scope can still perceive addressed speech.
- `move` references only an offered reachable destination. `interact` references only an offered visible persistent target when it names an existing Entity, while the interaction method may remain natural language.
- An Incidental Prop is a value object embedded in one Event payload with a locally unique key, restricted type and description. It has no Entity ID, durable state or dedicated asset and cannot represent a character, place or critical plot object.
- Future promotion of an Incidental Prop may create a new Entity that references its origin Event and local prop key, but no promotion or dynamic persistent Entity creation occurs in this MVP.
- Agent Memory is private and append-only by `agent_id` and namespace. Enabled record types are Observation, Belief and Commitment; Reflection retains a schema placeholder but is not generated automatically.
- Character output may propose its own belief and commitment changes. They are accepted only with a successful Wave and cannot modify another Agent's Memory.
- New beliefs supersede earlier records by reference, and commitment completion/cancellation is represented by a new state record rather than in-place mutation.
- Memory retrieval always includes active commitments, then filters relevant records by agent, namespace, entity/location tags, time and importance. No vector search and no injection of the entire memory history are used.
- Scenario Seed may initialize multiple atomic Memory records. Stable character background belongs in Character Skill and does not replace detailed Memory.

### Generation Batch, Wave and Event Session

- A Generation Batch has a unique run identity, records start/end versions and statuses, and advances only the FIFO queue head plus one deterministic successor lineage.
- A Generation Wave is a lockstep decision boundary, not a fixed-duration simulation tick. All Character calls use the same starting World Version and World Time and may run concurrently under a configurable global semaphore that defaults to four.
- In the MVP scheduler, Director runs only after all valid Character proposals are available. It may resolve proposal conflicts, add environmental/object consequences and unowned bridge events, and assign semantic start/end times and causal references. This scheduling order is not part of the Director interface invariant: a second-stage request intake may invoke Director between Batches. Segment Draft represents External Event Candidates without a fabricated Character actor and retains their source/evidence references.
- Director may not replace a Character's intent, invent important Character decisions or dialogue, alter motivations, commit facts or request presentation changes.
- World Time is semantic time measured as nonnegative integer milliseconds from the Scenario origin. An optional timezone-aware calendar anchor is display-only; Provider latency is stored only in Trace.
- Director may order or overlap actions within the configured Wave duration, defaulting to at most five narrative minutes. No action may cross the Wave barrier in the MVP; early-finished characters implicitly idle without generated filler actions.
- Event Session has a stable identity, fixed participants and one Interaction Scope. A Location is a persistent Entity containing stable Scope keys and explicit reachability edges; Character position is Location plus Scope.
- Sharing a Scope alone does not join a Session. A direct interaction may atomically replace affected Sessions with a merged successor carrying parent references.
- At the end of a Wave, Runtime calculates participant partitions from candidate final positions. Position changes, old-Session closure, all successor creation and deterministic FIFO enqueueing belong to the same commit transaction.
- All successors are enqueued in deterministic participant-ID order. The current Batch continues only the queue-head successor; other successors remain for later Batches.
- Director may propose `resolved` only after at least one Wave and when no direct response, in-progress action or key commitment remains unresolved. Runtime validates the transition.
- A configurable `max_waves` defaults to six. Reaching it closes the current Session as `limit_reached`, commits the Session/Queue change through a control Segment and ends the Batch successfully with a warning.
- `wait` is intentional and may advance World Time. `no_op` creates no character action or Event. A wholly no-op, still-open Wave writes only Batch/Wave bookkeeping and Trace; a valid `resolved` or `limit_reached` transition uses an Event-free control Segment and advances World Version.

### Validation and atomic commit

- Treat all model output as untrusted candidates. Structural JSON/Pydantic validation is distinct from domain-semantic validation.
- Proposal Validator is deterministic and side-effect free. It checks the action discriminant, source World Version, actor ownership, PerceptionFrame target membership, reachability, addressee constraints, Incidental Prop constraints and ownership of proposed Memory changes.
- Invalid Character output produces stable diagnostic codes and field paths. Only the failing Character call is repaired once; already-valid proposals remain fixed for the Wave.
- Segment Validator is deterministic and side-effect free. It compares Segment Draft with the common Snapshot and original proposals, checking intent preservation, entity existence, ownership, resource conflicts, allowed state transitions, time bounds/order, causal references, Session closure and partition rules. For an External Event Candidate it also checks source evidence, event kind and affected Scope, and rejects any candidate that attributes a persistent Character's important action, dialogue or motivation without that Character's Action Proposal.
- Invalid Segment Draft produces stable diagnostic codes and field paths and is returned to Director for one repair. A second failure aborts the Batch without committing the Wave.
- Validators never invent defaults that alter semantics, drop a conflicting proposal, change a target, convert an error to `no_op`, call a model or write persistence state.
- World Committer accepts exactly two authoritative plan forms: a Genesis Commit Plan built from a fully validated Scenario Seed during `init`, or a Validated Commit Plan produced by Segment Validator for a Generation Wave. The MVP does not build a generic rules engine or configurable validation DSL.
- For a Validated Commit Plan, Committer uses one SQLite transaction to stage World Segment and Entity Revision changes, apply Session/Queue transitions, run Event Recognizer, project Observation records, accept allowed Belief/Commitment changes, build Snapshot/checksum and publish the new World Version. A Genesis Commit Plan uses the same transaction owner but creates no ordinary World Event or Observation.
- Event Recognizer deterministically derives zero or more queryable World Events from the validated pending Segment and Revisions. It cannot alter facts and does not imply one Event per Proposal.
- PerceptionProjector derives every permitted Observation from pending Events using event time, Character position, Scope and field permissions. Events, Observations, accepted Memory changes and Snapshot become visible together; any failure rolls back the complete Wave.
- World Committer is the sole writer of authoritative World, Session, queue and Agent Memory state. Agents only return candidates. Batch, Trace, Render metadata and Skill binding use their own constrained repositories.
- WorldInitializer owns Seed loading, temporary database migration, fsync and atomic file publication but does not write authoritative rows itself. World Committer owns the Genesis transaction and receives injected Clock and ID Generator dependencies; operational temporary filenames are outside the deterministic domain record.

### Model execution and failure policy

- Character, Director and Broadcast use separate Runtime Skills and Agent Contracts while sharing the same global Provider/model configuration in the MVP.
- Runtime Skill is immutable versioned Markdown with metadata for stable skill ID, declared version and Agent kind. Natural-language creative configuration belongs in the body; credentials, model choice, permissions and tool settings do not.
- Agent Contract determines projected inputs, absence of tools, output schema and validation rules. Runtime Skill cannot grant permissions.
- `skill-bind` appends an audited binding record with operator, reason, previous version and new version. It does not advance World Version and becomes effective at the next Batch.
- Runtime records the effective Skill version and content hash for every generation. A changed content hash under an unchanged declared version is rejected.
- Network errors, rate limits and Provider server errors may retry at most twice. The default timeout is 120 seconds per request.
- Schema or semantic failure is not a transport retry and permits only one diagnostic repair for the Agent output that failed.
- Actual Provider requests, including retries and repairs, count toward a per-command budget. Defaults are 40 for `advance` and 6 for `render`; exhaustion fails the command and exits nonzero.
- Any Character transport failure after retries fails the entire Wave and Batch; it is never converted into a fictional `no_op`.
- Previously committed World Versions survive later Batch failure. Generation Trace retains diagnostics for failed candidates without promoting them to facts.

### Broadcast and WebGAL rendering

- `advance` never invokes Broadcast. Only explicit `render` and the rendering phase of `demo` begin a Broadcast Run.
- A Broadcast Run fixes a target World Version and selects every World Event up to that version without a Broadcast Disposition, independent of Generation Batch boundaries.
- Older Events may be supplied as context or explicitly cited for flashback/recap. They cannot independently trigger an otherwise empty Render Run.
- Broadcast outputs a structured Broadcast Plan and ordered Beats rather than WebGAL source. It cannot add or edit World facts.
- Each new Event receives exactly one immutable `included` or `omitted` disposition. An omitted Event requires a reason; an included Event must be cited by at least one Beat.
- Presentation Order may omit, compress, reorder, interleave Sessions or use flashback, but source references must prevent false facts, impossible knowledge and false causality.
- Dialogue Beats reproduce committed utterance text exactly or omit the complete utterance. Narration may summarize one or more explicitly sourced facts.
- Supported MVP Beat kinds are chapter, start/stop BGM, background, show/hide character, dialogue and narration. Show/dialogue may use validated Live2D motion, expression and entrance effects.
- Asset Manifest is a manually approved, version-controlled whitelist mapping stable domain IDs to paths relative to WebGAL asset-category roots. Broadcast receives only valid candidate IDs and never emits raw paths.
- Render Planner validates Event coverage, provenance, self-contained stage state, Beat count/duration and asset capabilities, then resolves IDs into a Render Job.
- Render Compiler is a narrow deterministic Python interface that escapes text, validates manifest entries, real files and Live2D metadata, emits WebGAL DSL and computes the content hash. It is not a general DSL parser.
- Each Render is immutable, self-contained and stored as one WebGAL scene script. A single Broadcast Run may produce multiple ordered Renders; each defaults to no more than 40 Beats or an estimated eight minutes.
- Render Gateway first receives a fully validated local canonical artifact. It writes a temporary file inside the target WebGAL filesystem and atomically renames it into a generated-scene namespace keyed by World, Render ID and content hash; it never overwrites the WebGAL entry scene or a mutable latest alias.
- After files publish successfully, a short SQLite transaction records Render metadata and Broadcast Dispositions. If the process stops between publication and database finalization, a retry verifies the existing immutable file hash, reuses an exact match and completes the database records; a mismatched file fails safely.
- A per-World Render lock prevents two Broadcast Runs from consuming the same frontier. Database uniqueness constraints provide a final duplicate-consumption guard.
- MVP generates and validates scripts but never launches the WebGAL player.

## Testing Decisions

- The highest automated test seam is the production CLI/application workflow over an isolated temporary World: initialize a Scenario Seed, run one or more Generation Batches, render new Events and inspect externally observable receipts, canonical database exports and generated scripts.
- Fixture tests replace only `ModelGateway`. They use the real prompt/context assembly, Pydantic contracts, Proposal Validator, Segment Validator, World Committer, SQLite repositories, Event Recognizer, PerceptionProjector, Render Planner, Render Compiler and Render Gateway.
- A versioned Fixture directory contains a YAML scenario/call manifest and JSON structured Agent responses. Response lookup uses Agent type, Agent ID, Batch/Wave identity and call kind, while asserting a normalized input hash.
- Tests inject deterministic UUID generation, clock, random seed and model responses. Two clean executions must yield equal canonical Ledger, Snapshot, Memory, Broadcast Plan and WebGAL outputs, including Snapshot checksums and script content hashes.
- Determinism compares canonical domain exports and generated script bytes, not raw SQLite database bytes, because internal page layout is not a domain contract.
- Default tests use a repository-local minimal Asset Manifest and fake asset tree. They must never require the developer's external MyGO installation, network access or API credentials.
- The primary Fixture vertical slice must verify initialization, at least one multi-Character Wave, validation, atomic commit, Event recognition, Observation/Memory persistence, incremental Broadcast and deterministic WebGAL compilation.
- A dedicated split Fixture must move a Character out of the shared Interaction Scope and verify old-Session closure, all deterministic successor enqueue operations, post-move perception isolation and continuation from the queue head in the next Batch.
- Validator tests should assert external accept/reject behavior and stable diagnostic codes for invisible targets, unreachable movement, stale versions, ownership violations, altered Character intent, time/causal errors, resource conflicts and illegal Session transitions. Tests should not assert private helper call order.
- The first Fixture Wave includes at least one Director-authored unowned environmental or bridge Event. Tests prove it commits and projects without a Character actor, retains generic provenance, and that a superficially actorless Event cannot smuggle in Character dialogue or an important Character action.
- Transaction tests must inject failures during Event recognition, Observation projection, Memory persistence and Snapshot construction and verify that no partial World Version becomes visible.
- Failure tests cover Provider timeout, retryable transport errors, single semantic repair, final invalid output, request-budget exhaustion, cancellation, stale running-Batch recovery and retention of earlier committed Waves.
- No-op tests distinguish pure unresolved no-op Waves from Event-free Session closure control Segments and verify World Version behavior.
- Render tests cover Event disposition completeness, provenance, exact dialogue preservation, narration sourcing, self-contained stage state, Beat/duration limits, missing assets, unsupported Beat types, invalid Live2D motion/expression and content hashing.
- Render recovery tests interrupt after immutable file publication but before database finalization, then verify that an equal hash is reused and dispositions are finalized without duplicate output. A different existing hash must fail safely.
- Existing Node authoring and dynamic-render tests provide prior art and Golden compatibility examples for WebGAL command syntax, asset validation, Live2D capability checks, content hashing, self-contained renders and immutable active output. The Python Runtime must not invoke Node in production.
- Real Provider tests are marked explicit `live`, consume configured request budgets and are excluded from normal CI. They enforce schemas, permissions, time, causality, provenance and asset validity as hard requirements; prose quality is assessed with a human checklist rather than exact text or an LLM judge.
- The MVP Live acceptance run uses Anon and Soyo, requires at least one valid action from each, reaches natural `resolved` within six Waves, invokes Director and Broadcast through the real Provider adapter, validates against real MyGO assets and publishes at least one valid Render.
- Model-quality evaluation may later compare a fixed Eval dataset for quality, latency and cost, but it does not replace deterministic acceptance tests.

## Out of Scope

- Automatically launching or controlling the WebGAL player.
- Player choices, free-text Player Event Request intake, request persistence, Director request-parsing prompts or an interactive gameplay loop. Their second-stage addition must use the reserved External Event Candidate path rather than grant Director commit authority.
- Asynchronous per-Character clocks, long-running actions that cross Wave boundaries or event-driven scheduling.
- Concurrent advancement of multiple Event Sessions or model-selected Session priority.
- Coordinates, geometric distance, collision, route planning or a general movement simulation.
- Whisper/private speech channels within one Interaction Scope.
- Vector databases, embedding retrieval or automatic Memory Reflection.
- Automatic Character Profile revision or self-updating Runtime Skills.
- Dynamic creation of persistent Entities, including promotion of Incidental Props.
- Multiple Provider implementations, per-Agent model overrides, model tool calls or a general Agent framework.
- PydanticAI integration.
- A general WebGAL DSL parser, arbitrary WebGAL commands, branching/choices, video, per-frame control, complex Live2D motion mixing or generated art.
- Node.js as a production dependency for the new Runtime.
- Distributed deployment, remote databases, multi-process World writers or PostgreSQL.
- Automatic database migration during normal commands or a dedicated database-upgrade CLI wrapper.
- Treating Scenario YAML as live persistent state after initialization.
- Treating Generation Trace, Agent Memory, Snapshot, Broadcast Plan or Render output as objective World facts.
- Full production narrative quality, automatic LLM judging or default-CI calls to paid/external models.

## Further Notes

- The project uses one bounded domain context. The canonical vocabulary is World, World Version, World Segment, World Event, Generation Batch, Generation Wave, Event Session, Interaction Scope, Runnable Session Queue, Agent Memory, Director, Broadcast, Render and Beat.
- A World Version is analogous to an atomic commit, not an individual Event sequence number. One successful Wave may create several Events while advancing the World exactly once.
- Fixture and Live paths share all domain and persistence code. Only the `ModelGateway`, deterministic infrastructure inputs and asset root differ.
- The real MyGO 3.1.1 asset tree is an external runtime input for Live acceptance and is never copied into persistent World state.
- Existing JavaScript authoring and dynamic-render facilities remain available as references and compatibility fixtures; this specification does not require their removal or migration.
- This specification is intentionally broader than one safe implementation context. Execution should be decomposed into tracer-bullet tickets covering foundation/persistence/CLI, Fixture-driven World advancement, Broadcast/WebGAL compilation and real Provider/Live acceptance.
