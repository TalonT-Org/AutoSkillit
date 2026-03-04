"""Tests for server/prompts.py: open_kitchen and close_kitchen gate management."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _make_mock_ctx():
    """Return a minimal mock ToolContext with a gate."""
    gate = MagicMock()
    gate.enabled = False
    ctx = MagicMock()
    ctx.gate = gate
    return ctx


# T2a
def test_open_kitchen_enables_gate(tmp_path, monkeypatch):
    """After _open_kitchen_handler(), gate is enabled (no gate file written)."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            from autoskillit.server.prompts import _open_kitchen_handler

            _open_kitchen_handler()

    mock_ctx.gate.enable.assert_called_once()
    # No gate file should be written
    gate_file = tmp_path / "temp" / ".kitchen_gate"
    assert not gate_file.exists(), "Gate file should NOT be written after open_kitchen"


# T2b
def test_close_kitchen_disables_gate(tmp_path, monkeypatch):
    """After _close_kitchen_handler(), gate is disabled (no gate file removed)."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            from autoskillit.server.prompts import _close_kitchen_handler

            _close_kitchen_handler()

    mock_ctx.gate.disable.assert_called_once()


# T2c
def test_close_kitchen_no_file_no_error(tmp_path, monkeypatch):
    """_close_kitchen_handler() doesn't raise when no gate file exists."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            from autoskillit.server.prompts import _close_kitchen_handler

            _close_kitchen_handler()  # Should not raise
