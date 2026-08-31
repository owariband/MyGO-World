# Agent Instructions

本目录是 Generative Go World 的自研工程，也是 Wiki、Agent Runtime、结构化作品和 WebGAL Adapter 的唯一开发入口。

## 开工顺序

1. 先阅读 `wiki/index.md`。
2. 开发 Agent Runtime 前继续完整阅读 `wiki/agent-runtime-implementation.md`、`wiki/decisions.md` 和 `wiki/difficulty-ledger.md`。
3. 修改架构或改变已冻结边界时，同步更新 Wiki；未解决问题和被否决方案保留在 `wiki/difficulty-ledger.md`，不要抹掉历史代价。

## 仓库边界

- 本目录维护 `wiki/`、`agent_runtime/`、`extensions/`、`tools/`、`tests/`、`projects/` 和其它新增自研代码。
- 固定版 WebGAL/MyGO 播放器和大体积素材位于兄弟目录 `../MyGO_v3.1.1_ForScript`。默认路径可由 `WEBGAL_ROOT` 覆盖。
- 不要在 `../MyGO_v3.1.1_ForScript` 新增业务代码或文档，不要修改压缩 Bundle；底层目录只作为 Render Backend、素材库和兼容性验证对象。
- World/Event 真相属于外部 Runtime；Dynamic Render 只拥有 RenderJob 校验/编译、播放队列、状态和 Viewer Cursor。
- Character、Director、Broadcast 只产生 Proposal/Plan；只有 World Committer 和 Render Gateway 能产生持久副作用。

## 实现约束

- 以最小、可验证的改动推进，沿用 Wiki 已冻结的 `agent / event / world` 所有权边界。
- 不复制 Stanford 原型的旧 LLM wrapper、字符串 Prompt 替换、JSON mailbox 或全量 JSON memory。
- Agent 编排使用 Python 3.12、LangChain Core `Runnable` 与强类型 Pydantic 契约；当前不使用 LangGraph。
- Python 版本、虚拟环境、依赖和锁文件只使用 uv 管理：以 `.python-version + pyproject.toml + uv.lock` 为唯一口径；安装使用 `uv sync --frozen`，命令使用 `uv run`，不要新增并行的 requirements.txt、Poetry/Conda 配置或直接执行 `pip install`。
- `Runnable.with_types()` 只提供类型/Schema 元数据，不做运行时校验；外部输入、模型输出和持久化边界必须显式经过 strict/frozen Pydantic Model，内部类型由 pyright strict 检查。
- 不把 World/Event 权威放进 Agent Runnable、Dynamic Render 或 WebGAL 状态。
- 外部 Event Scheduler 独占循环；`PersonActAgent.decide` 每次只返回当前角色的一个 `ActionProposal`，不实现 `Persona.move()`、Maze、寻路或逐 tile movement。
- 默认先用可注入时钟、ID、随机源和 Fixture Agent 跑通可重放 Golden Trace，再接真实模型。
- Python Runtime 改动依次运行 `uv run ruff format --check agent_runtime`、`uv run ruff check agent_runtime`、`uv run pyright` 和 `uv run pytest`；Node 改动运行 `npm test`。

目标目录在迁移时没有独立 Git 根。执行任何 Git 写操作前先确认 `git rev-parse --show-toplevel` 确实指向本目录；若命中祖先目录，停止 Git 操作。未经用户明确授权，不执行 `git init`、commit 或历史改写。
