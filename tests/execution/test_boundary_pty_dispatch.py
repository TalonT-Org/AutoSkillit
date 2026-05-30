"""Layer-boundary integration tests for DefaultSubprocessRunner + PTY wrapping.

These tests exercise the real DefaultSubprocessRunner + pty_wrap_command +
script(1) subprocess infrastructure, catching issues at the exact seam where
the production failure occurred. The fleet E2E tests silently discard pty_mode;
these tests verify the real infrastructure boundary.
"""

from __future__ import annotations

import shutil
import signal
import sys
import textwrap
from pathlib import Path

import pytest

from autoskillit.core.types import TerminationReason
from autoskillit.execution.process import DefaultSubprocessRunner

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]

# ANSI TUI cleanup sequence emitted by Claude Code when it enters TUI mode
_ANSI_SHIM = textwrap.dedent("""\
    import sys, os, signal
    sys.stdout.buffer.write(
        b"\x1b[?1006l\x1b[?1003l\x1b[?1002l\x1b[?1000l"
        b"\x1b[>4m\x1b[<u\x1b[?1004l\x1b[?2031l\x1b[?2004l"
        b"\x1b[?25h\x1b\x37\x1b[r\x1b\x38\x1b]0;\x07\x1b[?25h"
    )
    sys.stdout.buffer.flush()
    import os, signal
    os.kill(os.getpid(), signal.SIGTERM)
""")

_JSONL_SUCCESS_SHIM = textwrap.dedent("""\
    import sys, json
    payload = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "%%BOUNDARY_DONE%%",
        "session_id": "test-boundary",
    }
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\\n")
    sys.stdout.flush()
    sys.exit(0)
""")


@pytest.mark.skipif(shutil.which("script") is None, reason="script(1) not available")
class TestBoundaryPtyDispatch:
    """Layer-boundary tests: real DefaultSubprocessRunner + PTY + Python shim.

    These tests exercise the infrastructure layer that fleet E2E tests cannot
    reach: pty_wrap_command -> script(1) -> subprocess -> temp file I/O ->
    two-channel race. The production failure occurred at this exact boundary.
    """

    @pytest.mark.anyio
    async def test_real_runner_pty_ansi_shim_sigterm(self, tmp_path: Path) -> None:
        """Real runner + PTY + ANSI shim: exits SIGTERM, ANSI escape bytes appear in stdout."""
        shim = tmp_path / "ansi_shim.py"
        shim.write_text(_ANSI_SHIM)
        shim.chmod(0o755)

        runner = DefaultSubprocessRunner()
        result = await runner(
            [sys.executable, str(shim)],
            cwd=tmp_path,
            timeout=10.0,
            pty_mode=True,
        )

        assert result.returncode in {143, -signal.SIGTERM}, (
            f"Expected SIGTERM exit (143 or -{signal.SIGTERM}), got {result.returncode}"
        )
        assert result.termination in {
            TerminationReason.NATURAL_EXIT,
            TerminationReason.SIGNAL_DEATH,
        }
        # ESC byte (0x1B) must appear — ANSI sequences were written to stdout
        assert "\x1b" in result.stdout, (
            f"Expected ANSI escape bytes in stdout, got: {result.stdout!r}"
        )

    @pytest.mark.anyio
    async def test_execute_headless_real_runner_ansi_shim(self, tool_ctx, tmp_path: Path) -> None:
        """_execute_claude_headless + DefaultSubprocessRunner + ANSI shim:
        Result: success=False, lifespan_started=False."""
        from autoskillit.core import CmdSpec
        from autoskillit.execution.headless._headless_execute import _execute_claude_headless
        from autoskillit.execution.process import DefaultSubprocessRunner
        from tests.execution.conftest import _mock_backend

        tool_ctx.runner = DefaultSubprocessRunner()

        shim = tmp_path / "ansi_shim.py"
        shim.write_text(_ANSI_SHIM)
        shim.chmod(0o755)

        backend = _mock_backend(pty_required=True)
        spec = CmdSpec(cmd=(sys.executable, str(shim)), env={})

        result = await _execute_claude_headless(
            spec,
            cwd=str(tmp_path),
            ctx=tool_ctx,
            timeout=10.0,
            stale_threshold=60.0,
            step_backend=backend,
        )

        assert result.success is False
        assert result.lifespan_started is False

    @pytest.mark.anyio
    async def test_real_runner_pty_jsonl_shim_positive_case(self, tmp_path: Path) -> None:
        """DefaultSubprocessRunner + pty_mode=True + JSONL shim: completion marker reaches stdout.

        Positive case: verifies the layer-boundary infrastructure works for the happy path.
        """
        shim = tmp_path / "jsonl_shim.py"
        shim.write_text(_JSONL_SUCCESS_SHIM)
        shim.chmod(0o755)

        runner = DefaultSubprocessRunner()
        result = await runner(
            [sys.executable, str(shim)],
            cwd=tmp_path,
            timeout=10.0,
            pty_mode=True,
        )

        assert result.termination != TerminationReason.TIMED_OUT
        assert "BOUNDARY_DONE" in result.stdout, (
            f"Expected completion marker in stdout, got: {result.stdout!r}"
        )
