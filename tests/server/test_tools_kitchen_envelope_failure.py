"""Tests for tools_kitchen.py: failure and success envelope behavior."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autoskillit.core import BackendCapabilities
from tests.server._helpers import (
    _PATCHED_DEFAULTS,
    _SERVER_ONLY_KEYS,
    _configure_admitted_recipe,
    _make_finalized_projection,
    _with_finalized_projection,
)
from tests.server.conftest import _make_mock_ctx

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _claude_code_backend_mock() -> MagicMock:
    backend = MagicMock()
    backend.capabilities = BackendCapabilities(unnegotiated_tool_result_token_limit=46_500)
    backend.name = "claude-code"
    return backend


# ---------------------------------------------------------------------------
# Group F — failure envelopes — Phase 3 (#711 Part B)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_open_kitchen_no_name_returns_json_envelope_with_success_true(tmp_path, monkeypatch):
    """No-recipe open_kitchen returns JSON envelope with success=True."""
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
    assert parsed["success"] is True, parsed
    assert parsed["kitchen"] == "open"
    assert "Kitchen is open" in parsed["content"]
    assert parsed["ingredients_table"] is None


@pytest.mark.anyio
async def test_open_kitchen_exempt_surface_renders_real_content():
    """Issue #4399 criterion 4: the open_kitchen formatter must render real recipe content
    for an exempt surface, not just `## open_kitchen ✓ v`.

    When a payload contains substantive fields (`content`, `summary`, `diagram`,
    `ingredients_table`, `orchestration_rules`), `_fmt_open_kitchen` must emit
    sections for each. The `--- STEP FLOW ---` marker is the regression assertion:
    it is only emitted when the payload's `summary` is non-empty. Without the fix,
    `summary` arrives empty and the formatter degrades to a degenerate
    `## open_kitchen ✓ v` stub.

    This is a formatter-level regression test: it verifies `_fmt_open_kitchen`'s
    own contract — given a well-formed payload with real content fields, it renders
    that content rather than a degenerate stub. A payload dict handed directly to
    `_format_response` bypasses the upstream delivery-decision routing in
    `_recipe_delivery.py` (`finalize_recipe_delivery`/`exemption_overrides_envelope`),
    which is a separate layer with its own existing test coverage.
    """
    from autoskillit.hooks.formatters.pretty_output_hook import _format_response

    payload = {
        "valid": True,
        "suggestions": [],
        "content": "name: my-recipe\nsteps:\n  do:\n    tool: run_cmd\n",
        "summary": "Run a quick smoke check on the project.",
        "diagram": "step do --> done\n",
        "ingredients_table": "--- INGREDIENTS TABLE ---\n  task  required\n--- END TABLE ---",
        "orchestration_rules": "Run as orchestrator.",
        "kitchen": "open",
        "version": "1.2.3",
    }

    formatted = _format_response("open_kitchen", json.dumps(payload), pipeline=False)
    assert formatted is not None
    assert "## open_kitchen" in formatted
    assert "v1.2.3" in formatted
    # Real content must be present, not just the header.
    assert "--- STEP FLOW ---" in formatted, (
        "Formatter must emit --- STEP FLOW --- marker when payload summary is "
        "non-empty. If missing, the formatter degrades to a degenerate "
        "`## open_kitchen ✓ v` stub — see #4399 criterion 4."
    )
    assert "Run a quick smoke check" in formatted
    assert "--- RECIPE ---" in formatted
    assert "--- INGREDIENTS TABLE ---" in formatted


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
    mock_ctx.recipes.load_and_validate.return_value = _with_finalized_projection(
        mock_ctx.recipes.load_and_validate.return_value,
        projection=_make_finalized_projection(),
    )
    _configure_admitted_recipe(mock_ctx, tmp_path / "demo.yaml")
    mock_ctx.config.migration.suppressed = []

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import open_kitchen

                    result_str = await open_kitchen(name="demo", ctx=mock_ctx)

    assert isinstance(result_str, str), (
        f"open_kitchen returned {type(result_str).__name__}: {result_str!r}"
    )
    parsed = json.loads(result_str)
    assert parsed["success"] is True, parsed
    assert parsed["kitchen"] == "open"
    assert "version" in parsed
    assert "--- INGREDIENTS TABLE ---" in result_str


# DIAG_C4
@pytest.mark.anyio
async def test_open_kitchen_injects_hidden_ingredient_overrides(tmp_path, monkeypatch):
    """open_kitchen injects all SERVER_AUTHORITATIVE_INGREDIENTS keys into ingredient_overrides."""
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
    _configure_admitted_recipe(mock_ctx, tmp_path / "demo.yaml")
    mock_ctx.config.migration.suppressed = []
    mock_ctx.kitchen_id = "test-kitchen-abc"

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    with patch(
                        "autoskillit.server.tools.tools_kitchen.resolve_kitchen_id",
                        return_value="test-kitchen-abc",
                    ):
                        with patch(
                            "autoskillit.server.tools.tools_kitchen.resolve_ingredient_defaults",
                            return_value=_PATCHED_DEFAULTS,
                        ):
                            from autoskillit.server.tools.tools_kitchen import open_kitchen

                            mock_ctx.config.linux_tracing.log_dir = ""
                            await open_kitchen(name="demo", ctx=mock_ctx)

    from autoskillit.config.ingredient_defaults import SERVER_AUTHORITATIVE_INGREDIENTS

    call_kwargs = mock_ctx.recipes.load_and_validate.call_args.kwargs
    overrides = call_kwargs["ingredient_overrides"]
    assert overrides["kitchen_id"] == "test-kitchen-abc"
    assert overrides["diagnostics_log_dir"]  # non-empty string
    for key in SERVER_AUTHORITATIVE_INGREDIENTS:
        assert overrides[key] == _PATCHED_DEFAULTS[key], (
            f"SERVER_AUTHORITATIVE key {key!r}: expected {_PATCHED_DEFAULTS[key]!r}, "
            f"got {overrides[key]!r}"
        )


# 1b
@pytest.mark.anyio
async def test_config_layer_keys_match_server_authoritative_ingredients(tmp_path, monkeypatch):
    """build_config_authoritative_layer in open_kitchen must inject exactly
    SERVER_AUTHORITATIVE_INGREDIENTS plus server-only keys (kitchen_id, diagnostics_log_dir)."""
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
    _configure_admitted_recipe(mock_ctx, tmp_path / "demo.yaml")
    mock_ctx.config.migration.suppressed = []
    mock_ctx.kitchen_id = "test-kitchen-abc"

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    with patch(
                        "autoskillit.server.tools.tools_kitchen.resolve_kitchen_id",
                        return_value="test-kitchen-abc",
                    ):
                        with patch(
                            "autoskillit.server.tools.tools_kitchen.resolve_ingredient_defaults",
                            return_value=_PATCHED_DEFAULTS,
                        ):
                            from autoskillit.server.tools.tools_kitchen import open_kitchen

                            mock_ctx.config.linux_tracing.log_dir = ""
                            # No caller overrides — _merged_overrides == _auto_overrides
                            await open_kitchen(name="demo", ctx=mock_ctx)

    from autoskillit.config.ingredient_defaults import (
        CONFIG_DEFAULT_INGREDIENTS,
        SERVER_AUTHORITATIVE_INGREDIENTS,
    )

    call_kwargs = mock_ctx.recipes.load_and_validate.call_args.kwargs
    overrides = call_kwargs["ingredient_overrides"]
    config_resolvable_in_overrides = frozenset(overrides.keys()) - _SERVER_ONLY_KEYS
    expected = SERVER_AUTHORITATIVE_INGREDIENTS | CONFIG_DEFAULT_INGREDIENTS
    assert config_resolvable_in_overrides == expected, (
        f"Mismatch: added={config_resolvable_in_overrides - expected}, "
        f"missing={expected - config_resolvable_in_overrides}"
    )


@pytest.mark.anyio
async def test_open_kitchen_smoke_test_renders_resolved_base_branch(monkeypatch):
    """T7: open_kitchen smoke-test renders the config-resolved base_branch value."""
    monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
    import autoskillit.recipe._api_cache as cache_mod
    from autoskillit.core import pkg_root
    from autoskillit.recipe.repository import DefaultRecipeRepository

    project_dir = pkg_root().parent.parent
    monkeypatch.chdir(project_dir)
    monkeypatch.setattr(cache_mod, "_LOAD_CACHE", cache_mod.LoadCache())
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_kitchen.resolve_ingredient_defaults",
        lambda _: {"base_branch": "develop"},
    )

    mock_ctx = _make_mock_ctx()
    mock_ctx.project_dir = project_dir
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.quota_refresh_task = None
    # The smoke-test recipe's rendered inline payload (~25KB with flow records)
    # must fit the conservative 1:1 admission policy, so backend=None with its
    # CLAUDE_CODE_CAPABILITIES fallback (23,250 tokens) may route to ENVELOPE.
    # Provide a mock backend with high enough limit for this ingredient test.
    mock_ctx.backend = _claude_code_backend_mock()
    mock_ctx.recipes = DefaultRecipeRepository()
    mock_ctx.config.migration.suppressed = []

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    with patch(
                        "autoskillit.server.tools.tools_kitchen._check_dispatch_feasibility",
                        return_value=None,
                    ):
                        from autoskillit.server.tools.tools_kitchen import open_kitchen

                        result_str = await open_kitchen(name="smoke-test", ctx=mock_ctx)

    parsed = json.loads(result_str)
    assert parsed["success"] is True, parsed
    ing_table = parsed.get("ingredients_table") or ""
    assert ing_table, "ingredients_table must be present and non-empty"
    assert "develop" in ing_table
    # base_branch row must NOT show the YAML literal "main"
    base_branch_rows = [line for line in ing_table.splitlines() if "base_branch" in line]
    assert base_branch_rows, "base_branch row must appear in ingredients_table"
    assert all("main" not in row for row in base_branch_rows)


@pytest.mark.anyio
async def test_open_kitchen_rejects_config_authority_override(tmp_path, monkeypatch):
    """open_kitchen rejects caller-supplied overrides for server-authoritative
    keys with a structured envelope — the config-layer silent-overwrite behavior
    is replaced by explicit rejection (authority gate at function entry)."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.recipes = MagicMock()
    _configure_admitted_recipe(mock_ctx, tmp_path / "demo.yaml")
    mock_ctx.config.migration.suppressed = []
    mock_ctx.kitchen_id = "test-kitchen-abc"
    mock_ctx.config.linux_tracing.log_dir = ""

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    with patch(
                        "autoskillit.server.tools.tools_kitchen.resolve_kitchen_id",
                        return_value="test-kitchen-abc",
                    ):
                        with patch(
                            "autoskillit.server.tools.tools_kitchen.resolve_ingredient_defaults",
                            return_value={
                                "base_branch": "develop",
                                "pipeline_health": "false",
                                "is_fleet_dispatch": "false",
                                "dispatch_id": "",
                            },
                        ):
                            from autoskillit.server.tools.tools_kitchen import open_kitchen

                            result_str = await open_kitchen(
                                name="demo",
                                overrides={"base_branch": "main"},
                                ctx=mock_ctx,
                            )

    parsed = json.loads(result_str)
    assert parsed["success"] is False
    assert parsed["stage"] == "ingredient_authority_validation"
    assert parsed["retriable"] is False
    assert "base_branch" in parsed["error"]
    # Authority gate runs at function entry, before recipes.load / serve_recipe.
    mock_ctx.recipes.load.assert_not_called()
    mock_ctx.recipes.load_and_validate.assert_not_called()


@pytest.mark.anyio
async def test_open_kitchen_rejects_authority_override_with_envelope(tmp_path, monkeypatch):
    """open_kitchen rejects base_branch override and returns a structured envelope
    naming the clobbered key and its config path."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.recipes = MagicMock()
    _configure_admitted_recipe(mock_ctx, tmp_path / "demo.yaml")
    mock_ctx.config.migration.suppressed = []
    mock_ctx.kitchen_id = "test-kitchen-abc"
    mock_ctx.config.linux_tracing.log_dir = ""

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    with patch(
                        "autoskillit.server.tools.tools_kitchen.resolve_kitchen_id",
                        return_value="test-kitchen-abc",
                    ):
                        with patch(
                            "autoskillit.server.tools.tools_kitchen.resolve_ingredient_defaults",
                            return_value={
                                "base_branch": "develop",
                                "is_fleet_dispatch": "false",
                                "dispatch_id": "",
                            },
                        ):
                            from autoskillit.server.tools.tools_kitchen import open_kitchen

                            result_str = await open_kitchen(
                                name="demo",
                                overrides={"base_branch": "custom-branch"},
                                ctx=mock_ctx,
                            )

    parsed = json.loads(result_str)
    assert parsed["success"] is False
    assert parsed["stage"] == "ingredient_authority_validation"
    assert parsed["retriable"] is False
    assert "base_branch" in parsed["error"]
    # user_visible_message should mention the config path for actionable feedback.
    assert "branching.default_base_branch" in parsed["user_visible_message"], (
        f"Expected user_visible_message to name the config path "
        f"branching.default_base_branch; got {parsed['user_visible_message']!r}"
    )


@pytest.mark.anyio
async def test_open_kitchen_with_config_authority_ingredient(monkeypatch):
    """Full open_kitchen path: caller-supplied base_branch override is rejected
    at function entry — no recipe load, no projection, no session mutation."""
    monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
    import autoskillit.recipe._api_cache as cache_mod
    from autoskillit.core import pkg_root
    from autoskillit.recipe.repository import DefaultRecipeRepository

    project_dir = pkg_root().parent.parent
    monkeypatch.chdir(project_dir)
    monkeypatch.setattr(cache_mod, "_LOAD_CACHE", cache_mod.LoadCache())
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_kitchen.resolve_ingredient_defaults",
        lambda _: {
            "base_branch": "develop",
            "pipeline_health": "false",
            "is_fleet_dispatch": "false",
            "dispatch_id": "",
        },
    )

    mock_ctx = _make_mock_ctx()
    mock_ctx.project_dir = project_dir
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.quota_refresh_task = None
    mock_ctx.backend = _claude_code_backend_mock()
    mock_ctx.recipes = DefaultRecipeRepository()
    mock_ctx.config.migration.suppressed = []

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    with patch(
                        "autoskillit.server.tools.tools_kitchen._check_dispatch_feasibility",
                        return_value=None,
                    ):
                        from autoskillit.server.tools.tools_kitchen import open_kitchen

                        result_str = await open_kitchen(
                            name="smoke-test",
                            overrides={"base_branch": "main"},
                            ctx=mock_ctx,
                        )

    parsed = json.loads(result_str)
    assert parsed["success"] is False
    assert parsed["stage"] == "ingredient_authority_validation"
    assert "base_branch" in parsed["error"]


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
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import open_kitchen

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
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import open_kitchen

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
    from autoskillit.server.tools.tools_kitchen import open_kitchen

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
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache",
                new=raise_type_error,
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import open_kitchen

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
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache",
                new=raise_runtime,
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import open_kitchen

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
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    with patch(
                        "autoskillit.server.tools.tools_kitchen.create_background_task",
                        side_effect=RuntimeError("task creation failed"),
                    ):
                        from autoskillit.server.tools.tools_kitchen import open_kitchen

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
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import open_kitchen

                    result_str = await open_kitchen(name="demo", ctx=mock_ctx)

    parsed = json.loads(result_str)
    assert parsed["success"] is False
    assert parsed["stage"] == "load_and_validate"


@pytest.mark.anyio
async def test_open_kitchen_fails_on_empty_content(tmp_path, monkeypatch):
    """open_kitchen must return success=false when load_and_validate produces
    content='' and valid=False (dangling route wipe)."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.recipes = MagicMock()
    mock_ctx.recipes.load_and_validate.return_value = {
        "content": "",
        "valid": False,
        "errors": ["[post-prune] dangling route: Step 'x' routes to unknown step 'y'"],
        "suggestions": [],
    }
    mock_ctx.config.migration.suppressed = []

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import open_kitchen

                    result_str = await open_kitchen(name="demo", ctx=mock_ctx)

    parsed = json.loads(result_str)
    assert parsed["success"] is False, (
        "open_kitchen must not return success=true when content is empty"
    )
    assert parsed["kitchen"] == "failed"


@pytest.mark.anyio
async def test_open_kitchen_fails_on_invalid_recipe(tmp_path, monkeypatch):
    """open_kitchen must return success=false when load_and_validate sets valid=False."""
    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.recipes = MagicMock()
    mock_ctx.recipes.load_and_validate.return_value = {
        "content": "name: demo\nsteps:\n  do:\n    tool: run_cmd\n",
        "valid": False,
        "errors": ["some structural error"],
        "suggestions": [{"severity": "error", "rule": "test", "message": "bad"}],
    }
    mock_ctx.config.migration.suppressed = []

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import open_kitchen

                    result_str = await open_kitchen(name="demo", ctx=mock_ctx)

    parsed = json.loads(result_str)
    assert parsed["success"] is False


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
    _configure_admitted_recipe(mock_ctx, tmp_path / "demo.yaml")
    mock_ctx.config.migration.suppressed = []

    async def raise_apply(*a, **kw):
        raise RuntimeError("triage failed")

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    with patch(
                        "autoskillit.server.tools.tools_kitchen._apply_triage_gate",
                        new=raise_apply,
                    ):
                        from autoskillit.server.tools.tools_kitchen import open_kitchen

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
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    from autoskillit.server.tools.tools_kitchen import open_kitchen

                    result_str = await open_kitchen(ctx=mock_ctx)

    parsed = json.loads(result_str)
    assert parsed["success"] is False
    assert parsed["stage"] == "enable_components"


@pytest.mark.anyio
async def test_open_kitchen_sous_chef_projection_raises_returns_failure_envelope(
    tmp_path,
    monkeypatch,
):
    """Projection failure returns the project_sous_chef failure envelope."""
    from autoskillit.execution.backends import ClaudeCodeBackend
    from autoskillit.workspace import SkillsDirectoryProvider

    monkeypatch.chdir(tmp_path)
    mock_ctx = _make_mock_ctx()
    mock_ctx.enable_components = AsyncMock()
    mock_ctx.project_dir = tmp_path
    mock_ctx.backend = ClaudeCodeBackend()
    mock_ctx.skill_resolver = SkillsDirectoryProvider().resolver

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server.logger"):
            with patch(
                "autoskillit.server.tools.tools_kitchen._prime_quota_cache", new=AsyncMock()
            ):
                with patch("autoskillit.server.tools.tools_kitchen._write_hook_config"):
                    with patch(
                        "autoskillit.server.tools._serve_helpers.project_agent_skill_document",
                        side_effect=OSError("projection failed"),
                    ):
                        from autoskillit.server.tools.tools_kitchen import open_kitchen

                        result_str = await open_kitchen(ctx=mock_ctx)

    parsed = json.loads(result_str)
    assert parsed["success"] is False
    assert parsed["stage"] == "project_sous_chef"


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
    from autoskillit.server.tools.tools_kitchen import _kitchen_failure_envelope

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
    from autoskillit.server.tools.tools_kitchen import _kitchen_failure_envelope

    envelope = json.loads(_kitchen_failure_envelope(RuntimeError("test"), stage=stage))
    assert isinstance(envelope["success"], bool)
