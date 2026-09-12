# Generative Go World — Session Transfer

更新时间：2026-09-12（Asia/Shanghai）

## 一句话结论

可以开始 M5.1（Director 的最小触发/决策契约）。M4 的工程底座、真实 Ark 多 Agent 运行、可恢复 runner、StoryLine 和横向 worktree HTML 已经形成 checkpoint；但 **M4 仍不能标记为 DONE**：真实运行尚未产生 `split`，也尚未补齐真实 `SIGINT` / `SIGKILL` 进程级恢复证据。

## 新 Session 建议先做什么

1. 运行 `git status --short --branch`，确认从本轮已推送的 `master` checkpoint 开始；再看 `git log -1 --oneline --decorate`。
2. 阅读：
   - `wiki/design/dev_plan_MVP.md` 的 M5 段落
   - `wiki/design/director_broadcast_agent.md`
   - `wiki/design/M4_dev_log.md` 的末尾真实运行复盘
3. 从 **M5.1 严格数据契约 + fixture** 开始，不要先接模型、后台 Agent 或 Broadcast。
4. M5 开发不要顺手宣告 M4 完成；两项 M4 验收债务可以独立补齐。

## Git checkpoint

- 分支：`master`
- 远端：`origin`（`owariband/MyGO-World`）
- 本轮 checkpoint：见 `git log -1`
- checkpoint 覆盖：M4 runtime、migration、CLI、模型预算、真实 Hogwarts 项目、角色技能、测试、StoryLine HTML 自动化、真实运行审查产物和 wiki/transfer 更新。

## M4 当前真实状态

已经完成并验证的工程能力：

- 项目隔离的 World bootstrap、运行时存储和 migration。
- `WorldRunner` 的 session/dispatch 生命周期、pause/resume、预算停止与恢复边界。
- 角色原子 step、committed `EventEntry`、收件人快照和可见性过滤。
- merge / transfer / split 的 runtime 与 StoryLine 表达能力。
- provider 调用预算、运行 manifest、审核产物和确定性 HTML 导出。
- 横向 Git-worktree 风格 Viewer A：合流时归线，离开时分叉；每个点直接显示“谁对谁说了什么 / 谁做了什么”，原始 ID 收进技术详情。

尚未关闭的验收项：

- 真实 Ark 运行有 merge 和 transfer，**没有真实 split**。
- runner 有恢复测试，但还缺真实 OS 进程 `SIGINT` / `SIGKILL` 后续跑的审查证据。

因此文档状态是 “ENGINEERING READY / REAL ARK RUN REVIEW”，不是 M4 DONE。

## 真实 Ark 多 Agent 运行证据

审查目录：`artifacts/m4-hogwarts/ark-review-20260912-01/`

- 3 次 attempt。
- 80 次持久化 dispatch；79 次成功 character step。
- 176 次逻辑 provider call。
- 应用级 failed dispatch：0。
- 最终状态：`paused / budget_exhausted`。
- `decisionSeq = 79`，`worldVersion = 79`。
- 78 个 committed entries：58 dialogue、5 action、9 behavior、6 transition。
- 10 个角色均被调度。
- transition：1 merge、5 transfer、0 split。

关键文件：

- `review.md`：事实和验收结论。
- `run-manifest.json` / `run-attempt-*.json`：运行参数与 attempt 证据。
- `storyline.json`：canonical StoryLine。
- `storyline.html`：面向人工审查的 worktree Viewer A。

这次运行是真实 Ark provider 运行，但因缺 split 明确标为未完全验收，不要把它改写成成功通过全部 M4 gate。

## 可见性、Director 与 Broadcast 的事实

- 场景初始化提供角色和初始分区。
- 可见性不是把初始化名单永久复制给每个角色；每个 committed `EventEntry` 都会固化自己的 recipient snapshot，角色读取时按该 entry 的收件范围过滤。
- 当前真实运行 **没有 Director Agent，也没有 Broadcast Agent** 参与调控。
- 当前 join / leave / transfer 来源于普通角色动作与 runtime transition 规则，不是 Director 编排。
- M5 Director 只能读取经过裁剪的 committed 事实和明确允许的 runtime state；不能读取私密、未提交或与触发无关的内容。

## Hogwarts 内容版本

真实 Ark 审查目录中的旧 World 使用 Scenario v1 / 旧角色 spec。此后已升级内容：

- `projects/mygo-hogwarts/scenario.yaml` 升到 v2：加入 Flitwick 魔咒课、羽毛漂浮练习、魔药坩埚和课程规则，增强 Harry Potter 校园元素。
- 新增 `content/skills/characters/anon-3.1.0.md`。
- `agents.json` 将爱音固定到 3.1.0：明确她暗恋素世，并增加目标、记忆与亲密倾向。
- 当前 seed hash：`aa9878a3b4712af09c0d31cec83b03a8ccc76eba2392ddd1aca7a4d5c3c595b6`。

**不要用 Scenario v2 去 resume 旧 World。** 下一次真实运行必须创建新的 `world_id`，否则会把两套定义混入同一条历史。

## StoryLine HTML 自动化

日常入口：

```bash
npm run story:html -- artifacts/m4-hogwarts/ark-review-20260912-01
```

脚本同时接受 artifact 目录或 `storyline.json` 路径；给目录时自动读取其中的 canonical JSON，并输出同目录 `storyline.html`。

底层入口：

```bash
node tools/storyline-to-html.mjs <artifact-dir-or-json> [output.html]
```

当前审查 HTML SHA-256：

```text
db666bae107c6caa672567a646f351dbff005aae8e2515b828977ed481e18265
```

Viewer 只保留 A 款。页面不再使用会裁掉图的固定高度内框；浏览器缩放/筛选重置也已处理。

## M5.1 推荐最小切片

目标是先冻结边界，不做完整导演智能体：

1. 定义严格、可版本化的 `DirectorTrigger`、`DirectorView` 和可辨识联合类型 `EventStaffDecision`。
2. 一次调用只处理一个 committed trigger / staff item；输入只带 bounded causal window、直接相关角色状态和当前 affordances。
3. 决策动作只允许 `enqueue`、`keep`、`release`、`cancel`、`no_op`，每种 payload 拒绝未知字段。
4. audience / delivery 在 release 时按最新世界状态重新校验；不要过早冻结，也不要让 Director 改写事件主体或完成事实。
5. fixtures 必须证明会拒绝：private entry、uncommitted proposal、无关角色状态和任意扩大的 audience。

命名接缝：D-044 等旧文档偶尔使用 `WorldEvent`，当前 runtime 的权威 committed 事实类型是 `EventEntry`。M5 应复用/包装 `EventEntry`，不要另造第二套客观事件日志。

完成上述契约、fixture 和纯函数级测试后，再接最薄的 deterministic staff queue；模型调用、常驻 Director loop 和 Broadcast 均放到后续小步。

## 本轮验证基线

- Python：505 tests passed。
- Node：31 tests passed。
- Hogwarts focused tests：4 passed。
- StoryLine + Hogwarts focused tests：15 passed。
- StoryLine HTML 目录入口已实际生成并核对 hash。
- push 前还执行了完整 Ruff、Ruff format check、Pyright、`git diff --check` 和 build gate；如 checkpoint 后出现新改动，请重新运行对应检查。

常用命令：

```bash
.venv/bin/pytest -q
npm test
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pyright
uv build
```

Node 测试会监听本地 `127.0.0.1` 端口；若受执行沙箱限制出现 `EPERM`，那是环境权限问题，不等于测试逻辑失败。

## 安全与协作注意

- 不要打开、打印或提交 `.env`。
- Ark 凭据通过本地环境变量注入；运行 manifest 可以保留非秘密的 model / endpoint 标识，但绝不能写入 API key。
- 真实审查 artifact 已做凭据形态扫描；当前没有发现 API key 或私钥。
- 当前没有后台模型进程，也没有遗留的 hook babysit 任务。
- 本轮未使用子 Agent。
- 下一 session 未经用户再次授权，不要自动 commit / push。
