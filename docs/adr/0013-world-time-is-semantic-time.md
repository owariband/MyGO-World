# World Time 不随模型调用耗时推进

MVP 的 World Time 完全由 Director Resolution 提议的受限相对剧情语义时长与 Runtime 校验结果决定；Segment Assembler 以 Snapshot World Time 为起点推导绝对 Wave 时间。模型调用的墙钟耗时只记录在 Generation Trace 中，用于性能、成本和失败诊断，不作为 World Time 的约束或证据。本决策取代 `MVP.md` 与旧 Wiki 中“实际模型耗时参与事后时间绑定”的规则，使相同剧情不会因供应商延迟或网络波动获得不同世界时间。

权威值使用从 Scenario 起点计算的非负整数毫秒；Scenario 可以提供带时区的 ISO 日历锚点用于 CLI、Trace 和演出显示，但日历字符串不参与排序、时长或因果校验。World Event 保存开始与结束毫秒，避免浮点误差和字符串时区歧义。
