# Durable World runtime

The Python 3.13 runtime is managed with `uv`:

```bash
uv sync --locked --dev
uv run mygo-world init \
  --world-id first-meeting \
  --seed examples/scenarios/minimal.yaml
uv run mygo-world advance --world-id first-meeting --gateway fixture --json
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

The real adapter uses the same `ModelGateway` contract. Select it explicitly
with `--gateway provider` and provide `MYGO_MODEL_BASE_URL`,
`MYGO_MODEL_API_KEY`, and `MYGO_MODEL_ID`. Credentials are used only for the
HTTP request and are not placed in Generation Trace records or receipts.

Alembic can also create a database directly:

```bash
uv run alembic -x db_path=/absolute/path/to/world.sqlite3 upgrade head
```
