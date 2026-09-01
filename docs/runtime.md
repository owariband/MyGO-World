# 持久 World Runtime 运行指引

本项目的 Python 3.13 Runtime 使用 `uv` 管理。以下命令可运行完全离线的 Fixture 流程：

```bash
uv sync --locked --dev
uv run mygo-world init \
  --world-id first-meeting \
  --seed examples/scenarios/minimal.yaml
uv run mygo-world advance --world-id first-meeting --gateway fixture --json
uv run mygo-world render \
  --world-id first-meeting \
  --asset-manifest examples/assets/minimal/manifest.yaml \
  --webgal-root examples/assets/minimal/webgal \
  --gateway fixture \
  --json
uv run mygo-world show --world-id first-meeting --json
uv run pytest
```

`show` 默认只返回客观 World Snapshot 与 World Event，不返回任何 Agent 的私有
Memory。需要诊断单个 Agent 时，必须同时限定身份和 namespace：

```bash
uv run mygo-world show \
  --world-id first-meeting \
  --memory-agent-id character-anon \
  --memory-namespace default \
  --json
```

## World 数据与生命周期

World 数据库默认位于 `.mygo/worlds/<world_id>/world.sqlite3`。可使用
`--worlds-dir` 隔离测试数据或将 World 保存在其他目录。World 会保存 Scenario Seed
的 ID、声明版本及源文件 SHA-256；初始化完成后，不会再次读取 Seed 作为运行时状态。

初始化会先校验完整 Seed，在临时数据库中执行最新迁移，然后原子发布数据库。已有
World 数据库不会被覆盖。除初始化以外的命令都会检查数据库是否处于最新 Alembic
版本。

`advance --gateway fixture` 会在当前进程内执行确定性的 Generation Wave，且不会建立
网络连接。Runtime 会投影 Character 有权读取的 Snapshot 与私有 Memory，依次校验
Character Proposal、Director 生成的客观结果和无主体环境候选，最后原子提交新的
World Version。`show --json` 会输出已提交的 World Event 与 Observation，便于其他
进程核验。

## Render 与 WebGAL 发布

只有显式执行 `render` 才会启动 Broadcast。该命令固定到指定的 `--world-version`
或当前版本，为该版本之前所有尚未处理的 Event 写入 included/omitted disposition，
并依据 Asset Manifest 校验结构化 Beat。

Python 编译器会将规范化本地产物写入 `.mygo/renders`，并将不可变场景脚本发布到：

```text
<webgal-root>/game/scene/generated/<world_id>/
```

Runtime 不会修改 `start.txt`，不会创建 `latest.txt` 别名，也不会启动 WebGAL
播放器。仓库内的最小 Manifest 和模拟素材树用于无需凭据的 Fixture 测试。

## 真实 Provider 配置

真实适配器与 Fixture 使用同一个 `ModelGateway` 契约。必须显式指定
`--gateway provider`，并提供：

- `MYGO_MODEL_BASE_URL`：OpenAI-compatible API 的 Base URL。
- `MYGO_MODEL_API_KEY`：Provider API key。
- `MYGO_MODEL_ID`：Character、Director 与 Broadcast 共用的模型 ID。
- `MYGO_MODEL_STRUCTURED_OUTPUT_MODE`：`json_schema`（默认）或 `json_text`。
- `MYGO_MODEL_PARAMETERS_JSON`：所有模型调用共用的参数 JSON 对象。
- `MYGO_MODEL_TIMEOUT_SECONDS`：单次请求超时秒数，默认 120。

凭据只用于 HTTP 请求，不会写入 Generation Trace 或 CLI Receipt。仅仅存在这些环境
变量不会自动触发网络调用；必须显式选择 Provider 或执行 Live 命令。传输保留字段
及疑似凭据的模型参数会被拒绝。

只有使用 `--env-file` 或设置 `MYGO_ENV_FILE` 时，Runtime 才会读取本地环境文件；
进程环境中的同名变量优先于文件内容。可复制仓库中的 `.env.example` 创建被 Git
忽略的 `.env.local`。Provider 配置缺失或非法时，Runtime 会在修改 World 或发起
网络请求前失败。

## Anon × Soyo 真实 Live 验收

真实 Live 验收是显式、非交互且会产生 Provider 成本的操作：

```bash
MYGO_ENV_FILE=.env.local \
WEBGAL_ROOT=/Users/yyu03/project/dev/MyGO_v3.1.1 \
uv run mygo-world live-demo \
  --world-id anon-soyo-live-001 \
  --output-dir .mygo/live/anon-soyo-live-001 \
  --json
```

执行前会预检 Provider 配置、版本化 Scenario/Skill、全部真实素材路径以及 Live2D
motion/expression 元数据；预检通过后才会创建 World。随后执行生产路径
`init → advance → render`，并使用默认的 40/6 请求预算。

成功后，指定输出目录中会包含：

- 稳定字段顺序的 CLI Receipt；
- canonical domain export；
- 完整且不含凭据的 Generation Trace；
- Render 元数据和最终 Live Receipt。

验收程序会重新读取每个已发布场景脚本，并核对文件 SHA-256、Receipt 与数据库记录；
整个流程不会启动 WebGAL 播放器。

也可以通过显式 Live 测试运行同一验收：

```bash
MYGO_ENV_FILE=.env.local \
WEBGAL_ROOT=/Users/yyu03/project/dev/MyGO_v3.1.1 \
uv run pytest -m live tests_py/test_live_provider_demo.py
```

普通的 `uv run pytest` 会排除 `live` marker，因此不会读取缺失的凭据或外部素材，也
不会访问 Provider。

## MyGO 五人 Provider 试跑

实现与离线五人测试完成后，可先显式预检独立 Asset Manifest。此命令不调用模型：

```bash
uv run python -c \
  'from pathlib import Path; from mygo_world.rendering import load_asset_manifest, validate_asset_manifest_files; manifest = load_asset_manifest(Path("examples/assets/mygo-five/manifest.yaml")); validate_asset_manifest_files(manifest, Path("/Users/yyu03/project/dev/MyGO_v3.1.1")); print("asset preflight passed")'
```

真实 Provider 试跑必须使用从未初始化过的新 World ID，并显式读取被 Git 忽略的环境
文件。以下三个命令依次初始化 Seed、以现有并发和请求预算推进最多六个 Wave，再把
已提交 Event 编排为 WebGAL 场景：

```bash
uv run mygo-world init \
  --world-id mygo-five-provider-001 \
  --seed examples/scenarios/mygo-five-character.yaml
uv run mygo-world advance \
  --world-id mygo-five-provider-001 \
  --gateway provider \
  --env-file .env.local \
  --max-waves 6 \
  --request-budget 40 \
  --json
uv run mygo-world render \
  --world-id mygo-five-provider-001 \
  --asset-manifest examples/assets/mygo-five/manifest.yaml \
  --webgal-root /Users/yyu03/project/dev/MyGO_v3.1.1 \
  --gateway provider \
  --env-file .env.local \
  --request-budget 6 \
  --json
```

每次试跑都应替换 `mygo-five-provider-001`，避免复用已有 World。该流程会访问真实
Provider 并产生费用，不属于默认测试；它也不会启动 WebGAL 或修改入口场景。

## Provider 质量评测

以下入口使用固定、已脱敏的 Pydantic Evals 样例，报告每个样例的质量结果、延迟、
token 用量，以及 Provider 返回时的成本字段：

```bash
uv run mygo-world eval-provider --env-file .env.local --json
```

Eval 仅用于质量观测，不能替代 Runtime 对 Schema、权限、因果、来源、Session 与
素材的确定性硬校验。

## OpenTelemetry

Runtime 操作和模型调用尝试会通过标准 API 生成 OpenTelemetry span。未配置 exporter
时该功能为 no-op，不影响 Runtime 正常运行。设置 `MYGO_OTEL_EXPORTER=console` 可使用
内置的本地 console exporter；嵌入其他应用时也可调用 `configure_telemetry()` 注入
SDK exporter。

Span 属性只记录标识、尝试次数、结果、耗时及用量，不记录完整 Prompt、原始响应、
私有 Memory、API key 或 Authorization header。

## Alembic

也可以直接用 Alembic 创建最新版本的数据库：

```bash
uv run alembic -x db_path=/absolute/path/to/world.sqlite3 upgrade head
```
