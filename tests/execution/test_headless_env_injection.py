"""Phase 2 tests: AUTOSKILLIT_HEADLESS=1 env var injection in headless.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.core.types import SubprocessResult, TerminationReason
from tests.conftest import MockSubprocessRunner


@pytest.mark.anyio
async def test_headless_command_includes_headless_env_var(tmp_path: Path) -> None:
    """run_headless_core must inject AUTOSKILLIT_HEADLESS=1 into the subprocess command."""
    from autoskillit.config import AutomationConfig
    from autoskillit.execution.headless import run_headless_core
    from autoskillit.pipeline.gate import DefaultGateState
    from autoskillit.server._factory import make_context

    success_result = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "result": "done",
            "session_id": "test-session",
            "is_error": False,
        }
    )
    mock_runner = MockSubprocessRunner()
    mock_runner.set_default(
        SubprocessResult(
            returncode=0,
            stdout=success_result,
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=12345,
        )
    )

    ctx = make_context(AutomationConfig(), runner=mock_runner, plugin_dir=str(tmp_path))
    ctx.gate = DefaultGateState(enabled=True)
    ctx.config.linux_tracing.log_dir = str(tmp_path / "logs")

    await run_headless_core("/investigate foo", str(tmp_path), ctx)

    assert mock_runner.call_args_list, "runner was never called"
    cmd, _cwd, _timeout, kwargs = mock_runner.call_args_list[0]
    env = kwargs.get("env")
    assert env is not None
    assert env["AUTOSKILLIT_HEADLESS"] == "1", (
        "run_headless_core must inject AUTOSKILLIT_HEADLESS=1 via the env kwarg "
        "so PreToolUse hooks can identify headless sessions."
    )
    assert cmd[0] != "env", "argv must no longer carry a leading ['env', ...] prefix"
