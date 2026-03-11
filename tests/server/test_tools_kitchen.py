"""Tests for server/tools_kitchen.py: open_kitchen and close_kitchen gate management."""

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
    """After _open_kitchen_handler(), gate is enabled."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.tools_kitchen._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.server.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools_kitchen import _open_kitchen_handler

                    await _open_kitchen_handler()

    mock_ctx.gate.enable.assert_called_once()


# T2b
def test_close_kitchen_disables_gate(tmp_path, monkeypatch):
    """After _close_kitchen_handler(), gate is disabled."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            from autoskillit.server.tools_kitchen import _close_kitchen_handler

            _close_kitchen_handler()

    mock_ctx.gate.disable.assert_called_once()


# T2c
def test_close_kitchen_no_file_no_error(tmp_path, monkeypatch):
    """_close_kitchen_handler() doesn't raise when no gate file exists."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            from autoskillit.server.tools_kitchen import _close_kitchen_handler

            _close_kitchen_handler()  # Should not raise


# T-CACHE-1
@pytest.mark.anyio
async def test_open_kitchen_primes_quota_cache(tmp_path, monkeypatch):
    """open_kitchen must call _prime_quota_cache before any run_skill hook fires."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    prime_mock = AsyncMock()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.tools_kitchen._prime_quota_cache", prime_mock):
                with patch("autoskillit.server.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools_kitchen import _open_kitchen_handler

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
            with patch("autoskillit.server.tools_kitchen._prime_quota_cache", new=AsyncMock()):
                from autoskillit.server.tools_kitchen import _open_kitchen_handler

                await _open_kitchen_handler()

    assert mock_get_ctx.call_count >= 2, (
        "_get_ctx must be called in both _open_kitchen_handler and _write_hook_config; "
        "if call_count < 2 the patch did not cover _write_hook_config's deferred import"
    )
    hook_cfg = tmp_path / ".autoskillit" / "temp" / ".autoskillit_hook_config.json"
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
            with patch("autoskillit.server.tools_kitchen._prime_quota_cache", new=AsyncMock()):
                from autoskillit.server.tools_kitchen import (
                    _close_kitchen_handler,
                    _open_kitchen_handler,
                )

                await _open_kitchen_handler()
                _close_kitchen_handler()

    hook_cfg = tmp_path / ".autoskillit" / "temp" / ".autoskillit_hook_config.json"
    assert not hook_cfg.exists(), "Hook config must be removed by close_kitchen"


# T-CACHE-4
def test_open_kitchen_handler_is_async():
    """_open_kitchen_handler must be an async def so it can await _prime_quota_cache."""
    import inspect

    from autoskillit.server.tools_kitchen import _open_kitchen_handler

    assert inspect.iscoroutinefunction(_open_kitchen_handler), (
        "_open_kitchen_handler must be async"
    )


# T-VISIBILITY-1: open_kitchen tool calls ctx.enable_components
@pytest.mark.anyio
async def test_open_kitchen_tool_calls_enable_components(tmp_path, monkeypatch):
    """open_kitchen tool must call ctx.enable_components(tags={'kitchen'})."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.tools_kitchen._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.server.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools_kitchen import open_kitchen

                    await open_kitchen(ctx=mock_ctx)

    mock_ctx.enable_components.assert_called_once_with(tags={"kitchen"})


# T-VISIBILITY-2: close_kitchen tool calls ctx.reset_visibility
@pytest.mark.anyio
async def test_close_kitchen_tool_calls_reset_visibility(tmp_path, monkeypatch):
    """close_kitchen tool must call ctx.reset_visibility()."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.reset_visibility = AsyncMock()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            from autoskillit.server.tools_kitchen import close_kitchen

            await close_kitchen(ctx=mock_ctx)

    mock_ctx.reset_visibility.assert_called_once()


@pytest.mark.anyio
async def test_open_kitchen_does_not_write_gate_file(tmp_path, monkeypatch):
    """_open_kitchen_handler must never write a gate file."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.config.quota_guard.threshold = None
    mock_ctx.config.quota_guard.cache_max_age = None
    mock_ctx.config.quota_guard.cache_path = None
    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.tools_kitchen._prime_quota_cache", new=AsyncMock()):
                from autoskillit.server.tools_kitchen import _open_kitchen_handler

                await _open_kitchen_handler()
    gate_file = tmp_path / ".autoskillit" / "temp" / ".kitchen_gate"
    assert not gate_file.exists()


def test_close_kitchen_does_not_produce_gate_file(tmp_path, monkeypatch):
    """_close_kitchen_handler must not interact with any gate file path."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            from autoskillit.server.tools_kitchen import _close_kitchen_handler

            _close_kitchen_handler()
    gate_file = tmp_path / ".autoskillit" / "temp" / ".kitchen_gate"
    assert not gate_file.exists()


@pytest.mark.anyio
async def test_open_kitchen_includes_categorized_tool_listing(tmp_path, monkeypatch):
    """open_kitchen response contains static categorized tool groups from TOOL_CATEGORIES."""
    from autoskillit.core.types import TOOL_CATEGORIES
    from autoskillit.server.tools_kitchen import open_kitchen

    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.tools_kitchen._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.server.tools_kitchen._write_hook_config"):
                    result = await open_kitchen(ctx=mock_ctx)

    seen: set[str] = set()
    for category_name, tools in TOOL_CATEGORIES:
        assert category_name in result, (
            f"Category '{category_name}' missing from open_kitchen response"
        )
        for tool_name in tools:
            if tool_name not in seen:
                assert tool_name in result, (
                    f"Tool '{tool_name}' missing from open_kitchen response"
                )
                seen.add(tool_name)


# ---------------------------------------------------------------------------
# Headless gate enforcement for kitchen tools
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_open_kitchen_denied_by_gate_when_headless(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.chdir(tmp_path)
    from autoskillit.server.tools_kitchen import open_kitchen

    result = json.loads(await open_kitchen())
    assert result["success"] is False
    assert result["subtype"] == "headless_error"


@pytest.mark.anyio
async def test_close_kitchen_denied_when_headless(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.chdir(tmp_path)
    from autoskillit.server.tools_kitchen import close_kitchen

    result = json.loads(await close_kitchen())
    assert result["success"] is False
    assert result["subtype"] == "headless_error"
