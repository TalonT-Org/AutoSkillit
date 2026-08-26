"""Integration test: autoskillit serve subprocess receives SIGTERM and writes scenario.json.

Regression guard for issue #745. Synchronizes with the subprocess via the
filesystem sentinel written by the lifespan (``readiness_sentinel_path``).
File existence is atomic — no string-parse race, no wall-clock settle-sleep.
"""

from __future__ import annotations

import json
import signal
import subprocess
import sys

import pytest

from tests._subprocess_ready import wait_for_subprocess_ready
from tests.conftest import production_interpreter_env

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]

pytest.importorskip("api_simulator")


@pytest.mark.integration
def test_sigterm_writes_scenario_json(tmp_path):
    """Server writes scenario.json when terminated by SIGTERM.

    Invariant: must be deterministically passing. A single miss is a
    structural failure — do not bump deadlines as a fix.
    """
    output_dir = tmp_path / "scenario"
    output_dir.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    home_dir = tmp_path / "home"
    home_dir.mkdir()

    env = {
        **production_interpreter_env(),
        "RECORD_SCENARIO": "1",
        "RECORD_SCENARIO_DIR": str(output_dir),
        "RECORD_SCENARIO_RECIPE": "test-recipe",
        "AUTOSKILLIT_STATE_DIR": str(state_dir),
        "HOME": str(home_dir),
    }
    # Use sys.executable -m to ensure we run the worktree-installed version,
    # not a system-wide `autoskillit` binary that may lack the lifespan fix.
    # cwd=tmp_path, AUTOSKILLIT_STATE_DIR, and HOME isolate the subprocess's
    # filesystem state from the project tree and the developer's real
    # ~/.autoskillit/config.yaml (e.g. any real execution markers under CWD,
    # or stale config keys from a prior schema) so the sentinel location and
    # config layers are deterministic.
    stdout_path = tmp_path / "server.stdout"
    stderr_path = tmp_path / "server.stderr"
    with stdout_path.open("wb") as stdout_stream, stderr_path.open("wb") as stderr_stream:
        proc = subprocess.Popen(
            [sys.executable, "-m", "autoskillit"],
            stdin=subprocess.PIPE,
            stdout=stdout_stream,
            stderr=stderr_stream,
            env=env,
            cwd=tmp_path,
        )

        # Wait for the filesystem sentinel — written inside the lifespan's try:
        # block AFTER the anyio signal receiver is armed. Observing the sentinel
        # guarantees SIGTERM will be caught by the event-loop-routed handler.
        # Compute the path manually from the overridden state dir rather than
        # calling readiness_sentinel_path(), because the test process's own
        # AUTOSKILLIT_STATE_DIR may differ from the subprocess's. File-backed
        # output prevents an unread pipe from blocking startup before the sentinel.
        sentinel_path = state_dir / "kitchen_state" / f"server_ready_{proc.pid}.sentinel"
        try:
            wait_for_subprocess_ready(proc, sentinel_path, deadline_s=10.0)

            # SIGTERM is the exact signal Claude Code sends on /exit. Close stdin so
            # the stdio transport detects EOF and the event loop can fully unwind.
            proc.stdin.close()
            proc.stdin = None
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        except BaseException:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            raise

    stdout = stdout_path.read_text(errors="replace")
    stderr = stderr_path.read_text(errors="replace")

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
