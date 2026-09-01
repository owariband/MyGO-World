# Runtime Skill 承载创作配置，后端契约执行权限

Character、Director 和 Broadcast 分别加载版本化 Runtime Skill，以自然语言配置人物人格、动机、关系、形象或创作风格；这些 Skill 与 Codex 的 `.agents/skills` 无关，也不作为权限机制。后端 Agent Contract 独立决定输入投影、工具白名单、Pydantic 输出 Schema 和校验规则，并通过一个统一运行接口封装 Skill 加载、Prompt 组装、模型调用与结果校验。

Prompt 按职责组合为后端拥有的 Agent Contract、版本化 Runtime Skill、场景或 Session 策略，以及当前 Runtime Context。结构字段、稳定 ID、可见性、来源证明和权限属于 Agent Contract：能由后端确定的值由后端提供，其余值由 Schema 与 Validator 校验。自然语言可以向模型解释这些约束，但不能成为唯一执行机制。Runtime Skill 不重复字段复制规则，也不根据具体 World Version、实体 ID 或测试步骤编排动作。

版本控制中的 Runtime Skill 使用带 YAML frontmatter 的 Markdown：frontmatter 保存稳定 ID、声明版本和 Agent kind，正文保存自然语言创作配置；模型、密钥、输入权限和工具配置不得写入 Skill。

Character Skill 保留跨场景稳定的人格、长期驱动力、关系倾向、判断方式和表达风格；运行经历进入 Agent Memory，当前故事目标与收束条件属于 Scenario Policy。Director Skill 保留群像叙事、节奏、冲突与后果等创作取向；角色来源、时间、因果和提交条件仍由 Agent Contract 与 Validator 负责。显式验收允许使用用途明确的 `-live` Acceptance Skill 强制覆盖某个动作或轮次，但不得把它当作正式人格配置。

MVP 不实现 Character Profile Revision 或 Agent 自生成人格。运行中的经历和行为变化由 Agent Memory 承载；如需人工调整人格，操作者创建具有新声明版本和内容哈希的 Character Skill，再显式将单个 World 重绑到该版本。重绑记录操作者、原因和前后版本，并从下一 Generation Batch 的第一个 Wave 生效；修改 Skill 内容却不提升版本会被视为哈希冲突，已有 World 不自动跟随仓库中的最新版。

Skill 重绑追加独立的 Agent 配置绑定记录，不生成 World Event、Entity Revision 或新的 World Version；正在运行的 Batch 持有 World 独占锁，因此绑定只能在 Batch 之间改变。
