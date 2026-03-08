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
    """After _open_kitchen_handler(), gate is enabled and gate file is written."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            from autoskillit.server.prompts import _open_kitchen_handler

            _open_kitchen_handler()

    mock_ctx.gate.enable.assert_called_once()
    gate_file = tmp_path / "temp" / ".kitchen_gate"
    assert gate_file.exists(), "Gate file must be written by open_kitchen for native_tool_guard"


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


# T-GATE-1
def test_open_kitchen_writes_gate_file(tmp_path, monkeypatch):
    """open_kitchen must write temp/.kitchen_gate so native_tool_guard.py can read it."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            from autoskillit.server.prompts import _open_kitchen_handler

            _open_kitchen_handler()

    gate_file = tmp_path / "temp" / ".kitchen_gate"
    assert gate_file.exists(), "Gate file must exist after open_kitchen for hook subprocess access"


# T-GATE-2
def test_close_kitchen_removes_gate_file(tmp_path, monkeypatch):
    """close_kitchen must remove temp/.kitchen_gate written by open_kitchen."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            from autoskillit.server.prompts import _close_kitchen_handler, _open_kitchen_handler

            _open_kitchen_handler()
            _close_kitchen_handler()

    gate_file = tmp_path / "temp" / ".kitchen_gate"
    assert not gate_file.exists(), "Gate file must be removed by close_kitchen"
