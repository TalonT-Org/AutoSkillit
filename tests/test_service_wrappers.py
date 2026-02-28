"""Behavior tests for DefaultRecipeRepository and DefaultMigrationService.

REQ-ARCH-006: DefaultRecipeRepository observable behavior.
REQ-ARCH-007: DefaultMigrationService observable behavior.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import autoskillit


class TestDefaultRecipeRepository:
    def setup_method(self) -> None:
        from autoskillit.recipe import DefaultRecipeRepository

        self.repo = DefaultRecipeRepository()
        # Path to the package's bundled recipes directory
        self._recipes_dir = Path(autoskillit.__file__).parent / "recipes"

    def test_list_all_returns_recipes_key(self, tmp_path: Path) -> None:
        """list_all() returns a dict containing a 'recipes' key."""
        result = self.repo.list_all(project_dir=tmp_path)
        assert isinstance(result, dict), "list_all() must return a dict"
        assert "recipes" in result, f"Expected 'recipes' key, got: {list(result)}"

    def test_load_and_validate_returns_content_and_valid(self, tmp_path: Path) -> None:
        """load_and_validate() for a bundled recipe returns 'content' and 'valid' keys."""
        result = self.repo.load_and_validate("implementation-pipeline", tmp_path)
        assert isinstance(result, dict), "load_and_validate() must return a dict"
        assert "content" in result, f"Expected 'content' key in result, got: {list(result)}"
        assert "valid" in result, f"Expected 'valid' key in result, got: {list(result)}"

    def test_validate_from_path_returns_findings(self) -> None:
        """validate_from_path() returns a dict with 'valid' and 'findings' keys."""
        recipe_path = self._recipes_dir / "implementation-pipeline.yaml"
        assert recipe_path.exists(), f"Bundled recipe not found: {recipe_path}"

        result = self.repo.validate_from_path(recipe_path)
        assert isinstance(result, dict), "validate_from_path() must return a dict"
        assert "valid" in result, f"Expected 'valid' key in result, got: {list(result)}"
        assert "findings" in result, f"Expected 'findings' key in result, got: {list(result)}"


class TestDefaultMigrationService:
    @pytest.mark.asyncio
    async def test_migrate_up_to_date_for_current_version(self, tmp_path: Path) -> None:
        """A recipe whose autoskillit_version matches the installed version returns up_to_date."""
        from autoskillit.core.io import dump_yaml
        from autoskillit.migration import DefaultMigrationService, default_migration_engine
        from autoskillit.recipe.schema import AUTOSKILLIT_VERSION_KEY

        recipe_data = {
            "name": "test-recipe",
            "description": "Enforcement test fixture",
            "summary": "Fixture recipe at current version",
            AUTOSKILLIT_VERSION_KEY: autoskillit.__version__,
            "steps": [],
        }
        recipe_path = tmp_path / "test-recipe.yaml"
        dump_yaml(recipe_data, recipe_path)

        service = DefaultMigrationService(default_migration_engine())
        result = await service.migrate(recipe_path)

        assert isinstance(result, dict), "migrate() must return a dict"
        assert result.get("status") == "up_to_date", (
            f"Expected status='up_to_date' for recipe at current version, got: {result}"
        )
        assert result.get("name") == "test-recipe", f"Expected name='test-recipe', got: {result}"

    @pytest.mark.asyncio
    async def test_migrate_result_has_standard_structure(self, tmp_path: Path) -> None:
        """migrate() always returns a dict with 'name' and either 'status' or 'error'."""
        from autoskillit.core.io import dump_yaml
        from autoskillit.migration import DefaultMigrationService, default_migration_engine
        from autoskillit.recipe.schema import AUTOSKILLIT_VERSION_KEY

        recipe_data = {
            "name": "structure-test",
            "description": "Structure assertion fixture",
            "summary": "Verifies result shape invariant",
            AUTOSKILLIT_VERSION_KEY: autoskillit.__version__,
            "steps": [],
        }
        recipe_path = tmp_path / "structure-test.yaml"
        dump_yaml(recipe_data, recipe_path)

        service = DefaultMigrationService(default_migration_engine())
        result = await service.migrate(recipe_path)

        assert isinstance(result, dict), "migrate() must return a dict"
        assert "name" in result, f"'name' key missing from result: {result}"
        assert "status" in result or "error" in result, (
            f"Result must have 'status' or 'error', got keys: {list(result)}"
        )
