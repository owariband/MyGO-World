# 缩窄 Director 契约并确定性组装 Segment Draft

Status: ready-for-agent

## Problem Statement

当前 Director 的模型接口要求一次返回完整 Segment Draft。除了环境反应、交互结果、
状态变化和 Event Session 收束意图这些创意判断外，模型还必须重复填写 Snapshot 的
World Version、当前 Session、绝对 World Time、Action Proposal 的角色、动作类型、来源、
地点和逐动作 payload。Segment Validator 虽然能够拒绝不一致结果，却只能在生成之后
发现错误；Runtime 随后把诊断附回原始请求，让 Director 重新生成整份 Segment Draft。

真实 Provider trace 已证明这个宽接口会造成非局部修复：World Version 10 上，爱音提出
一个 60,000 毫秒的 `wait`，Director 首次完整保留该 Proposal Event，却错误返回 World
Version 11；修复调用改对版本后把 `proposal_events` 清空，最终以“Proposal 没有客观结果”
再次失败。修复请求只包含原始上下文和诊断，没有上一版输出，因此模型既看不到需要保留
的局部结果，也持续拥有删除角色事件、改写来源或抄错权威字段的能力。

用户需要让 Agent Contract 在结构上表达权限：Director 只返回它真正需要判断的创意
结果，Runtime 负责把 Snapshot、当前 Event Session、Action Proposal 和创意结果组装为
完整 Segment Draft。这样，角色已经通过 Proposal Validator 的意图不会因为 Director
抄写或修复其他字段而丢失，Segment Validator 仍作为提交前最后一道确定性门禁。

## Solution

引入一个纯确定性的 Segment Assembler module，并把 Director 的结算响应从完整
Segment Draft 缩窄为 Director Resolution。Director Resolution 只包含本 Wave 的相对
语义时长、可选结果摘要、创意 External Event、合法 Entity State Change 和 Event
Session 收束意图；它不包含版本、Session、绝对时间或 Proposal Event 的权威字段。

Runtime 在 Proposal Validator 接受 Action Proposal 后调用 Director，然后把共同
Snapshot、当前 Event Session、原始 Proposal、Director trace 身份与 Director Resolution
交给 Segment Assembler。Assembler 确定性生成 Proposal Event、来源关联、绝对时间、
事件键和 `move` 的角色位置变化，再把完整 Segment Draft 交给现有 Segment Validator。
只有 Validator 产出的 Validated Commit Plan 可以进入 World Committer。

修复仍最多一次，但只修复 Director 拥有的创意输出。修复请求携带上一版响应和精确诊断；
Assembler 拥有的权威字段每次都从原始输入重新构造，不交给模型修复。测试通过现有
`advance_world` 行为 seam 覆盖五种 Action，并把真实爱音 `wait` 失败重放为回归用例。

## User Stories

1. As a Character author, I want an accepted Action Proposal to remain present through Director repair, so that another model mistake cannot erase the Character's chosen action.
2. As a Character author, I want utterance text copied exactly from the Action Proposal, so that the Director cannot rewrite a Character's dialogue.
3. As a Character author, I want utterance addressees and response semantics copied exactly, so that the Director cannot change who was addressed or who owes a response.
4. As a Character author, I want interaction targets and stated intent copied exactly, so that an objective result remains attributable to the action the Character actually proposed.
5. As a Character author, I want wait duration and reason copied exactly, so that intentional passage of World Time is not shortened, lengthened or reinterpreted silently.
6. As a Character author, I want move destinations copied exactly and applied as a deterministic Character position change, so that movement cannot be omitted after the Proposal Validator accepts it.
7. As a Character author, I want `no_op` to produce no Character World Event, so that absence of action is not turned into invented agency.
8. As a Director, I want to return only creative decisions, so that I can focus on consequences instead of reproducing Runtime context.
9. As a Director, I want to propose a relative semantic duration, so that pacing remains creative while absolute World Time stays Runtime-owned.
10. As a Director, I want to describe the outcome of an interaction, so that an intent can become an observable objective result without rewriting the intent itself.
11. As a Director, I want to propose External Event content and ordering through local references, so that environment reactions can retain causal relationships without inventing Runtime event keys.
12. As a Director, I want one precise repair request containing my previous output and diagnostics, so that I can correct the invalid creative field without regenerating unrelated facts blindly.
13. As a Runtime maintainer, I want Snapshot World Version injected by Runtime, so that a stale or incremented model value can never enter a Segment Draft.
14. As a Runtime maintainer, I want the active Event Session injected by Runtime, so that the Director cannot redirect a Wave to another Session.
15. As a Runtime maintainer, I want absolute Wave times derived from Snapshot World Time and a bounded relative duration, so that the model does not perform absolute-time arithmetic.
16. As a Runtime maintainer, I want Proposal Event actor, type and source derived from the Action Proposal, so that provenance is correct by construction.
17. As a Runtime maintainer, I want Proposal Event location derived from the actor's base Snapshot position, so that an action is recorded where it began even when it moves the actor.
18. As a Runtime maintainer, I want event keys generated deterministically, so that causal links do not depend on arbitrary model strings.
19. As a Runtime maintainer, I want External Event local references resolved by the Assembler, so that generated keys and causal references cannot drift apart.
20. As a Runtime maintainer, I want conflicting or duplicate Entity State Changes diagnosed before commit, so that assembly has one coherent result per Entity.
21. As a Runtime maintainer, I want Director-supplied Character movement rejected unless it is the deterministic consequence of that Character's accepted `move`, so that External Event resolution cannot manufacture Character agency.
22. As a Runtime maintainer, I want Segment Validator retained after assembly, so that hand-built drafts and Assembler regressions cannot bypass world invariants.
23. As a Runtime maintainer, I want deterministic-field invariant failures treated as Runtime defects rather than model repair opportunities, so that requests are not spent asking Director to change fields outside its interface.
24. As a Runtime maintainer, I want creative-field validation failures routed back to Director once, so that existing bounded repair behavior remains intact.
25. As a Runtime maintainer, I want a second invalid Director Resolution to fail the Batch without committing the Wave, so that repair remains bounded and atomicity is preserved.
26. As a Runtime maintainer, I want transport retry and request-budget semantics unchanged, so that narrowing the response contract does not alter operational limits.
27. As a World operator, I want failed assembly or validation to leave World Version, World Time and the World Ledger unchanged, so that rejected candidates never become facts.
28. As a World operator, I want successful assembled results to continue through the existing World Committer, so that persistence and Observation projection retain their current atomic behavior.
29. As a World operator, I want existing Worlds and committed World Segments to remain readable without migration, so that this model-interface refactor does not invalidate history.
30. As an auditor, I want Generation Trace to record the exact Director Resolution and its validation diagnostics, so that model responsibility is distinguishable from Runtime assembly.
31. As an auditor, I want repair traces to show the previous output supplied to the model, so that non-local repair failures can be diagnosed.
32. As an auditor, I want committed Proposal Events to retain exact proposal IDs and action payloads, so that Ledger facts can be traced back to Character decisions.
33. As a test author, I want the real 60-second Aina wait failure replayed with deterministic responses, so that correcting an invalid Director field can never delete the Proposal Event again.
34. As a test author, I want all five Action kinds exercised through the production Runtime seam, so that schema, assembly, validation and commit behavior are tested together.
35. As a test author, I want an attempted Director override of version, source or Character payload rejected at the model contract, so that authority is verified by behavior rather than prompt wording.
36. As a test author, I want creative External Events and Entity State Changes covered through the same seam, so that narrowing Proposal ownership does not remove legitimate Director capabilities.
37. As a fixture maintainer, I want checked-in Director fixtures to use the narrow resolution shape, so that offline and Provider execution share the same Agent Contract.
38. As a module maintainer, I want callers and tests to use one Segment Assembler interface, so that construction rules remain local and internal refactors do not spread across Runtime code.
39. As a domain maintainer, I want the glossary and ADR to distinguish assembly from validation and commit, so that future features preserve the same authority model.
40. As a future Player Event Request implementer, I want External Event assembly to preserve explicit source identity without granting Director commit authority, so that the reserved extension seam remains compatible.

## Implementation Decisions

- The Generation Wave pipeline becomes `Action Proposal → Proposal Validator → Director Resolution → Segment Assembler → Segment Validator → World Committer`. Segment Assembler is an in-process, pure deterministic module: it performs no model call, I/O, persistence or mutation and returns either one Segment Draft or stable diagnostics.
- The Segment Assembler interface accepts the authoritative Wave context (Snapshot, current Event Session and Director trace identity), one already-validated Action Proposal, and one Director Resolution. The current scheduler permits at most one Proposal per Wave; this feature does not reintroduce lockstep multi-Proposal assembly.
- Add a strict Director Resolution contract with `schema_version` plus exactly these top-level decisions: `elapsed_ms`, optional `outcome_summary`, `external_events`, `entity_changes` and `session_intent`. Extra fields are forbidden. `world_version`, `session_id`, absolute Wave times, Proposal Events, event keys and proposal-owned action fields are absent from the model response interface.
- `elapsed_ms` is a non-negative relative World Time duration bounded by the configured maximum Wave duration. Assembler sets Wave start to Snapshot World Time and Wave end to start plus this duration. A `wait` Proposal Event always lasts exactly the Proposal's `duration_ms`, and the Resolution duration must contain it. A `no_op` Resolution uses zero elapsed time; the existing Event-free control Segment rules for `resolved` and `limit_reached` remain unchanged.
- `outcome_summary` is bounded observable-result text, not hidden reasoning. It is required and non-empty for `interact`, optional for `utterance`, `move` and `wait`, and absent for `no_op`. When present, Assembler adds it to the Proposal Event payload without changing copied Proposal fields.
- Define a narrow Creative External Event contract containing only creative event type, optional actor, relative start/end offsets, Location/Interaction Scope, payload and local cause/evidence references. It does not contain absolute World Time, event key, source kind or source reference.
- Creative External Events are ordered. Cause and evidence references use a discriminated local-reference shape: `proposal` identifies the single Proposal Event, while `external` carries a zero-based index that may identify only an earlier External Event. Assembler rejects missing, forward or cyclic references, generates stable event keys from the Proposal identity and event position, converts offsets to absolute times, and maps local references to generated keys.
- Assembler sets every External Event source kind to `director` and its source reference to the Director Generation Trace identity. Existing Segment Validator rules continue to reject External Events that invent persistent Character dialogue, important actions, choices or motivation changes.
- Assembler creates exactly one Proposal Event for every non-`no_op` Proposal and none for `no_op`. Its event key is Runtime-generated; event type is `action.kind`; actor is `proposal.actor_id`; source kind is `action_proposal`; source reference is the exact proposal ID; and Location/Interaction Scope are the actor's position in the base Snapshot.
- For `no_op`, `elapsed_ms` is zero, `outcome_summary` is absent, and both creative External Events and Entity changes are empty. Director may still propose `keep_open` or `resolved`; existing Session preconditions and Event-free control Segment behavior decide whether that intent is valid.
- Proposal Events begin at the Wave start. `wait` ends at start plus its exact requested duration. Other non-`no_op` Proposal Events end at the assembled Wave end. All External Event offsets must be contained by the Wave duration. Existing no-cross-Wave and maximum-duration rules remain unchanged.
- Canonical Proposal Event payloads are assembled as follows:

| Action | Fields copied exactly from Action Proposal | Runtime-owned consequence |
| --- | --- | --- |
| `utterance` | `intent_summary`, `text`, `addressee_ids`, `expects_response`, `response_to_event_id` | One Proposal Event at the actor's base position |
| `wait` | `intent_summary`, `duration_ms`, `reason` | One Proposal Event whose duration equals `duration_ms` |
| `move` | `intent_summary`, destination `location_id` and `scope_key` | One Proposal Event at the source position plus one actor position change to the destination |
| `interact` | `intent_summary`, `target_id`, `description` | One Proposal Event including required `outcome_summary`; validated creative Entity changes may describe the result |
| `no_op` | None | No Character Event and no deterministic Entity change |

- For `move`, Assembler always creates the actor's destination Entity State Change. A compatible Director state patch for the same actor is merged into that one change; the Director cannot supply or override the actor's Location/Interaction Scope. Director-supplied position changes for any persistent Character without that Character's accepted `move` are invalid.
- Assembler normalizes Entity State Changes to at most one final change per Entity. Non-conflicting state patches may be combined. Conflicting values, duplicate position changes or a Director attempt to replace a deterministic move produce stable field-path diagnostics and no Segment Draft.
- Segment Validator remains a separate pure gate and continues to validate the assembled draft against the Snapshot, original Proposal and Session rules. Its checks are defense in depth; Assembler does not gain commit authority and World Committer still accepts only a Validated Commit Plan.
- Diagnostics retain ownership. Schema errors and diagnostics caused by Director-owned outcome summaries, External Events, Entity changes, relative duration or Session intent permit one Director repair. A violation of an Assembler-owned field is an internal assembly invariant failure and does not spend a repair request asking the model to modify an unavailable field.
- The Director repair request retains the original input and adds a `repair` object containing `previous_output` and the complete stable diagnostic list with code, path and message. For a schema-valid first response, `previous_output` is its Director Resolution object; for schema-invalid output, it is the exact raw response. The second attempt still uses the narrow Director Resolution contract.
- Rename the Director settlement call kinds from `segment_draft`/`segment_draft_repair` to `director_resolution`/`director_resolution_repair`, including fixture addresses and trace assertions, so observability names the model's actual responsibility. Turn selection remains a separate Director call.
- Generation Trace `structured_result` records Director Resolution rather than an assembled Segment Draft. Validation metadata identifies schema, assembly or Segment Validator diagnostics. The committed World Segment remains the durable record of the fully assembled and validated facts, so no new persistence table is required.
- Migrate built-in fixture responses, checked-in demo fixture responses, fixture call manifests and test gateways to Director Resolution. Regenerate any exact fixture input hashes through the repository's existing fixture workflow rather than weakening hash verification.
- Keep Segment Draft as the internal contract between Segment Assembler and Segment Validator. Keep Proposal Event Candidate, External Event Candidate and Validated Commit Plan as downstream domain contracts; they are no longer all exposed as Director output.
- Update the domain glossary with Segment Assembler and refine Director to describe Director Resolution rather than full Segment Draft construction. Update ADR 0022 to record Runtime-owned assembly before validation, ownership-aware repair and the rule that assembly is construction by authority, not silent post-hoc correction of model output.
- ADR 0013 remains valid: Director still proposes semantic pacing, now as a bounded relative duration, while Runtime derives absolute World Time. ADRs governing one atomic Action Proposal, bounded repair, utterance response semantics, Decision Turn scheduling and External Event Character agency remain unchanged.
- Existing databases, committed World Segments and historical Generation Trace JSON require no migration. Readers must continue to tolerate historical `segment_draft` call kinds and full-draft structured results while new executions write the new call kinds and resolution shape.

## Testing Decisions

- Use one primary high-level seam: initialize a fresh World and call the production `advance_world` flow with a deterministic Model Gateway adapter. Assert through the returned receipt, persisted World Ledger, Snapshot, Entity Revisions and Generation Trace rather than calling Assembler helpers from every test.
- Parameterize high-level cases for `utterance`, `wait`, `move`, `interact` and `no_op`. Each case supplies an accepted Action Proposal and a narrow Director Resolution, then observes the canonical committed event payload, source, actor, location, absolute time and resulting Entity state. `no_op` observes the existing no-Event/no-version behavior when the Session stays open.
- The `utterance` case proves exact dialogue, addressee, `expects_response` and `response_to_event_id` preservation, including pending-response behavior already covered by Session lineage tests.
- The `wait` case proves exact duration/reason preservation and that World Time contains the wait. Use the sanitized Aina Proposal and 60,000 millisecond duration from the real failure, with the first Director response attempting to return `base_world_version + 1` through the now-forbidden version field.
- The `move` case proves the event remains at the base Snapshot position while the next Snapshot and Entity Revision place the actor at the Proposal destination, causing existing deterministic Session lineage behavior when scopes split.
- The `interact` case proves exact target/description preservation, required outcome text and a legal object state change. A conflicting or unauthorized Character position change must fail without commit.
- The contract-ownership case has Director attempt to return former Segment Draft fields such as World Version, Session, Proposal Events, event key, source and rewritten action payload. The strict response contract rejects these fields; a valid repair cannot choose whether the original Proposal Event exists because Assembler constructs it.
- Reproduce the observed repair sequence as a regression: the first Director response contains a forbidden/stale version field while otherwise describing the Aina wait; the repair receives that previous output and exact diagnostic, returns a valid Resolution, and the committed result still contains the exact wait Proposal Event. Also assert that the repair request does not ask Director to emit a Segment Draft.
- Add a creative External Event case with relative timing and a cause reference to the Proposal Event. Assert the persisted event has Runtime-generated keys, Director trace provenance and resolved cause Event IDs without exposing local reference syntax downstream.
- Add failure cases for an External Event outside the Wave duration, a forward/missing local cause, duplicate Entity changes, elapsed time shorter than a wait, premature Session resolution and a second invalid repair. Every failure leaves World Version, World Time and Ledger facts unchanged.
- Retain focused Segment Validator tests that construct malicious Segment Drafts directly as defense-in-depth tests. Replace tests and fixtures whose only purpose was to make Director manually copy a valid Proposal Event; they should now test through the Assembler-owning Runtime seam.
- Prior art is the existing generation-wave contract tests, bounded semantic-repair Batch tests, no-op control Segment tests and Session lineage movement tests. Extend those behavior patterns rather than creating a second Runtime harness.
- Run focused Runtime and Session tests during development, then run the repository gates `uv run pytest`, `uv run ruff check .` and `uv run ruff format --check .`. Tests use no network, credentials, external WebGAL installation or real Provider quota.

## Out of Scope

- Changing Action Proposal kinds, Character Agent contracts, Proposal Validator ownership rules or Decision Turn scheduling.
- Reintroducing multiple concurrent Character Proposals in one Generation Wave.
- Changing World Committer transaction behavior, World Ledger schemas, database migrations, World Version sequencing or Observation projection.
- Changing the five-minute Wave maximum, request budgets, transport retries, one-repair limit, cancellation or Batch recovery behavior.
- Implementing Player Event Request intake, persistence or natural-language interpretation; the External Event source seam is only preserved.
- Expanding Director authority over persistent Character dialogue, important choices, movement or motivation.
- Redesigning Broadcast, Render, Asset Manifest, Character Skill or Scenario Policy behavior.
- Introducing a configurable rules engine, Validator DSL or a second model gateway interface.
- Migrating or rewriting historical Generation Trace records and committed World Segments.
- Running a real Provider, mutating an existing long-running World, publishing WebGAL output, committing, pushing, reviewing or deploying.

## Further Notes

- The real failure is not merely a prompt-quality issue. The first Director result contained the required Aina wait and failed only on a copied World Version; the repair corrected that version but returned an empty Proposal Event list. Removing both fields from Director authority prevents this failure class structurally.
- Segment Assembler is a deep module: its small interface hides action-specific payload construction, provenance, timing, causal-link resolution, move-state generation and consistency checks. Deleting it would spread those rules back across Runtime, fixtures and tests.
- Segment Validator still rejects any malformed Segment Draft regardless of origin. “Runtime-owned” means constructed from authoritative inputs, not exempt from validation.
- This is a bounded contract/runtime/fixture/test/documentation refactor and fits one implementation context.
