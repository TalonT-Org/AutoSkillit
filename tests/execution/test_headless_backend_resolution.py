"""Tests for _resolve_pty_mode and _resolve_session_log_dir capability-driven helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.execution.backends import CodexBackend
from tests.execution.conftest import _mock_backend

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestResolvePtyMode:
    def test_pty_required_true_returns_true(self, minimal_ctx) -> None:
        import autoskillit.execution.headless as _headless_mod

        minimal_ctx.backend = _mock_backend(pty_required=True)
        assert _headless_mod._resolve_pty_mode(minimal_ctx.backend) is True

    def test_pty_required_false_returns_false(self, minimal_ctx) -> None:
        import autoskillit.execution.headless as _headless_mod

        minimal_ctx.backend = _mock_backend(pty_required=False)
        assert _headless_mod._resolve_pty_mode(minimal_ctx.backend) is False

    def test_pty_mode_false_for_codex_backend(self, minimal_ctx) -> None:
        import autoskillit.execution.headless as _headless_mod

        minimal_ctx.backend = CodexBackend()
        assert _headless_mod._resolve_pty_mode(minimal_ctx.backend) is False


class TestResolveSessionLogDir:
    def test_channel_b_capable_true_returns_path(self, minimal_ctx, monkeypatch) -> None:
        import autoskillit.execution.headless as _headless_mod

        minimal_ctx.backend = _mock_backend(channel_b_capable=True)
        monkeypatch.setattr(
            "autoskillit.execution.headless._headless_helpers._session_log_dir",
            lambda cwd: Path("/fake/log/dir"),
        )
        result = _headless_mod._resolve_session_log_dir("/some/cwd", minimal_ctx.backend)
        assert isinstance(result, Path)

    def test_channel_b_capable_false_returns_none(self, minimal_ctx) -> None:
        import autoskillit.execution.headless as _headless_mod

        minimal_ctx.backend = _mock_backend(channel_b_capable=False)
        result = _headless_mod._resolve_session_log_dir("/some/cwd", minimal_ctx.backend)
        assert result is None


class TestStepBackendPtyOverride:
    @pytest.mark.anyio
    async def test_execute_claude_headless_uses_step_backend_pty_mode(
        self, minimal_ctx, tmp_path: Path
    ) -> None:
        """_execute_claude_headless uses _step_backend.pty_required, not ctx.backend."""
        import json
        from unittest.mock import Mock, patch

        from autoskillit.core import CmdSpec
        from autoskillit.core.types import SubprocessResult, TerminationReason
        from autoskillit.execution.headless._headless_execute import _execute_claude_headless
        from tests.fakes import MockSubprocessRunner

        runner = MockSubprocessRunner()
        runner.set_default(
            SubprocessResult(
                returncode=0,
                stdout=json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "result": "done",
                        "session_id": "s1",
                        "is_error": False,
                    }
                ),
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=1,
            )
        )
        minimal_ctx.runner = runner
        minimal_ctx.backend = _mock_backend(pty_required=True)

        codex_step = _mock_backend(pty_required=False, channel_b_capable=False)
        codex_step.name = "codex"
        _mock_parsed = Mock()
        _mock_parsed.raw = {"subtype": "success"}
        _mock_parsed.error = None
        _mock_parsed.session_id = "s1"
        _mock_parsed.success = True
        _mock_parsed.output = "done"
        _mock_parser = Mock()
        _mock_parser.parse_stdout.return_value = _mock_parsed
        codex_step.result_parser.return_value = _mock_parser

        spec = CmdSpec(cmd=("codex", "--print", "do something"), env={})
        with patch("autoskillit.execution.headless._headless_execute.assert_headless_cmd"):
            await _execute_claude_headless(
                spec,
                cwd=str(tmp_path),
                ctx=minimal_ctx,
                timeout=10.0,
                stale_threshold=60.0,
                step_backend=codex_step,
            )

        assert runner.last_pty_mode is False, (
            f"step_backend has pty_required=False; expected pty_mode=False, "
            f"got {runner.last_pty_mode!r}"
        )
