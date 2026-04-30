"""Tests for tools_kitchen.py: hook drift warnings and failure envelopes."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.server.conftest import _make_mock_ctx

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


# ---------------------------------------------------------------------------
# Group E — hook drift / diagnostic warnings
# ---------------------------------------------------------------------------


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
    import autoskillit.server._misc as _misc_mod
    from autoskillit.server._misc import _prime_quota_cache

    async def raise_type_error(*a, **kw):
        raise TypeError("float() argument must be a string or a real number, not 'NoneType'")

    monkeypatch.setattr(_misc_mod, "check_and_sleep_if_needed", raise_type_error)

    mock_ctx = MagicMock()
    mock_ctx.config.quota_guard = MagicMock()

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server._misc.logger") as mock_logger:
            # Must not raise — fails open
            await _prime_quota_cache()
            mock_logger.warning.assert_called_once_with("quota_prime_failed", exc_info=True)


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
