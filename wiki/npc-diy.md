# NPC DIY：Python + LangChain Core 的受限角色运行边界

## 这是不是我们自己的 ADK？

**是。更准确地说，这是项目自研、面向多角色世界的领域型 NPC ADK。**

它不是重新实现 LangChain，也不是让创作者任意拼图的通用 Agent 平台。两者关系是：

```text
Generative Go World NPC ADK（项目拥有）
  ├── Creator Manifest 与能力白名单
  ├── Manifest -> CompiledPersonActSpec 编译
  ├── Persona 私有 State / Memory / Retrieval
  ├── PersonActAgent.decide 认知语义
  ├── 版本化 Character Skill 与 typed Model Gateway
  ├── ActionProposal 强类型协议与权限校验
  └── Fixture / Golden / Trace 约束

LangChain Core（内部依赖）
  └── Runnable、调用配置以及 ChatModel structured output/Prompt/Tool 接入

World Runtime（ADK 外部权威，待继续实现）
  └── EventSessionRunner、AgentViewBuilder、Validator、WorldUpdater、WorldEventHistory、EventStaff Director
```

判断标准不是“有没有调用 LangChain”，而是谁定义产品契约和领域语义。NPC 怎样声明、能看到什么、如何形成记忆、可以提出哪些 Action、target 是 Character 还是 Object，以及 Proposal 为什么不能直接成为事实，全部由本项目定义，因此它属于我们的 NPC ADK。

当前已经实现的是这套 ADK 的 PersonAct 核心 Slice，不应扩大描述为完整 World Runtime 或成熟通用 SDK。

## 结论与状态

NPC DIY 当前采用一条窄而可审计的路径：创作者只声明角色素材与有限能力，Python Runtime 把不受信配置编译为不可变运行规格，外部 Event Scheduler 再按需调用该角色的 `PersonActAgent.decide`，获得一个候选 Proposal。

```text
Creator agents.json（不受信）
  -> strict Pydantic load
  -> semantic validation + trusted Catalog resolution
  -> frozen CompiledPersonActSpec
  -> Event Scheduler 调用 PersonActAgent.decide
  -> one strict/frozen ActionProposal
  -> optional camelCase wire JSON serialization
  -> Validator -> WorldUpdater（待实现）
  -> committed WorldEvent（待实现）
  -> DirectorRunner 只在提交后管理 EventStaff（待实现）
```

必须区分当前事实：

- **已实现的基础：**Manifest 严格读取、受信编译、Catalog 权限收敛、稳定 digest、Persona 私有 Memory/State 的部分基础能力、版本化 Character Skill 与整文件 hash pin，以及固定 `ActionProposal` envelope + discriminated action union 的 World-owned contract；
- **已实现的 PersonAct Slice：**`PersonActAgent.decide` 已实现 prepare/perceive/retrieve/plan/propose，每次只返回当前角色一个 strict/frozen `ActionProposal`；旧 `PersonActPipeline` 公共门面已退出，`proposal.py` 只保留最终 Proposal 构造与权限校验；
- **已实现的模型接线：**`ModelCognitionStrategy` 通过 typed LangChain ChatModel/Fixture Gateway 实现 poignancy、daily plan 与 action draft；输出显式进入 strict Pydantic 校验，Proposal 再经过 affordance/evidence 语义校验，结构或语义失败最多 repair 一次；
- **尚未实现：**长期 Memory Store、EventStaff Director、WorldUpdater、完整咖啡 Golden Trace、真实 Provider acceptance、Generation Trace 持久化、Dynamic Render ingress、热更新和 UI；
- **不变的权威边界：**外部 EventSessionRunner 拥有循环；PersonAct 只能产生 `ActionProposal`，不能修改 World、WorldEventHistory、其他角色 Memory 或 Render 状态。

这不是完整 Agent Runtime，也不是允许创作者自由拼装任意 Runnable、模型与 Tool 的通用构建器。

## 技术边界

现行 PersonAct Slice 使用：

- Python 3.12；
- `langchain-core==1.6.1`；
- Pydantic 2 strict/frozen Model；
- LangChain `RunnableLambda + RunnableSequence`；
- pyright strict、ruff、pytest 与 uv；
- 当前不使用 LangGraph。

### LangChain 负责什么

| 需求 | 当前机制 | 使用方式 |
|---|---|---|
| `decide` 内部组合 | `RunnableLambda` / `RunnableSequence` | 组织 typed cognition stages，不定义 Agent 生命周期 |
| 公共调用入口 | `PersonActAgent.decide()` | 接收一次 `DecisionRequest`，返回一个 strict/frozen `ActionProposal` |
| 调用上下文 | `RunnableConfig` | run name、tags、metadata 与后续 callback/tracing |
| 策略替换点 | 项目定义的 `CognitionStrategy` Protocol | Fixture 与 ModelCognitionStrategy 使用相同 typed seam |
| 模型/Prompt/Tool | typed LangChain ChatModel Gateway 已接入，生产 Provider 尚未验收 | 只替换受信策略，不扩张 Manifest 权限 |

LangChain 不负责：

- 解析不受信 Manifest；
- 运行时输入/输出校验；
- Memory namespace 授权；
- affordance、target、evidence 与 world-version 语义；
- World Update、WorldEventHistory 或 Render 副作用；
- Event Scheduler 循环、下一角色选择或再次唤醒。

### `with_types()` 不是运行时校验

`Runnable.with_types()` 只绑定或暴露 Runnable 的输入/输出类型信息，便于生成 Schema、组合与观测；它不会保证 `invoke()` 收到或返回的是合法 Pydantic 对象。当前实现没有把它当校验器。

真正的边界是：

1. 外部 `agents.json` 通过 `model_validate_json(..., strict=True)` 进入 strict Pydantic Model；
2. 所有契约继承统一 `StrictModel`，拒绝未知字段和隐式类型转换，并保持 frozen；
3. 每个 Runnable 节点的 callable 显式声明输入/输出，由 pyright strict 检查；
4. 模型输出已在 Gateway 显式 parse 成 strict Pydantic Model；后续 Tool 输出也必须遵守同一边界；
5. Proposal 再由领域代码检查 capability、affordance、target 与 visible evidence。

`RunnableSequence` 内部会擦除部分中间泛型，因此不能仅凭最外层泛型宣称整条链已经在运行时自动验型；更不能把 Runnable 本身当作 Persona 认知实现已经完成的证据。

### 为什么当前不用 LangGraph

`decide` 是一次短、有界的决策调用，没有必要为它引入图状态、checkpoint 与迁移成本。当前内部使用 typed Runnable 组织阶段，但它不拥有外部循环。若未来出现经过验证的复杂分支、暂停恢复或长生命周期执行需求，再单独评估 LangGraph。即使引入，图状态也不能替代 EventSessionRunner、Persona 长期 Memory、SQLite 当前状态、WorldEventHistory 或 commit protocol。

## 创作者配置

Manifest v2 在项目内约定 `projects/<project-id>/agents.json`。创作者可以配置 Persona、初始私有信念、受限 Proposal 类型、受信 Tool 引用、有限行为参数、Character Skill 和 Prompt Profile；加入必填 `characterSkill` 是破坏性变更，因此旧 v1 输入显式拒绝：

```json
{
  "formatVersion": 2,
  "projectId": "coffee-golden",
  "agents": [
    {
      "id": "anon",
      "displayName": "千早爱音",
      "persona": {
        "identity": "月之森转学生，主动但会掩饰不安",
        "traits": ["外向", "在意关系反馈"],
        "goals": [
          {"id": "keep-conversation", "description": "维持与爽世的自然交流"}
        ],
        "relationships": [
          {"targetId": "soyo", "description": "希望靠近，但会观察对方反应"}
        ],
        "voice": {
          "style": "轻快、会试探性确认",
          "avoid": ["上帝视角陈述", "替其他角色决定"]
        }
      },
      "memory": {
        "seeds": [
          {
            "id": "knows-soyo",
            "type": "background",
            "content": "爱音认识爽世，但不确定爽世当前真实想法",
            "tags": ["soyo", "relationship"]
          }
        ],
        "retrieval": {"recentLimit": 8, "tagLimit": 8},
        "writePolicy": ["observation", "committed_outcome"]
      },
      "capabilities": {
        "proposalKinds": ["interact", "utter", "respond", "wait", "no_op"],
        "tools": [
          {"id": "visible_location.query", "maxCallsPerRun": 1}
        ]
      },
      "behavior": {
        "initiative": "balanced",
        "responsePriority": "addressed_first",
        "maxContextRounds": 1,
        "reflection": "off"
      },
      "characterSkill": {"skillId": "mygo.character.anon", "version": "3.0.0"},
      "promptProfile": "personact.v1"
    }
  ]
}
```

字段边界：

- `persona` 是角色素材，不是安全策略；自由文本不能替换系统指令；
- `memory.seeds` 是角色初始信念，不是 World Fact，Compiler 为其生成 provenance；
- `capabilities.proposalKinds` 是最大能力集合，运行时还要与当前 Frame affordance 取交集；
- `tools` 只能引用受信 Catalog 中的 query/compute Tool；
- `behavior` 只暴露有限枚举和小预算；
- `characterSkill` 只引用受信 Catalog 中的 exact version；Compiler 再固定整文件 SHA-256；
- `promptProfile` 只引用受信模板，具体版本和 digest 由 Compiler 解析。

Manifest Persona 保存实例身份、当前目标、关系事实与硬权限；Character Skill 保存跨场景稳定的气质、驱动力、判断倾向和表达风格；Prompt Profile 保存后端任务模板与输出契约。三者共同进入 `CompiledPersonActSpec` digest，但拥有不同职责和演化周期。Skill 可以影响主观判断与表达，不能覆盖 Manifest Persona、World Frame、affordance 或权限。当前 Compiler 已固定 Prompt Profile 的 ID/version/digest，但受信 Prompt 模板正文的 Catalog 加载与渲染尚未实现，`ModelCognitionStrategy` 仍使用代码内任务模板。

Manifest 不提供：

- Memory namespace 或跨角色读取范围；
- World current state、WorldEventHistory 或 world-version 写权限；
- model/provider/API key；
- URL、请求头、脚本、任意函数或任意 MCP server；
- WorldUpdater、Render Gateway 或 WebGAL 命令；
- Fixture/生产模式选择；
- Runnable/Graph 拓扑。

Fixture 与生产模型的绑定属于运行环境，不能由创作者越权选择。

## 受信编译

Manifest 不能直接进入 `PersonActAgent`：

```text
strict Pydantic decode
  -> format / ID / relationship validation
  -> proposal kind / budget validation
  -> Tool Catalog allowlist + read-only check
  -> Prompt Catalog exact-version resolution
  -> Character Skill exact-version resolution + whole-file hash pin
  -> derive project/{project_id}/persona/{agent_id} memory scope
  -> canonicalize + SHA-256 digest
  -> frozen CompiledPersonActSpec
```

关键规则：

1. 未知字段与错误类型直接失败；
2. namespace 固定派生为 `project/{project_id}/persona/{agent_id}`，避免不同作品的同名角色碰撞；
3. Catalog 中存在但带 mutation 权限的 Tool 仍必须拒绝；
4. Prompt 与 Character Skill 只能引用精确受信版本，不能内联 system prompt；
5. Skill 的整文件 hash 进入 compiled spec digest；同版本源码与绑定不一致时，在模型调用前拒绝；
6. 超预算报错，不静默截断；
7. canonical digest 写入 Trace，一个 Runtime Session 固定一个 digest；
8. MVP 不写第二份 compiled 文件，避免源配置与生成物漂移。

Pydantic 负责结构约束；关系引用、Catalog 权限、Prompt 解析和能力收敛仍由 Compiler 显式完成。

## `PersonActAgent.decide` 与 Proposal JSON

冻结后的公共入口是一次性 typed 调用：

```python
proposal = person_act_agent.decide(request)
proposal_json = proposal.model_dump_json(by_alias=True)
```

`DecisionRequest` 公开字段只有 `proposalId` 与 `frame`。`CompiledPersonActSpec`、Persona state、Memory、CognitionStrategy 和 embedding provider 都由受信 `PersonActAgent` 实例持有，调用方不能在单次请求中替换身份或权限。

调用语义固定为：

```text
Event Scheduler 选择 agent + committed frame
  -> PersonActAgent.decide(DecisionRequest)
  -> 校验本人 scope、capability、affordance、typed target 与 evidence
  -> 注入 agentId = spec.agent_id
  -> 输出一个 strict/frozen ActionProposal
  -> 跨 wire 时显式序列化为 camelCase JSON
  -> 返回 EventSessionRunner / Validator / WorldUpdater 链
```

`decide` 不选择下一角色、不继续下一 tick、不等待提交，也不移动角色。内部 Runnable 只组织 prepare/perceive/retrieve/plan/propose；所有中间值使用 frozen Pydantic Model，不跨步骤共享可变 dict。`RunnableConfig` 只携带调用观测上下文，不携带 Scheduler 或 World 写权限。

公开 envelope 固定为：

```json
{
  "proposalId": "proposal-001",
  "agentId": "anon",
  "eventSessionId": "cafe",
  "basedOnWorldVersion": 7,
  "action": {
    "kind": "interact",
    "target": {"kind": "character", "id": "soyo"},
    "description": "把菜单递给爽世"
  },
  "evidenceIds": ["soyo-visible"]
}
```

`action` 是以 `kind` 为 discriminator 的 union，固定 `act / interact / utter / respond / wait / no_op`。`interact.target` 恰好一个，显式区分 `{kind: character, id}` 与 `{kind: object, id}`；`utter/respond.target` 必须是 character。契约不包含 `move`、`locationId` 或多目标 `targetIds`。

`no_op` 是 scheduler yield，不需要世界 affordance，但必须带 `next_wakeup`。其它 Proposal 必须匹配当前 Frame affordance；所有 evidence 必须来自 `visible_evidence_ids`。

这些检查仍只是 Agent 边界。Proposal 通过后还要经过 WorldChangeValidator 和 WorldUpdater；这些完整链路当前尚未实现。Director 不在 Character 提交链中，它只在 WorldEvent committed 之后读取受限 DirectorView 并管理 EventStaff。

## 当前实现证据与限制

当前实现锚点：

- `agent_runtime/model.py`：统一 strict/frozen Pydantic policy；
- `agent_runtime/agent/personact/manifest.py`：不受信配置模型与 loader；
- `agent_runtime/agent/personact/compiler.py`：Catalog 解析、权限收敛与 digest；
- `agent_runtime/agent/memory/` 与 `agent_runtime/agent/personact/state.py`：共享 Memory 机制与 Persona 私有 State；
- `agent_runtime/agent/personact/agent.py`：`PersonActAgent.decide` 门面、调用串行化、replay 与 immutable private snapshot 原子替换；
- `agent_runtime/agent/personact/loop.py`：真实 typed prepare/perceive/retrieve/plan/propose 认知循环；
- `agent_runtime/agent/personact/model_strategy.py`：模型型 CognitionStrategy 与最多一次 repair；
- `agent_runtime/agent/skill.py`：strict Runtime Skill loader、Catalog 与整文件 hash pin；
- `agent_runtime/model_gateway.py`：typed structured-output Gateway、transport retry 与非秘密 trace；
- `agent_runtime/world/contracts.py`：固定 Proposal envelope、action union 与 typed target；它不是完整 World 实现；
- `agent_runtime/agent/personact/proposal.py`：最终 Proposal 构造、actor 注入与 capability/affordance/evidence 校验。

当前可以陈述的实现事实：

- 相同配置产生稳定 digest；
- Character Skill 的 ID、version 和整文件 hash 进入 compiled spec；
- project/format version 进入 digest，Memory scope 由系统按 project + agent 派生；
- 未知字段、类型 coercion、写 Tool和未知 Prompt 有 strict/semantic 拒绝边界；
- Proposal contract 已表达固定 envelope、六类 action 与 character/object typed target。
- `decide` 已实现确定性 attention、全 EVENT stream canonical novelty、受 write policy 约束的本人 Memory 写入、本轮全部 Observation 与历史 memory 隔离的 literal + ranked retrieval、显式 touch、部分 schedule/current-slot remaining duration、private state/focus 与单 Proposal 产出；
- Proposal 的 `agent_id` 由 `spec.agent_id` 注入；只有 Proposal 通过校验后才原子替换本人的 state/memory。
- Fixture Gateway 与 LangChain ChatModel adapter 共用同一强类型契约；schema 与 Proposal 语义错误最多修复一次，transport retry 不被当作语义 repair。

尚未完成：

- reflection 与 commit feedback；未提交 Proposal 不能假触发 committed-outcome reflection；
- 长期 Memory、World/Event 存储与提交；
- EventStaff Director、AgentViewBuilder、Validator、WorldUpdater；
- 生产 Provider 调用与输出质量验收；
- Generation Trace 的 append-only 持久化；当前 strategy 只在有界进程内保留不含正文的 `ModelCallTrace`；
- 多 Event 调度和跨实体隔离；
- Broadcast、RenderJob 投递与 Viewer 状态；
- 热更新、UI 或通用 Agent Builder。

## 下一步

1. 保持 `PersonActAgent.decide` 与固定 Proposal contract，补提交后的 outcome memory / conditional reflection；
2. 实现外部 Event Scheduler，证明循环、actor 选择和 wakeup 不进入 Agent；
3. 完成咖啡场景的 AgentViewBuilder、Validator、WorldUpdater、EventStaff Director 和 Golden Trace；
4. 将现有模型 Strategy seam 接到真实 Provider 做 shadow run；
5. shadow 稳定后再允许 gated commit，并把当前非秘密调用 provenance 接入 append-only Generation Trace；
6. 分别实现 EventStaff Director 与 Broadcast，不复制 PersonAct 内部拓扑，也不预设共用 CognitiveController；
7. 只有出现真实复杂状态编排需求时才重新评估 LangGraph；NPC DIY Manifest 不随之扩权。

第一版不做可视化编辑器、热更新、任意 MCP/远程插件、创作者自选 provider、向量库、Marketplace 或 Director/Broadcast DIY。

代码落地后按 `AGENT.md` 运行格式、lint、pyright strict 与 pytest。
