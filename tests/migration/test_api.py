"""Tests for migration/_api.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autoskillit.migration._api import check_and_migrate
from autoskillit.migration.engine import MigrationResult

# ---------------------------------------------------------------------------
# T6 — migration/_api.py recipe imports are deferred
# ---------------------------------------------------------------------------


def test_migration_api_recipe_imports_are_deferred() -> None:
    """migration/_api.py must not import recipe/ at module load time."""
    import importlib
    import sys

    # Save then remove all autoskillit.recipe and migration._api modules so we can
    # test a cold import without corrupting the worker's sys.modules state (xdist safety).
    saved: dict[str, object] = {}
    to_remove = [
        k for k in sys.modules if "autoskillit.recipe" in k or k == "autoskillit.migration._api"
    ]
    for key in to_remove:
        saved[key] = sys.modules.pop(key)

    try:
        importlib.import_module("autoskillit.migration._api")

        # recipe imports should not have been triggered at module load
        loaded_recipe = [k for k in sys.modules if k.startswith("autoskillit.recipe")]
        assert not loaded_recipe, (
            f"migration._api loaded recipe modules at import time: {loaded_recipe}"
        )
    finally:
        # Remove any modules loaded during this test (cold imports are disposable)
        for key in list(sys.modules.keys()):
            if "autoskillit.recipe" in key or key == "autoskillit.migration._api":
                del sys.modules[key]
        # Restore original modules to preserve class identity for other tests in this worker
        sys.modules.update(saved)


# ---------------------------------------------------------------------------
# C5-2: Behavioral tests for check_and_migrate
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_check_and_migrate_recipe_not_found(tmp_path: Path) -> None:
    """Returns {"error": "No recipe named 'x' found"} when find_recipe_by_name → None."""
    with (
        patch("autoskillit.recipe.find_recipe_by_name", return_value=None),
        patch("autoskillit.migration._api.applicable_migrations", return_value=[]),
    ):
        result = await check_and_migrate("nonexistent", tmp_path, "0.1.0")

    assert result == {"error": "No recipe named 'nonexistent' found"}


@pytest.mark.anyio
async def test_check_and_migrate_up_to_date(tmp_path: Path) -> None:
    """Returns {"status": "up_to_date", "name": name} when applicable_migrations → []."""
    mock_match = MagicMock(path=tmp_path / "test.yaml")
    mock_recipe = MagicMock(version="0.1.0")

    with (
        patch("autoskillit.recipe.find_recipe_by_name", return_value=mock_match),
        patch("autoskillit.recipe.load_recipe", return_value=mock_recipe),
        patch("autoskillit.migration._api.applicable_migrations", return_value=[]),
    ):
        result = await check_and_migrate("test", tmp_path, "0.1.0")

    assert result == {"status": "up_to_date", "name": "test"}


@pytest.mark.anyio
async def test_check_and_migrate_migrated_successfully(tmp_path: Path) -> None:
    """Returns {"status": "migrated", "name": name} when engine.migrate_file → success=True."""
    mock_match = MagicMock(path=tmp_path / "test.yaml")
    mock_recipe = MagicMock(version="0.1.0")
    mock_engine = MagicMock()
    mock_engine.migrate_file = AsyncMock(
        return_value=MigrationResult(success=True, name="test")
    )

    with (
        patch("autoskillit.recipe.find_recipe_by_name", return_value=mock_match),
        patch("autoskillit.recipe.load_recipe", return_value=mock_recipe),
        patch("autoskillit.migration._api.applicable_migrations", return_value=["note"]),
        patch("autoskillit.migration._api.default_migration_engine", return_value=mock_engine),
    ):
        result = await check_and_migrate("test", tmp_path, "0.1.0")

    assert result == {"status": "migrated", "name": "test"}


@pytest.mark.anyio
async def test_check_and_migrate_migration_fails(tmp_path: Path) -> None:
    """Returns {"error": "Migration error msg", "name": name} when migrate_file → success=False."""
    mock_match = MagicMock(path=tmp_path / "test.yaml")
    mock_recipe = MagicMock(version="0.1.0")
    mock_engine = MagicMock()
    mock_engine.migrate_file = AsyncMock(
        return_value=MigrationResult(success=False, name="test", error="Schema validation failed")
    )

    with (
        patch("autoskillit.recipe.find_recipe_by_name", return_value=mock_match),
        patch("autoskillit.recipe.load_recipe", return_value=mock_recipe),
        patch("autoskillit.migration._api.applicable_migrations", return_value=["note"]),
        patch("autoskillit.migration._api.default_migration_engine", return_value=mock_engine),
    ):
        result = await check_and_migrate("test", tmp_path, "0.1.0")

    assert result == {"error": "Schema validation failed", "name": "test"}


@pytest.mark.anyio
async def test_check_and_migrate_llm_runner_blocked(tmp_path: Path) -> None:
    """Returns {"error": ..., "name": name} when engine returns LLM-runner-blocked failure."""
    mock_match = MagicMock(path=tmp_path / "test.yaml")
    mock_recipe = MagicMock(version="0.1.0")
    mock_engine = MagicMock()
    mock_engine.migrate_file = AsyncMock(
        return_value=MigrationResult(
            success=False,
            name="test",
            error="LLM-driven migration requires a headless runner.",
        )
    )

    with (
        patch("autoskillit.recipe.find_recipe_by_name", return_value=mock_match),
        patch("autoskillit.recipe.load_recipe", return_value=mock_recipe),
        patch("autoskillit.migration._api.applicable_migrations", return_value=["note"]),
        patch("autoskillit.migration._api.default_migration_engine", return_value=mock_engine),
    ):
        result = await check_and_migrate("test", tmp_path, "0.1.0")

    assert result["name"] == "test"
    assert "LLM-driven migration" in result["error"]


@pytest.mark.anyio
async def test_check_and_migrate_version_is_passed_to_applicable_migrations(
    tmp_path: Path,
) -> None:
    """Verifies recipe.version and installed_version flow through applicable_migrations."""
    mock_match = MagicMock(path=tmp_path / "test.yaml")
    mock_recipe = MagicMock(version="0.3.0")

    captured_args: list[tuple] = []

    def _capture_applicable_migrations(recipe_version: object, installed: object) -> list:
        captured_args.append((recipe_version, installed))
        return []

    with (
        patch("autoskillit.recipe.find_recipe_by_name", return_value=mock_match),
        patch("autoskillit.recipe.load_recipe", return_value=mock_recipe),
        patch(
            "autoskillit.migration._api.applicable_migrations",
            side_effect=_capture_applicable_migrations,
        ),
    ):
        result = await check_and_migrate("test", tmp_path, "0.6.0")

    assert result == {"status": "up_to_date", "name": "test"}
    assert len(captured_args) == 1
    assert captured_args[0] == ("0.3.0", "0.6.0")
