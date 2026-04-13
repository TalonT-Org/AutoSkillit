"""Shared subprocess readiness helper for integration tests.

This is the ONLY approved mechanism for waiting on MCP subprocess readiness
in tests. Inline ``stderr.readline()`` polls for log tokens are forbidden and
enforced by ``tests/execution/test_readiness_helper_contract.py``.

Import as::

    from tests._subprocess_ready import wait_for_subprocess_ready

Usage::

    proc = subprocess.Popen(...)
    sentinel_path = readiness_sentinel_path(proc.pid)
    wait_for_subprocess_ready(proc, sentinel_path, deadline_s=10.0)
    # subprocess is now ready — safe to interact with it
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path


def wait_for_subprocess_ready(
    proc: subprocess.Popen,
    sentinel_path: Path,
    *,
    deadline_s: float = 10.0,
    poll_interval_s: float = 0.05,
) -> None:
    """Wait until ``sentinel_path`` exists or ``proc`` exits; raise on deadline.

    Polls the filesystem at a fixed interval. Does NOT read proc.stdout/stderr.

    :param proc: The subprocess to monitor.
    :param sentinel_path: Path to the sentinel file written by the subprocess
        lifespan once it is fully initialized and ready to accept signals.
    :param deadline_s: Maximum seconds to wait. Raise ``TimeoutError`` on expiry.
    :param poll_interval_s: Polling interval in seconds.

    :raises TimeoutError: If the sentinel does not appear within ``deadline_s``.
    :raises RuntimeError: If the process exits before the sentinel appears
        (includes captured stderr for diagnostics).
    """
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        if sentinel_path.exists():
            return
        exit_code = proc.poll()
        if exit_code is not None:
            # Process died before writing sentinel — capture stderr for diagnosis
            try:
                remaining_stderr = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
            except Exception:
                remaining_stderr = "<stderr read failed>"
            raise RuntimeError(
                f"Subprocess exited with code {exit_code} before readiness sentinel appeared.\n"
                f"Sentinel path: {sentinel_path}\n"
                f"Stderr: {remaining_stderr!r}"
            )
        time.sleep(poll_interval_s)
    raise TimeoutError(
        f"Subprocess readiness sentinel not seen within {deadline_s}s.\n"
        f"Sentinel path: {sentinel_path}\n"
        f"Process still running: {proc.poll() is None}"
    )
