"""Tests for tools_kitchen.py: hook config lifecycle, overlay, and quota guard tool."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from autoskillit.config.settings import QuotaGuardConfig
from autoskillit.hooks.formatters._fmt_primitives import _HOOK_CONFIG_PATH_COMPONENTS
from tests.server._helpers import _HOOK_CONFIG_OVERLAY_RELPATH
from tests.server.conftest import _make_mock_ctx

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def test_hook_config_path_is_inside_temp_dir(tmp_path):
    """_hook_config_path() must resolve to a path inside the project temp directory.

    This invariant ensures .hook_config.json receives automatic gitignore coverage
    from temp/.gitignore ('*') and can never produce a gitignore gap regardless of
    whatever entries are present in .gitignore or _AUTOSKILLIT_GITIGNORE_ENTRIES.
    """
    from autoskillit.core.io import resolve_temp_dir
    from autoskillit.server._misc import _hook_config_path

    hook_path = _hook_config_path(tmp_path)
    temp_dir = resolve_temp_dir(tmp_path, None)

    assert hook_path.is_relative_to(temp_dir), (
        f"_hook_config_path() returned {hook_path!r} which is NOT inside "
        f"temp dir {temp_dir!r}. Session-bridge files must live in temp/ "
        f"to receive automatic gitignore coverage via temp/.gitignore."
    )


# T-CACHE-1
@pytest.mark.anyio
async def test_open_kitchen_primes_quota_cache(tmp_path, monkeypatch):
    """open_kitchen must call _prime_quota_cache before any run_skill hook fires."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    prime_mock = AsyncMock()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch("autoskillit.server.tools.tools_kitchen._prime_quota_cache", prime_mock):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import _open_kitchen_handler

                    await _open_kitchen_handler()

    prime_mock.assert_called_once()


# T-CACHE-2
@pytest.mark.anyio
async def test_open_kitchen_writes_hook_config_json(tmp_path, monkeypatch):
    """open_kitchen must write .autoskillit/.hook_config.json with user quota_guard values."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.project_dir = tmp_path
    mock_ctx.config.quota_guard.short_window_threshold = 85.0
    mock_ctx.config.quota_guard.long_window_threshold = 98.0
    mock_ctx.config.quota_guard.long_window_patterns = ["weekly", "sonnet", "opus"]
    mock_ctx.config.quota_guard.cache_max_age = 300
    mock_ctx.config.quota_guard.cache_path = "/custom/path.json"
    mock_ctx.config.quota_guard.buffer_seconds = 60

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx) as mock_get_ctx:
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                from autoskillit.server.tools.tools_kitchen import _open_kitchen_handler

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
    mock_ctx.project_dir = tmp_path
    mock_ctx.config.quota_guard = QuotaGuardConfig(
        enabled=enabled, cache_max_age=300, cache_path="/p/q.json", buffer_seconds=60
    )

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                from autoskillit.server.tools.tools_kitchen import _open_kitchen_handler

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
    mock_ctx.project_dir = tmp_path
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
                from autoskillit.server.tools.tools_kitchen import (
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

    from autoskillit.server.tools.tools_kitchen import _open_kitchen_handler

    assert inspect.iscoroutinefunction(_open_kitchen_handler), (
        "_open_kitchen_handler must be async"
    )


# ---------------------------------------------------------------------------
# Group G — disable_quota_guard tool
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_disable_quota_guard_writes_disabled_flag(tmp_path, monkeypatch):
    """disable_quota_guard() sets quota_guard.disabled=True in the overlay file."""
    monkeypatch.chdir(tmp_path)
    hook_cfg_path = tmp_path.joinpath(*_HOOK_CONFIG_PATH_COMPONENTS)
    hook_cfg_path.parent.mkdir(parents=True, exist_ok=True)
    hook_cfg_path.write_text(
        json.dumps({"quota_guard": {"cache_path": "/some/path.json", "cache_max_age": 300}})
    )

    from autoskillit.server import _state

    mock_state_ctx = _make_mock_ctx()
    mock_state_ctx.project_dir = tmp_path
    monkeypatch.setattr(_state, "_ctx", mock_state_ctx)

    from autoskillit.server.tools.tools_kitchen import disable_quota_guard

    result_str = await disable_quota_guard()
    parsed = json.loads(result_str)
    assert parsed["success"] is True

    overlay_path = tmp_path.joinpath(*_HOOK_CONFIG_OVERLAY_RELPATH)
    overlay_payload = json.loads(overlay_path.read_text())
    assert overlay_payload["quota_guard"]["disabled"] is True
    base_payload = json.loads(hook_cfg_path.read_text())
    assert "disabled" not in base_payload.get("quota_guard", {})


@pytest.mark.anyio
async def test_disable_quota_guard_denies_headless(tmp_path, monkeypatch):
    """disable_quota_guard() returns an error when called from a headless session."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")

    from autoskillit.server.tools.tools_kitchen import disable_quota_guard

    result_str = await disable_quota_guard()
    parsed = json.loads(result_str)
    assert parsed["success"] is False


@pytest.mark.anyio
async def test_disable_quota_guard_returns_error_when_kitchen_not_open(tmp_path, monkeypatch):
    """disable_quota_guard() returns an error when the kitchen is not open."""
    monkeypatch.chdir(tmp_path)
    from autoskillit.server import _state

    monkeypatch.setattr(_state, "_ctx", None)

    from autoskillit.server.tools.tools_kitchen import disable_quota_guard

    result_str = await disable_quota_guard()
    parsed = json.loads(result_str)
    assert parsed["success"] is False


# ---------------------------------------------------------------------------
# _update_hook_config_with_recipe — recipe authorization in hook config
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_update_hook_config_with_recipe_includes_recipe_allows_pr_create(
    tmp_path, monkeypatch
):
    """_update_hook_config_with_recipe adds recipe_allows_pr_create for PR-creating recipes."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.project_dir = tmp_path
    mock_ctx.recipe_name = "merge-prs"
    mock_ctx.kitchen_id = "test-kitchen-id"
    mock_ctx.config.quota_guard.cache_max_age = 300
    mock_ctx.config.quota_guard.cache_path = "/p/q.json"
    mock_ctx.config.quota_guard.buffer_seconds = 60

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                from autoskillit.server.tools.tools_kitchen import (
                    _open_kitchen_handler,
                    _update_hook_config_with_recipe,
                )

                await _open_kitchen_handler()
                _update_hook_config_with_recipe()

    hook_cfg = tmp_path.joinpath(*_HOOK_CONFIG_PATH_COMPONENTS)
    data = json.loads(hook_cfg.read_text())
    assert data["recipe_allows_pr_create"] is True
    assert "quota_guard" in data
    assert "kitchen_id" in data


@pytest.mark.anyio
async def test_update_hook_config_with_recipe_excludes_pr_create_for_non_pr_recipes(
    tmp_path, monkeypatch
):
    """_update_hook_config_with_recipe does NOT set recipe_allows_pr_create for non-PR recipes."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.project_dir = tmp_path
    mock_ctx.recipe_name = "smoke-test"
    mock_ctx.kitchen_id = "test-kitchen-id"
    mock_ctx.config.quota_guard.cache_max_age = 300
    mock_ctx.config.quota_guard.cache_path = "/p/q.json"
    mock_ctx.config.quota_guard.buffer_seconds = 60

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                from autoskillit.server.tools.tools_kitchen import (
                    _open_kitchen_handler,
                    _update_hook_config_with_recipe,
                )

                await _open_kitchen_handler()
                _update_hook_config_with_recipe()

    hook_cfg = tmp_path.joinpath(*_HOOK_CONFIG_PATH_COMPONENTS)
    data = json.loads(hook_cfg.read_text())
    assert "recipe_allows_pr_create" not in data


# ---------------------------------------------------------------------------
# Group H — layered hook config: overlay isolation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_disable_quota_guard_survives_write_hook_config(tmp_path, monkeypatch):
    """disable_quota_guard writes to overlay; subsequent _write_hook_config cannot erase it."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.project_dir = tmp_path
    mock_ctx.config.quota_guard.cache_max_age = 300
    mock_ctx.config.quota_guard.cache_path = "/some/path.json"
    mock_ctx.config.quota_guard.buffer_seconds = 60

    from autoskillit.server import _state

    monkeypatch.setattr(_state, "_ctx", mock_ctx)

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                from autoskillit.server.tools.tools_kitchen import (
                    _open_kitchen_handler,
                    _write_hook_config,
                    disable_quota_guard,
                )

                await _open_kitchen_handler()
                result_str = await disable_quota_guard()
                parsed = json.loads(result_str)
                assert parsed["success"] is True

                _write_hook_config()

    from autoskillit.hooks._hook_settings import read_merged_hook_config

    merged = read_merged_hook_config(tmp_path)
    assert merged["quota_guard"]["disabled"] is True


def test_write_hook_config_does_not_touch_overlay(tmp_path, monkeypatch):
    """_write_hook_config() only writes the base file and never modifies the overlay."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.project_dir = tmp_path
    mock_ctx.kitchen_id = "test-kitchen-id"
    mock_ctx.config.quota_guard.cache_max_age = 300
    mock_ctx.config.quota_guard.cache_path = "/p/q.json"
    mock_ctx.config.quota_guard.buffer_seconds = 60

    overlay_path = tmp_path.joinpath(*_HOOK_CONFIG_OVERLAY_RELPATH)
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    overlay_path.write_text(json.dumps({"quota_guard": {"disabled": True}}))
    mtime_before = overlay_path.stat().st_mtime

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            from autoskillit.server.tools.tools_kitchen import _write_hook_config

            _write_hook_config()

    assert overlay_path.stat().st_mtime == mtime_before
    assert json.loads(overlay_path.read_text())["quota_guard"]["disabled"] is True


def test_merged_hook_config_overlay_wins():
    """merge_hook_configs gives overlay precedence over base."""
    from autoskillit.hooks._hook_settings import merge_hook_configs

    base = {"quota_guard": {"disabled": False, "cache_max_age": 60}, "kitchen_id": "k1"}
    overlay = {"quota_guard": {"disabled": True}}
    merged = merge_hook_configs(base, overlay)
    assert merged["quota_guard"]["disabled"] is True
    assert merged["quota_guard"]["cache_max_age"] == 60
    assert merged["kitchen_id"] == "k1"


@pytest.mark.anyio
async def test_close_kitchen_cleans_overlay(tmp_path, monkeypatch):
    """close_kitchen removes the overlay file, resetting runtime mutations."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.project_dir = tmp_path

    overlay_path = tmp_path.joinpath(*_HOOK_CONFIG_OVERLAY_RELPATH)
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    overlay_path.write_text(json.dumps({"quota_guard": {"disabled": True}}))

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            from autoskillit.server.tools.tools_kitchen import _close_kitchen_handler

            _close_kitchen_handler()

    assert not overlay_path.exists()
