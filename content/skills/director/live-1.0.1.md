---
skill_id: mygo.director.live
version: 1.0.1
agent_kind: director
---
Resolve the supplied Character proposals into objective events without adding
Character choices, dialogue, or motives. Preserve every non-`no_op` proposal
as exactly one `proposal_event`. For each such event:

- set `source_kind` exactly to `action_proposal`;
- copy the proposal's `proposal_id` exactly into `source_ref`;
- copy `actor_id` and `intent_summary` exactly, placing `intent_summary` inside
  the event `payload`;
- use the shared Session location and scope; and
- preserve the matching action-specific fields in the event type and payload.

For an utterance, preserve `text` and `addressee_ids` exactly. Start at the
supplied `world_time_ms`, use non-overlapping times within five minutes, and
cite earlier event keys for causality only when needed.

At World Version 1 return `session_intent: keep_open`; a Session cannot resolve
in its first Wave. At a later World Version, if both proposals are `no_op`,
return no events or entity changes and `session_intent: resolved`. Do not create
entity changes unless directly required by a proposal. Do not invent external
Character actions. Return only the requested structured object.
