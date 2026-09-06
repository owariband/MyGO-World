# 显式声明 Utterance 的回应语义

`utterance.addressee_ids` 只表示发言的主要接收者，不再同时隐式表示“等待回应”。
Character 必须用 `expects_response` 明确声明是否为这些接收者建立待回应关系，并在回应
已经感知的前序发言时用 `response_to_event_id` 引用对应 World Event。

Runtime 只在 `expects_response` 为真时把 Character 收件人加入 Session 的
`pending_response_ids`。待回应 Character 只有提交带 `response_to_event_id` 的发言，
或直接与另一 Character 互动时才清除自己的待回应状态；回应可以面向原说话者，也可以
作为当前 Session 内的公开发言。`response_to_event_id` 必须属于该 Character 已感知的
World Event。

`addressee_ids` 只能包含当前可见的 Character。Location、Object 和其他非 Character
实体仍可作为 `interact.target_id`，但不能成为 utterance addressee，也不能参与 Session
合并或分区计算。

本决策取代 ADR 0005 中“`addressee_ids` 同时标记待回应关系”的隐式规则。显式字段使
问题、普通定向陈述和最终回应可以被确定性地区分，避免双方每次回应都把对方重新加入
pending 而令 Session 无法自然结束。
