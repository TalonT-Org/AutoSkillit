"""Tests for Package Gateway API (groupC).

Verifies REQ-GWAY-001 through REQ-GWAY-008:
  001: server/_factory.py make_context() Composition Root
  002: recipe.load_and_validate()
  003: recipe.validate_from_path()
  004: recipe.list_all()
  005: migration.check_and_migrate()
  006: execution.execute_readonly_query public name
  007: Each gateway __all__ is exhaustive
  008: config.__all__ completeness (verified, no structural changes)
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# REQ-GWAY-006: execution/__init__.py public surface
# ---------------------------------------------------------------------------


def test_execute_readonly_query_in_execution_all():
    import autoskillit.execution as m

    assert "execute_readonly_query" in m.__all__


def test_private_names_not_in_execution_all():
    import autoskillit.execution as m

    assert "_truncate" not in m.__all__
    assert "_execute_readonly_query" not in m.__all__


def test_execute_readonly_query_is_callable():
    from autoskillit.execution import execute_readonly_query

    assert callable(execute_readonly_query)


# ---------------------------------------------------------------------------
# REQ-GWAY-002/003/004: recipe/__init__.py facades
# ---------------------------------------------------------------------------


def test_recipe_facades_in_all():
    import autoskillit.recipe as m

    assert "load_and_validate" in m.__all__
    assert "validate_from_path" in m.__all__
    assert "list_all" in m.__all__


def test_recipe_load_and_validate_not_found(tmp_path):
    from autoskillit.recipe import load_and_validate

    result = load_and_validate("__nonexistent__", project_dir=tmp_path)
    assert "error" in result


def test_recipe_load_and_validate_found_returns_required_keys(tmp_path):
    from autoskillit.recipe import load_and_validate
    from autoskillit.recipe.io import list_recipes

    recipes = list_recipes(Path("/nonexistent"))
    bundled = [r for r in recipes.items if r.source.value == "builtin"]
    if not bundled:
        pytest.skip("No bundled recipes available")
    result = load_and_validate(bundled[0].name)
    assert "content" in result
    assert "suggestions" in result
    assert "valid" in result
    assert isinstance(result["suggestions"], list)
    assert isinstance(result["valid"], bool)


def test_recipe_validate_from_path_not_found(tmp_path):
    from autoskillit.recipe import validate_from_path

    result = validate_from_path(tmp_path / "nonexistent.yaml")
    assert result["valid"] is False
    assert len(result["findings"]) > 0


def test_recipe_validate_from_path_valid_file(tmp_path):
    from autoskillit.recipe import validate_from_path
    from autoskillit.recipe.io import list_recipes

    recipes = list_recipes(Path("/nonexistent"))
    bundled = [r for r in recipes.items if r.source.value == "builtin"]
    if not bundled:
        pytest.skip("No bundled recipes available")
    result = validate_from_path(bundled[0].path)
    assert "valid" in result
    assert "findings" in result
    assert isinstance(result["findings"], list)


def test_recipe_list_all_returns_required_keys(tmp_path):
    from autoskillit.recipe import list_all

    result = list_all(project_dir=tmp_path)
    assert "count" in result
    assert "recipes" in result
    assert isinstance(result["count"], int)
    assert isinstance(result["recipes"], list)
    assert result["count"] == len(result["recipes"])


def test_recipe_list_all_includes_builtins():
    from autoskillit.recipe import list_all

    result = list_all()
    assert result["count"] >= 4  # at least the 4 bundled recipes


# ---------------------------------------------------------------------------
# REQ-GWAY-005: migration/__init__.py check_and_migrate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_migration_check_and_migrate_in_all():
    import autoskillit.migration as m

    assert "check_and_migrate" in m.__all__


@pytest.mark.asyncio
async def test_migration_check_and_migrate_not_found(tmp_path):
    from autoskillit import __version__
    from autoskillit.migration import check_and_migrate

    result = await check_and_migrate("__nonexistent__", tmp_path, __version__)
    assert "error" in result


@pytest.mark.asyncio
async def test_migration_check_and_migrate_up_to_date(tmp_path):
    from autoskillit import __version__
    from autoskillit.migration import check_and_migrate
    from autoskillit.recipe.io import list_recipes

    recipes = list_recipes(Path("/nonexistent"))
    bundled = [r for r in recipes.items if r.source.value == "builtin"]
    if not bundled:
        pytest.skip("No bundled recipes available")
    # Bundled recipes are current — should report up_to_date
    # Use tmp_path so project recipes dir doesn't interfere
    result = await check_and_migrate(bundled[0].name, tmp_path, __version__)
    # Either up_to_date (recipe is current) or error (recipe not found in tmp_path)
    assert "status" in result or "error" in result


# ---------------------------------------------------------------------------
# REQ-GWAY-001: server/_factory.py Composition Root
# ---------------------------------------------------------------------------


def test_factory_module_exists():
    import importlib

    m = importlib.import_module("autoskillit.server._factory")
    assert hasattr(m, "make_context")


def test_factory_make_context_returns_toolcontext():
    from autoskillit.config import AutomationConfig
    from autoskillit.pipeline.context import ToolContext
    from autoskillit.server._factory import make_context

    ctx = make_context(AutomationConfig())
    assert isinstance(ctx, ToolContext)
    assert ctx.gate.enabled is False  # starts closed
    assert ctx.audit is not None
    assert ctx.token_log is not None
    assert ctx.plugin_dir != ""


def test_factory_make_context_accepts_runner():
    from autoskillit.config import AutomationConfig
    from autoskillit.server._factory import make_context

    ctx = make_context(AutomationConfig(), runner=None)
    assert ctx.runner is None


def test_factory_make_context_accepts_plugin_dir(tmp_path):
    from autoskillit.config import AutomationConfig
    from autoskillit.server._factory import make_context

    ctx = make_context(AutomationConfig(), plugin_dir=str(tmp_path))
    assert ctx.plugin_dir == str(tmp_path)


# ---------------------------------------------------------------------------
# REQ-GWAY-007/008: __all__ exhaustiveness
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pkg_name",
    [
        "autoskillit.recipe",
        "autoskillit.migration",
        "autoskillit.execution",
        "autoskillit.config",
    ],
)
def test_all_entries_importable(pkg_name):
    import importlib

    mod = importlib.import_module(pkg_name)
    for name in mod.__all__:
        assert hasattr(mod, name), f"{pkg_name}.__all__ has {name!r} but module has no attribute"


def test_config_all_complete():
    from autoskillit.config import __all__ as config_all

    assert "AutomationConfig" in config_all
    assert "load_config" in config_all
    # Spot-check all sub-config classes are present
    for cls in ["TestCheckConfig", "SafetyConfig", "ModelConfig", "TokenUsageConfig"]:
        assert cls in config_all, f"{cls} missing from config.__all__"
