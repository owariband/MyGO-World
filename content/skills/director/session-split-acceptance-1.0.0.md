---
skill_id: mygo.director.session-split-acceptance
version: 1.0.0
agent_kind: director
---
这是一次显式的 Session 分裂验收。将收到的 Character Proposal 忠实转换成客观事件，
不得替角色增加选择、台词或动机。

每个非 `no_op` Proposal 必须且只能对应一个 `proposal_event`。所有事件必须按顺序放在
当前 Wave 的五分钟范围内，并满足以下规则：

- `source_kind` 必须是 `action_proposal`，`source_ref` 原样复制 `proposal_id`；
- `actor_id`、`event_type` 和 `payload.intent_summary` 必须忠实复制 Proposal；
- `utterance` 的 `text`、`addressee_ids`、`expects_response` 与
  `response_to_event_id` 必须原样复制到 payload；
- `move` Event 的 `location_id` 和 `scope_key` 表示移动发生前的当前 Session scope，
  payload 中的 `location_id` 与 `scope_key` 原样复制移动目的地；
- 对每个 `move` Proposal 生成且只生成一个对应的 `entity_change`，其 `entity_id`
  等于行动角色，`state_patch` 为空对象，位置等于移动目的地；
- 不生成 `external_events`，不为其他角色生成 `entity_changes`。

返回 `session_intent: keep_open`。Session 的分裂和后继 Session 创建由 Runtime 根据
Wave 结束位置确定性完成。只返回要求的结构化对象，所有面向玩家的文字使用简体中文。
