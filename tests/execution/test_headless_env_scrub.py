"""Launch-site env-scrub contract test for run_headless_core.

Asserts that ``CLAUDE_CODE_SSE_PORT`` and other IDE discovery vars are
stripped from the env passed to the subprocess runner, and that the
auto-connect suppressor is always present.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import _make_result

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


@pytest.mark.anyio
async def test_run_headless_core_env_excludes_ide_vars(
    tool_ctx, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SSE_PORT", "23270")
    monkeypatch.setenv("ENABLE_IDE_INTEGRATION", "true")
    monkeypatch.setenv("CLAUDE_CODE_IDE_HOST_OVERRIDE", "host")

    from autoskillit.execution.headless import run_headless_core

    tool_ctx.runner.set_default(_make_result())

    await run_headless_core("/investigate foo", str(tmp_path), tool_ctx)

    assert tool_ctx.runner.call_args_list, "runner was never called"
    _cmd, _cwd, _timeout, kwargs = tool_ctx.runner.call_args_list[0]
    env = kwargs.get("env")
    assert env is not None
    assert "CLAUDE_CODE_SSE_PORT" not in env
    assert "ENABLE_IDE_INTEGRATION" not in env
    assert "CLAUDE_CODE_IDE_HOST_OVERRIDE" not in env
    assert env["CLAUDE_CODE_AUTO_CONNECT_IDE"] == "0"
    assert env["AUTOSKILLIT_HEADLESS"] == "1"
