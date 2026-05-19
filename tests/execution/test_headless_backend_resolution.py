"""Tests for _resolve_pty_mode and _resolve_session_log_dir capability-driven helpers."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from autoskillit.core import CLAUDE_CODE_CAPABILITIES, CmdSpec

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _make_backend(
    *,
    pty_required: bool = True,
    channel_b_capable: bool = True,
    session_resume_capable: bool = True,
    **kw,
):
    """Build a mock backend with configurable capabilities."""
    caps = replace(
        CLAUDE_CODE_CAPABILITIES,
        pty_required=pty_required,
        channel_b_capable=channel_b_capable,
        session_resume_capable=session_resume_capable,
        **kw,
    )
    backend = Mock()
    backend.capabilities = caps
    backend.build_resume_cmd.return_value = CmdSpec(
        cmd=("claude", "--print", "emit marker", "--resume", "test-session"),
        env={"KEY": "val"},
    )
    return backend


class TestResolvePtyMode:
    def test_none_backend_returns_true(self, minimal_ctx) -> None:
        import autoskillit.execution.headless as _headless_mod

        minimal_ctx.backend = None
        assert _headless_mod._resolve_pty_mode(minimal_ctx) is True

    def test_pty_required_true_returns_true(self, minimal_ctx) -> None:
        import autoskillit.execution.headless as _headless_mod

        minimal_ctx.backend = _make_backend(pty_required=True)
        assert _headless_mod._resolve_pty_mode(minimal_ctx) is True

    def test_pty_required_false_returns_false(self, minimal_ctx) -> None:
        import autoskillit.execution.headless as _headless_mod

        minimal_ctx.backend = _make_backend(pty_required=False)
        assert _headless_mod._resolve_pty_mode(minimal_ctx) is False


class TestResolveSessionLogDir:
    def test_none_backend_returns_path(self, minimal_ctx, monkeypatch) -> None:
        import autoskillit.execution.headless as _headless_mod

        minimal_ctx.backend = None
        monkeypatch.setattr(
            "autoskillit.execution.headless._session_log_dir",
            lambda cwd: Path("/fake/log/dir"),
        )
        result = _headless_mod._resolve_session_log_dir("/some/cwd", minimal_ctx)
        assert isinstance(result, Path)

    def test_channel_b_capable_true_returns_path(self, minimal_ctx, monkeypatch) -> None:
        import autoskillit.execution.headless as _headless_mod

        minimal_ctx.backend = _make_backend(channel_b_capable=True)
        monkeypatch.setattr(
            "autoskillit.execution.headless._session_log_dir",
            lambda cwd: Path("/fake/log/dir"),
        )
        result = _headless_mod._resolve_session_log_dir("/some/cwd", minimal_ctx)
        assert isinstance(result, Path)

    def test_channel_b_capable_false_returns_none(self, minimal_ctx) -> None:
        import autoskillit.execution.headless as _headless_mod

        minimal_ctx.backend = _make_backend(channel_b_capable=False)
        result = _headless_mod._resolve_session_log_dir("/some/cwd", minimal_ctx)
        assert result is None
