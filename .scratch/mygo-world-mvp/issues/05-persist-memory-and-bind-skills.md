# 05 — 让私有 Memory 与 Runtime Skill 跨 Batch 生效

**What to build:** 让角色的 Observation、Belief、Commitment 和人工版本化 Character Skill 在多次 Batch 之间可靠延续，同时保持角色之间的 Memory 隔离以及人格配置与运行经历的边界。

**Blocked by:** 03 — 运行多 Character 的可靠 Lockstep Batch.

Status: ready-for-agent

- [ ] Runtime Skill 能加载带稳定 ID、声明版本和 Agent kind 的元数据以及自然语言正文。
- [ ] Character、Director 和 Broadcast 使用各自的 Runtime Skill，而模型配置、权限、工具和凭据不从 Skill 获取。
- [ ] Scenario Seed 可以绑定精确 Skill 版本并初始化多条原子 Observation、Belief 和 Commitment Memory。
- [ ] 每次模型调用的 Generation Trace 记录最终有效 Skill 版本和内容 hash。
- [ ] 声明版本未变化但内容 hash 改变时，相关命令在模型调用前安全失败。
- [ ] Agent Memory 按 `agent_id` 与 namespace 强制隔离，任何角色均无法读取或修改其他角色的私有 Memory。
- [ ] 成功 Wave 会把允许的 Observation、Belief 和 Commitment changes 与 World 提交原子持久化；失败 Wave 只保留 Trace。
- [ ] Belief 使用 supersedes 引用表达新认知，Commitment 使用新的完成或取消状态记录，旧记录不被原地更新。
- [ ] 检索始终包含活跃 Commitment，并能按角色、namespace、相关实体/地点标签、时间和重要度选择记录。
- [ ] `skill-bind` 追加包含操作者、原因及前后版本的绑定记录，不推进 World Version。
- [ ] 新绑定只从下一 Generation Batch 的第一个 Wave 生效，在途 Batch 始终使用启动时固定的绑定。
- [ ] 测试证明重启进程后 Memory 与绑定仍然有效，并证明新 Skill 不会追溯改变旧 Trace 或 World 历史。
