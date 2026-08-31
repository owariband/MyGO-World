---
skill_id: mygo.broadcast.live
version: 1.0.0
agent_kind: broadcast
---
Create one concise, self-contained Render from committed events. Give every
frontier Event exactly one disposition and include every event that can be
faithfully shown. Begin the Render with a chapter, `bgm` or `stop_bgm`, and the
approved background. Show a Character before their dialogue. Dialogue must
copy the committed utterance text exactly and cite exactly that Event. Every
included Event must be cited; narration may summarize only cited facts.

Use only IDs in `asset_candidates`. Prefer `background-ring-lounge`,
`bgm-mygo-title`, `model-anon-live`, and `model-soyo-live`. Use only listed
motions, expressions, and entrance effects. Use lowercase render/beat IDs and
return only the requested structured object.
