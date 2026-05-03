"""Tests for tools_kitchen.py: visibility, component management, sous-chef, redisable_subsets."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autoskillit.core.types._type_constants import SOUS_CHEF_MANDATORY_SECTIONS
from tests.server.conftest import _make_mock_ctx

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


# ---------------------------------------------------------------------------
# Group C — visibility + component management
# ---------------------------------------------------------------------------


# T-VISIBILITY-1: open_kitchen tool calls ctx.enable_components
@pytest.mark.anyio
async def test_open_kitchen_tool_calls_enable_components(tmp_path, monkeypatch):
    """open_kitchen tool must call ctx.enable_components(tags={'kitchen'})."""
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
            from autoskillit.server.tools.tools_kitchen import close_kitchen

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
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import open_kitchen

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
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import open_kitchen

                    result_str = await open_kitchen(name="implementation", ctx=mock_ctx)

    result = json.loads(result_str)
    assert result["success"] is True
    discipline = result["sous_chef_discipline"]
    present = [s for s in SOUS_CHEF_MANDATORY_SECTIONS if s in discipline]
    for header in SOUS_CHEF_MANDATORY_SECTIONS:
        assert header in discipline, (
            f"sous_chef_discipline missing section: {header!r}. "
            f"Only {len(present)} of {len(SOUS_CHEF_MANDATORY_SECTIONS)} sections present."
        )


@pytest.mark.anyio
async def test_sous_chef_rules_injected_at_open_kitchen(tmp_path, monkeypatch):
    """Path B (no-name) must inject full sous-chef SKILL.md into response text."""
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

    parsed = json.loads(result)
    content = parsed["content"]
    for header in SOUS_CHEF_MANDATORY_SECTIONS:
        assert header in content, (
            f"open_kitchen no-name response missing sous-chef section: {header!r}"
        )


@pytest.mark.anyio
async def test_open_kitchen_degrades_gracefully_without_sous_chef(tmp_path, monkeypatch):
    """When sous-chef SKILL.md is absent, Path B must return a valid response without raising."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()

    import autoskillit.server.tools.tools_kitchen as tk_mod

    fake_pkg_root_dir = tmp_path / "fake_pkg"
    (fake_pkg_root_dir / "skills" / "sous-chef").mkdir(parents=True)
    # sous-chef SKILL.md is deliberately absent — directory exists, file does not

    def fake_pkg_root() -> object:
        return fake_pkg_root_dir

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    with patch.object(tk_mod, "pkg_root", fake_pkg_root):
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
