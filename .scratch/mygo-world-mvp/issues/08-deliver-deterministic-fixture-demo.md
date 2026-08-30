# 08 — 交付可重复的本地 Fixture MVP Demo

**What to build:** 提供一个无需网络或外部 MyGO 安装即可运行的完整本地演示，从新 Scenario Seed 初始化 World、推进多 Character Session、处理分裂并增量渲染，且在重复运行时产生完全一致的规范结果。

**Blocked by:** 04 — 跨 Batch 调度 EventSession lineage; 05 — 让私有 Memory 与 Runtime Skill 跨 Batch 生效; 07 — 实现增量编排与可恢复 Render 发布.

Status: ready-for-agent

**Execution goal:** `../goals/08-deliver-deterministic-fixture-demo.md`

- [ ] `demo` 只针对不存在的新 World 串联初始化、推进和渲染，并返回包含 World、Batch、版本和产物信息的稳定 Receipt。
- [ ] 相同 World ID 已存在时 `demo` 安全失败，不续跑、不删除也不覆盖已有 World。
- [ ] Fixture 使用版本化目录组织；场景和调用清单使用 YAML，结构化 Agent 响应使用 JSON。
- [ ] Fixture 响应通过 Agent type、Agent ID、Batch/Wave 和 call kind 定位，并断言规范化输入 hash。
- [ ] 测试注入固定时钟、UUID 生成器、随机种子和所有 Character/Director/Broadcast 响应。
- [ ] 主 Fixture 从空目录完整执行 CLI `init → advance → render`，不绕过任何生产 Validator、Repository、Committer、Recognizer、Projector、Planner、Compiler 或 Gateway。
- [ ] 两次独立运行产生完全相同的规范 Ledger、Snapshot、Memory、Broadcast Plan 和 WebGAL 脚本文本。
- [ ] 两次独立运行产生完全相同的 Snapshot checksum 和脚本内容 hash。
- [ ] Golden 比较基于规范领域导出而不是 SQLite 原始文件字节。
- [ ] 分裂 Fixture 验证旧 Session 关闭、所有后继确定性入队、感知隔离及下一 Batch 从队首继续。
- [ ] 故障 Fixture 验证 Validator 最终失败、事务回滚、请求预算耗尽、取消恢复和 Render 发布恢复。
- [ ] 默认 pytest 不读取 Provider 凭据、不访问网络，也不依赖外部 MyGO 资源目录。
- [ ] 完整 Python 测试套件通过。
- [ ] 现有 Node 测试套件通过。
