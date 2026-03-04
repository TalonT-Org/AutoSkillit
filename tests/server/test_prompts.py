"""Tests for server/prompts.py: open_kitchen and close_kitchen gate file management."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch


def _make_mock_ctx():
    """Return a minimal mock ToolContext with a gate."""
    gate = MagicMock()
    gate.enabled = False
    ctx = MagicMock()
    ctx.gate = gate
    return ctx


# T2a
def test_open_kitchen_writes_gate_file(tmp_path, monkeypatch):
    """After _open_kitchen_handler(), temp/.kitchen_gate exists."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()

    # _get_ctx is imported locally inside the handler from autoskillit.server
    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            from autoskillit.server.prompts import _open_kitchen_handler

            _open_kitchen_handler()

    gate_file = tmp_path / "temp" / ".kitchen_gate"
    assert gate_file.exists(), "Gate file should exist after open_kitchen"


# T2b
def test_close_kitchen_removes_gate_file(tmp_path, monkeypatch):
    """After _close_kitchen_handler(), temp/.kitchen_gate is gone."""
    monkeypatch.chdir(tmp_path)
    # Pre-create the gate file
    gate_dir = tmp_path / "temp"
    gate_dir.mkdir(parents=True)
    (gate_dir / ".kitchen_gate").write_text("99")

    mock_ctx = _make_mock_ctx()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            from autoskillit.server.prompts import _close_kitchen_handler

            _close_kitchen_handler()

    gate_file = tmp_path / "temp" / ".kitchen_gate"
    assert not gate_file.exists(), "Gate file should be removed after close_kitchen"


# T2c
def test_close_kitchen_no_file_no_error(tmp_path, monkeypatch):
    """_close_kitchen_handler() doesn't raise when gate file already absent."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            from autoskillit.server.prompts import _close_kitchen_handler

            _close_kitchen_handler()  # Should not raise
