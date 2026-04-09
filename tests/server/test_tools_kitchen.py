"""Tests for server/tools_kitchen.py: open_kitchen and close_kitchen gate management."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_mock_ctx():
    """Return a minimal mock ToolContext with a gate."""
    gate = MagicMock()
    gate.enabled = False
    ctx = MagicMock()
    ctx.gate = gate
    ctx.config.subsets.disabled = []  # REQ-VIS-008: no subsets disabled by default
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

    # Gate file was never created — confirm it still does not exist
    assert not (tmp_path / ".autoskillit" / "temp" / ".autoskillit_hook_config.json").exists()


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
    # Confirm kitchen_id rename: hook config must contain 'kitchen_id' (not 'pipeline_id')
    assert "kitchen_id" in data, (
        "hook config must contain 'kitchen_id' after rename from 'pipeline_id'"
    )
    assert isinstance(data["kitchen_id"], str) and data["kitchen_id"], (
        "kitchen_id must be a non-empty string (UUID set by _open_kitchen_handler)"
    )


# T-CACHE-3
@pytest.mark.anyio
async def test_close_kitchen_removes_hook_config_json(tmp_path, monkeypatch):
    """close_kitchen must remove temp/.autoskillit_hook_config.json to prevent stale config."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.config.quota_guard.threshold = 85.0
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
    """open_kitchen response contains static categorized tool groups from _DISPLAY_CATEGORIES."""
    from autoskillit.server.tools_kitchen import _DISPLAY_CATEGORIES, open_kitchen

    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.tools_kitchen._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.server.tools_kitchen._write_hook_config"):
                    result = await open_kitchen(ctx=mock_ctx)

    seen: set[str] = set()
    for category_name, tools in _DISPLAY_CATEGORIES:
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
# open_kitchen with recipe name
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_open_kitchen_with_recipe_returns_combined_response(tmp_path, monkeypatch):
    """open_kitchen(name='x') opens kitchen AND loads the recipe in one call."""
    monkeypatch.chdir(tmp_path)
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    yaml_content = (
        "name: test-recipe\ndescription: test\nsteps:\n  do:\n    tool: run_cmd\n"
        "    with:\n      cmd: echo hi\n    on_success: done\n    on_failure: done\n"
        "  done:\n    action: stop\n    message: Done\n"
    )
    (recipes_dir / "test-recipe.yaml").write_text(yaml_content)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.recipes = MagicMock()
    mock_ctx.recipes.load_and_validate.return_value = {
        "content": yaml_content,
        "valid": True,
        "suggestions": [],
        "diagram": None,
    }
    mock_ctx.recipes.find.return_value = None
    mock_ctx.config.migration.suppressed = []

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.tools_kitchen._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.server.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools_kitchen import open_kitchen

                    result_str = await open_kitchen(name="test-recipe", ctx=mock_ctx)

    result = json.loads(result_str)
    assert result["kitchen"] == "open"
    assert "version" in result
    assert "content" in result
    assert "test-recipe" in result["content"]
    mock_ctx.gate.enable.assert_called_once()
    mock_ctx.enable_components.assert_called_once_with(tags={"kitchen"})


@pytest.mark.anyio
async def test_open_kitchen_with_recipe_not_found(tmp_path, monkeypatch):
    """open_kitchen(name='nonexistent') returns error with kitchen still open."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.recipes = MagicMock()
    mock_ctx.recipes.load_and_validate.return_value = {
        "error": "No recipe named 'nonexistent' found",
    }
    mock_ctx.recipes.find.return_value = None
    mock_ctx.config.migration.suppressed = []

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.tools_kitchen._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.server.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools_kitchen import open_kitchen

                    result_str = await open_kitchen(name="nonexistent", ctx=mock_ctx)

    result = json.loads(result_str)
    assert "error" in result
    assert "nonexistent" in result["error"]
    assert result["kitchen"] == "open"
    # Kitchen should still be opened even if recipe fails
    mock_ctx.gate.enable.assert_called_once()


@pytest.mark.anyio
async def test_open_kitchen_without_recipe_returns_plain_text(tmp_path, monkeypatch):
    """open_kitchen() without name returns plain text (not JSON)."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.tools_kitchen._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.server.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools_kitchen import open_kitchen

                    result = await open_kitchen(ctx=mock_ctx)

    assert isinstance(result, str)
    assert "Kitchen is open" in result
    assert "content" not in result, "No-recipe open_kitchen should not contain recipe content key"


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


# T-VIS-003
@pytest.mark.anyio
async def test_open_kitchen_redisables_subsets(tmp_path, monkeypatch):
    """open_kitchen must call ctx.disable_components for each disabled subset."""
    from autoskillit.config.settings import AutomationConfig, SubsetsConfig

    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.disable_components = AsyncMock()
    mock_ctx.config = AutomationConfig(subsets=SubsetsConfig(disabled=["github", "ci"]))

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.tools_kitchen._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.server.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools_kitchen import open_kitchen

                    await open_kitchen(ctx=mock_ctx)

    disable_calls = mock_ctx.disable_components.call_args_list
    disabled_tags = [
        c.kwargs.get("tags") or (c.args[0] if c.args else None) for c in disable_calls
    ]
    assert {"github"} in disabled_tags
    assert {"ci"} in disabled_tags


# T-VIS-004
@pytest.mark.anyio
async def test_open_kitchen_redisable_order(tmp_path, monkeypatch):
    """ctx.disable_components must be called after ctx.enable_components (order matters)."""
    from autoskillit.config.settings import AutomationConfig, SubsetsConfig

    monkeypatch.chdir(tmp_path)
    call_order = []
    mock_ctx = _make_mock_ctx()

    async def record_enable(**kwargs):
        call_order.append(("enable", kwargs))

    async def record_disable(**kwargs):
        call_order.append(("disable", kwargs))

    mock_ctx.enable_components = record_enable
    mock_ctx.disable_components = record_disable
    mock_ctx.config = AutomationConfig(subsets=SubsetsConfig(disabled=["github"]))

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.tools_kitchen._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.server.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools_kitchen import open_kitchen

                    await open_kitchen(ctx=mock_ctx)

    enable_idx = next(i for i, (op, _) in enumerate(call_order) if op == "enable")
    disable_idx = next(i for i, (op, _) in enumerate(call_order) if op == "disable")
    assert enable_idx < disable_idx, "disable_components must be called after enable_components"


# T-VIS-005
@pytest.mark.anyio
async def test_open_kitchen_no_redisable_when_empty(tmp_path, monkeypatch):
    """open_kitchen must NOT call disable_components when no subsets are disabled."""
    from autoskillit.config.settings import AutomationConfig, SubsetsConfig

    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.disable_components = AsyncMock()
    mock_ctx.config = AutomationConfig(subsets=SubsetsConfig(disabled=[]))

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.tools_kitchen._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.server.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools_kitchen import open_kitchen

                    await open_kitchen(ctx=mock_ctx)

    mock_ctx.disable_components.assert_not_called()


# REQ-PACK-008: open_kitchen stores active_recipe_packs
@pytest.mark.anyio
async def test_open_kitchen_sets_active_recipe_packs(tmp_path, monkeypatch):
    """After _open_kitchen_handler(), ctx.active_recipe_packs is frozenset()."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.tools_kitchen._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.server.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools_kitchen import _open_kitchen_handler

                    await _open_kitchen_handler()

    assert mock_ctx.active_recipe_packs == frozenset()


# REQ-PACK-008: close_kitchen clears active_recipe_packs
def test_close_kitchen_clears_active_recipe_packs(tmp_path, monkeypatch):
    """After _close_kitchen_handler(), ctx.active_recipe_packs is None."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.active_recipe_packs = frozenset(["research"])

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            from autoskillit.server.tools_kitchen import _close_kitchen_handler

            _close_kitchen_handler()

    assert mock_ctx.active_recipe_packs is None


# T-REFRESH-1
@pytest.mark.anyio
async def test_open_kitchen_starts_quota_refresh_task(tmp_path, monkeypatch):
    """After _open_kitchen_handler(), ctx.quota_refresh_task is a running asyncio.Task."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()

    async def instant_loop(config):
        await asyncio.sleep(0)

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.tools_kitchen._prime_quota_cache", AsyncMock()):
                with patch("autoskillit.server.tools_kitchen._write_hook_config"):
                    with patch(
                        "autoskillit.server.tools_kitchen._quota_refresh_loop", instant_loop
                    ):
                        from autoskillit.server.tools_kitchen import _open_kitchen_handler

                        await _open_kitchen_handler()

    assert mock_ctx.quota_refresh_task is not None
    assert isinstance(mock_ctx.quota_refresh_task, asyncio.Task)
    mock_ctx.quota_refresh_task.cancel()


# T-REFRESH-2
def test_close_kitchen_cancels_quota_refresh_task(tmp_path, monkeypatch):
    """_close_kitchen_handler cancels ctx.quota_refresh_task and sets it to None."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_task = MagicMock(spec=asyncio.Task)
    mock_ctx.quota_refresh_task = mock_task

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            from autoskillit.server.tools_kitchen import _close_kitchen_handler

            _close_kitchen_handler()

    mock_task.cancel.assert_called_once()
    assert mock_ctx.quota_refresh_task is None


# T-REFRESH-3
def test_tool_context_has_quota_refresh_task_field():
    """ToolContext must have a quota_refresh_task field defaulting to None."""
    from autoskillit.pipeline.context import ToolContext

    fields = {f.name: f for f in dataclasses.fields(ToolContext)}
    assert "quota_refresh_task" in fields
    assert fields["quota_refresh_task"].default is None


# T-KITCHEN-1
@pytest.mark.anyio
async def test_open_kitchen_warns_on_orphaned_hooks(tmp_path, monkeypatch):
    """When settings.json contains a hook not in HOOK_REGISTRY, open_kitchen()
    must include a drift warning in its response."""
    from autoskillit.hook_registry import HookDriftResult

    settings_dir = tmp_path / ".claude"
    settings_dir.mkdir()
    (settings_dir / "settings.json").write_text("{}")

    monkeypatch.setattr(
        "autoskillit.hook_registry._claude_settings_path",
        lambda scope: settings_dir / "settings.json",
    )
    monkeypatch.setattr(
        "autoskillit.hook_registry._count_hook_registry_drift",
        lambda _: HookDriftResult(missing=0, orphaned=1),
    )
    monkeypatch.setattr(
        "autoskillit.hook_registry.find_broken_hook_scripts",
        lambda _: [],
    )

    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.tools_kitchen._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.server.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools_kitchen import open_kitchen

                    result = await open_kitchen(ctx=mock_ctx)

    assert (
        "orphan" in result.lower() or "drift" in result.lower() or "install" in result.lower()
    ), "open_kitchen() must include a hook drift warning when orphaned > 0"


# T-KITCHEN-2
@pytest.mark.anyio
async def test_open_kitchen_warns_on_missing_hook_scripts(tmp_path, monkeypatch):
    """When hook scripts are absent from disk, open_kitchen() must warn."""
    from autoskillit.hook_registry import HookDriftResult

    settings_dir = tmp_path / ".claude"
    settings_dir.mkdir()
    (settings_dir / "settings.json").write_text("{}")

    monkeypatch.setattr(
        "autoskillit.hook_registry._claude_settings_path",
        lambda scope: settings_dir / "settings.json",
    )
    monkeypatch.setattr(
        "autoskillit.hook_registry.find_broken_hook_scripts",
        lambda _: ["python3 /missing/status_health_guard.py"],
    )
    monkeypatch.setattr(
        "autoskillit.hook_registry._count_hook_registry_drift",
        lambda _: HookDriftResult(missing=0, orphaned=0),
    )

    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.tools_kitchen._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.server.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools_kitchen import open_kitchen

                    result = await open_kitchen(ctx=mock_ctx)

    assert "Hook scripts not found" in result, (
        "open_kitchen() must include the exact _build_hook_diagnostic_warning phrase"
    )
