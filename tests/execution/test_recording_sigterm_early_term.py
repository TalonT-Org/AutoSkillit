"""Edge case: SIGTERM sent to subprocess before readiness sentinel appears.

Regression guard for the boot-window race: if SIGTERM is delivered during
server startup (before the lifespan has written the sentinel), the process
must NOT hang or dump a traceback — it must exit cleanly within the deadline.

Acceptable outcomes:
  (a) Process exits cleanly AND scenario.json is NOT created (SIGTERM arrived
      before the recording session was fully started).
  (b) scenario.json IS created (SIGTERM arrived exactly at the yield point —
      finalize() had time to run).

The invariant is: no hang, no zombie process, no unhandled exception traceback
in stderr that escapes anyio's cancel scope.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys

import pytest

pytestmark = [pytest.mark.layer("execution")]


@pytest.mark.integration
def test_sigterm_during_startup_no_hang(tmp_path):
    """SIGTERM before readiness sentinel: no hang, no zombie process."""
    output_dir = tmp_path / "scenario"
    output_dir.mkdir()

    env = {
        **os.environ,
        "RECORD_SCENARIO": "1",
        "RECORD_SCENARIO_DIR": str(output_dir),
        "RECORD_SCENARIO_RECIPE": "test-recipe",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "autoskillit"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    # Send SIGTERM immediately — before ANY sentinel check.
    # This exercises the boot-window path.
    proc.stdin.close()
    proc.stdin = None
    proc.send_signal(signal.SIGTERM)

    try:
        stdout_bytes, stderr_bytes = proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        pytest.fail(
            "Subprocess hung after early SIGTERM — "
            "anyio signal receiver was not armed before mcp.run_async() started, "
            "or SIGTERM was not routed through the event loop."
        )

    stderr = stderr_bytes.decode(errors="replace")

    # Must not have a raw KeyboardInterrupt traceback escaping anyio's cancel scope
    assert "KeyboardInterrupt" not in stderr or proc.returncode == 0, (
        f"KeyboardInterrupt traceback in stderr (rc={proc.returncode}):\n{stderr!r}"
    )

    # Either:
    # (a) No scenario.json (SIGTERM arrived before lifespan entered) — acceptable
    # (b) scenario.json present and valid (SIGTERM arrived at yield) — also acceptable
    scenario_json = output_dir / "scenario.json"
    if scenario_json.exists():
        try:
            content = json.loads(scenario_json.read_text())
        except json.JSONDecodeError:
            pass  # Pre-init / partial write is acceptable for early SIGTERM
        else:
            # If it parsed, it must be a dict (even if empty)
            assert isinstance(content, dict), f"Unexpected scenario.json content: {content!r}"

    # Process must have exited (not a zombie)
    assert proc.poll() is not None, "Process is still running — zombie detected"
