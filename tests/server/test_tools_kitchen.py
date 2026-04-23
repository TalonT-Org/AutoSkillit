"""Tests for server/tools_kitchen.py: open_kitchen and close_kitchen gate management."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autoskillit.config.settings import QuotaGuardConfig
from autoskillit.hooks._fmt_primitives import _HOOK_CONFIG_PATH_COMPONENTS

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


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


def test_hook_config_path_is_inside_temp_dir(tmp_path):
    """_hook_config_path() must resolve to a path inside the project temp directory.

    This invariant ensures .hook_config.json receives automatic gitignore coverage
    from temp/.gitignore ('*') and can never produce a gitignore gap regardless of
    whatever entries are present in .gitignore or _AUTOSKILLIT_GITIGNORE_ENTRIES.
    """
    from autoskillit.core.io import resolve_temp_dir
    from autoskillit.server.helpers import _hook_config_path

    hook_path = _hook_config_path(tmp_path)
    temp_dir = resolve_temp_dir(tmp_path, None)

    assert hook_path.is_relative_to(temp_dir), (
        f"_hook_config_path() returned {hook_path!r} which is NOT inside "
        f"temp dir {temp_dir!r}. Session-bridge files must live in temp/ "
        f"to receive automatic gitignore coverage via temp/.gitignore."
    )


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
    assert not tmp_path.joinpath(*_HOOK_CONFIG_PATH_COMPONENTS).exists()


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
    """open_kitchen must write .autoskillit/.hook_config.json with user quota_guard values."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.config.quota_guard.short_window_threshold = 85.0
    mock_ctx.config.quota_guard.long_window_threshold = 98.0
    mock_ctx.config.quota_guard.long_window_patterns = ["weekly", "sonnet", "opus"]
    mock_ctx.config.quota_guard.cache_max_age = 300
    mock_ctx.config.quota_guard.cache_path = "/custom/path.json"
    mock_ctx.config.quota_guard.buffer_seconds = 60

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
    hook_cfg = tmp_path.joinpath(*_HOOK_CONFIG_PATH_COMPONENTS)
    assert hook_cfg.exists(), "Hook config file must be written by open_kitchen"
    data = json.loads(hook_cfg.read_text())
    assert data["quota_guard"]["cache_max_age"] == 300
    assert data["quota_guard"]["cache_path"] == "/custom/path.json"
    assert data["quota_guard"]["buffer_seconds"] == 60
    # threshold fields are pre-computed into should_block in the cache — not written to hook_config
    assert "threshold" not in data["quota_guard"]
    assert "short_window_threshold" not in data["quota_guard"]
    assert "long_window_threshold" not in data["quota_guard"]
    assert "long_window_patterns" not in data["quota_guard"]
    # disabled is always written by _quota_guard_hook_payload
    # MagicMock.enabled is truthy by default, so disabled must be False
    assert data["quota_guard"]["disabled"] is False
    # Confirm kitchen_id rename: hook config must contain 'kitchen_id' (not 'pipeline_id')
    assert "kitchen_id" in data, (
        "hook config must contain 'kitchen_id' after rename from 'pipeline_id'"
    )
    assert isinstance(data["kitchen_id"], str) and data["kitchen_id"], (
        "kitchen_id must be a non-empty string (UUID set by _open_kitchen_handler)"
    )


@pytest.mark.parametrize("enabled,expected_disabled", [(True, False), (False, True)])
@pytest.mark.anyio
async def test_open_kitchen_bridges_enabled_flag_as_disabled(
    tmp_path, monkeypatch, enabled, expected_disabled
):
    """_write_hook_config() must serialize cfg.enabled as disabled: not cfg.enabled.

    Regression test for bridge completeness: QuotaGuardConfig.enabled must
    translate to quota_guard.disabled in .hook_config.json so that the hook
    subprocess can respect the profile-wide opt-out.
    """
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.config.quota_guard = QuotaGuardConfig(
        enabled=enabled, cache_max_age=300, cache_path="/p/q.json", buffer_seconds=60
    )

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.tools_kitchen._prime_quota_cache", new=AsyncMock()):
                from autoskillit.server.tools_kitchen import _open_kitchen_handler

                await _open_kitchen_handler()

    data = json.loads(tmp_path.joinpath(*_HOOK_CONFIG_PATH_COMPONENTS).read_text())
    assert data["quota_guard"]["disabled"] is expected_disabled, (
        f"enabled={enabled} must produce disabled={expected_disabled} in hook config"
    )


# T-CACHE-3
@pytest.mark.anyio
async def test_close_kitchen_removes_hook_config_json(tmp_path, monkeypatch):
    """close_kitchen must remove .autoskillit/.hook_config.json to prevent stale config."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.config.quota_guard.short_window_threshold = 85.0
    mock_ctx.config.quota_guard.long_window_threshold = 98.0
    mock_ctx.config.quota_guard.long_window_patterns = ["weekly", "sonnet", "opus"]
    mock_ctx.config.quota_guard.cache_max_age = 300
    mock_ctx.config.quota_guard.cache_path = "~/.claude/quota_cache.json"
    mock_ctx.config.quota_guard.buffer_seconds = 60

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.tools_kitchen._prime_quota_cache", new=AsyncMock()):
                from autoskillit.server.tools_kitchen import (
                    _close_kitchen_handler,
                    _open_kitchen_handler,
                )

                await _open_kitchen_handler()
                _close_kitchen_handler()

    hook_cfg = tmp_path.joinpath(*_HOOK_CONFIG_PATH_COMPONENTS)
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
    mock_ctx.config.quota_guard.short_window_threshold = 85.0
    mock_ctx.config.quota_guard.long_window_threshold = 98.0
    mock_ctx.config.quota_guard.long_window_patterns = ["weekly", "sonnet", "opus"]
    mock_ctx.config.quota_guard.cache_max_age = 300
    mock_ctx.config.quota_guard.cache_path = "~/.claude/quota_cache.json"
    mock_ctx.config.quota_guard.buffer_seconds = 60
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
                    result_str = await open_kitchen(ctx=mock_ctx)

    parsed = json.loads(result_str)
    content = parsed["content"]
    seen: set[str] = set()
    for category_name, tools in _DISPLAY_CATEGORIES:
        assert category_name in content, (
            f"Category '{category_name}' missing from open_kitchen response"
        )
        for tool_name in tools:
            if tool_name not in seen:
                assert tool_name in content, (
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
    assert result["success"] is True
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
async def test_open_kitchen_without_recipe_returns_json_envelope(tmp_path, monkeypatch):
    """open_kitchen() without name returns JSON envelope with success=True."""
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
    parsed = json.loads(result)
    assert parsed["success"] is True
    assert parsed["kitchen"] == "open"
    assert "Kitchen is open" in parsed["content"]
    assert parsed["ingredients_table"] is None


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
    assert result["kitchen"] == "failed"
    assert "user_visible_message" in result
    assert len(result["user_visible_message"]) > 0
    assert result["stage"] == "headless_guard"


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

    parsed = json.loads(result)
    content = parsed["content"]
    assert (
        "orphan" in content.lower() or "drift" in content.lower() or "install" in content.lower()
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

    parsed = json.loads(result)
    content = parsed["content"]
    assert "Hook scripts not found" in content, (
        "open_kitchen() must include the exact _build_hook_diagnostic_warning phrase"
    )


@pytest.mark.anyio
async def test_prime_quota_cache_catches_typeerror(monkeypatch):
    """_prime_quota_cache must catch TypeError and not propagate — 'fails open' contract."""
    import autoskillit.server.helpers as _helpers_mod
    from autoskillit.server.helpers import _prime_quota_cache

    async def raise_type_error(*a, **kw):
        raise TypeError("float() argument must be a string or a real number, not 'NoneType'")

    monkeypatch.setattr(_helpers_mod, "check_and_sleep_if_needed", raise_type_error)

    mock_ctx = MagicMock()
    mock_ctx.config.quota_guard = MagicMock()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.helpers.logger") as mock_logger:
            # Must not raise — fails open
            await _prime_quota_cache()
            mock_logger.warning.assert_called_once_with("quota_prime_failed", exc_info=True)


# ---------------------------------------------------------------------------
# Envelope contract tests — Phase 3 (#711 Part B)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_open_kitchen_no_name_returns_json_envelope_with_success_true(tmp_path, monkeypatch):
    """No-recipe open_kitchen returns JSON envelope with success=True."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.tools_kitchen._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.server.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools_kitchen import open_kitchen

                    result = await open_kitchen(ctx=mock_ctx)

    parsed = json.loads(result)
    assert parsed["success"] is True
    assert parsed["kitchen"] == "open"
    assert "Kitchen is open" in parsed["content"]
    assert parsed["ingredients_table"] is None


@pytest.mark.anyio
async def test_open_kitchen_recipe_found_returns_envelope_with_content_and_ingredients_table(
    tmp_path, monkeypatch
):
    """Recipe loads successfully: success=True, kitchen=open, version present."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.recipes = MagicMock()
    mock_ctx.recipes.load_and_validate.return_value = {
        "content": "name: demo\nsteps:\n  do:\n    tool: run_cmd\n",
        "valid": True,
        "suggestions": [],
        "diagram": None,
        "ingredients_table": "--- INGREDIENTS TABLE ---\n  task  required\n--- END TABLE ---",
    }
    mock_ctx.recipes.find.return_value = None
    mock_ctx.config.migration.suppressed = []

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.tools_kitchen._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.server.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools_kitchen import open_kitchen

                    result_str = await open_kitchen(name="demo", ctx=mock_ctx)

    parsed = json.loads(result_str)
    assert parsed["success"] is True
    assert parsed["kitchen"] == "open"
    assert "version" in parsed
    assert "--- INGREDIENTS TABLE ---" in result_str


@pytest.mark.anyio
async def test_open_kitchen_smoke_test_renders_resolved_base_branch(monkeypatch):
    """T7: open_kitchen smoke-test renders the config-resolved base_branch value."""
    import autoskillit.recipe._api as api_mod
    from autoskillit.core import pkg_root
    from autoskillit.recipe.repository import DefaultRecipeRepository

    project_dir = pkg_root().parent.parent
    monkeypatch.chdir(project_dir)
    monkeypatch.setattr(api_mod, "_LOAD_CACHE", {})
    monkeypatch.setattr(
        "autoskillit.server.tools_kitchen.resolve_ingredient_defaults",
        lambda _: {"base_branch": "integration"},
    )

    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.quota_refresh_task = None
    mock_ctx.recipes = DefaultRecipeRepository()
    mock_ctx.config.migration.suppressed = []

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.tools_kitchen._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.server.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools_kitchen import open_kitchen

                    result_str = await open_kitchen(name="smoke-test", ctx=mock_ctx)

    parsed = json.loads(result_str)
    assert parsed["success"] is True
    ing_table = parsed.get("ingredients_table") or ""
    assert ing_table, "ingredients_table must be present and non-empty"
    assert "integration" in ing_table
    # base_branch row must NOT show the YAML literal "main"
    base_branch_rows = [line for line in ing_table.splitlines() if "base_branch" in line]
    assert base_branch_rows, "base_branch row must appear in ingredients_table"
    assert all("main" not in row for row in base_branch_rows)


@pytest.mark.anyio
async def test_open_kitchen_recipe_not_found_returns_failure_envelope(tmp_path, monkeypatch):
    """Invalid recipe name returns failure envelope (via load_and_validate raising)."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.recipes = MagicMock()
    mock_ctx.recipes.load_and_validate.side_effect = ValueError("No recipe 'bad' found")
    mock_ctx.config.migration.suppressed = []

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.tools_kitchen._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.server.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools_kitchen import open_kitchen

                    result_str = await open_kitchen(name="bad", ctx=mock_ctx)

    parsed = json.loads(result_str)
    assert parsed["success"] is False
    assert parsed["kitchen"] == "failed"
    assert len(parsed["user_visible_message"]) > 0
    assert "ValueError" in parsed["error"]


@pytest.mark.anyio
async def test_open_kitchen_server_not_initialized_returns_failure_envelope(tmp_path, monkeypatch):
    """tool_ctx.recipes is None → failure envelope with user_visible_message."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.recipes = None
    mock_ctx.config.migration.suppressed = []

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.tools_kitchen._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.server.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools_kitchen import open_kitchen

                    result_str = await open_kitchen(name="demo", ctx=mock_ctx)

    parsed = json.loads(result_str)
    assert parsed["success"] is False
    assert parsed["kitchen"] == "failed"
    assert "user_visible_message" in parsed
    assert "not initialized" in parsed["user_visible_message"]


@pytest.mark.anyio
async def test_open_kitchen_headless_denied_returns_failure_envelope(tmp_path, monkeypatch):
    """AUTOSKILLIT_HEADLESS=1: failure envelope with user_visible_message present."""
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.chdir(tmp_path)
    from autoskillit.server.tools_kitchen import open_kitchen

    result = json.loads(await open_kitchen())
    assert result["success"] is False
    assert result["kitchen"] == "failed"
    assert "user_visible_message" in result
    assert len(result["user_visible_message"]) > 0
    assert result["stage"] == "headless_guard"


@pytest.mark.anyio
async def test_open_kitchen_prime_quota_cache_typeerror_returns_failure_envelope(
    tmp_path, monkeypatch
):
    """_prime_quota_cache raising TypeError → failure envelope with stage=prime_quota_cache."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()

    async def raise_type_error():
        raise TypeError("float() argument must be a string or a real number, not 'NoneType'")

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools_kitchen._prime_quota_cache",
                new=raise_type_error,
            ):
                with patch("autoskillit.server.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools_kitchen import open_kitchen

                    result_str = await open_kitchen(ctx=mock_ctx)

    parsed = json.loads(result_str)
    assert parsed["success"] is False
    assert parsed["kitchen"] == "failed"
    assert parsed["stage"] == "prime_quota_cache"
    assert "TypeError" in parsed["error"]
    assert len(parsed["user_visible_message"]) > 0


@pytest.mark.anyio
async def test_open_kitchen_prime_quota_cache_runtimeerror_returns_failure_envelope(
    tmp_path, monkeypatch
):
    """_prime_quota_cache raising RuntimeError → failure envelope."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()

    async def raise_runtime():
        raise RuntimeError("unexpected failure")

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools_kitchen._prime_quota_cache",
                new=raise_runtime,
            ):
                with patch("autoskillit.server.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools_kitchen import open_kitchen

                    result_str = await open_kitchen(ctx=mock_ctx)

    parsed = json.loads(result_str)
    assert parsed["success"] is False
    assert parsed["stage"] == "prime_quota_cache"


@pytest.mark.anyio
async def test_open_kitchen_create_background_task_raises_returns_failure_envelope(
    tmp_path, monkeypatch
):
    """create_background_task raising → failure envelope with stage=start_quota_refresh."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.tools_kitchen._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.server.tools_kitchen._write_hook_config"):
                    with patch(
                        "autoskillit.server.tools_kitchen.create_background_task",
                        side_effect=RuntimeError("task creation failed"),
                    ):
                        from autoskillit.server.tools_kitchen import open_kitchen

                        result_str = await open_kitchen(ctx=mock_ctx)

    parsed = json.loads(result_str)
    assert parsed["success"] is False
    assert parsed["stage"] == "start_quota_refresh"


@pytest.mark.anyio
async def test_open_kitchen_load_and_validate_raises_returns_failure_envelope(
    tmp_path, monkeypatch
):
    """load_and_validate raising → failure envelope with stage=load_and_validate."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.recipes = MagicMock()
    mock_ctx.recipes.load_and_validate.side_effect = OSError("disk full")
    mock_ctx.config.migration.suppressed = []

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.tools_kitchen._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.server.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools_kitchen import open_kitchen

                    result_str = await open_kitchen(name="demo", ctx=mock_ctx)

    parsed = json.loads(result_str)
    assert parsed["success"] is False
    assert parsed["stage"] == "load_and_validate"


@pytest.mark.anyio
async def test_open_kitchen_apply_triage_gate_raises_returns_failure_envelope(
    tmp_path, monkeypatch
):
    """_apply_triage_gate raising → failure envelope with stage=apply_triage_gate."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.recipes = MagicMock()
    mock_ctx.recipes.load_and_validate.return_value = {
        "content": "test",
        "valid": True,
        "suggestions": [],
    }
    mock_ctx.recipes.find.return_value = None
    mock_ctx.config.migration.suppressed = []

    async def raise_apply(*a, **kw):
        raise RuntimeError("triage failed")

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.tools_kitchen._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.server.tools_kitchen._write_hook_config"):
                    with patch(
                        "autoskillit.server.tools_kitchen._apply_triage_gate",
                        new=raise_apply,
                    ):
                        from autoskillit.server.tools_kitchen import open_kitchen

                        result_str = await open_kitchen(name="demo", ctx=mock_ctx)

    parsed = json.loads(result_str)
    assert parsed["success"] is False
    assert parsed["stage"] == "apply_triage_gate"


@pytest.mark.anyio
async def test_open_kitchen_enable_components_raises_returns_failure_envelope(
    tmp_path, monkeypatch
):
    """ctx.enable_components raising → failure envelope with stage=enable_components."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock(side_effect=RuntimeError("enable failed"))

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.tools_kitchen._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.server.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools_kitchen import open_kitchen

                    result_str = await open_kitchen(ctx=mock_ctx)

    parsed = json.loads(result_str)
    assert parsed["success"] is False
    assert parsed["stage"] == "enable_components"


@pytest.mark.anyio
async def test_open_kitchen_sous_chef_read_raises_returns_failure_envelope(tmp_path, monkeypatch):
    """Path.read_text raising OSError → failure envelope with stage=read_sous_chef."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()

    import autoskillit.server.tools_kitchen as tk_mod

    _original_pkg_root = tk_mod.pkg_root

    def fake_pkg_root():
        root = tmp_path / "fake_pkg"
        sc_path = root / "skills" / "sous-chef"
        sc_path.mkdir(parents=True, exist_ok=True)
        skill_md = sc_path / "SKILL.md"
        skill_md.write_text("dummy")
        skill_md.chmod(0o000)
        return root

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.tools_kitchen._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.server.tools_kitchen._write_hook_config"):
                    with patch.object(tk_mod, "pkg_root", fake_pkg_root):
                        from autoskillit.server.tools_kitchen import open_kitchen

                        result_str = await open_kitchen(ctx=mock_ctx)

    # Restore permissions for cleanup
    for p in tmp_path.rglob("SKILL.md"):
        p.chmod(0o644)

    parsed = json.loads(result_str)
    assert parsed["success"] is False
    assert parsed["stage"] == "read_sous_chef"


# Parametrized: every failure envelope has user_visible_message
_FAILURE_STAGES = [
    "headless_guard",
    "prime_quota_cache",
    "start_quota_refresh",
    "enable_components",
    "load_and_validate",
    "apply_triage_gate",
    "recipe_context",
]


@pytest.mark.parametrize("stage", _FAILURE_STAGES)
def test_every_failure_envelope_has_user_visible_message(stage):
    """All failure envelopes have a non-empty user_visible_message string."""
    from autoskillit.server.tools_kitchen import _kitchen_failure_envelope

    envelope = json.loads(_kitchen_failure_envelope(RuntimeError("test"), stage=stage))
    assert isinstance(envelope["user_visible_message"], str)
    assert len(envelope["user_visible_message"]) > 0
    assert envelope["success"] is False
    assert envelope["kitchen"] == "failed"


@pytest.mark.parametrize(
    "stage",
    _FAILURE_STAGES + ["hook_diagnostic", "read_sous_chef", "redisable_subsets"],
)
def test_every_return_path_parses_as_json_and_has_boolean_success(stage):
    """Every failure envelope parses as JSON with boolean success."""
    from autoskillit.server.tools_kitchen import _kitchen_failure_envelope

    envelope = json.loads(_kitchen_failure_envelope(RuntimeError("test"), stage=stage))
    assert isinstance(envelope["success"], bool)


@pytest.mark.anyio
async def test_disable_quota_guard_writes_disabled_flag(tmp_path, monkeypatch):
    """disable_quota_guard() sets quota_guard.disabled=True in the hook config file."""
    monkeypatch.chdir(tmp_path)
    hook_cfg_path = tmp_path.joinpath(*_HOOK_CONFIG_PATH_COMPONENTS)
    hook_cfg_path.parent.mkdir(parents=True, exist_ok=True)
    hook_cfg_path.write_text(
        json.dumps({"quota_guard": {"cache_path": "/some/path.json", "cache_max_age": 300}})
    )

    from autoskillit.server.tools_kitchen import disable_quota_guard

    result_str = await disable_quota_guard()
    parsed = json.loads(result_str)
    assert parsed["success"] is True

    payload = json.loads(hook_cfg_path.read_text())
    assert payload["quota_guard"]["disabled"] is True


@pytest.mark.anyio
async def test_disable_quota_guard_denies_headless(tmp_path, monkeypatch):
    """disable_quota_guard() returns an error when called from a headless session."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")

    from autoskillit.server.tools_kitchen import disable_quota_guard

    result_str = await disable_quota_guard()
    parsed = json.loads(result_str)
    assert parsed["success"] is False


@pytest.mark.anyio
async def test_disable_quota_guard_returns_error_when_kitchen_not_open(tmp_path, monkeypatch):
    """disable_quota_guard() returns an error when the kitchen is not open."""
    monkeypatch.chdir(tmp_path)

    from autoskillit.server.tools_kitchen import disable_quota_guard

    result_str = await disable_quota_guard()
    parsed = json.loads(result_str)
    assert parsed["success"] is False


# ---------------------------------------------------------------------------
# Delivery-verification: sous-chef discipline on named open_kitchen path (1a)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_sous_chef_discipline_injected_on_named_open_kitchen_path(tmp_path, monkeypatch):
    """Named path (Path A) must deliver sous-chef discipline to all session types.

    Headless L2 sessions receive no system prompt injection, so the only delivery
    channel is the open_kitchen response. This test verifies the discipline section
    is present in the result dict under the 'sous_chef_discipline' key.
    """
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.recipes = MagicMock()
    mock_ctx.recipes.load_and_validate.return_value = {
        "content": "name: implementation\nsteps:\n  do:\n    tool: run_cmd\n",
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

                    result_str = await open_kitchen(name="implementation", ctx=mock_ctx)

    result = json.loads(result_str)
    assert result["success"] is True
    discipline = result["sous_chef_discipline"]
    assert "STEP EXECUTION IS NOT DISCRETIONARY" in discipline, (
        "Named open_kitchen path must inject sous-chef discipline — "
        "headless L2 sessions have no system prompt injection"
    )
    assert "NEVER skip a step because" in discipline
    assert "on_context_limit routing" in discipline, (
        "Discipline section must include context-ownership line so model does not "
        "use context pressure as a rationalization for step-skipping"
    )


# ---------------------------------------------------------------------------
# Path B baseline regression tests (1e): guard against accidental breakage
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_sous_chef_rules_injected_at_open_kitchen(tmp_path, monkeypatch):
    """Path B (no-name) must inject full sous-chef SKILL.md into response text."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.tools_kitchen._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.server.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools_kitchen import open_kitchen

                    result = await open_kitchen(ctx=mock_ctx)

    parsed = json.loads(result)
    content = parsed["content"]
    assert "STEP EXECUTION IS NOT DISCRETIONARY" in content, (
        "Path B open_kitchen must include sous-chef STEP EXECUTION discipline in response"
    )
    assert "NEVER skip a step because" in content


@pytest.mark.anyio
async def test_open_kitchen_degrades_gracefully_without_sous_chef(tmp_path, monkeypatch):
    """When sous-chef SKILL.md is absent, Path B must return a valid response without raising."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()

    import autoskillit.server.tools_kitchen as tk_mod

    fake_pkg_root_dir = tmp_path / "fake_pkg"
    (fake_pkg_root_dir / "skills" / "sous-chef").mkdir(parents=True)
    # sous-chef SKILL.md is deliberately absent — directory exists, file does not

    def fake_pkg_root() -> object:
        return fake_pkg_root_dir

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.tools_kitchen._prime_quota_cache", new=AsyncMock()):
                with patch("autoskillit.server.tools_kitchen._write_hook_config"):
                    with patch.object(tk_mod, "pkg_root", fake_pkg_root):
                        from autoskillit.server.tools_kitchen import open_kitchen

                        result = await open_kitchen(ctx=mock_ctx)

    parsed = json.loads(result)
    assert parsed["success"] is True


@pytest.mark.anyio
async def test_redisable_subsets_includes_feature_tags() -> None:
    """When franchise feature is disabled, _redisable_subsets disables franchise tag."""
    from autoskillit.server.tools_kitchen import _redisable_subsets

    mock_ctx = AsyncMock()

    await _redisable_subsets(mock_ctx, [], features={"franchise": False})

    calls = mock_ctx.disable_components.call_args_list
    disabled_tag_sets = [c.kwargs.get("tags", set()) for c in calls]
    assert any("franchise" in tags for tags in disabled_tag_sets), (
        "franchise tag must be disabled when franchise feature is off"
    )


def test_exclusive_feature_tools_fully_hidden() -> None:
    """EXCLUSIVE_FEATURE_TOOLS is a dict (currently empty — structural test)."""
    from autoskillit.core import EXCLUSIVE_FEATURE_TOOLS

    assert isinstance(EXCLUSIVE_FEATURE_TOOLS, dict)
    assert EXCLUSIVE_FEATURE_TOOLS == {}


@pytest.mark.anyio
async def test_redisable_subsets_does_not_disable_kitchen_core_tag() -> None:
    """_redisable_subsets must not pass kitchen-core to disable_components.

    FastMCP union model: any enabled tag keeps the tool visible. Verifies that
    kitchen-core is not included in the suppressed tag sets so that tools with
    the kitchen-core tag retain visibility after the feature gate pass.
    """
    from autoskillit.server.tools_kitchen import _redisable_subsets

    disabled_tags: list[set] = []
    mock_ctx = AsyncMock()

    async def capture_disable(*, tags):
        disabled_tags.append(tags)

    mock_ctx.disable_components.side_effect = capture_disable

    # No subsets disabled, but franchise feature is explicitly disabled
    await _redisable_subsets(mock_ctx, [], features={"franchise": False})

    # franchise tag should be disabled
    assert any("franchise" in t for t in disabled_tags), (
        "franchise tag must be suppressed when feature is off"
    )
    # kitchen-core must NOT be in the disabled set (union model: still visible)
    assert not any("kitchen-core" in t for t in disabled_tags), (
        "kitchen-core tag must never be disabled by the feature gate pass"
    )
