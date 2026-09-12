# M4 Ark 真实群演审片记录

- 日期：2026-09-12
- Project / World：`mygo-hogwarts / ark-review-20260912-01`
- 结论：**真实 Provider 链路通过；本 World 暂不接受为 M4 最终证据**
- 原因：没有由模型 Proposal 实际提交的 `split/leave`

## 运行与追踪

使用仓库根目录、Git ignored 的 `.env` 加载 `ARK_API_KEY` 与 `ARK_ENDPOINT_ID`，命令行只使用无值 `--model`，没有把配置值写入命令或本文。

运行期间只追踪 CLI 进程存活状态。每段运行结束后，以脱敏的 `run_attempt-*.json`、World 持久状态和公开 `storyline.json` 为权威证据；未启用 `--debug`，不审阅或收录 API Key、完整私有 Prompt 或 Persona Memory。

第一次受限执行被本地沙箱的网络策略拦截。World 已经完成创建和首次 dispatch 预扣，但进程在 Provider 请求前被工具层终止，没有形成应用级 attempt manifest。随后获得联网权限后，同一个 World 正常恢复并完成三段真实模型运行。

| Attempt | Dispatch | 成功步骤 | Provider 调用 | 失败 dispatch |
| --- | ---: | ---: | ---: | ---: |
| `8b68f01aeec9470eb8927d5c8c5f06ce` | 1 → 40 | 39 | 93 | 0 |
| `cf88385084f442a598e2b7572f583bc6` | 40 → 60 | 20 | 42 | 0 |
| `157dfbf759094fe0adc585dc8868fcbb` | 60 → 80 | 20 | 41 | 0 |
| 合计 | 80 次 dispatch | 79 | 176 | 0 |

最终 World 为 `paused / budget_exhausted`；`decisionSeq=79`，公开 StoryLine built through `worldVersion=79`。

## StoryLine 统计

- 78 条 committed Entry，其中 72 条非 transition。
- 58 条 dialogue、5 条 action、9 条 behavior、6 条 session transition。
- 十名角色均至少成功派发一次。
- Session transition：1 次 merge、5 次 transfer、0 次 split。
- 15 条 StoryLine lane，包含完整 parent/child lineage。
- Viewer 已从最终 JSON 确定性重新生成，并通过 17 项离线 Viewer 测试。主卡片使用角色公开中文名，直接写明“谁对谁说了什么”或“谁做了什么”；runtime ID 仅保留在折叠技术信息中。图谱按完整高度撑开，不再套用固定高度的内部滚动视窗。

## M4 硬门禁

| 门禁 | 结果 |
| --- | --- |
| 真实 Ark Gateway，不使用 Fixture | 通过 |
| 十名角色均成功派发 | 通过 |
| 至少 12 条非 transition Entry | 通过（72） |
| 至少一次 add / merge / transfer | 通过（1 merge + 5 transfer） |
| 至少一次 split / leave | **未通过（0）** |
| 暂停、load / resume 与追加运行 | 通过 |
| 生成 raw JSON 与 standalone HTML | 通过 |

## 内容审片

有效部分：

- 睦主动转入爽世一组、乐奈追随灯的共鸣、爱音加入爽世、海铃转去支援立希，分组变化与人物动机基本一致。
- 十名角色均产生公开行为；五个可操作物件均成功触发。
- 角色没有越权直接修改 Session；所有分组变化都来自模型选择的可信 affordance。

主要问题：

- 后半段陷入三组重复协商。祥子/若麦反复讨论“观察试奏”，灯/立希反复讨论“拿谱页和开节拍器”，爽世/初华/爱音反复讨论“整理谱页”。
- 多个 dialogue 声称“去拿、已经挪好、现在去协调”，但没有对应的可提交动作。文本承诺没有形成新的公共状态，下一轮模型因此再次规划同一件事。
- `leave_current_session` 始终可用；缺失 split 不是 Runtime 漏发 affordance，而是当前公开状态没有形成足够具体、可观察的离组收益。乐奈更倾向直接 transfer 到另一个有趣声源，而不是先离开成为独立分组。
- 世界时间和作业完成度没有推进，已激活物件也缺少后续“试奏、对齐、验收”状态，导致场景无法自然进入收束或重组阶段。

## 下一步建议

不要继续在这个 World 上无差别追加模型调用。先做一次最小内容/认知修正，再创建全新 World：

1. 为“试奏、对齐、提交结果”增加可提交且可观察的状态进展，避免角色用 dialogue 冒充动作。
2. 在通用 action planning 约束中明确：若当前 affordance 能执行意图，应选择动作，而不是用台词承诺未来执行；台词不得声称未提交动作已经完成。
3. 给场景加入真实的独立工作动机，让某个角色在完成支援后自然退出当前对话，而不是为了验收机械离组。
4. 新 World 仍保留严格 dispatch / Provider 上限；若未出现 split，保留失败证据，不手写 Entry 或直接改 Session 表。

## 产物哈希

- `run_manifest.json`: `b82aac31aa6859034f5e034d6272bf112571753a524f0ad9c4e9c5eb4b4787fb`
- `storyline.json`: `e9203397cec5a7c150ab97cd1975252e7f181e5a57ca1cc9fb70a9c29494dcdc`
- `storyline.html`: `db666bae107c6caa672567a646f351dbff005aae8e2515b828977ed481e18265`
