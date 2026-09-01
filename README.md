# Generative Go World

这是从固定版 MyGO/WebGAL 成品包中拆出的自研工程。世界状态、Agent Runtime、结构化作品、动态 Render Adapter、Wiki 和测试都在这里维护；兄弟目录 `../MyGO_v3.1.1_ForScript` 只提供 WebGAL/MyGO 播放器与大体积素材。

```text
generative_go_world/
├── AGENT.md
├── wiki/
├── agent_runtime/          # Python 3.12 + LangChain Core；当前为 PersonAct Slice
│   ├── agent/
│   │   ├── personact/
│   │   │   ├── agent.py    # PersonActAgent 门面与 snapshot 事务
│   │   │   └── loop.py     # typed prepare/perceive/retrieve/plan/propose
│   │   ├── memory/         # scoped Agent Memory
│   │   ├── director/       # Director 边界；实现待补
│   │   └── broadcast/      # Broadcast 边界；实现待补
│   ├── event/              # 外部 Scheduler/Event 生命周期（边界已建，实现待补）
│   ├── world/              # 客观事实与 Commit 权威
│   └── rendergateway/      # RenderJob 出站边界
├── extensions/
│   └── dynamic-render/     # RenderJob 到 WebGAL 的外置 Adapter
├── index.html              # 从外部加载 WebGAL Bundle 的自研页面壳
├── tools/                  # Story 编译、项目管理与开发服务器
├── tests/
├── projects/               # 结构化作品、Fixture Timeline 与构建结果
└── authoring/              # 早期单作品输入，保留兼容

../MyGO_v3.1.1_ForScript/
├── assets/
├── game/
├── lib/
└── webgal-engine.json
```

## 边界

- `generative_go_world` 决定世界事实、Agent 行为、Event、结构化剧本和 RenderJob，并维护所有自研代码与文档。
- `MyGO_v3.1.1_ForScript` 是可替换的 Render Backend 和素材库；不要在其中继续新增 Wiki、Agent Runtime、Extension 或业务代码。
- Dynamic Render 只负责校验、编译、播放队列、播放状态和 Viewer Cursor，不拥有 World/Event 真相。
- 不修改压缩后的 WebGAL Bundle，也不把 WebGAL Stage、Backlog 或 GameVar 当作世界权威。

开发工具默认从同级 `../MyGO_v3.1.1_ForScript` 读取播放器和素材。若本机布局不同，可以显式设置：

```bash
WEBGAL_ROOT=/absolute/path/to/MyGO_v3.1.1_ForScript npm test
```

## 立即运行

制作层需要 Node.js 20 或更高版本。首次运行先安装锁定依赖：

```bash
npm install
npm test
npm run dynamic -- --project rain-after
```

Agent Runtime 当前已实现项目自研 NPC ADK 的 PersonAct 核心 Slice。`agent_runtime/agent/personact/loop.py` 是显式认知循环，`agent.py` 是串行化、replay 与 private snapshot 原子替换的门面；LangChain Core 是内部编排依赖，不是 ADK 本身。外部 Scheduler 与 World 提交链仍待实现。Python 环境、依赖和工具统一使用 uv：

```bash
uv sync --frozen
uv run ruff format --check agent_runtime
uv run ruff check agent_runtime
uv run pyright
uv run pytest
```

当前已落地受限 Manifest、`CompiledPersonActSpec`、Persona 私有 Memory/State 与检索基础，以及 `PersonActAgent.decide` 的 prepare/perceive/retrieve/plan/propose Slice。外部 Event Scheduler 拥有循环；`decide` 一次只返回 `spec.agent_id` 对应角色的一个 strict/frozen `ActionProposal`，需要 wire JSON 时再调用 `model_dump_json(by_alias=True)`。Proposal 使用固定 envelope + discriminated action union，不包含 `move` variant、`locationId` 或多目标 `targetIds`。Reflection/commit feedback、Director、World Commit、Event Scheduler 与 Render ingress 仍待实现。LangChain `Runnable.with_types()` 不做运行时校验，实际结构边界由 strict/frozen Pydantic Model 保证；当前不使用 LangGraph。

默认项目是 `rain-after`。开发服务器优先提供本仓库的自研 Overlay，并在文件不存在时从 `WEBGAL_ROOT` 提供 WebGAL 页面、Bundle、素材和媒体文件。

常用命令：

```bash
npm run projects
npm run create -- --project new-story --title "新的故事"
npm run check -- --project new-story
npm run compile -- --project new-story
npm run dev -- --project new-story
npm run dynamic -- --project rain-after
npm test
```

- `projects`：列出已有作品及其独立存档 Key。
- `create`：创建一部带可运行初始场景的新作品。
- `check`：校验项目配置、剧情、素材、Live2D 能力和结局可达性。
- `compile`：生成项目自己的 `build/start.txt`、`config.txt` 和 `player.json`。
- `dev`：构建、监听并通过外部 WebGAL Runtime 预览指定作品。
- `dynamic`：启动 Dynamic Render，读取项目的 `timeline.json` 并逐段注入已验证 Render。
- `test`：运行制作层与 Dynamic Render 测试。

## 项目模型

每个 `projects/<id>/` 是一部独立作品：

```text
projects/rain-after/
├── project.json
├── story.json
├── timeline.json
└── build/
```

作品独立拥有剧情、配置、存档命名空间和构建结果，共用兄弟目录中的 WebGAL/MyGO Runtime、Live2D、背景、BGM、动画和 UI。

Agent 默认只编辑目标项目的 `project.json`、`story.json` 和明确授权的 Timeline/Runtime 文件，不直接修改压缩 Bundle，也不手写 `build/` 生成文件。

当前支持的 Beat：

| `type` | 主要字段 | 含义 |
| --- | --- | --- |
| `chapter` | `title`, 可选 `subtitle` | 显示章节标题卡 |
| `bgm` | `asset`, 可选 `volume` / `fadeMs` | 播放并循环背景音乐 |
| `stop_bgm` | 可选 `fadeMs` | 淡出并停止背景音乐 |
| `background` | `asset` | 切换背景 |
| `show` | `character`, `position`, `motion`, `expression`, `enter` | 显示或更新 Live2D 人物 |
| `hide` | `position` | 隐藏指定位置人物 |
| `dialogue` | `character`, `text`, 可选 `motion` / `expression` | 人物台词与演出更新 |
| `narration` | `text` | 旁白 |
| `choice` | `options[{text,target}]` | 跳转到不同场景 |
| `jump` | `target` | 无条件跳转 |
| `ending` | `title`, 可选 `text` | 显示结局并返回标题页 |

修改后至少运行：

```bash
npm run check -- --project <id>
npm test
```

架构与一期开发入口见 [Wiki](wiki/index.md) 和 [Agent Runtime 实施方案](wiki/agent-runtime-implementation.md)。
