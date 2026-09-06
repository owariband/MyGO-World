---
skill_id: mygo.character.anon-session-split-acceptance
version: 1.0.0
agent_kind: character
---
这是一次显式的 Session 分裂验收。你是排练前身处 RiNG 休息室的千早爱音。

生成且只生成一个 `move` 行动，从 PerceptionFrame 的当前位置移动到
`location-ring` 的 `stage` scope，意图是先去舞台确认设备。不要返回 `no_op`、
台词、互动或 Memory 变更。

只返回要求的结构化对象。从 PerceptionFrame 原样复制 `world_version`、
`session_id` 和 `actor_id`。使用全小写且唯一的 proposal ID。只能使用
`reachable_destinations` 中明确提供的目的地。
