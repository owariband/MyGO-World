---
skill_id: mygo.broadcast.session-split-acceptance
version: 1.0.0
agent_kind: broadcast
---
将 Session 分裂前已经提交的全部 frontier Event 编排成一个连贯、自包含的 Render。
所有 Event 都必须标记为 `included` 并由 Beat 引用，不得省略。

Render 开头依次安排章节、BGM 和 `background-ring-lounge`。每个 `utterance` Event
必须生成且只生成一个 `dialogue` Beat，逐字复制原台词并引用该 Event；说话角色必须
先用对应 Live2D model 显示。`move` Event 用一句简短旁白忠实呈现。保持 World Event
顺序，不添加未发生的动作、台词或因果。

只使用 `asset_candidates` 提供的 ID、motion、expression 和 entrance effect。
render ID 与 beat ID 使用全小写形式，所有面向玩家的文字使用自然的简体中文。
只返回要求的结构化对象。
