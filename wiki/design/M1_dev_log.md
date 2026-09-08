# M1 开发记录

> 状态：M1.1 / M1.2 / M1.3 开发与自动验收完成，待用户 Review。
> 日期：2026-09-08；开发基线：`master@fc1d402`。
> 交付：用户已于 2026-09-08 确认提交并推送 M1；对应版本以本文件的 Git 历史为准。
> 下文“未 commit / push”均为各次开发验收当时的历史状态，不代表后续交付状态。
> 总计划：[dev_plan_MVP.md](dev_plan_MVP.md)；领域设计：[MVP_dev.md](MVP_dev.md)。
> 同日追加：DeepSeek 接入见第 8 节，本地轻量 Trace 见第 9 节；第 1–7 节保留此前 M1 三项基础交付的验收记录。
> 实网更新：持久化本机配置与 Flash 单角色试跑成功见第 10 节；不改写此前离线测试记录。

本文由 `M1_dev_plan.md` 更名而来。固定约定：`dev_plan_MVP.md` 管阶段范围、验收条件与状态；`Mx_dev_log.md` 管该阶段实际实现、文件清单、测试证据与遗留边界。后续修订按日期追加记录，不再把“预计实现”与“已经验证”混写，也不全量翻修旧 Wiki。

## 1. 本次交付结论

M1 完成三项基础能力：

- **M1.1：Gateway 标准解析路径修复。** 保留唯一业务入口 `generate`，合法 JSON 数组可进入严格 tuple 模型；不降低 strict/frozen，不增加 Provider 或依赖。
- **M1.2：World 身份与最小角色状态。** `WorldRef` 贯穿 AgentView、Proposal、State、Memory、模型请求和 Trace；实例检查先于 replay 和认知调用。移除 daily，接入不按日期重置的普通 Plan queue。
- **M1.3：UnionPart。** 泛型集合分区，显式 merge/split、直接 root 查询、只读成员快照；没有业务信息、Session 历史或额外控制器。

这不是完整 World Runtime：Project 独立 SQLite / Scenario 在 M2，World 与私有状态共同提交及 outcome 在 M3，自由互动 / 暂停续跑 / 50 轮对话约束在 M4。Director、Broadcast 与 JSON 导出仍按后续阶段实施。

## 2. 实际文件清单

树中 `UPDATE` 表示完善已有文件，`NEW` 表示新增；不是泛指某个 Folder 的模糊计划。

```text
agent_runtime/
├── model_gateway.py             [UPDATE · M1.1/M1.2] Schema → JSON strict 校验；请求/Trace 带 WorldRef
├── world/
│   └── contracts.py             [UPDATE · M1.2] 唯一 WorldRef；AgentView 替代 PerceptionFrame；Proposal 补身份
├── agent/
│   ├── personact/
│   │   ├── __init__.py          [UPDATE · M1.2] 导出 PlanDraft/PlanningInput，移除 Daily 类型
│   │   ├── agent.py             [UPDATE · M1.2] 实例绑定；replay 前检查；保留锁和单次 private snapshot 发布
│   │   ├── loop.py              [UPDATE · M1.2] 直接入口防串用；空队列 planning；State/Memory/Trace 身份传播
│   │   ├── state.py             [UPDATE · M1.2] PlanItem、plan_queue、active_plan_id；删除 daily 和旧计时行动/冷却
│   │   ├── proposal.py          [UPDATE · M1.2] 校验受信 WorldRef、Spec、View，再注入提案 envelope
│   │   └── model_strategy.py    [UPDATE · M1.2] 普通 plan；score 显式接收 WorldRef；repair/失败 Trace 保留来源
│   └── memory/
│       ├── contracts.py         [UPDATE · M1.2] Record/Touch 完整 owner；Retrieval 配对校验
│       ├── stream.py            [UPDATE · M1.2] 构造、append、touch 校验；不可变重建保留身份
│       └── retrieval.py         [UPDATE · M1.2] Touch 从 Record 派生 owner；打分/排序不变
├── common/                      [NEW 目录]
│   ├── __init__.py              [NEW · M1.3] 无业务初始化副作用
│   └── union_part.py            [NEW · M1.3] 纯泛型 merge/split 和只读查询
└── tests/
    ├── test_model_strategy.py   [UPDATE · M1.1/M1.2] 标准 SDK parser、严格类型、重试/repair、来源和组合链路
    ├── test_personact.py        [UPDATE · M1.2] builder、wire 迁移与禁止模型伪造身份
    ├── test_personact_agent.py  [UPDATE · M1.2] 实例/Loop 校验、replay、零副作用、Plan 与私有 JSON 重建
    ├── test_persona_state.py    [UPDATE · M1.2] WorldRef/View/State strict/frozen、队列不变量和旧字段拒绝
    ├── test_memory.py           [UPDATE · M1.2] 四轴归属、访问更新、配对、排序回归及 JSON 重建
    └── test_union_part.py       [NEW · M1.3] 通用分区边界和确定性操作序列

wiki/
├── index.md                     [UPDATE] 只补当前总计划与 M1 记录导航
└── design/
    ├── MVP_dev.md               [UPDATE] 只校准 M1 已实现与 PersonaState 新字段
    ├── dev_plan_MVP.md          [UPDATE · 原 worktree 未跟踪文件] 固定 plan/log 职责；登记 M1 证据
    └── M1_dev_log.md            [RENAME + UPDATE] 原 M1_dev_plan.md → 本记录
```

生产/测试文件共 19 个：11 个生产 UPDATE、2 个生产 NEW；5 个测试 UPDATE、1 个测试 NEW。只新增 `common/` 源码目录，未更改 Manifest Compiler、StrictModel、Character Skill、agents.json Fixture、Node/WebGAL、pyproject.toml 或 uv.lock。

实际行数以 `git diff --numstat -- agent_runtime` 加上 3 个未跟踪新增文件的 `wc -l` 核对，排除文档；新增和删除分别统计，不把净增当开发量：

| 范围 | 文件数 | 新增行 | 删除行 | 净增行 |
| --- | --- | --- | --- | --- |
| 生产代码 | 13 | 435 | 284 | 151 |
| 测试 | 6 | 1522 | 385 | 1137 |
| 合计 | 19 | 1957 | 669 | 1288 |

旧稿 800–1400 行估算早于 daily 裁剪及普通 Plan queue 的范围修正，且不是同一新增/删除统计口径；本表用实际 diff 取代旧估算，不使用人日指标。

## 3. M1.1：Gateway 的实现

业务接口没有扩张：

```python
generate(request, response_type, config=None) -> ModelGeneration
# 返回 structured + trace
```

`LangChainModelGateway.generate` 使用：

```text
response_type.model_json_schema(by_alias=True)
→ ChatModel.with_structured_output(schema)
→ 标准 LangChain JSON tool parser
→ json.dumps(parsed, allow_nan=False)
→ response_type.model_validate_json(payload, strict=True)
→ 原 strict/frozen 模型 + 脱敏 ModelCallTrace
```

传入 Schema 字典，避免 LangChain 提前将 JSON list 用 Python 模式塞进 strict tuple；最终 JSON 验证仍在现有 Gateway 内，不增加 parse/retry/repair 公开入口。

错误处理：

- model_id 不匹配、明确不支持 structured output：`ModelRequestRejectedError`。
- 传输失败：仅匹配配置的 transport 异常，受 `max_transport_attempts` 限制。
- 坏 JSON、缺少匹配 tool result、错误类型/未知字段：`ModelOutputInvalidError`。
- 编码这一局部步骤的 TypeError/ValueError（如 set、NaN、Infinity）：同样是非法输出，不泄露内容。
- 输出错误分类优先于 transport 配置，宽泛的 retryable 类型也不能吞掉已识别的 schema/编码错误；其他 SDK 配置或编程错误不自动伪装成模型输出。
- Strategy 的 schema/语义错误共用最多一次 repair；不叠加两个 repair 额度。

没有截取自然语言 JSON、`default=str`、tuple → list 业务迁移、include_raw 或实网 Provider 装配。测试 Fake 实现 `bind_tools/_generate`，**没有覆盖 `with_structured_output`**，因此不会绕过标准解析器。

独立 Review 补修：Pydantic 的 error.loc 也可能包含模型生成的秘密字段名或动态字典键。诊断现只保留最多 6 项“编号 + 错误类型”，其余只计数量；不记录 loc/msg/input/context。测试覆盖字段名、动态 key、数量限幅和 repair 脱敏。取舍是 repair 不再获得字段路径提示，而需结合 Schema 与错误类型修正；不新增复杂 Schema 遍历器。

## 4. M1.2：身份、角色状态与兼容边界

### 4.1 WorldRef 由运行实例绑定

```python
class WorldRef(StrictModel):
    project_id: Identifier
    world_id: Identifier
```

无默认 World，JSON 为 `{"projectId":"coffee-golden","worldId":"save-001"}`。它标识同一作品的一份独立发展副本，不是递增版本、存档正文或鉴权凭证。Manifest/CompiledPersonActSpec 仍属于 Project，可以实例化到多个 World。

受信入口：

```python
PersonActAgent(spec, state, memory, strategy, embedding_provider, *, world_ref)
PersonActLoop(*, spec, strategy, embedding_provider, world_ref)
build_action_proposal(*, world_ref, spec, view, proposal_id, draft)
```

Agent 先检查 Spec Project、State/Memory owner，再在每次 decide 的锁内检查 View 的 World/Agent，之后才计算 fingerprint 或返回 replay。直接调用 Loop 也检查相同边界；错配不会调用模型/embedding，不替换 State、Memory、DecisionTrace，也不消耗 proposal ID。

字段传播：

- AgentView / ActionProposal / PersonaState / DecisionTrace：必填 WorldRef。
- MemoryStream / MemoryRecord / MemoryTouch：owner 是 `(world_ref, agent_id, scope)`。Touch 在查 ID 前检查完整 owner；Retrieval 的 Record/Touch 按完整 owner、ID、顺序配对。
- ModelRequest / ModelCallTrace：从受信调用传播；成功、repair、最终非法输出和 transport failure 都保留 WorldRef。
- `score_poignancy(spec, candidate, *, world_ref)` 显式接收；plan 从 State 取，plan_action 从 View 取并检查 State 一致。共享 Strategy/Gateway 不持有可变“当前 World”。
- ProposalDraft 仍只有 action/evidence；模型夹带 project/world/actor/session/version 字段被拒绝。

Scope 仍遵守原有授权规则：Stream 内是 exact scope，Agent 可使用 Spec namespace 的合法子 scope。模型 plan 有 Stream，要求召回记录与它 exact 匹配；plan_action 无 Stream 参数，检查记录处于该 Spec namespace 内。WorldRef 是此基础上新增的 World 归属，不把 namespace 误称为数据库隔离。

### 4.2 普通 Plan queue，完全去 daily

实际 PersonaState：

```text
world_ref / agent_id
plan_queue: tuple[PlanItem, ...]
  PlanItem = plan_id + description
active_plan_id: optional reference into plan_queue
cognitive_config
reflection_remaining / reflection_new_memory_count
last_world_time / last_world_version
known_place_ids
```

- 空队列才调用 `CognitionStrategy.plan(PlanningInput) -> PlanDraft(items)`；顺序来自模型或 Fixture，不由 Runtime 编造角色任务。
- Plan ID 必须唯一；active_plan_id 若非空必须指向队列现有项。无 active 引用且队列非空时选择队首；`active_plan` 是派生属性，不保存第二份行动正文。
- 空计划合法，仍允许 plan_action 依据现场行动；下次队列仍空时可再次规划。
- 已有队列跨日期和 State JSON 重载保持不变；没有 new-day 判定、日程时间槽或自动日重置。
- **Proposal 不代表完成。** M1 不按预计耗时完成、取消或出队；M3 接 outcome 后再落实等待/完成/取消与队列推进。
- 生成的计划仅随最终合法 Proposal 一起发布进程内 private snapshot；计划/Proposal 校验失败时本次感知和记忆暂存结果不发布。

删除：DailyPlan、ScheduleItem、DailyPlanDraft、DailyPlanningInput、NewDayStatus、plan_day、daily 字段、旧 ActiveAction 计时提示和旧 `peer_agent_id + until` ConversationCooldown。旧格式直接报错，不静默忽略。

保留字段的真实作用：

| 字段 | M1 实际消费者 / 限制 |
| --- | --- |
| last_world_time / last_world_version | 拒绝输入倒退；不能当作 World 事务版本校验的替代 |
| reflection_remaining / reflection_new_memory_count | 新颖且允许写入的 Observation 累计计数；没有 Reflection 调用、触发或重置流程 |
| cognitive_config | attention_budget、三项检索权重与 recency_decay 有消费者；retention、reflection_threshold/count 暂保留配置，不宣称算法已接入 |
| known_place_ids | 按用户要求保留、可序列化；不实现地点学习、移动或可见性判断 |

连续对话 50 轮后接行为不是被删掉：已分配给 M4.1，待 Session 调度时冻结计数与重置语义。不沿用旧时间 cooldown，也不在 M1 伪造“对话已达上限”。

### 4.3 一次性 Runtime 契约迁移

`PerceptionFrame → AgentView`，`DecisionRequest.frame / ActionPlanningInput.frame / builder.frame → view`。没有 alias、双写或旧格式兜底。缺少 WorldRef 的旧 Runtime 对象需要显式迁移；角色配置 Manifest formatVersion 未变。

**恢复能力的精确口径：** M1 验证 State/Memory JSON 重建后，新的 Agent 能继续原计划与记忆，不会按日期重新生成。尚无 SQLite 文件、暂停协议、epoch、调度进度、跨进程 replay/used_proposal_ids 恢复，也无 World/Agent 原子提交。不能称为完整存档恢复。

## 5. M1.3：UnionPart

实现位置 `common/union_part.py`，75 行。类型只要求 hash/equality 稳定的 Hashable：

```python
UnionPart(items: Iterable[T])
root_of(item: T) -> T
members_of(root: T) -> frozenset[T]
connected(left: T, right: T) -> bool
merge(*, keep_root: T, merged_roots: Collection[T]) -> None
split(root: T, parts: Mapping[T, Collection[T]]) -> None
```

内部仅 `_root_by_item` 和 `_members_by_root`，初始化每项 singleton，root 直接指向自己。merge/split 在可预见输入错误全部检查后才更新索引。

- 空结构、空 merge、原分组 split 为合法 no-op；显式单组 split 可以换 root。
- 重复初始化、重复 merge、非 root 参数、遗漏/重复/额外成员、组 root 不在组内拒绝。
- 未知 item/root 为 KeyError；结构无效为 ValueError；不可 hash 的 item 为 TypeError。
- members_of 返回独立 frozenset 快照；之后操作不修改旧快照。
- root/connected 平均 O(1)，members/merge/split 为 O(k)，不要求 item 可排序。
- 不增加 add/remove、业务 hook、Session lineage、历史持久化或锁；不承诺自定义 hash/equality 中途抛错的强异常安全或并发原子性。

EventSession 的继承/组合与 join/leave 规则留到 M4。通用分区测试通过不等于角色已经可以自由互动。

## 6. 自动测试与验收证据

### 可追踪的验收映射

| 编号 | 证据文件与代表性用例 |
| --- | --- |
| M1.1-T1/T2 | test_model_strategy：standard_pydantic_parser_reproduces_the_old_tuple_failure；gateway_strict_json_accepts_nested_tuples_through_standard_parser；gateway_parses_real_proposal_union_and_evidence_array |
| M1.1-T3/T4 | test_model_strategy：gateway_keeps_strict_types_and_unknown_field_rejection；gateway_rejects_bad_missing_or_non_json_results_without_transport_retry |
| M1.1-T5/T6 | test_model_strategy：model_strategy_shares_one_repair_budget_for_schema_and_semantics；transport、model override、SDK configuration 与 unsupported structured output 用例 |
| M1.1 诊断隐私补修 | test_model_strategy：validation_diagnostics_never_expose_model_authored_error_locations；validation_diagnostics_have_a_bounded_number_of_error_details；repair_diagnostics_redact_private_keys_and_keep_one_repair_budget |
| M1.2-T1 | test_persona_state：world_ref_is_required_strict_frozen_and_round_trips；agent_view_requires_world_and_has_no_legacy_frame_alias |
| M1.2-T2/T3 | test_personact_agent：independent_worlds_can_reuse_all_internal_ids；direct_loop_rejects_foreign_world_before_cognitive_nodes |
| M1.2-T4 | test_personact_agent：world_mismatch_is_rejected_before_replay_and_cognition；direct_loop_rejects_foreign_agent_or_scope_before_cognition |
| M1.2-T5/T6 | test_memory：Record/Touch 四轴错配、批量更新、Record/Touch 配对、JSON round-trip、既有排序测试 |
| M1.2-T7 | test_personact：proposal_builder_refuses_foreign_view_even_for_no_op；model_draft_cannot_author_runtime_identity；test_personact_agent 的 replay 回归 |
| M1.2-T8 | test_model_strategy：shared_strategy_preserves_each_world_without_mutable_current_world；repair/failure provenance；test_personact_agent 的 DecisionTrace 检查 |
| M1.2-T9 | test_personact_agent：private_json_reload_continues_queue_across_dates_without_replanning；legacy frame 拒绝；原 attention/novelty/focus/write-policy/并发/失败回滚用例迁移 |
| M1.2 新增 Plan 验收 | test_persona_state：唯一 ID、悬空 active 引用、不可变与旧字段拒绝；test_personact_agent：非法计划修正后用同一 proposal ID 重试成功 |
| M1.3-T1～T8 | test_union_part：构造、merge/split、异常/no-op/reroot、旧快照、非排序 Hashable；8 个 seed × 60 步 = 480 步确定性不变量检查 |
| M1 组合验收 | test_model_strategy：personact_loop_uses_model_strategy_without_moving_world 参数化 Fixture / 标准 parser，跑完整 Agent → score → Plan → action → Proposal → private State/Memory/Trace，并验证 replay 无重复调用 |

表内省略测试函数共同的 `test_` 前缀；对应源文件位于 `agent_runtime/tests/`。测试实现和参数化实例才是证据，不将上表等同于实网 Provider 或数据库验证。

### 执行记录

2026-09-08 本次实际执行：

| 命令 | 结果 |
| --- | --- |
| `uv run ruff format --check agent_runtime` | 通过，34 files already formatted |
| `uv run ruff check agent_runtime` | 通过，All checks passed |
| `uv run pyright` | 通过，0 errors / 0 warnings |
| `uv run pytest` | **193 passed in 0.74s** |
| `uv run pytest -o addopts='' agent_runtime/tests` | 再次通过，193 passed in 0.53s；用于核对逐文件收集数量 |
| `git diff --check` | 通过，无空白错误 |

按文件汇总的测试实例数（含参数化）：

| 测试文件 | 通过数 |
| --- | --- |
| test_model_strategy.py | 49 |
| test_personact.py | 37 |
| test_personact_agent.py | 36 |
| test_persona_state.py | 10 |
| test_memory.py | 29 |
| test_union_part.py | 32 |
| 合计 | **193** |

开工前重新运行基线 Python：64 passed；当前数量净增 129，包含替换旧 daily 用例，不能说是“额外新增 193 个测试”。验证环境为 Python 3.12.13 / Pydantic 2.13.5 / LangChain-core 1.6.1 / pytest 9.1.1；依赖与锁文件未改。

未运行实网或付费模型；没有建立/修改正式 World 存档。没有 Node/制作层变更，因此本轮不重跑 npm test，也不把旧 Node 14 项记录当作本轮结果。World/Entry/Session 数据库、Director/Broadcast、完整 pause/resume 不在本次测试证明范围内。

## 7. 独立 Review 与下一阶段交接

- 对 WorldRef、Loop/facade、State/Proposal 做了独立只读 Review；未发现阻塞性实现问题。补足了其建议的 builder 身份拒绝、同 proposal ID 失败重试、直接 Loop 的 Agent/scope 前置检查测试。
- Gateway/Strategy 标准解析、身份与失败分类另做独立 Review；发现的 error.loc 隐私问题已修复、补测试，并由原 reviewer 再次只读确认闭合，无剩余阻塞问题。
- M2：为现在明确携带 WorldRef 的 State/Memory 增加 Project 独立 SQLite 存储，初始化/加载不能降级为默认 World。
- M3：保留队列作为当前私有计划真值，接入 outcome；改造当前 Proposal 后立即发布 private snapshot 的时机，使 World/Agent 状态一起提交。不可用当前 M1 replay 代替持久幂等。
- M4：消费纯 UnionPart，不扩张底层业务字段；实现 50 轮对话约束与 pause/resume。
- Worker 与 WebGAL 转译继续由 Codex 进程外承担，不在此阶段新增接口。

## 8. 2026-09-08 追加：DeepSeek 模型接入

用户确认通过 OpenAI 兼容方式接入 DeepSeek。实际客户端为 `ChatOpenAI → OpenAI SDK`，实际服务为 DeepSeek，不调用 OpenAI 的模型、不使用 ChatGPT 登录或订阅。沿用现有 `ModelGateway.generate()` 与 `ModelCognitionStrategy`，不新增 Provider 类、Manager 或编排层。

### 8.1 本次文件与实现

```text
项目根目录/
├── pyproject.toml                      [UPDATE] 增加 langchain-openai==1.6.0
├── uv.lock                             [UPDATE] 锁定兼容依赖，含 openai==3.8.0
├── agent_runtime/
│   ├── model_gateway.py                [UPDATE] 可选 function_calling 模式；SDK 请求拒绝安全分类
│   ├── model_provider.py               [NEW] create_deepseek_gateway，配装既有 Gateway
│   ├── model_smoke.py                  [NEW] Gold Skill + Anon 单次 decide 命令
│   └── tests/
│       ├── test_model_provider.py      [NEW] 真实 SDK 的 Mock HTTP 契约及 Agent/repair 链路
│       └── test_model_smoke.py         [NEW] 单角色 Fixture、CLI 配置、帮助和失败脱敏
└── wiki/design/
    └── M1_dev_log.md                    [UPDATE] 仅追加本节，不翻修旧设计
```

本次新增生产文件约 240 行，新增测试约 420 行；Gateway 只增补配置和错误分类。依赖仍由 uv 管理，原 `langchain-core==1.6.1`、Pydantic 和测试工具版本不变。

- Endpoint 固定 `https://api.deepseek.com`，走 `/chat/completions`；默认模型 `deepseek-v4-flash`。模型选择优先级：CLI `--model` → `DEEPSEEK_MODEL` → 默认值。模型名称依据接入时的 [DeepSeek 官方文档](https://api-docs.deepseek.com/)，不把名称当作永不变化的版本快照。
- 密钥仅从 `DEEPSEEK_API_KEY` 或 factory 显式参数读取；缺失立即失败，不回退到 `OPENAI_API_KEY`。不创建密钥文件、不自动读取 `.env`。忽略 OpenAI endpoint 环境配置，清除 SDK 自动读入的 OpenAI organization/project 信息，防止发送给 DeepSeek。
- 显式选择 `function_calling`，关闭 thinking，省略远端 `strict` 和未文档化的 `parallel_tool_calls`；本地 JSON → Pydantic strict/frozen 校验与最多一次 schema/semantic repair 不变。DeepSeek 的 thinking/tool-choice 兼容限制及 `max_tokens` 字段见 [官方兼容说明](https://api-docs.deepseek.com/quick_start/agent_integrations/oh_my_pi/)。不使用客户端默认 `json_schema` 模式。
- 输出上限为每次请求 2048 tokens，以 `extra_body.max_tokens` 发送，避免 ChatOpenAI 改写成 `max_completion_tokens`。HTTP 各阶段超时设为 60 秒，**不是整次 Agent 决策的墙钟上限**。
- SDK `max_retries=0`；Gateway 对连接/超时、429、5xx 最多尝试 3 次。400/401/402/403/404/422 直接报安全的请求拒绝，不当作模型输出错误，不进入 repair。诊断不输出 SDK 原始错误 body。
- 独立 Review 发现 OpenAI organization/project 环境变量会混入 DeepSeek 请求头；已清除同步/异步 SDK 客户端的对应公开配置，并用污染环境变量的 HTTP 测试验证请求头不再携带这些信息。

### 8.2 如何试跑

在仓库根目录运行；Key 由本机安全地注入环境变量，不要提交仓库或发进聊天。下面 `read` 是当前 zsh 的隐藏输入，不把 Key 写进命令历史：

```zsh
uv sync --frozen
read -rs 'DEEPSEEK_API_KEY?DeepSeek API Key: '
export DEEPSEEK_API_KEY
uv run python -m agent_runtime.model_smoke

# 可选：切换同一 Provider 的模型
uv run python -m agent_runtime.model_smoke --model deepseek-v4-pro
```

不带 `--help` 的命令会请求真实 DeepSeek、消耗 API 额度。默认一次决策通常依次调用 `score_poignancy → plan → plan_action` 共 3 次；失败时 repair/transport retry 会增加请求次数，不承诺固定费用。`--help` 不创建客户端、不发请求。

试跑复用 `testdata/npc_diy/agents.json` 与正式 Gold Character Skill，以一条“爽世向爱音打招呼”的固定可见观察运行 Anon 一次，输出 `{proposal, traces}` JSON。提案带 WorldRef / Agent / Session / World version / evidence，Trace 保留模型与 Skill 来源；不输出完整 Prompt。场景与 World ID 只是 smoke Fixture，不是 Scenario 初始化或已建立的 World 存档。

**边界：** Soyo 此时是输入观察的来源，不是第二个运行中的 Agent；输出 Proposal 尚未发生 World commit。没有多角色循环、SQLite、自由互动、生产 embedding 或语义检索验收；embedding 为空向量占位。World Runtime 仍按 M2–M4 实施，不把此次接入算作完整 MVP 已可运行。

### 8.3 本次测试证据

- 原 M1 193 项回归保留；新增 Provider HTTP 测试 28 项，Smoke 测试 6 项，完整 Python 测试 **227 passed**。
- HTTP 测试不覆盖/替换 `ChatOpenAI.with_structured_output`，只在 HTTP transport 返回模拟响应；验证实际 URL、鉴权、请求 Schema、参数与 SDK 解析。真实 PlanDraft tuple、ProposalDraft action union / evidence 数组通过；坏 JSON、错误类型和未知字段仍失败。
- 用真实 SDK 跑通完整 Anon 单次决策，额外验证 schema/语义失败后的单次 repair、WorldRef 保留、调用 ID 区分及原始输入不变。repair Trace 的既有名称为 `plan_action_repair`。
- 覆盖瞬时失败恢复、恰好 3 次尝试上限、认证/余额错误不重试，以及无 Key、不回退 OpenAI Key、环境 metadata 隔离和 CLI 失败不打印秘密。
- `uv run ruff format --check agent_runtime`、`uv run ruff check agent_runtime`、`uv run pyright`、`uv run pytest`、`git diff --check` 全部通过；另执行了 CLI `--help`。
- **未发起真实 API 请求。** 测试证明客户端接线与本地处理，不证明账户 Key/余额可用、DeepSeek 服务端已接受当前完整 Proposal Schema，或真实角色输出质量合格。首次实网试跑仍待有效 Key 与实际调用验证；未 commit / push。

## 9. 2026-09-08 追加：本地轻量 Agent Trace

用户确认参考 `../generative_agents` 的本地记录与 debug 输出方式，不接 Langfuse 等服务。参考的是原版 `print_prompt.py` 的控制台调试、`reverie.py` 的逐步文件记录思路；**不照搬其 World/Memory JSON 存档，也不把 Trace 当成 EventEntry 或可恢复存档。** 无新增依赖、数据库表、日志服务或异步队列。

### 9.1 文件与已实现能力

```text
agent_runtime/
├── trace.py                         [NEW] LocalTrace、JSONL 记录、决策作用域、显式 debug
├── model_gateway.py                 [UPDATE] Fixture/实际 SDK 的逐次请求、重试、失败记录
├── model_smoke.py                   [UPDATE] CLI 默认写 Trace；--debug / --trace-root
├── agent/personact/
│   ├── agent.py                     [UPDATE] 可选 trace_log；决策、失败、replay、结果
│   ├── loop.py                      [UPDATE] 五个认知阶段的开始、完成与摘要
│   └── model_strategy.py            [UPDATE] 模型最终状态与单次 repair 的前后关联
└── tests/
    ├── test_trace.py                [NEW] 写入、隔离、并发、异常、debug 边界
    ├── test_trace_personact.py      [NEW] 完整角色决策、失败、replay 与关闭 debug 的行为
    ├── test_trace_model.py          [NEW] 真实 SDK Mock HTTP、重试、repair、失败与隐私
    └── test_model_smoke.py          [UPDATE] 默认文件输出与失败时的 --debug 行为
wiki/design/
└── M1_dev_log.md                    [UPDATE] 仅追加实现与验收记录
```

新增 Trace 实现文件 204 行，三个新增测试文件合计 722 行；已有调用点做局部接线。测试行数高于此前估计，包含独立角色 Fixture、真实 SDK 的 HTTP 模拟与故障路径，不对应新增业务框架。

- 一次 `decide()` 分配一个 `traceId`；每行带 `worldRef`、`agentKind`、`agentId`、`seq`、`recordedAt`、`event`、`data`。受信身份检查后，`decision.input` 记录 Proposal ID、EventSession ID、World version；其它行通过 `traceId` 关联，不记录被拒绝的外来 View 正文。
- 认知链路记录 `prepare / perceive / retrieve / plan / propose` 的 start/end；默认摘要包括可见/选中观察 ID、novelty 结果、检索 Memory ID、Plan ID、行动类型与 evidence ID。
- `model.attempt.*` 保留每次物理调用和重试，`model.result` 保留现有 ModelCallTrace 的状态、耗时、模型/Prompt/Skill 来源与 hash。repair 使用新 `callId`，通过 `repairOfCallId` 指回原调用。最终失败、中断和 replay 均留痕；replay 不重新调用模型。
- 默认只写元数据，不写完整 Prompt、角色记忆正文或提案正文。`debug=True` 才增加所选观察、计划、Proposal/DecisionTrace、实际 system message/input，以及**通过 Schema 校验的结构化输出**，同时逐行输出到 stderr。无 debug 时不额外序列化这些私有载荷。
- SDK 配置、鉴权头、原始错误 body、不合法模型原文不进入 Trace。debug 文件可能含角色私有信息，仅供开发诊断，不可作为导演、导播或其它角色的可见信息来源。

### 9.2 使用与隔离

```text
projects/<project_id>/.runtime/traces/<world_id>/<execution_id>.jsonl
```

一次 `LocalTrace` 创建独立文件；同 World 可让多个 Agent 共用该 writer。重启另开新文件，不覆盖旧记录。小写规范 ID 保留可读目录名，其余 ID 用保留前缀加 UTF-8 hex 编码，避免路径穿越及 Mac 大小写不敏感导致的目录碰撞；拒绝符号链接重定向。文件权限 0600，已有 `.runtime/` ignore 生效。

已有 smoke 命令默认开启本地 Trace，stderr 打印实际路径，stdout 仍为 `{proposal, traces}` JSON。**以下会调用真实 DeepSeek，要求已有 Key 并消耗 API 额度：**

```zsh
uv run python -m agent_runtime.model_smoke
# 需要查看完整模型输入、角色结果时显式打开：
uv run python -m agent_runtime.model_smoke --debug
```

库调用 `PersonActAgent` 仍需显式传入 `trace_log=LocalTrace(...)`，不传则无文件副作用；不是所有现有调用者自动落日志。生命周期由调用者用 `with LocalTrace(...)` 管理。`--trace-root` 可指定 Project 日志根目录；`--help` 不创建客户端或日志。

每行在阶段发生时写入并 flush，文件内 `seq` 在锁下递增；它是诊断写入顺序，**不是 World 事件顺序或故事叙事时间**。不做 fsync，不承诺断电或强杀零丢失。运行中的写入失败会警告并关闭后续记录，不把已完成的角色决策改成失败；日志目录/文件创建失败则在启动时直接报错。没有日志轮转与保留期清理。

### 9.3 本次验收

- 新增 42 个参数化测试实例，完整 Python 测试 **269 passed in 2.75s**，此前 227 项保留。
- 验证 Project/World/执行文件隔离、大小写和路径碰撞、符号链接、并发 seq、作用域恢复、失败/KeyboardInterrupt、日志故障警告及 debug 隐私边界。
- 验证完整 Agent 决策、失败保留中途阶段、replay 无重复认知调用、外来身份拒绝，以及不开 debug 时不构造私有调试载荷。
- 通过真实 SDK 的 Mock HTTP 验证 429 / 503 / timeout 恰好最多 3 次、重试后恢复、schema/semantic 单次 repair；CLI 失败仍保留 Trace，debug 不污染 stdout JSON。
- `uv run ruff format --check agent_runtime`：42 files already formatted；`uv run ruff check agent_runtime`：通过；`uv run pyright`：0 errors / 0 warnings；`git diff --check`：通过。另验证 CLI `--help`。
- 独立只读 Review 发现并确认修复 Mac 大小写目录碰撞；未新增实网 API 调用、World 存档或 commit/push。Director/Broadcast 尚未实现，本节不宣称其轨迹已接入，也不证明完整多角色 World Runtime 可运行。

## 10. 2026-09-08 追加：本机配置与 Flash 实网验收

- 用户选择持久化本机配置：根目录 `.env` [NEW，不提交] 保存 `DEEPSEEK_API_KEY` 与 `DEEPSEEK_MODEL=deepseek-v4-flash`，权限 0600；`.gitignore` [UPDATE] 增加 `.env` / `.env.*`。已确认文件被忽略且未被 Git 跟踪；不在文档、代码或 Trace 复制 Key。不修改全局 shell 配置，不增加 dotenv 依赖，使用已有 uv 的 `--env-file` 加载。
- 首次非交互请求因旧的同名环境变量覆盖 `.env` 而鉴权失败。只比较两者是否相等，没有输出 Key；改为在子进程清除旧变量，再从 `.env` 加载，成功请求 Flash。下次重启仍可在仓库根目录使用：

  ```zsh
  env -u DEEPSEEK_API_KEY uv run --env-file .env python -m agent_runtime.model_smoke --model deepseek-v4-flash
  ```

- **实网成功：** 退出码 0；Anon 单次决策约 5.83 秒，`score_poignancy → plan → plan_action → plan_action_repair` 共 4 次调用，每次 transportAttempts=1。初稿选择了当前 View 未开放的 `respond`，语义校验触发一次 repair，修正为合法 `utter`，最终对 Soyo 输出台词并携带 `soyo-greeting` evidence。没有修改 Prompt、放宽校验或人工改写模型结果。
- 成功 Trace：`projects/coffee-golden/.runtime/traces/model-smoke/c9836459c06944a289165acc08a4b33c.jsonl`，27 行，traceId=`e67ce284277d45c9a06bb217cf981da2`，以 `decision.end` 结束；默认非 debug。旧变量导致失败的 Trace 文件名为 `b1beb7b0d6054670b7bd2c9041596690.jsonl`。两份文件均已检查无 Key/鉴权头特征，保留作本地诊断，不提交 Git。
- **本机环境注意：** 交互 PTY 中另发现 SOCKS 代理导致 SDK 初始化缺少 `socksio`；本轮实网成功来自不带该 SOCKS 配置的非交互环境。未改动代理或安装额外依赖；若在使用 SOCKS 的终端重现 ImportError，仍需为该执行环境补齐 SOCKS 支持，不能把此问题归因于 Key。
- 本轮仅改本机配置、ignore 与本节记录，未改 Python 业务代码；此前 269 项自动测试记录保持有效，本轮没有重跑全量测试。M1 开发和自动验收已完成、仍待 Review 签收；本次额外证明真实 Flash、严格模型、单次 repair 与本地 Trace 可用，不证明多角色自由互动、World commit、SQLite 或读档续跑。未 commit / push。

## 11. 2026-09-08 提交交接

用户确认推送 M1。提交覆盖基础契约、Plan queue、UnionPart、DeepSeek、轻量 Trace、对应测试与阶段文档，共 34 个文件；不包含 `.env` 或运行日志。提交前再次验证：269 passed in 3.49s，Ruff 格式/Lint、Pyright 与 diff 检查通过。Author / Committer 均核对为 `hypnos <cooiiy@qq.com>`，目标为 `origin/master`；实际提交与远端状态以 Git 历史为准，不预填 hash。M2 尚未开始。
