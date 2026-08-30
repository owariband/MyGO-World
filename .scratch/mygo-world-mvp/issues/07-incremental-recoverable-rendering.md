# 07 — 实现增量编排与可恢复 Render 发布

**What to build:** 让编辑者能够跨 Batch 和 Event Session 增量剪辑已提交历史，生成一个或多个有来源的 Render，并让重复调用、并发调用及文件/数据库之间的中断都不会重复消费或损坏发布结果。

**Blocked by:** 04 — 跨 Batch 调度 EventSession lineage; 06 — 将已提交 Event 编译为首个 WebGAL Render.

Status: ready-for-agent

- [ ] Broadcast frontier 按目标 World Version 内缺少 Disposition 的 Event 计算，不按 Generation Batch 切分。
- [ ] 每个 frontier Event 恰好得到一个不可变 `included` 或带原因的 `omitted` Disposition。
- [ ] 每个 included Event 至少被一个 Beat 引用，遗漏来源或重复处理会被确定性拒绝。
- [ ] Broadcast 可以省略、压缩、重排、交错不同 Session 或引用旧 Event 倒叙，但不能新增事实、泄露当时不可知信息或制造虚假因果。
- [ ] 旧 Event 可以作为上下文或显式回顾来源，但不能在没有新 frontier Event 时单独触发 Render。
- [ ] 一次 Broadcast Run 可以产生多个有序、不可变、自包含 Render；每个默认不超过 40 Beat 和预计八分钟。
- [ ] 无新 Event 时 `render` 返回成功 `no_work`，不调用模型、不创建空 Render 或 Disposition。
- [ ] 每个 World 的 Render 锁会串行化两个 Render 调用，同时允许 Render 基于固定版本与后续 `advance` 并存。
- [ ] Render Gateway 在目标文件系统内先写临时文件，再原子重命名到由 World、Render ID 和内容 hash 确定的不可变目标。
- [ ] 所有文件发布成功后才通过短 SQLite 事务记录 Render 元数据和 Broadcast Disposition。
- [ ] 进程在文件发布后、数据库收尾前中断时，重试会验证并复用 hash 相同的文件，再补齐数据库记录。
- [ ] 不可变目标已存在但内容 hash 不匹配时，重试安全失败且不写 Disposition。
- [ ] 数据库唯一约束阻止同一个 Event frontier 被重复消费。
- [ ] 任一 Render 校验或发布失败都不会消耗 frontier，之后可以安全重试。
