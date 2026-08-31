from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from mygo_world.live_demo import run_live_demo


@pytest.mark.live
def test_live_provider_demo() -> None:
    env_file_value = os.environ.get("MYGO_ENV_FILE", "").strip()
    webgal_root_value = os.environ.get("WEBGAL_ROOT", "").strip()
    if not env_file_value or not webgal_root_value:
        pytest.fail(
            "MYGO_ENV_FILE and WEBGAL_ROOT are required for explicit Live acceptance"
        )

    world_id = f"live-{uuid.uuid4().hex[:12]}"
    output_dir = Path(".mygo/live") / world_id
    receipt = run_live_demo(
        world_id,
        output_dir=output_dir,
        webgal_root=Path(webgal_root_value),
        env_file=Path(env_file_value),
    )

    assert receipt["status"] == "completed"
    assert receipt["session_closure_reason"] == "resolved"
    assert receipt["provider_request_count"] <= 46
    assert receipt["renders"]
    assert (output_dir / "receipt.json").is_file()
    assert (output_dir / "canonical-domain.json").is_file()
    assert (output_dir / "generation-traces.json").is_file()
    assert (output_dir / "render-metadata.json").is_file()
