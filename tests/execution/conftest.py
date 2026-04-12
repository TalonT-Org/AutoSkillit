"""Shared fixtures and helpers for tests/execution/."""

from __future__ import annotations

import pathlib
import textwrap

import pytest

from autoskillit.config.settings import LinuxTracingConfig

# Simulates Claude CLI process that writes a result line then hangs.
# Used by test_process_channel_b.py and test_process_monitor.py.
WRITE_RESULT_THEN_HANG_SCRIPT = textwrap.dedent("""\
    import sys, time, json
    result = {"type": "result", "subtype": "success", "is_error": False,
              "result": "done", "session_id": "s1"}
    sys.stdout.write(json.dumps(result, separators=(",", ":")) + "\\n")
    sys.stdout.flush()
    time.sleep(3600)
""")


@pytest.fixture
def isolated_tracing_config(tmp_path: pathlib.Path) -> LinuxTracingConfig:
    """Pre-isolated LinuxTracingConfig for tracing tests.
    Always writes to a tmp_path subdir, never to the real /dev/shm.
    Use this fixture for all new tests that need a LinuxTracingConfig."""
    shm = tmp_path / "shm"
    shm.mkdir(parents=True, exist_ok=True)
    return LinuxTracingConfig(enabled=True, proc_interval=0.05, tmpfs_path=str(shm))
