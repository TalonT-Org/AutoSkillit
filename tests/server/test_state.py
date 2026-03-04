"""Tests for server/_state.py: server initialization and gate file cleanup."""

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
def test_initialize_cleans_stale_gate_file(tmp_path, monkeypatch):
    """A pre-existing temp/.kitchen_gate is removed on server _initialize()."""
    monkeypatch.chdir(tmp_path)

    # Pre-create a stale gate file
    gate_dir = tmp_path / "temp"
    gate_dir.mkdir(parents=True)
    stale_gate = gate_dir / ".kitchen_gate"
    stale_gate.write_text("99999")
    assert stale_gate.exists()

    mock_ctx = _make_mock_ctx(tmp_path)

    # Patch recover_crashed_sessions to avoid actual filesystem side effects
    with patch("autoskillit.execution.recover_crashed_sessions", return_value=0):
        from autoskillit.server._state import _initialize

        _initialize(mock_ctx)

    assert not stale_gate.exists(), "Stale gate file should be cleaned up on _initialize()"
