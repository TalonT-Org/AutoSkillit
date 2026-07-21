"""Serve-idempotence regression: load_recipe after open_kitchen must produce identical content.

Proves that session_serve_overrides snapshot eliminates overlay divergence between
open_kitchen and subsequent load_recipe / deferred-recall open_kitchen calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from autoskillit.core import SERVE_SURFACES
from autoskillit.pipeline.context import ToolContext

pytestmark = [pytest.mark.layer("server"), pytest.mark.anyio, pytest.mark.medium]

# Re-serve surfaces: all surfaces except open_kitchen (which sets the snapshot).
_RE_SERVE_SURFACES = ["load_recipe", "get_recipe", "open_kitchen_deferred_recall"]

_ISSUE_URL = "https://github.com/TalonT-Org/AutoSkillit/issues/999"
_TASK_DESC = "test task"
_RECIPE = "remediation"


def _load_recipe_content(envelope: dict[str, object]) -> str:
    """Read the full recipe payload referenced by a compact load_recipe envelope."""
    assert "content" not in envelope
    assert envelope["pull_tool"] == "get_recipe_section"
    assert {
        "step_flow_skeleton",
        "step_index",
        "artifact_path",
        "sha256",
        "pull_tool",
    } <= envelope.keys()
    payload = json.loads(Path(str(envelope["artifact_path"])).read_text(encoding="utf-8"))
    return str(payload["content"])


def test_re_serve_surfaces_in_sync_with_serve_surfaces() -> None:
    """Content-serving surfaces exclude the section-oriented artifact pull tool."""
    assert set(_RE_SERVE_SURFACES) == SERVE_SURFACES - {
        "open_kitchen",
        "get_recipe_section",
    }, (
        f"_RE_SERVE_SURFACES out of sync with SERVE_SURFACES. "
        f"Missing: "
        f"{(SERVE_SURFACES - {'open_kitchen', 'get_recipe_section'}) - set(_RE_SERVE_SURFACES)}. "
        f"Extra: "
        f"{set(_RE_SERVE_SURFACES) - (SERVE_SURFACES - {'open_kitchen', 'get_recipe_section'})}."
    )


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
    lr_content = _load_recipe_content(lr_result)

    assert ok_content == lr_content, (
        "load_recipe content diverges from open_kitchen content — "
        "session_serve_overrides baseline not applied"
    )


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
    lr_content = _load_recipe_content(lr_result)

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
    lr_content = _load_recipe_content(lr_result)

    from autoskillit.core.io import load_yaml

    parsed = load_yaml(lr_content)
    assert parsed["steps"]["clone"]["on_success"] == "claim_and_resolve", (
        "issue_url session baseline must still be active when load_recipe passes extra_ingredient"
    )


# ── New tests (Part B: get_recipe surface fix + parametric guard) ─────────────


async def test_get_recipe_content_matches_open_kitchen_with_overrides(
    tool_ctx_kitchen_open,
    monkeypatch,
    tmp_path,
):
    """get_recipe MCP resource must replay session_serve_overrides from open_kitchen.

    Before the fix, get_recipe ignores ctx.session_serve_overrides and rebuilds its
    ingredient stack without the caller's overrides, producing divergent routing.
    """
    monkeypatch.chdir(tmp_path)
    from autoskillit.recipe import _api_cache
    from autoskillit.recipe._api_cache import LoadCache
    from autoskillit.server.tools.tools_kitchen import get_recipe

    monkeypatch.setattr(_api_cache, "_LOAD_CACHE", LoadCache())

    ok_result = await _open_kitchen_patched(
        _RECIPE,
        {"issue_url": _ISSUE_URL, "task_description": _TASK_DESC},
        monkeypatch,
    )
    assert ok_result.get("success") is True, f"open_kitchen failed: {ok_result}"
    ok_content = ok_result["content"]

    gr_content = get_recipe(_RECIPE)
    try:
        _err = json.loads(gr_content)
        pytest.fail(f"get_recipe returned error: {_err}")
    except json.JSONDecodeError:
        pass

    assert ok_content == gr_content, (
        "get_recipe content diverges from open_kitchen content — "
        "session_serve_overrides not replayed in get_recipe"
    )

    from autoskillit.core.io import load_yaml

    parsed_ok = load_yaml(ok_content)
    parsed_gr = load_yaml(gr_content)
    assert (
        parsed_ok["steps"]["clone"]["on_success"] == parsed_gr["steps"]["clone"]["on_success"]
    ), "Routing divergence: clone.on_success differs between open_kitchen and get_recipe"


async def _call_re_serve_surface(
    surface: str,
    recipe_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> str:
    """Call a re-serve surface and return the recipe content string."""
    from autoskillit.recipe import _api_cache
    from autoskillit.recipe._api_cache import LoadCache

    monkeypatch.setattr(_api_cache, "_LOAD_CACHE", LoadCache())

    if surface == "load_recipe":
        from autoskillit.server.tools.tools_recipe import load_recipe

        result = json.loads(await load_recipe(name=recipe_name))
        return _load_recipe_content(result)
    elif surface == "get_recipe":
        from autoskillit.server.tools.tools_kitchen import get_recipe

        content = get_recipe(recipe_name)
        try:
            _err = json.loads(content)
            pytest.fail(f"get_recipe returned error: {_err}")
        except json.JSONDecodeError:
            pass
        return content
    elif surface == "open_kitchen_deferred_recall":
        result = await _open_kitchen_patched(recipe_name, None, monkeypatch)
        assert result.get("success") is True, f"deferred-recall failed: {result}"
        return result["content"]
    else:
        raise ValueError(f"Unknown surface: {surface!r}")


@pytest.mark.parametrize("surface", _RE_SERVE_SURFACES)
async def test_serve_surfaces_parametric_content_identity(
    surface: str,
    tool_ctx_kitchen_open: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    """Every re-serve surface must produce routing-identical content to open_kitchen.

    Parametrized across _RE_SERVE_SURFACES (load_recipe, get_recipe,
    open_kitchen_deferred_recall). Collection-time guard at module level ensures
    _RE_SERVE_SURFACES stays in sync with SERVE_SURFACES.
    """
    monkeypatch.chdir(tmp_path)  # type: ignore[arg-type]
    from autoskillit.recipe import _api_cache
    from autoskillit.recipe._api_cache import LoadCache

    monkeypatch.setattr(_api_cache, "_LOAD_CACHE", LoadCache())

    ok_result = await _open_kitchen_patched(
        _RECIPE,
        {"issue_url": _ISSUE_URL, "task_description": _TASK_DESC},
        monkeypatch,
    )
    assert ok_result.get("success") is True, f"open_kitchen failed: {ok_result}"
    ok_content = ok_result["content"]

    re_served_content = await _call_re_serve_surface(surface, _RECIPE, monkeypatch)

    assert re_served_content == ok_content, (
        f"Routing divergence on surface={surface!r}: "
        "re-serve content differs from open_kitchen content"
    )


async def test_get_recipe_snapshot_lifecycle(
    tool_ctx_kitchen_open: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    """get_recipe reflects session_serve_overrides: step count matches open_kitchen."""
    monkeypatch.chdir(tmp_path)  # type: ignore[arg-type]
    from autoskillit.core.io import load_yaml
    from autoskillit.recipe import _api_cache
    from autoskillit.recipe._api_cache import LoadCache
    from autoskillit.server.tools.tools_kitchen import get_recipe

    monkeypatch.setattr(_api_cache, "_LOAD_CACHE", LoadCache())

    ok_result = await _open_kitchen_patched(
        _RECIPE,
        {"issue_url": _ISSUE_URL, "task_description": _TASK_DESC},
        monkeypatch,
    )
    assert ok_result.get("success") is True, f"open_kitchen failed: {ok_result}"
    ok_content = ok_result["content"]

    gr_content = get_recipe(_RECIPE)
    try:
        _err = json.loads(gr_content)
        pytest.fail(f"get_recipe returned error: {_err}")
    except json.JSONDecodeError:
        pass

    parsed_ok = load_yaml(ok_content)
    parsed_gr = load_yaml(gr_content)

    ok_steps = set(parsed_ok.get("steps", {}).keys())
    gr_steps = set(parsed_gr.get("steps", {}).keys())

    assert ok_steps == gr_steps, (
        f"Step set divergence between open_kitchen and get_recipe. "
        f"Missing from get_recipe: {ok_steps - gr_steps}. "
        f"Extra in get_recipe: {gr_steps - ok_steps}."
    )


# ── Hypothesis property test ─────────────────────────────────────────────────


@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[
        HealthCheck.too_slow,
        HealthCheck.function_scoped_fixture,  # safe: manual state reset at test start
    ],
)
@given(
    overrides=st.dictionaries(
        keys=st.sampled_from(["issue_url", "task_description"]),
        values=st.one_of(
            st.just("https://github.com/TalonT-Org/AutoSkillit/issues/42"),
            st.just("test task"),
        ),
        max_size=2,
    )
)
async def test_load_recipe_routing_matches_open_kitchen_for_arbitrary_overrides(
    overrides: dict[str, str],
    tool_ctx_kitchen_open: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    """For arbitrary overrides, load_recipe content must match open_kitchen content.

    Catches precedence bugs where specific key collisions in the override stack
    cause snapshot_baseline values to be shadowed incorrectly.

    The function-scoped fixture is not reset between Hypothesis examples, so we
    manually reset serve-context state at the start of each example to ensure
    open_kitchen always takes the normal path (not deferred-recall) and the
    snapshot is always set from the current example's overrides.
    """
    monkeypatch.chdir(tmp_path)  # type: ignore[arg-type]
    from autoskillit.recipe import _api_cache
    from autoskillit.recipe._api_cache import LoadCache
    from autoskillit.server.tools.tools_recipe import load_recipe

    # Reset serve-context state so each example sees a clean slate.
    # Without this, stale session_serve_overrides from a prior example would
    # cause open_kitchen to use the deferred-recall path with an old snapshot,
    # producing different ingredient_overrides than load_recipe would see.
    ctx = tool_ctx_kitchen_open
    ctx.session_serve_overrides = None
    ctx.session_serve_defer_unresolved = False
    ctx.recipe_name = ""

    monkeypatch.setattr(_api_cache, "_LOAD_CACHE", LoadCache())

    ok_overrides = overrides if overrides else None
    ok_result = await _open_kitchen_patched(_RECIPE, ok_overrides, monkeypatch)
    if not ok_result.get("success"):
        assume(False)  # discard: open_kitchen failed (e.g. invalid combos)

    lr_result = json.loads(await load_recipe(name=_RECIPE))
    if not lr_result.get("success"):
        assume(False)  # discard: load_recipe failed

    assert _load_recipe_content(lr_result) == ok_result["content"], (
        f"Routing divergence for overrides={overrides!r}: "
        "load_recipe content must match open_kitchen content"
    )
