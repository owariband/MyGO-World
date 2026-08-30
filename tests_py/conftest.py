from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPOSITORY_ROOT = Path(__file__).parents[1]
MINIMAL_SEED = REPOSITORY_ROOT / "examples/scenarios/minimal.yaml"


@pytest.fixture
def worlds_dir(tmp_path: Path) -> Path:
    return tmp_path / "worlds"


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mygo_world", *args],
        cwd=REPOSITORY_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def json_output(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return json.loads(result.stdout)
