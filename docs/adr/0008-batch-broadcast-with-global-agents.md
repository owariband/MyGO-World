# 在固定 World Version 上执行增量编排

Broadcast 不按 Generation Batch 切分输入。每次显式 `render` 在固定目标 World Version 上选择所有尚无 Broadcast Disposition 的已提交 World Event，不论它们由哪个 Batch 产生；更早历史只作为上下文，除非为倒叙或回顾而显式再次引用。没有新 Event 时不调用模型，也不生成空 Render。

系统使用一个全局 Broadcast，因此一次 Broadcast Run 可以跨 Event Session 编排；全局 Director 也服务所有 Session，但每次调用使用 Session 隔离上下文，两者职责和记忆命名空间保持分离。只有全部 Render 校验并发布成功后，才为本次候选 Event 原子记录 `included` 或带原因的 `omitted` Disposition；失败不得消耗增量 frontier。
