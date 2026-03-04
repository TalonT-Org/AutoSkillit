"""Tests for server/_state.py: server initialization."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch


def _make_mock_ctx(tmp_path: Path) -> MagicMock:
    """Return a minimal mock ToolContext for _initialize tests."""
    ctx = MagicMock()
    ctx.plugin_dir = None
    # Provide a minimal linux_tracing config stub
    tracing_cfg = MagicMock()
    tracing_cfg.tmpfs_path = str(tmp_path / "tmpfs")
    tracing_cfg.log_dir = str(tmp_path / "logs")
    ctx.config.linux_tracing = tracing_cfg
    return ctx


# T3a
def test_initialize_runs_without_error(tmp_path, monkeypatch):
    """Server _initialize() completes without raising."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx(tmp_path)

    with patch("autoskillit.execution.recover_crashed_sessions", return_value=0):
        from autoskillit.server._state import _initialize

        _initialize(mock_ctx)  # Should not raise
