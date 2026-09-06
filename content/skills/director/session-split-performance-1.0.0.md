---
skill_id: mygo.director.session-split-performance
version: 1.0.0
agent_kind: director
---
这是 Session 分裂与舞台续演的显式验收。将每个非 `no_op` Character Proposal 忠实
转换成且只转换成一个 `proposal_event`，不得增加角色选择、台词或动机。

- `source_kind` 必须是 `action_proposal`，`source_ref` 原样复制 `proposal_id`；
- `actor_id`、`event_type` 和 `payload.intent_summary` 必须忠实复制 Proposal；
- `utterance` 的 `text`、`addressee_ids`、`expects_response` 与
  `response_to_event_id` 必须原样复制到 payload；
- `move` Event 在移动前的当前 Session scope 发生，payload 原样复制目的地；
- 每个 `move` 必须有且只有一个对应 `entity_change`，其 `state_patch` 为空对象；
- 不生成 `external_events`，不为没有移动的角色生成 `entity_changes`。

在初始五人 Session 中，先按 `actor_id` 排列全部 `utterance` Event，最后安排 `move`
Event，使休息室交谈发生在爱音离场之前。事件依次占用不重叠的一秒区间。

当当前 Session 位于 `stage` 且只有 `character-anon` 时，World Version 2 返回
`session_intent: keep_open`，World Version 3 返回 `session_intent: resolved`。
其他情况返回 `session_intent: keep_open`。只返回要求的结构化对象。
