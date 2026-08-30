# Generation Trace 固定 Skill 与模型来源

Scenario Seed 显式引用每个 Runtime Skill 的版本，每次模型调用在 Generation Trace 中记录 Skill 内容哈希、模型 ID、模型参数、输入引用、原始结果和校验结果。数据库无需复制 Skill 的全部正文，但必须能精确定位本次运行使用的内容，从而区分世界重放的确定性与模型生成的可追溯性。

未提交的 Action Proposal 和 Segment Draft 作为带 Schema 版本的结构化 JSON 保存在对应 Generation Trace 中，已提交 Segment 通过 Trace ID 引用其生成证据，不为候选产物建立第二套权威领域表。Trace 还保存完整模型输入、原始响应、结构化结果、重试与修复诊断；API 密钥和认证头永不落盘，终端默认只显示脱敏摘要。
