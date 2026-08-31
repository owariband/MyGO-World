# Durable World runtime

The Python 3.13 runtime is managed with `uv`:

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

World databases default to `.mygo/worlds/<world_id>/world.sqlite3`. Pass
`--worlds-dir` to isolate tests or keep Worlds elsewhere. A World retains the
Scenario Seed ID, declared version, and source-byte SHA-256; it never rereads
the Seed after initialization.

Initialization validates the complete Seed and builds a migrated temporary
database before atomically publishing it. Existing World databases are never
overwritten. Every non-initialization command checks that the database is at
the latest Alembic revision.

`advance --gateway fixture` runs one deterministic, in-process Generation
Wave and never opens a network connection. It projects one Character's
permitted Snapshot and private Memory, validates the Character proposal,
validates the Director's objective and unowned environment candidates, and
then atomically commits one new World Version. `show --json` exposes committed
World Events and Observation records so a separate process can verify them.

`render` is the only command that starts Broadcast. It fixes either the
requested `--world-version` or the current version, assigns every previously
undisposed Event through that version an included/omitted disposition, and
validates the resulting structured Beats against the supplied Asset Manifest.
The Python compiler writes canonical local artifacts under `.mygo/renders`
and immutable scenes under
`<webgal-root>/game/scene/generated/<world_id>/`; it never changes `start.txt`,
creates a `latest.txt` alias, or launches a WebGAL player. The repository-local
minimal manifest and fake asset tree support credential-free fixture runs.

The real adapter uses the same `ModelGateway` contract. Select it explicitly
with `--gateway provider` and provide `MYGO_MODEL_BASE_URL`,
`MYGO_MODEL_API_KEY`, and `MYGO_MODEL_ID`. Credentials are used only for the
HTTP request and are not placed in Generation Trace records or receipts.
Provider use is always explicit; merely having these variables does not enable
network access. `MYGO_MODEL_STRUCTURED_OUTPUT_MODE` selects `json_schema`
(default) or `json_text`, `MYGO_MODEL_PARAMETERS_JSON` supplies one global JSON
object used by Character, Director, and Broadcast, and
`MYGO_MODEL_TIMEOUT_SECONDS` defaults to 120. Reserved transport fields and
credential-like parameters are rejected.

An ignored environment file is loaded only when named with `--env-file` or
`MYGO_ENV_FILE`; process variables override file values. See `.env.example`.
Invalid Provider configuration fails before a World is changed or a request is
attempted.

Run the paid, non-interactive Anon × Soyo acceptance explicitly:

```bash
MYGO_ENV_FILE=.env.local \
WEBGAL_ROOT=/Users/yyu03/project/dev/MyGO_v3.1.1 \
uv run mygo-world live-demo \
  --world-id anon-soyo-live-001 \
  --output-dir .mygo/live/anon-soyo-live-001 \
  --json
```

This preflights Provider settings, the versioned Scenario/Skills, and every
real Asset Manifest path and Live2D capability before creating the World. It
then runs the production `init → advance → render` path with the default 40/6
request budgets. Success writes sorted CLI receipts, a canonical domain export,
complete credential-free Generation Traces, and Render metadata. Every
published scene is reread and its SHA-256 checked against the receipt and
database; the WebGAL player is never started.

The equivalent explicit test is:

```bash
MYGO_ENV_FILE=.env.local \
WEBGAL_ROOT=/Users/yyu03/project/dev/MyGO_v3.1.1 \
uv run pytest -m live tests_py/test_live_provider_demo.py
```

Normal `uv run pytest` excludes the `live` marker, so it neither reads missing
credentials/external assets nor contacts a Provider. The separate
quality-observation entry point uses fixed, redacted Pydantic Evals cases and
reports per-case quality, latency, token usage, and cost fields when present:

```bash
uv run mygo-world eval-provider --env-file .env.local --json
```

Eval results are informational and never weaken Runtime Schema, permission,
causality, source, Session, or asset validation.

Runtime operations and model attempts emit OpenTelemetry spans through the
standard API. With no exporter configured this is a no-op. Set
`MYGO_OTEL_EXPORTER=console` for the built-in local exporter, or call
`configure_telemetry()` with an SDK exporter from an embedding application.
Span attributes contain identifiers, attempt/result data, duration, and usage;
they exclude prompts, responses, Memory, API keys, and headers.

Alembic can also create a database directly:

```bash
uv run alembic -x db_path=/absolute/path/to/world.sqlite3 upgrade head
```
