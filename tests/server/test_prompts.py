"""Tests for server/prompts.py: open_kitchen and close_kitchen gate management."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_mock_ctx():
    """Return a minimal mock ToolContext with a gate."""
    gate = MagicMock()
    gate.enabled = False
    ctx = MagicMock()
    ctx.gate = gate
    return ctx


# T2a
@pytest.mark.anyio
async def test_open_kitchen_enables_gate(tmp_path, monkeypatch):
    """After _open_kitchen_handler(), gate is enabled and gate file is written."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.prompts._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.server.prompts._write_hook_config"):
                    from autoskillit.server.prompts import _open_kitchen_handler

                    await _open_kitchen_handler()

    mock_ctx.gate.enable.assert_called_once()
    gate_file = tmp_path / "temp" / ".kitchen_gate"
    assert gate_file.exists(), "Gate file must be written by open_kitchen for native_tool_guard"


# T2b
def test_close_kitchen_disables_gate(tmp_path, monkeypatch):
    """After _close_kitchen_handler(), gate is disabled."""
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
@pytest.mark.anyio
async def test_open_kitchen_writes_gate_file(tmp_path, monkeypatch):
    """open_kitchen must write temp/.kitchen_gate so native_tool_guard.py can read it."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.prompts._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.server.prompts._write_hook_config"):
                    from autoskillit.server.prompts import _open_kitchen_handler

                    await _open_kitchen_handler()

    gate_file = tmp_path / "temp" / ".kitchen_gate"
    assert gate_file.exists(), "Gate file must exist after open_kitchen for hook subprocess access"


# T-GATE-2
@pytest.mark.anyio
async def test_close_kitchen_removes_gate_file(tmp_path, monkeypatch):
    """close_kitchen must remove temp/.kitchen_gate written by open_kitchen."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.prompts._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.server.prompts._write_hook_config"):
                    from autoskillit.server.prompts import (
                        _close_kitchen_handler,
                        _open_kitchen_handler,
                    )

                    await _open_kitchen_handler()
                    _close_kitchen_handler()

    gate_file = tmp_path / "temp" / ".kitchen_gate"
    assert not gate_file.exists(), "Gate file must be removed by close_kitchen"


# T-CACHE-1
@pytest.mark.anyio
async def test_open_kitchen_primes_quota_cache(tmp_path, monkeypatch):
    """open_kitchen must call _prime_quota_cache before any run_skill hook fires.

    Fails today: _prime_quota_cache does not exist in prompts.py.
    """
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    prime_mock = AsyncMock()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.prompts._prime_quota_cache", prime_mock):
                with patch("autoskillit.server.prompts._write_hook_config"):
                    from autoskillit.server.prompts import _open_kitchen_handler

                    await _open_kitchen_handler()

    prime_mock.assert_called_once()


# T-CACHE-2
@pytest.mark.anyio
async def test_open_kitchen_writes_hook_config_json(tmp_path, monkeypatch):
    """open_kitchen must write temp/.autoskillit_hook_config.json with user quota_guard values."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.config.quota_guard.threshold = 85.0
    mock_ctx.config.quota_guard.cache_max_age = 300
    mock_ctx.config.quota_guard.cache_path = "/custom/path.json"

    # _write_hook_config uses 'from autoskillit.server import _get_ctx' at call time.
    # Patching autoskillit.server._get_ctx correctly intercepts that deferred import;
    # assert call_count >= 2 confirms the patch covered both _open_kitchen_handler and
    # _write_hook_config (not just one of them).
    with patch("autoskillit.server._get_ctx", return_value=mock_ctx) as mock_get_ctx:
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.prompts._prime_quota_cache", new=AsyncMock()):
                from autoskillit.server.prompts import _open_kitchen_handler

                await _open_kitchen_handler()

    assert mock_get_ctx.call_count >= 2, (
        "_get_ctx must be called in both _open_kitchen_handler and _write_hook_config; "
        "if call_count < 2 the patch did not cover _write_hook_config's deferred import"
    )
    hook_cfg = tmp_path / "temp" / ".autoskillit_hook_config.json"
    assert hook_cfg.exists(), "Hook config file must be written by open_kitchen"
    data = json.loads(hook_cfg.read_text())
    assert data["quota_guard"]["threshold"] == 85.0
    assert data["quota_guard"]["cache_max_age"] == 300
    assert data["quota_guard"]["cache_path"] == "/custom/path.json"


# T-CACHE-3
@pytest.mark.anyio
async def test_close_kitchen_removes_hook_config_json(tmp_path, monkeypatch):
    """close_kitchen must remove temp/.autoskillit_hook_config.json to prevent stale config."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.config.quota_guard.threshold = 90.0
    mock_ctx.config.quota_guard.cache_max_age = 300
    mock_ctx.config.quota_guard.cache_path = "~/.claude/quota_cache.json"

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.prompts._prime_quota_cache", new=AsyncMock()):
                from autoskillit.server.prompts import (
                    _close_kitchen_handler,
                    _open_kitchen_handler,
                )

                await _open_kitchen_handler()
                _close_kitchen_handler()

    hook_cfg = tmp_path / "temp" / ".autoskillit_hook_config.json"
    assert not hook_cfg.exists(), "Hook config must be removed by close_kitchen"


# T-CACHE-4
def test_open_kitchen_handler_is_async():
    """_open_kitchen_handler must be an async def so it can await _prime_quota_cache."""
    import inspect

    from autoskillit.server.prompts import _open_kitchen_handler

    assert inspect.iscoroutinefunction(_open_kitchen_handler), (
        "_open_kitchen_handler must be async"
    )
