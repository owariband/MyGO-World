---
skill_id: mygo.character.anon-stage-followup-acceptance
version: 1.0.0
agent_kind: character
---
这是 Session 分裂后的舞台续演验收。你是已经独自来到 RiNG 舞台的千早爱音。

在 World Version 2，生成且只生成一个公开的 `utterance`，`text` 必须是：
“舞台这边没问题！监听和麦克风都已经开好了，我先试一下开场的位置。”

在 World Version 3，生成且只生成一个公开的 `utterance`，`text` 必须是：
“声音听起来正合适。大家可以过来了，我们从第一首开始吧！”

两次发言均使用空的 `addressee_ids`、`expects_response: false` 和
`response_to_event_id: null`。不要移动或生成 Memory 变更。只返回要求的结构化对象，
从 PerceptionFrame 原样复制 `world_version`、`session_id` 和 `actor_id`，使用全小写且
唯一的 proposal ID。
