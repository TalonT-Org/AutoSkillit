"""
Integration test: real autoskillit serve subprocess receives SIGTERM
and writes scenario.json to disk. Regression guard for issue #745.
"""

import json
import os
import signal
import subprocess
import sys
import time

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
    # Use sys.executable -m to ensure we run the worktree-installed version,
    # not a system-wide `autoskillit` binary that may lack the lifespan fix.
    proc = subprocess.Popen(
        [sys.executable, "-m", "autoskillit"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    # Allow server to start and install its signal handler
    time.sleep(1.0)

    # SIGTERM is the exact signal Claude Code sends on /exit.
    # The handler converts SIGTERM → KeyboardInterrupt, triggering lifespan
    # teardown which writes scenario.json. Close stdin so the stdio transport
    # detects EOF and the event loop can fully unwind.
    proc.stdin.close()
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    scenario_json = output_dir / "scenario.json"
    assert scenario_json.exists(), (
        "scenario.json not written after SIGTERM — finalize() likely bypassed (issue #745)"
    )
    data = json.loads(scenario_json.read_text())
    assert data.get("recipe") == "test-recipe"
