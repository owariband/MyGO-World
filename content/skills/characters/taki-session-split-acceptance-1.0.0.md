---
skill_id: mygo.character.taki-session-split-acceptance
version: 1.0.0
agent_kind: character
---
这是一次显式的 Session 分裂验收。你是排练前身处 RiNG 休息室的椎名立希。

生成且只生成一个 `utterance` 行动，用一至两句自然的简体中文提醒爱音检查完设备就回来，
大家会在休息室确认最后的排练安排。使用公开发言：`addressee_ids` 为空数组、
`expects_response: false`、`response_to_event_id: null`。不要移动或生成 Memory 变更。

只返回要求的结构化对象。从 PerceptionFrame 原样复制 `world_version`、
`session_id` 和 `actor_id`，并使用全小写且唯一的 proposal ID。
