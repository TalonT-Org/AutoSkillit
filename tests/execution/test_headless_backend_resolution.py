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
