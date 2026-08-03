"""Tests for tools_kitchen.py: visibility, component management, sous-chef, redisable_subsets."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autoskillit.core.types._type_constants import SOUS_CHEF_MANDATORY_SECTIONS
from tests.server._helpers import _with_finalized_projection
from tests.server.conftest import _make_mock_ctx

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


# ---------------------------------------------------------------------------
# Group C — visibility + component management
# ---------------------------------------------------------------------------


# T-VISIBILITY-1a: open_kitchen calls enable_components for notification-capable backend
@pytest.mark.anyio
async def test_open_kitchen_calls_enable_components_for_notification_backend(
    tmp_path, monkeypatch
):
    """open_kitchen must call ctx.enable_components when backend supports notifications."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.backend.capabilities.supports_tool_list_changed = True

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import open_kitchen

                    await open_kitchen(ctx=mock_ctx)

    mock_ctx.enable_components.assert_called_once_with(tags={"kitchen"})


# T-VISIBILITY-1b: open_kitchen skips enable_components for pre-revealed backend
@pytest.mark.anyio
async def test_open_kitchen_skips_enable_components_for_pre_revealed_backend(
    tmp_path, monkeypatch
):
    """open_kitchen must NOT call ctx.enable_components when backend was pre-revealed.

    This is the ``_use_global_enable`` branch (formerly named ``_skip_notify``): when
    the backend was pre-revealed at boot, open_kitchen re-enables via the global
    ``mcp.enable(tags=...)`` provider rather than the session-scoped
    ``ctx.enable_components``. Emitting the tools/list_changed notification on that
    branch is a separate concern, covered by T-VISIBILITY-3 and the close->open
    roundtrip test.
    """
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.backend.capabilities.supports_tool_list_changed = False

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import open_kitchen

                    await open_kitchen(ctx=mock_ctx)

    mock_ctx.enable_components.assert_not_called()


# T-VISIBILITY-2: close_kitchen tool calls ctx.reset_visibility and mcp.disable
@pytest.mark.anyio
async def test_close_kitchen_tool_calls_reset_visibility(tmp_path, monkeypatch):
    """close_kitchen must call mcp.disable() for pre-revealed tags, then ctx.reset_visibility()."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.reset_visibility = AsyncMock()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.tools.tools_kitchen.mcp") as mock_mcp:
                from autoskillit.server.tools.tools_kitchen import close_kitchen

                await close_kitchen(ctx=mock_ctx)

    mock_mcp.disable.assert_any_call(tags={"kitchen"})
    mock_mcp.disable.assert_any_call(tags={"plan-review"})
    assert mock_mcp.disable.call_count == 2
    mock_ctx.reset_visibility.assert_called_once()


# T-VISIBILITY-2b: close_kitchen roundtrip — pre-revealed kitchen tools hidden after close
@pytest.mark.anyio
async def test_close_kitchen_hides_pre_revealed_tools(tmp_path, monkeypatch):
    """close_kitchen must remove pre-revealed kitchen tools from list_tools() roundtrip."""
    monkeypatch.chdir(tmp_path)
    from autoskillit.core import FLEET_DISPATCH_TOOLS, FLEET_TOOLS, GATED_TOOLS
    from autoskillit.server import mcp
    from autoskillit.server.tools.tools_kitchen import close_kitchen

    mcp.enable(tags={"kitchen"})
    tools_before = {t.name for t in await mcp.list_tools()}
    kitchen_gated = GATED_TOOLS - FLEET_TOOLS - FLEET_DISPATCH_TOOLS
    assert kitchen_gated.issubset(tools_before), "kitchen tools should be visible after enable"

    mock_ctx = _make_mock_ctx()
    mock_ctx.reset_visibility = AsyncMock()

    with patch("autoskillit.server.tools.tools_kitchen._close_kitchen_handler"):
        await close_kitchen(ctx=mock_ctx)

    tools_after = {t.name for t in await mcp.list_tools()}
    assert not kitchen_gated.intersection(tools_after), (
        "kitchen tools should be hidden after close_kitchen"
    )


# T-VISIBILITY-3a: client-cache coherence — close→open roundtrip restores tools via session
@pytest.mark.anyio
async def test_close_open_roundtrip_restores_tools_via_session(tool_ctx):
    """Issue #4399 criterion 2: after close_kitchen() hides pre-revealed kitchen tools from a
    connected Client session, a subsequent open_kitchen() must restore them — even when the
    backend declares `supports_tool_list_changed=False` (Claude Code / Codex).

    This test fails before the fix because open_kitchen's `_use_global_enable` branch
    (formerly `_skip_notify`) silently re-enables the global tags without sending a
    ToolListChangedNotification. The connected Client keeps serving the stale post-close
    cache, so `client.list_tools()` returns an empty kitchen-tools set even though the
    server-side state is correct.

    The fix adds an explicit `ctx.send_notification(ToolListChangedNotification())` call
    inside the `_use_global_enable` branch, which causes the Client to invalidate its
    cache and re-query tools/list — restoring the kitchen tools to the next
    `client.list_tools()` response.
    """
    from unittest.mock import MagicMock

    from fastmcp.client import Client

    from autoskillit.core import FLEET_DISPATCH_TOOLS, FLEET_TOOLS, GATED_TOOLS
    from autoskillit.server import mcp

    # Mirror production Claude Code / Codex: backend present and
    # supports_tool_list_changed=False so open_kitchen enters the _use_global_enable
    # branch. The Client is in-process (no subprocess); the FastMCP Context it creates
    # has a real `.session` so `ctx.reset_visibility()` and `ctx.send_notification()`
    # actually send notifications through the wire.
    mock_backend = MagicMock()
    mock_backend.capabilities.supports_tool_list_changed = False
    tool_ctx.backend = mock_backend

    # Simulate _pre_reveal_kitchen()'s effect on the global mcp singleton (boot-time
    # global enable; per the established convention used by kitchen_enabled and
    # test_server_init_gate::test_tool_list_changes_after_enable_within_session).
    mcp.enable(tags={"kitchen"})
    mcp.enable(tags={"plan-review"})

    # tool_ctx.gate_infrastructure_ready=True mirrors the post-_pre_reveal_kitchen()
    # boot state so the first open_kitchen would take the _skip_handler branch; close
    # unconditionally flips it back to False (line 612), so the subsequent open_kitchen
    # correctly enters `if not _skip_handler:` and exercises the _use_global_enable path.
    tool_ctx.gate_infrastructure_ready = True
    kitchen_gated = GATED_TOOLS - FLEET_TOOLS - FLEET_DISPATCH_TOOLS

    async with Client(mcp) as client:
        # Step 3: tools visible after global pre-reveal.
        tools_before = {t.name for t in await client.list_tools()}
        assert kitchen_gated.issubset(tools_before), (
            "kitchen tools should be visible after pre-reveal enable"
        )

        # Step 4: real close_kitchen through the connected Client session.
        await client.call_tool("close_kitchen", {})

        # Step 5: tools hidden after close_kitchen's notification propagates.
        tools_after_close = {t.name for t in await client.list_tools()}
        assert not kitchen_gated.intersection(tools_after_close), (
            "kitchen tools should be hidden after close_kitchen"
        )

        # Step 6: real open_kitchen (Claude Code backend, no tool_list_changed).
        await client.call_tool("open_kitchen", {})

        # Step 7: tools restored after open_kitchen's notification propagates.
        # BEFORE the fix, this fails — the global mcp.enable() calls succeed
        # server-side but no notification reaches the Client, so the Client keeps
        # serving the stale post-close cache.
        tools_after_reopen = {t.name for t in await client.list_tools()}
        assert kitchen_gated.issubset(tools_after_reopen), (
            "kitchen tools should be visible after open_kitchen restores pre-revealed tags"
        )


# T-VISIBILITY-3: close_kitchen→open_kitchen roundtrip — pre-revealed kitchen tools restored
@pytest.mark.anyio
async def test_open_kitchen_after_close_restores_pre_revealed_tools(tmp_path, monkeypatch):
    """Issue #4399: after close_kitchen() hides pre-revealed kitchen tools, a subsequent
    open_kitchen() with `_use_global_enable=True` (Claude Code backend, no tool/list_changed
    notification) must re-enable the kitchen and plan-review tags so the tools return
    to list_tools(). Without the fix, the `_use_global_enable` branch only logs a debug
    message and never calls `mcp.enable()`, leaving tools invisible.
    """
    monkeypatch.chdir(tmp_path)
    from autoskillit.core import FLEET_DISPATCH_TOOLS, FLEET_TOOLS, GATED_TOOLS
    from autoskillit.server import mcp
    from autoskillit.server.tools.tools_kitchen import close_kitchen

    # Simulate _pre_reveal_kitchen() at startup: enable both tags globally.
    mcp.enable(tags={"kitchen"})
    mcp.enable(tags={"plan-review"})
    tools_before = {t.name for t in await mcp.list_tools()}
    kitchen_gated = GATED_TOOLS - FLEET_TOOLS - FLEET_DISPATCH_TOOLS
    assert kitchen_gated.issubset(tools_before), (
        "kitchen tools should be visible after pre-reveal enable"
    )

    # First lifecycle: gate_infrastructure_ready=True (prior successful open_kitchen).
    # close_kitchen sets it back to False (line 603), so the next open_kitchen
    # correctly enters the `if not _skip_handler:` block at line 876.
    mock_ctx = _make_mock_ctx()
    mock_ctx.gate_infrastructure_ready = True
    mock_ctx.reset_visibility = AsyncMock()

    # close_kitchen uses real _close_kitchen_handler so gate_infrastructure_ready
    # transition (True → False) is authentic. Patch _get_ctx so _close_kitchen_handler
    # operates on mock_ctx (the function reads from _get_ctx, not the ctx parameter).
    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        await close_kitchen(ctx=mock_ctx)
    assert mock_ctx.gate_infrastructure_ready is False

    tools_after_close = {t.name for t in await mcp.list_tools()}
    assert not kitchen_gated.intersection(tools_after_close), (
        "kitchen tools should be hidden after close_kitchen"
    )

    # Second lifecycle: open_kitchen with Claude Code backend (no tool_list_changed).
    # _skip_handler = False (gate_infrastructure_ready was reset by close_kitchen),
    # so we enter the `if not _skip_handler:` block at line 876. Inside it,
    # _use_global_enable = (backend is not None and not supports_tool_list_changed) = True,
    # so the buggy branch at line 882 is executed. Without the fix, the tools
    # remain hidden. With the fix, global mcp.enable() restores visibility.
    mock_ctx.backend.capabilities.supports_tool_list_changed = False
    mock_ctx.enable_components = AsyncMock()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch(
                    "autoskillit.server.tools.tools_kitchen._open_kitchen_handler",
                    new=AsyncMock(return_value=None),
                ):
                    with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                        from autoskillit.server.tools.tools_kitchen import open_kitchen

                        await open_kitchen(ctx=mock_ctx)

    # Pre-revealed tools must be visible again — this is the assertion that fails before the fix.
    tools_after_reopen = {t.name for t in await mcp.list_tools()}
    assert kitchen_gated.issubset(tools_after_reopen), (
        "kitchen tools should be visible after open_kitchen restores pre-revealed tags"
    )


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
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                from autoskillit.server.tools.tools_kitchen import _open_kitchen_handler

                await _open_kitchen_handler()
    gate_file = tmp_path / ".autoskillit" / "temp" / ".kitchen_gate"
    assert not gate_file.exists()


def test_close_kitchen_does_not_produce_gate_file(tmp_path, monkeypatch):
    """_close_kitchen_handler must not interact with any gate file path."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            from autoskillit.server.tools.tools_kitchen import _close_kitchen_handler

            _close_kitchen_handler()
    gate_file = tmp_path / ".autoskillit" / "temp" / ".kitchen_gate"
    assert not gate_file.exists()


@pytest.mark.anyio
async def test_open_kitchen_includes_categorized_tool_listing(tmp_path, monkeypatch):
    """open_kitchen response contains static categorized tool groups from _DISPLAY_CATEGORIES."""
    from autoskillit.config.ingredient_defaults import _DISPLAY_CATEGORIES
    from autoskillit.server.tools.tools_kitchen import open_kitchen

    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
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
    mock_ctx.recipes.load_and_validate.return_value = _with_finalized_projection(
        mock_ctx.recipes.load_and_validate.return_value
    )
    mock_ctx.recipes.find.return_value = None
    mock_ctx.config.migration.suppressed = []

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import open_kitchen

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
    """open_kitchen(name='nonexistent') fails closed when recipe is not found."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.recipes = MagicMock()
    mock_ctx.recipes.load_and_validate.return_value = {
        "content": "",
        "valid": False,
        "errors": ["No recipe named 'nonexistent' found"],
        "suggestions": [],
    }
    mock_ctx.recipes.find.return_value = None
    mock_ctx.config.migration.suppressed = []

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import open_kitchen

                    result_str = await open_kitchen(name="nonexistent", ctx=mock_ctx)

    result = json.loads(result_str)
    assert result["success"] is False
    assert "nonexistent" in result["error"]
    assert result["kitchen"] == "failed"


@pytest.mark.anyio
async def test_open_kitchen_without_recipe_returns_json_envelope(tmp_path, monkeypatch):
    """open_kitchen() without name returns JSON envelope with success=True."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import open_kitchen

                    result = await open_kitchen(ctx=mock_ctx)

    assert isinstance(result, str)
    parsed = json.loads(result)
    assert parsed["success"] is True
    assert parsed["kitchen"] == "open"
    assert "Kitchen is open" in parsed["content"]
    assert parsed["ingredients_table"] is None


@pytest.mark.anyio
async def test_open_kitchen_denied_by_gate_when_headless(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.chdir(tmp_path)
    from autoskillit.server.tools.tools_kitchen import open_kitchen

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
    from autoskillit.server.tools.tools_kitchen import close_kitchen

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
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import open_kitchen

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
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import open_kitchen

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
    mock_ctx.config = AutomationConfig(
        subsets=SubsetsConfig(disabled=[]), features={"fleet": True}
    )

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import open_kitchen

                    await open_kitchen(ctx=mock_ctx)

    mock_ctx.disable_components.assert_not_called()


# ---------------------------------------------------------------------------
# Group H — sous-chef discipline injection
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_sous_chef_discipline_not_in_open_kitchen_result(tmp_path, monkeypatch):
    """open_kitchen does not inject sous_chef_discipline — delivery is via system prompt."""
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
    mock_ctx.recipes.load_and_validate.return_value = _with_finalized_projection(
        mock_ctx.recipes.load_and_validate.return_value
    )
    mock_ctx.recipes.find.return_value = None
    mock_ctx.config.migration.suppressed = []

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import open_kitchen

                    result_str = await open_kitchen(name="implementation", ctx=mock_ctx)

    result = json.loads(result_str)
    assert result["success"] is True
    assert "sous_chef_discipline" not in result, (
        "sous_chef_discipline must not be injected by open_kitchen; "
        "food truck delivery is handled by _build_admiral_dispatch_block() "
        "in fleet/_prompts.py"
    )


@pytest.mark.anyio
async def test_open_kitchen_result_keys_match_typed_dict(tmp_path, monkeypatch):
    """All keys in open_kitchen result are declared in OpenKitchenResult."""
    from autoskillit.recipe._recipe_ingredients import OpenKitchenResult

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
    mock_ctx.recipes.load_and_validate.return_value = _with_finalized_projection(
        mock_ctx.recipes.load_and_validate.return_value
    )
    mock_ctx.recipes.find.return_value = None
    mock_ctx.config.migration.suppressed = []

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import open_kitchen

                    result_str = await open_kitchen(name="implementation", ctx=mock_ctx)

    result = json.loads(result_str)
    declared = set(OpenKitchenResult.__annotations__)
    undeclared = set(result.keys()) - declared
    assert undeclared == set(), (
        f"open_kitchen returned keys not declared in OpenKitchenResult: {sorted(undeclared)}. "
        "Add each to OpenKitchenResult in recipe/_recipe_ingredients.py."
    )
    for key in ("success", "kitchen", "version"):
        assert key in result, (
            f"open_kitchen result missing always-present key {key!r}. "
            "OpenKitchenResult declares it but the handler does not populate it."
        )


@pytest.mark.anyio
async def test_sous_chef_rules_injected_at_open_kitchen(tmp_path, monkeypatch):
    """Path B (no-name) must inject full sous-chef SKILL.md into response text."""
    from autoskillit.execution.backends import ClaudeCodeBackend
    from autoskillit.workspace import SkillsDirectoryProvider

    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.project_dir = tmp_path
    mock_ctx.skill_resolver = SkillsDirectoryProvider().resolver
    mock_ctx.backend = ClaudeCodeBackend()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import open_kitchen

                    result = await open_kitchen(ctx=mock_ctx)

    parsed = json.loads(result)
    content = parsed["content"]
    for header in SOUS_CHEF_MANDATORY_SECTIONS:
        assert header in content, (
            f"open_kitchen no-name response missing sous-chef section: {header!r}"
        )


@pytest.mark.anyio
async def test_open_kitchen_degrades_gracefully_without_sous_chef(tmp_path, monkeypatch):
    """When sous-chef SKILL.md is absent, Path B must return a valid response without raising."""
    from types import SimpleNamespace

    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.skill_resolver.list_effective.return_value = SimpleNamespace(skills=())
    mock_ctx.skill_resolver.resolve_effective.return_value = None

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import open_kitchen

                    result = await open_kitchen(ctx=mock_ctx)

    parsed = json.loads(result)
    assert parsed["success"] is True


# ---------------------------------------------------------------------------
# Group I — _redisable_subsets unit tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_redisable_subsets_includes_feature_tags() -> None:
    """When fleet feature is disabled, _redisable_subsets disables fleet tag."""
    from autoskillit.server.tools.tools_kitchen import _redisable_subsets

    mock_ctx = AsyncMock()

    await _redisable_subsets(mock_ctx, [], features={"fleet": False})

    calls = mock_ctx.disable_components.call_args_list
    disabled_tag_sets = [c.kwargs.get("tags", set()) for c in calls]
    assert any("fleet" in tags for tags in disabled_tag_sets), (
        "fleet tag must be disabled when fleet feature is off"
    )


@pytest.mark.anyio
async def test_redisable_subsets_does_not_disable_kitchen_core_tag() -> None:
    """_redisable_subsets must not pass kitchen-core to disable_components.

    FastMCP union model: any enabled tag keeps the tool visible. Verifies that
    kitchen-core is not included in the suppressed tag sets so that tools with
    the kitchen-core tag retain visibility after the feature gate pass.
    """
    from autoskillit.server.tools.tools_kitchen import _redisable_subsets

    disabled_tags: list[set] = []
    mock_ctx = AsyncMock()

    async def capture_disable(*, tags):
        disabled_tags.append(tags)

    mock_ctx.disable_components.side_effect = capture_disable

    # No subsets disabled, but fleet feature is explicitly disabled
    await _redisable_subsets(mock_ctx, [], features={"fleet": False})

    # fleet tag should be disabled
    assert any("fleet" in t for t in disabled_tags), (
        "fleet tag must be suppressed when feature is off"
    )
    # kitchen-core must NOT be in the disabled set (union model: still visible)
    assert not any("kitchen-core" in t for t in disabled_tags), (
        "kitchen-core tag must never be disabled by the feature gate pass"
    )


@pytest.mark.anyio
async def test_redisable_subsets_uses_shared_helper() -> None:
    """_redisable_subsets delegates Pass 2 to _collect_disabled_feature_tags."""
    from unittest.mock import AsyncMock, patch

    from autoskillit.server.tools.tools_kitchen import _redisable_subsets

    mock_ctx = AsyncMock()

    with patch("autoskillit.server.tools.tools_kitchen._collect_disabled_feature_tags") as mock_h:
        mock_h.return_value = frozenset({"fleet"})
        await _redisable_subsets(mock_ctx, [], features={"fleet": False})

    mock_h.assert_called_once_with({"fleet": False}, experimental_enabled=False)
