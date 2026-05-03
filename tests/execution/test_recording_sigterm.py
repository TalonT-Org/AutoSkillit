"""Integration test: autoskillit serve subprocess receives SIGTERM and writes scenario.json.

Regression guard for issue #745. Synchronizes with the subprocess via the
filesystem sentinel written by the lifespan (``readiness_sentinel_path``).
File existence is atomic — no string-parse race, no wall-clock settle-sleep.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys

import pytest

from autoskillit.core.runtime.readiness import readiness_sentinel_path
from tests._subprocess_ready import wait_for_subprocess_ready

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


@pytest.mark.integration
def test_sigterm_writes_scenario_json(tmp_path):
    """Server writes scenario.json when terminated by SIGTERM.

    Invariant: must be deterministically passing. A single miss is a
    structural failure — do not bump deadlines as a fix.
    """
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

    # Wait for the filesystem sentinel — written inside the lifespan's try:
    # block AFTER the anyio signal receiver is armed. Observing the sentinel
    # guarantees SIGTERM will be caught by the event-loop-routed handler.
    sentinel_path = readiness_sentinel_path(proc.pid)
    wait_for_subprocess_ready(proc, sentinel_path, deadline_s=10.0)

    # SIGTERM is the exact signal Claude Code sends on /exit. Close stdin so
    # the stdio transport detects EOF and the event loop can fully unwind.
    proc.stdin.close()
    proc.stdin = None  # prevent communicate() from flushing the closed pipe
    proc.send_signal(signal.SIGTERM)
    try:
        stdout_bytes, stderr_bytes = proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout_bytes, stderr_bytes = proc.communicate()

    stdout = stdout_bytes.decode(errors="replace")
    stderr = stderr_bytes.decode(errors="replace")

    # Clean shutdown: event-loop-routed SIGTERM → scope.cancel() → finalize()
    assert proc.returncode == 0, (
        f"Expected clean exit (rc=0), got rc={proc.returncode}\n"
        f"stdout: {stdout!r}\n"
        f"stderr: {stderr!r}"
    )

    scenario_json = output_dir / "scenario.json"
    assert scenario_json.exists(), (
        "scenario.json not written after SIGTERM — finalize() likely bypassed (issue #745)\n"
        f"stdout: {stdout!r}\n"
        f"stderr: {stderr!r}"
    )
    data = json.loads(scenario_json.read_text())
    assert data.get("recipe") == "test-recipe"
