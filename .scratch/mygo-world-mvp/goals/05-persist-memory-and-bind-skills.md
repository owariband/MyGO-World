Goal: 完成 ticket 05，使版本化 Runtime Skill 与角色私有 Agent Memory 在多次 Generation Batch 和进程重启之间可靠生效

Completion criteria:
- [ ] Runtime Skill 从 `content/skills/**/*.md` 加载 YAML frontmatter 与 Markdown 正文，并校验稳定 skill ID、声明版本和 `character`、`director` 或 `broadcast` Agent kind。
- [ ] Runtime Skill 的结构化元数据不接受模型配置、凭据、权限或工具配置，Agent Contract 与进程环境仍是这些能力的唯一来源。
- [ ] Scenario Seed 能为每个 Character 以及全局 Director、Broadcast 绑定精确的 Runtime Skill ID 与声明版本。
- [ ] `init` 会校验所有 Seed Skill 引用，并把初始绑定的声明版本与内容 SHA-256 持久化到新 World。
- [ ] Character、Director 和 Broadcast 的模型请求实际使用各自当前有效的 Runtime Skill 正文，而不是代码中的硬编码占位文本。
- [ ] 每次 Character、Director 和 Broadcast 模型调用的 Generation Trace 都记录该次调用最终有效的 skill ID、声明版本和内容 SHA-256。
- [ ] 已绑定 Skill 在声明版本不变但文件内容 hash 改变时，`advance`、`render` 或 `skill-bind` 会在发起模型调用或改变绑定前以稳定错误安全失败。
- [ ] Scenario Seed 能初始化同一角色的多条原子 Observation、Belief 和 Commitment，并在新进程中完整读回类型、namespace、时间、重要度、实体标签、地点标签、来源与状态字段。
- [ ] 所有 Memory 读取与候选变更校验都强制限定 `agent_id + namespace`，测试证明 Character 无法读取、supersede、完成或取消其他 Character 的 Memory。
- [ ] Belief 更新只追加带合法 `supersedes_memory_id` 的新记录，被引用记录保持不变。
- [ ] Commitment 完成或取消只追加带状态与前序引用的新记录，被引用记录保持不变。
- [ ] Memory 检索始终包含当前活跃 Commitment，并能以确定性顺序按角色、namespace、相关实体/地点标签、相对时间和重要度执行有界选择。
- [ ] 成功 Wave 将 Observation、Belief 和 Commitment changes 与对应 World Version 在同一事务中持久化。
- [ ] Memory 持久化阶段失败时，当前 Wave 不留下 World Version、Ledger、Observation、Belief 或 Commitment 的部分提交，失败候选只保留在 Generation Trace。
- [ ] `skill-bind` 为指定 Character 追加不可变审计记录，包含 World、Character、操作者、原因、旧 Skill 版本/hash、新 Skill 版本/hash 和绑定时间。
- [ ] `skill-bind` 成功前后 World Version 保持不变，并同时提供稳定 JSON Receipt 与简洁人类输出。
- [ ] 新 Character Skill 绑定只对下一 Generation Batch 的第一个 Wave及后续调用生效；已启动 Batch 的所有 Wave 始终使用 Batch 启动时固定的绑定。
- [ ] 进程重启后，新绑定和 Memory 检索结果保持有效，旧 Generation Trace、旧 Memory 与既有 World Ledger 不被追溯修改。
- [ ] Alembic 能从空数据库升级到最新 Schema，非初始化命令仍拒绝旧 Schema，新增 Skill/Memory 历史记录由数据库约束或触发器拒绝原地更新和删除。
- [ ] `uv run pytest`、Ruff check/format、Python compileall 和现有 Node 测试均成功退出。

Constraints:
- 不实现 ticket 04 的 Event Session lineage 分裂/合并调度，也不改变 FIFO 与 World Version 语义。
- 不实现自动 Character Profile Revision、自动 Memory Reflection、向量数据库、embedding 检索或 Agent 自行修改 Runtime Skill。
- Runtime Skill 只承载自然语言创作配置；不得从 Skill 获取 Provider/model 参数、凭据、输入权限、工具白名单或其他执行能力。
- Agent Memory 保持 append-only 且不属于 World Ledger；World Committer 仍是 Observation、Belief 与 Commitment 的唯一提交者。
- `skill-bind` 使用同一 World mutation lock，只允许在 Batch 之间生效，不生成 World Event、Entity Revision 或新 World Version。
- 默认 Fixture 不发起网络请求，不引入新的 Provider/Agent 框架，并保留 ticket 01、02、03、06 与现有 Node 工具行为。
- 不修改与 ticket 05 无关的用户文件；保留未跟踪的 `skills-lock.json`。

Context:
- `.scratch/mygo-world-mvp/spec.md`
- `.scratch/mygo-world-mvp/issues/05-persist-memory-and-bind-skills.md`
- `MVP.md`
- `CONTEXT.md`
- `docs/adr/0009-agent-memory-is-private-and-post-commit.md`
- `docs/adr/0010-runtime-skills-are-creative-configuration.md`
- `docs/adr/0012-pin-generation-provenance.md`
- `docs/adr/0018-worlds-persist-across-batch-processes.md`
- `docs/adr/0021-use-an-explicit-model-gateway.md`
- `docs/adr/0022-validate-before-atomic-world-commit.md`
- `src/mygo_world/contracts.py`
- `src/mygo_world/perception.py`
- `src/mygo_world/committer.py`
- `src/mygo_world/runtime.py`
- `src/mygo_world/broadcasting.py`
- `src/mygo_world/cli.py`
- `src/mygo_world/db/models.py`
- `src/mygo_world/db/migrations/versions/`
