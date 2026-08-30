# World 跨 CLI Batch 持续存在

每个命名 `world_id` 拥有独立、持续使用的 SQLite 数据库；多次 Generation Batch 以不同 `run_id` 从最新 World Version 继续追加，而不是每次启动创建新世界。同一 `run_id` 不得重复提交；失败或中断后从最后一个完整 World Version 恢复，已提交 Wave 保留，当前未提交 Wave 回滚。

CLI 将世界推进与演出构建分开：`init` 从 Scenario Seed 创建 World，`advance` 有界推进，`render` 对指定 World Version 的 Ledger/Snapshot 只读并生成衍生产物，随后单独写入 Render 元数据和 Broadcast Disposition；`demo` 作为便利命令串联三者。Render 构建先写本地规范产物，再通过目标目录临时文件和原子重命名发布；若发布后数据库收尾前中断，重试根据不可变内容 hash 复用文件并补全记录，因此失败不会改变 World，也不会把未登记的可变文件误认为新的完成脚本。

`demo` 是面向人工验收的便利编排而不是测试框架；它只接受尚不存在的 `world_id`，重名时安全失败，不自动续跑或删除旧 World。自动断言由 pytest 承担，持续推进已有 World 必须显式使用 `advance` 和 `render`。

首期不提供常驻 Runtime 的 `start/stop`；`advance` 是有界前台进程，完成或失败后退出。收到 SIGINT/SIGTERM 时取消在途模型请求、回滚当前未提交 Wave、将 Batch 标记为 `cancelled` 并以非零状态退出；强制终止留下的 `running` 记录由下一次启动标记为 `interrupted`，恢复点始终是最后一个完整 World Version。

CLI 默认输出简洁人类摘要，并统一支持 `--json` 结构化 Receipt，其中包含 World、Batch、起止版本、状态、产物路径和错误码。没有 Runnable Session 的 `advance` 或没有新 Event 的 `render` 返回成功的 `no_work`，不调用模型，也不创建空事实或空 Render。
