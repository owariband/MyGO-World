---
skill_id: mygo.broadcast.live
version: 1.1.0
agent_kind: broadcast
---
将已提交的事件编排成一个简洁、自包含的 Render。为每个 frontier Event 给出且只给出
一个 disposition，并纳入所有能够忠实呈现的事件。所有面向玩家的标题、旁白和其他
文本均使用自然的简体中文。

Render 开头依次安排章节、`bgm` 或 `stop_bgm`，以及经过批准的背景。角色说话前必须
先显示该角色。对白必须逐字复制已提交的 utterance，并且只引用对应的 Event。每个
included Event 都必须被引用；旁白只能概括它所引用的事实。

只使用 `asset_candidates` 中提供的 ID。优先使用 `background-ring-lounge`、
`bgm-mygo-title`、`model-anon-live` 和 `model-soyo-live`，并且只使用清单中列出的
motion、expression 和 entrance effect。render ID 与 beat ID 使用全小写形式。
只返回要求的结构化对象。
