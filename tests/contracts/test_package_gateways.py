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

from tests.arch._helpers import SRC_ROOT, _runtime_import_froms

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
# P14-F3: server/__init__.py must declare __all__
# ---------------------------------------------------------------------------


def test_server_all_defined() -> None:
    import autoskillit.server as m

    assert hasattr(m, "__all__"), "server/__init__.py must declare __all__"


def test_server_all_contains_core_exports() -> None:
    import autoskillit.server as m

    for name in ("mcp", "version_info", "make_context"):
        assert name in m.__all__, f"'{name}' missing from server.__all__"


# ---------------------------------------------------------------------------
# P14-F4: _delete_directory_contents must not appear in workspace.__all__
# ---------------------------------------------------------------------------


def test_private_name_not_in_workspace_all() -> None:
    import autoskillit.workspace as m

    assert "_delete_directory_contents" not in m.__all__


# ---------------------------------------------------------------------------
# P14-F5: _execute_readonly_query must not be accessible at execution pkg level
# ---------------------------------------------------------------------------


def test_execute_readonly_query_private_not_at_execution_pkg_level() -> None:
    import autoskillit.execution as m

    assert not hasattr(m, "_execute_readonly_query"), (
        "_execute_readonly_query must not be accessible at "
        "autoskillit.execution package level after import-as fix"
    )


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


@pytest.mark.anyio
async def test_migration_check_and_migrate_in_all():
    import autoskillit.migration as m

    assert "check_and_migrate" in m.__all__


@pytest.mark.anyio
async def test_migration_check_and_migrate_not_found(tmp_path):
    from autoskillit import __version__
    from autoskillit.migration import check_and_migrate

    result = await check_and_migrate("__nonexistent__", tmp_path, __version__)
    assert "error" in result


@pytest.mark.anyio
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
    assert result.get("status") == "up_to_date"


# ---------------------------------------------------------------------------
# REQ-GWAY-001: server/_factory.py Composition Root
# ---------------------------------------------------------------------------


def test_factory_make_context_returns_toolcontext(monkeypatch):
    monkeypatch.delenv("AUTOSKILLIT_KITCHEN_OPEN", raising=False)
    from autoskillit.config import AutomationConfig
    from autoskillit.core.paths import pkg_root
    from autoskillit.pipeline.audit import DefaultAuditLog
    from autoskillit.pipeline.context import ToolContext
    from autoskillit.server._factory import make_context

    ctx = make_context(AutomationConfig())
    assert isinstance(ctx, ToolContext)
    assert ctx.gate.enabled is False  # starts closed
    assert isinstance(ctx.audit, DefaultAuditLog)
    assert ctx.token_log is not None
    assert ctx.plugin_dir == str(pkg_root())


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


# ---------------------------------------------------------------------------
# REQ-IMP-001 gateway completeness — Default* concrete classes
# ---------------------------------------------------------------------------


def test_execution_gateway_exports_default_classes() -> None:
    import autoskillit.execution as m

    for name in ("DefaultDatabaseReader", "DefaultHeadlessExecutor", "DefaultTestRunner"):
        assert name in m.__all__, f"{name} missing from execution.__all__"


def test_migration_gateway_exports_default_migration_service() -> None:
    import autoskillit.migration as m

    assert "DefaultMigrationService" in m.__all__


def test_recipe_gateway_exports_default_recipe_repository() -> None:
    import autoskillit.recipe as m

    assert "DefaultRecipeRepository" in m.__all__


def test_workspace_gateway_exports_default_workspace_manager() -> None:
    import autoskillit.workspace as m

    assert "DefaultWorkspaceManager" in m.__all__


def test_workspace_gateway_exports_public_delete_alias() -> None:
    import autoskillit.workspace as m

    assert "delete_directory_contents" in m.__all__


# ── REQ-ARCH-004: __all__ completeness ───────────────────────────────────────


def test_package_all_matches_exports() -> None:
    """REQ-ARCH-004: Each package __init__.__all__ must match its exported symbol set.

    Two checks:
    1. Every name in __all__ is importable from the package (no dead entries).
    2. Every public name re-exported via relative or autoskillit.* imports in __init__.py
       appears in __all__ (no undeclared exports).

    Packages without __all__ (root autoskillit) are skipped.
    """
    import importlib

    AUTOSKILLIT_ROOT = SRC_ROOT
    PACKAGES_WITH_ALL = [
        "core",
        "config",
        "pipeline",
        "execution",
        "workspace",
        "recipe",
        "migration",
        "cli",
        "server",
    ]
    violations: list[str] = []

    for pkg_name in PACKAGES_WITH_ALL:
        module = importlib.import_module(f"autoskillit.{pkg_name}")
        all_list: list[str] = getattr(module, "__all__", None)  # type: ignore[assignment]
        if all_list is None:
            continue  # package opted out of __all__ — skip

        # Check 1: every __all__ entry is importable
        for name in all_list:
            if not hasattr(module, name):
                violations.append(
                    f"autoskillit.{pkg_name}: '{name}' in __all__ but not importable"
                )

        # Check 2: every public name from relative / intra-package imports is in __all__
        # Only intra-package absolute imports (autoskillit.{pkg_name}.*) are checked —
        # cross-package imports (e.g. `from autoskillit.core import get_logger` in
        # recipe/__init__.py) are internal helpers, not re-exports, and must be excluded.
        init_path = AUTOSKILLIT_ROOT / pkg_name / "__init__.py"
        for node in _runtime_import_froms(init_path):
            is_relative = node.level and node.level > 0
            is_intra_package = node.module and node.module.startswith(f"autoskillit.{pkg_name}.")
            if not (is_relative or is_intra_package):
                continue  # skip stdlib / third-party / cross-package imports

            for alias in node.names:
                name = alias.asname if alias.asname else alias.name
                if name.startswith("_") or name == "*":
                    continue
                if name not in all_list:
                    violations.append(
                        f"autoskillit.{pkg_name}: '{name}' re-exported via import "
                        f"but not in __all__"
                    )

    assert not violations, "__all__ completeness violations:\n" + "\n".join(violations)
