---
skill_id: mygo.broadcast.session-split-performance
version: 1.0.0
agent_kind: broadcast
---
把 Session 分裂前后的全部 frontier Event 编排成一个连贯、自包含的正常演出。
所有 Event 都必须标记为 `included` 并由 Beat 引用，不得省略；只生成一个 Render。

严格采用以下演出结构：

1. 章节、BGM、`background-ring-lounge`；
2. 依 World Order 演出休息室中的所有 `utterance`，每句分别使用一个 `dialogue`
   Beat，并在说话前显示对应角色；
3. 用 `narration` 忠实呈现爱音的 `move` Event；
4. 随即切换到 `background-ring-stage`，显示爱音；
5. 依 World Order 演出舞台 scope 中爱音的每个 `utterance`，每句分别使用一个
   `dialogue` Beat。

所有对白必须逐字复制对应已提交 Event，并只引用该 Event。不要添加未发生的事实、
动作、台词或因果。只使用 `asset_candidates` 提供的 ID 与 Live2D 能力。render ID 与
beat ID 使用全小写形式，所有面向玩家的文字使用自然的简体中文。只返回要求的结构化对象。
