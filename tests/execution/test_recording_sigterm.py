"""
Integration test: real autoskillit serve subprocess receives SIGTERM
and writes scenario.json to disk. Regression guard for issue #745.
"""

import json
import os
import select
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

    # Poll stderr for any output (server startup log) to detect readiness,
    # rather than using a fixed sleep. Falls back to a 5-second ceiling so
    # the test is both responsive on fast hosts and resilient on slow CI.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        ready, _, _ = select.select([proc.stderr], [], [], 0.1)
        if ready or proc.poll() is not None:
            break

    # SIGTERM is the exact signal Claude Code sends on /exit.
    # The handler converts SIGTERM → KeyboardInterrupt, triggering lifespan
    # teardown which writes scenario.json. Close stdin so the stdio transport
    # detects EOF and the event loop can fully unwind.
    proc.stdin.close()
    proc.send_signal(signal.SIGTERM)
    try:
        stdout_bytes, stderr_bytes = proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout_bytes, stderr_bytes = proc.communicate()

    stdout = stdout_bytes.decode(errors="replace")
    stderr = stderr_bytes.decode(errors="replace")

    scenario_json = output_dir / "scenario.json"
    assert scenario_json.exists(), (
        "scenario.json not written after SIGTERM — finalize() likely bypassed (issue #745)\n"
        f"stdout: {stdout!r}\n"
        f"stderr: {stderr!r}"
    )
    data = json.loads(scenario_json.read_text())
    assert data.get("recipe") == "test-recipe"
