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

_READY_TOKEN = "lifespan_started"


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

    # Poll stderr line-by-line for the "sigterm_handler_ready" token which
    # serve() emits immediately after installing the SIGTERM handler. This
    # guarantees the handler is active before we send SIGTERM, while still
    # being responsive (no fixed sleep). Falls back after 5 s on slow CI.
    stderr_lines: list[str] = []
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        readable, _, _ = select.select([proc.stderr], [], [], min(remaining, 0.2))
        if not readable:
            if proc.poll() is not None:
                break
            continue
        line = proc.stderr.readline()
        if not line:
            break  # EOF — process died
        decoded = line.decode(errors="replace")
        stderr_lines.append(decoded)
        if _READY_TOKEN in decoded:
            break

    # SIGTERM is the exact signal Claude Code sends on /exit.
    # The handler converts SIGTERM → KeyboardInterrupt, triggering lifespan
    # teardown which writes scenario.json. Close stdin so the stdio transport
    # detects EOF and the event loop can fully unwind.
    proc.stdin.close()
    proc.stdin = None  # prevent communicate() from flushing the closed pipe
    proc.send_signal(signal.SIGTERM)
    try:
        stdout_bytes, remaining_stderr_bytes = proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout_bytes, remaining_stderr_bytes = proc.communicate()

    stdout = stdout_bytes.decode(errors="replace")
    stderr = "".join(stderr_lines) + remaining_stderr_bytes.decode(errors="replace")

    scenario_json = output_dir / "scenario.json"
    assert scenario_json.exists(), (
        "scenario.json not written after SIGTERM — finalize() likely bypassed (issue #745)\n"
        f"stdout: {stdout!r}\n"
        f"stderr: {stderr!r}"
    )
    data = json.loads(scenario_json.read_text())
    assert data.get("recipe") == "test-recipe"
