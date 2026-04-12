"""
Integration test: real autoskillit serve subprocess receives SIGTERM
and writes scenario.json to disk. Regression guard for issue #745.
"""

import json
import os
import signal
import subprocess
import time
from pathlib import Path

import pytest


@pytest.mark.integration
def test_sigterm_writes_scenario_json(tmp_path):
    """Server writes scenario.json when terminated by SIGTERM."""
    output_dir = tmp_path / "scenario"
    output_dir.mkdir()

    env = {
        **os.environ,
        "RECORD_SCENARIO": "1",
        "RECORD_SCENARIO_DIR": str(output_dir),
        "RECORD_SCENARIO_RECIPE": "test-recipe",
    }
    proc = subprocess.Popen(
        ["autoskillit", "serve"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    # Allow server to start and install its signal handler
    time.sleep(0.5)

    # SIGTERM is the exact signal Claude Code sends on /exit
    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=10)

    scenario_json = output_dir / "scenario.json"
    assert scenario_json.exists(), (
        "scenario.json not written after SIGTERM — "
        "finalize() likely bypassed (issue #745)"
    )
    data = json.loads(scenario_json.read_text())
    assert data.get("recipe") == "test-recipe"
