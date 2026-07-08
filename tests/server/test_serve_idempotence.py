"""Serve-idempotence regression: load_recipe after open_kitchen must produce identical content.

Proves that session_serve_overrides snapshot eliminates overlay divergence between
open_kitchen and subsequent load_recipe / deferred-recall open_kitchen calls.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.anyio, pytest.mark.medium]

_ISSUE_URL = "https://github.com/TalonT-Org/AutoSkillit/issues/999"
_TASK_DESC = "test task"
_RECIPE = "remediation"


def _mock_fmcp_ctx() -> MagicMock:
    """Return a minimal FastMCP Context mock with async component methods."""
    ctx = MagicMock()
    ctx.enable_components = AsyncMock()
    ctx.disable_components = AsyncMock()
    return ctx


async def _open_kitchen_patched(name, overrides, monkeypatch):
    """Call open_kitchen with all infrastructure side-effects patched out."""
    from autoskillit.recipe import _api_cache
    from autoskillit.recipe._api_cache import LoadCache
    from autoskillit.server.tools.tools_kitchen import open_kitchen

    monkeypatch.setattr(_api_cache, "_LOAD_CACHE", LoadCache())
    fmcp_ctx = _mock_fmcp_ctx()
    with patch("autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()):
        with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
            with patch("autoskillit.server.tools.tools_kitchen.create_background_task"):
                with patch(
                    "autoskillit.server.tools.tools_kitchen.resolve_kitchen_id",
                    return_value="test-kitchen",
                ):
                    return json.loads(
                        await open_kitchen(name=name, overrides=overrides, ctx=fmcp_ctx)
                    )


async def test_load_recipe_after_open_kitchen_with_overrides_serves_identical_content(
    tool_ctx_kitchen_open,
    monkeypatch,
    tmp_path,
):
    """load_recipe(name) in a session where open_kitchen(name, overrides={issue_url: ...})
    was called serves byte-identical content."""
    monkeypatch.chdir(tmp_path)
    from autoskillit.recipe import _api_cache
    from autoskillit.recipe._api_cache import LoadCache
    from autoskillit.server.tools.tools_recipe import load_recipe

    monkeypatch.setattr(_api_cache, "_LOAD_CACHE", LoadCache())

    ok_result = await _open_kitchen_patched(
        _RECIPE,
        {"issue_url": _ISSUE_URL, "task_description": _TASK_DESC},
        monkeypatch,
    )
    assert ok_result.get("success") is True, f"open_kitchen failed: {ok_result}"
    ok_content = ok_result["content"]

    lr_result = json.loads(await load_recipe(name=_RECIPE))
    assert "content" in lr_result, f"load_recipe missing content: {lr_result}"
    lr_content = lr_result["content"]

    assert ok_content == lr_content, (
        "load_recipe content diverges from open_kitchen content — "
        "session_serve_overrides baseline not applied"
    )

    from autoskillit.core.io import load_yaml

    parsed_ok = load_yaml(ok_content)
    parsed_lr = load_yaml(lr_content)
    assert parsed_ok["steps"]["clone"]["on_success"] == "claim_and_resolve"
    assert parsed_lr["steps"]["clone"]["on_success"] == "claim_and_resolve"


async def test_load_recipe_after_open_kitchen_without_overrides_serves_identical_content(
    tool_ctx_kitchen_open,
    monkeypatch,
    tmp_path,
):
    """Serve idempotence in the interactive flow (no overrides → defer_unresolved=True)."""
    monkeypatch.chdir(tmp_path)
    from autoskillit.recipe import _api_cache
    from autoskillit.recipe._api_cache import LoadCache
    from autoskillit.server.tools.tools_recipe import load_recipe

    monkeypatch.setattr(_api_cache, "_LOAD_CACHE", LoadCache())

    ok_result = await _open_kitchen_patched(
        _RECIPE,
        None,
        monkeypatch,
    )
    assert ok_result.get("success") is True, f"open_kitchen failed: {ok_result}"
    ok_content = ok_result["content"]

    assert tool_ctx_kitchen_open.session_serve_overrides == {}, (
        "session_serve_overrides must be empty dict (not None) when no overrides passed"
    )
    assert tool_ctx_kitchen_open.session_serve_defer_unresolved is True, (
        "session_serve_defer_unresolved must be True when no caller overrides present"
    )

    lr_result = json.loads(await load_recipe(name=_RECIPE))
    assert "content" in lr_result, f"load_recipe missing content: {lr_result}"
    lr_content = lr_result["content"]

    assert ok_content == lr_content, (
        "load_recipe content diverges from open_kitchen content (no-override path) — "
        "session_serve_overrides baseline not applied"
    )


async def test_deferred_recall_open_kitchen_serves_identical_to_first_serving(
    tool_ctx_kitchen_open,
    monkeypatch,
    tmp_path,
):
    """Deferred-recall open_kitchen (called again while gate is open) produces
    byte-identical content to the normal path first serving."""
    monkeypatch.chdir(tmp_path)
    from autoskillit.recipe import _api_cache
    from autoskillit.recipe._api_cache import LoadCache

    monkeypatch.setattr(_api_cache, "_LOAD_CACHE", LoadCache())

    first_result = await _open_kitchen_patched(
        _RECIPE,
        {"issue_url": _ISSUE_URL, "task_description": _TASK_DESC},
        monkeypatch,
    )
    assert first_result.get("success") is True, f"first open_kitchen failed: {first_result}"
    first_content = first_result["content"]

    deferred_result = await _open_kitchen_patched(
        _RECIPE,
        None,
        monkeypatch,
    )
    assert deferred_result.get("success") is True, (
        f"deferred-recall open_kitchen failed: {deferred_result}"
    )
    deferred_content = deferred_result["content"]

    assert first_content == deferred_content, (
        "Deferred-recall open_kitchen content diverges from first serving — "
        "session_serve_overrides not injected into deferred-recall _merged_overrides"
    )


async def test_session_serve_overrides_cleared_on_close_kitchen(
    tool_ctx_kitchen_open,
    monkeypatch,
    tmp_path,
):
    """Snapshot is set on open, cleared on close — no stale state leak."""
    monkeypatch.chdir(tmp_path)
    from autoskillit.recipe import _api_cache
    from autoskillit.recipe._api_cache import LoadCache
    from autoskillit.server.tools.tools_kitchen import close_kitchen

    monkeypatch.setattr(_api_cache, "_LOAD_CACHE", LoadCache())

    ok_result = await _open_kitchen_patched(
        _RECIPE,
        {"issue_url": _ISSUE_URL, "task_description": _TASK_DESC},
        monkeypatch,
    )
    assert ok_result.get("success") is True, f"open_kitchen failed: {ok_result}"

    assert tool_ctx_kitchen_open.session_serve_overrides is not None, (
        "session_serve_overrides must be set after open_kitchen"
    )
    assert tool_ctx_kitchen_open.session_serve_overrides == {
        "issue_url": _ISSUE_URL,
        "task_description": _TASK_DESC,
    }, (
        "session_serve_overrides must store ONLY caller-supplied values, "
        "not the full _merged_overrides"
    )

    json.loads(await close_kitchen())

    assert tool_ctx_kitchen_open.session_serve_overrides is None, (
        "session_serve_overrides must be cleared to None on close_kitchen"
    )
    assert tool_ctx_kitchen_open.session_serve_defer_unresolved is False, (
        "session_serve_defer_unresolved must be reset to False on close_kitchen"
    )


async def test_explicit_load_recipe_overrides_layer_on_top_of_session_baseline(
    tool_ctx_kitchen_open,
    monkeypatch,
    tmp_path,
):
    """Explicit overrides passed to load_recipe layer on top of the session baseline."""
    monkeypatch.chdir(tmp_path)
    from autoskillit.recipe import _api_cache
    from autoskillit.recipe._api_cache import LoadCache
    from autoskillit.server.tools.tools_recipe import load_recipe

    monkeypatch.setattr(_api_cache, "_LOAD_CACHE", LoadCache())

    ok_result = await _open_kitchen_patched(
        _RECIPE,
        {"issue_url": _ISSUE_URL, "task_description": _TASK_DESC},
        monkeypatch,
    )
    assert ok_result.get("success") is True, f"open_kitchen failed: {ok_result}"

    lr_result = json.loads(
        await load_recipe(name=_RECIPE, overrides={"extra_ingredient": "extra_value"})
    )
    assert "content" in lr_result, f"load_recipe missing content: {lr_result}"

    from autoskillit.core.io import load_yaml

    parsed = load_yaml(lr_result["content"])
    assert parsed["steps"]["clone"]["on_success"] == "claim_and_resolve", (
        "issue_url session baseline must still be active when load_recipe passes extra_ingredient"
    )
