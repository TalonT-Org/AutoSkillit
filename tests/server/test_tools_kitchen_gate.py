"""Tests for tools_kitchen.py: gate mechanics, hook config, recipe packs, refresh, misc."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autoskillit.config.settings import QuotaGuardConfig
from autoskillit.hooks._fmt_primitives import _HOOK_CONFIG_PATH_COMPONENTS
from tests.server.conftest import _make_mock_ctx

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


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
    from autoskillit.server.helpers import _hook_config_path

    from autoskillit.core.io import resolve_temp_dir

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


# ---------------------------------------------------------------------------
# Group G — disable_quota_guard tool
# ---------------------------------------------------------------------------


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
# Group J — T7, T8, T5, alwaysLoad MCP meta
# ---------------------------------------------------------------------------


def test_kitchen_failure_envelope_hint_says_install_not_reinstall() -> None:
    from autoskillit.server.tools_kitchen import _kitchen_failure_envelope

    result = json.loads(_kitchen_failure_envelope(exc=RuntimeError("x"), stage="test"))
    msg = result["user_visible_message"]
    assert "autoskillit install" in msg
    assert "reinstall" not in msg


def test_display_categories_omits_fleet_when_disabled() -> None:
    """Fleet category must not appear in iter_display_categories output when fleet is disabled."""
    from autoskillit.config import iter_display_categories

    cfg_features: dict[str, bool] = {"fleet": False}
    categories = [name for name, _ in iter_display_categories(cfg_features)]
    assert "Fleet" not in categories


def test_display_categories_includes_fleet_when_enabled() -> None:
    """Fleet category must appear in iter_display_categories output when fleet is enabled."""
    from autoskillit.config import iter_display_categories

    cfg_features: dict[str, bool] = {"fleet": True}
    categories = [name for name, _ in iter_display_categories(cfg_features)]
    assert "Fleet" in categories


# ---------------------------------------------------------------------------
# T5: close_kitchen cleans up review_gate_state.json
# ---------------------------------------------------------------------------

_REVIEW_GATE_STATE_RELPATH = (".autoskillit", "temp", "review_gate_state.json")


# T5-1
def test_close_kitchen_preserves_review_gate_when_loop_active(tmp_path, monkeypatch):
    """Preserve review_gate_state.json when an active review loop is in progress."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()

    state_path = tmp_path.joinpath(*_REVIEW_GATE_STATE_RELPATH)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "gate": "LOOP_REQUIRED",
                "review_verdict": "changes_requested",
                "check_review_loop_called": False,
                "pr_number": "1290",
                "set_at": "2026-04-26T04:30:00+00:00",
            }
        )
    )
    assert state_path.exists(), "State file must exist before close_kitchen"

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            from autoskillit.server.tools_kitchen import _close_kitchen_handler

            _close_kitchen_handler()

    assert state_path.exists(), "Active review loop state must survive close_kitchen"


# T5-2
def test_close_kitchen_no_review_gate_state_no_error(tmp_path, monkeypatch):
    """_close_kitchen_handler() must not raise when review_gate_state.json is absent."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            from autoskillit.server.tools_kitchen import _close_kitchen_handler

            _close_kitchen_handler()  # Must not raise

    assert not tmp_path.joinpath(*_REVIEW_GATE_STATE_RELPATH).exists()


# T5-3
def test_close_kitchen_removes_review_gate_when_loop_complete(tmp_path, monkeypatch):
    """Remove review_gate_state.json when check_review_loop_called is True."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()

    state_path = tmp_path.joinpath(*_REVIEW_GATE_STATE_RELPATH)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "gate": "LOOP_REQUIRED",
                "review_verdict": "changes_requested",
                "check_review_loop_called": True,
                "pr_number": "1290",
                "set_at": "2026-04-26T04:30:00+00:00",
            }
        )
    )

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            from autoskillit.server.tools_kitchen import _close_kitchen_handler

            _close_kitchen_handler()

    assert not state_path.exists(), "Completed loop state must be cleaned up on close"


# T5-4
def test_close_kitchen_removes_review_gate_when_gate_not_loop_required(tmp_path, monkeypatch):
    """_close_kitchen_handler() must remove review_gate_state.json when gate != LOOP_REQUIRED."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()

    state_path = tmp_path.joinpath(*_REVIEW_GATE_STATE_RELPATH)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "gate": "CLEAR",
                "check_review_loop_called": False,
                "pr_number": "1290",
                "set_at": "2026-04-26T04:30:00+00:00",
            }
        )
    )

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            from autoskillit.server.tools_kitchen import _close_kitchen_handler

            _close_kitchen_handler()

    assert not state_path.exists(), "Non-LOOP_REQUIRED gate state must be cleaned up on close"


# T5-5
def test_close_kitchen_removes_review_gate_on_corrupt_json(tmp_path, monkeypatch):
    """Delete review_gate_state.json when JSON is malformed (fail-safe)."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()

    state_path = tmp_path.joinpath(*_REVIEW_GATE_STATE_RELPATH)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("{not valid json")

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            from autoskillit.server.tools_kitchen import _close_kitchen_handler

            _close_kitchen_handler()

    assert not state_path.exists(), "Corrupt gate state must be deleted (fail-safe)"


@pytest.mark.anyio
async def test_open_kitchen_has_always_load_meta() -> None:
    """open_kitchen must carry anthropic/alwaysLoad: true in its MCP meta.

    alwaysLoad ensures open_kitchen is always in the initial tool context for
    direct 'claude' sessions (where ToolSearch is enabled). Defense-in-depth
    against the MCP first-call race for non-order/cook sessions.
    """
    from autoskillit.server import mcp

    tool = await mcp.get_tool("open_kitchen")
    assert tool is not None
    assert tool.meta is not None and tool.meta.get("anthropic/alwaysLoad") is True, (
        "open_kitchen missing anthropic/alwaysLoad:true — add to @mcp.tool(meta={...})"
    )
