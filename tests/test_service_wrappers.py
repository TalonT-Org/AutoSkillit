"""Behavior tests for DefaultRecipeRepository and DefaultMigrationService.

REQ-ARCH-006: DefaultRecipeRepository observable behavior.
REQ-ARCH-007: DefaultMigrationService observable behavior.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

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
    async def test_migrate_up_to_date_for_current_version(
        self, tmp_path: Path, monkeypatch
    ) -> None:  # SW-UPD-1
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

        monkeypatch.setattr(
            "autoskillit.recipe.load_recipe_card", lambda *a, **kw: {"skill_hashes": {}}
        )
        monkeypatch.setattr("autoskillit.recipe.check_contract_staleness", lambda *a, **kw: [])

        service = DefaultMigrationService(default_migration_engine())
        result = await service.migrate(recipe_path)

        assert isinstance(result, dict), "migrate() must return a dict"
        assert result.get("status") == "up_to_date", (
            f"Expected status='up_to_date' for recipe at current version, got: {result}"
        )
        assert result.get("name") == "test-recipe", f"Expected name='test-recipe', got: {result}"

    @pytest.mark.asyncio
    async def test_migrate_result_has_standard_structure(
        self, tmp_path: Path, monkeypatch
    ) -> None:  # SW-UPD-2
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

        monkeypatch.setattr(
            "autoskillit.recipe.load_recipe_card", lambda *a, **kw: {"skill_hashes": {}}
        )
        monkeypatch.setattr("autoskillit.recipe.check_contract_staleness", lambda *a, **kw: [])

        service = DefaultMigrationService(default_migration_engine())
        result = await service.migrate(recipe_path)

        assert isinstance(result, dict), "migrate() must return a dict"
        assert "name" in result, f"'name' key missing from result: {result}"
        assert "status" in result or "error" in result, (
            f"Result must have 'status' or 'error', got keys: {list(result)}"
        )

    @pytest.mark.asyncio
    async def test_migrate_stale_contract_no_version_migration_returns_migrated(  # SW-NEW-1
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Recipe at current version with a stale contract returns status=migrated."""
        from autoskillit.core.io import dump_yaml
        from autoskillit.migration import DefaultMigrationService, default_migration_engine
        from autoskillit.recipe import StaleItem
        from autoskillit.recipe.schema import AUTOSKILLIT_VERSION_KEY

        recipe_data = {
            "name": "test-recipe",
            "description": "Stale contract test fixture",
            "summary": "Fixture recipe at current version",
            AUTOSKILLIT_VERSION_KEY: autoskillit.__version__,
            "steps": [],
        }
        recipe_path = tmp_path / "test-recipe.yaml"
        dump_yaml(recipe_data, recipe_path)

        monkeypatch.setattr(
            "autoskillit.recipe.load_recipe_card",
            lambda *a, **kw: {"skill_hashes": {}},
        )
        monkeypatch.setattr(
            "autoskillit.recipe.check_contract_staleness",
            lambda *a, **kw: [
                StaleItem(
                    skill="(manifest)",
                    reason="version_mismatch",
                    stored_value="0.0",
                    current_value="1.0",
                )
            ],
        )
        monkeypatch.setattr("autoskillit.recipe.generate_recipe_card", lambda *a, **kw: {})

        service = DefaultMigrationService(default_migration_engine())
        result = await service.migrate(recipe_path)

        assert result["status"] == "migrated"
        assert result["contracts_regenerated"] == ["test-recipe"]
        assert result["name"] == "test-recipe"

    @pytest.mark.asyncio
    async def test_migrate_fresh_contract_and_no_version_migration_returns_up_to_date(  # SW-NEW-2
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Recipe at current version with a fresh contract returns status=up_to_date."""
        from autoskillit.core.io import dump_yaml
        from autoskillit.migration import DefaultMigrationService, default_migration_engine
        from autoskillit.recipe.schema import AUTOSKILLIT_VERSION_KEY

        recipe_data = {
            "name": "test-recipe",
            "description": "Fresh contract test fixture",
            "summary": "Fixture recipe at current version",
            AUTOSKILLIT_VERSION_KEY: autoskillit.__version__,
            "steps": [],
        }
        recipe_path = tmp_path / "test-recipe.yaml"
        dump_yaml(recipe_data, recipe_path)

        monkeypatch.setattr(
            "autoskillit.recipe.load_recipe_card",
            lambda *a, **kw: {"skill_hashes": {}},
        )
        monkeypatch.setattr("autoskillit.recipe.check_contract_staleness", lambda *a, **kw: [])

        service = DefaultMigrationService(default_migration_engine())
        result = await service.migrate(recipe_path)

        assert result == {"status": "up_to_date", "name": "test-recipe"}

    @pytest.mark.asyncio
    async def test_migrate_contract_regeneration_failure_is_nonfatal(  # SW-NEW-3
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Contract regeneration failure is non-fatal; migrate() does not raise."""
        from autoskillit.core.io import dump_yaml
        from autoskillit.migration import DefaultMigrationService, default_migration_engine
        from autoskillit.recipe.schema import AUTOSKILLIT_VERSION_KEY

        recipe_data = {
            "name": "test-recipe",
            "description": "Non-fatal failure test fixture",
            "summary": "Fixture recipe at current version",
            AUTOSKILLIT_VERSION_KEY: autoskillit.__version__,
            "steps": [],
        }
        recipe_path = tmp_path / "test-recipe.yaml"
        dump_yaml(recipe_data, recipe_path)

        monkeypatch.setattr("autoskillit.recipe.load_recipe_card", lambda *a, **kw: None)

        def _raise(*a, **kw):
            raise Exception("disk error")

        monkeypatch.setattr("autoskillit.recipe.generate_recipe_card", _raise)

        service = DefaultMigrationService(default_migration_engine())
        result = await service.migrate(recipe_path)

        assert "status" in result
        assert result["status"] == "up_to_date"

    @pytest.mark.asyncio
    async def test_migrate_contracts_regenerated_included_in_migrated_result(  # SW-NEW-4
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Full migration: version migration + stale contract both reflected in result."""
        import autoskillit.migration.loader as ml
        from autoskillit.core import RetryReason, SkillResult
        from autoskillit.core.io import dump_yaml
        from autoskillit.migration import DefaultMigrationService, default_migration_engine
        from autoskillit.recipe.schema import AUTOSKILLIT_VERSION_KEY

        installed_ver = autoskillit.__version__

        # Recipe at old version in proper project directory structure
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        recipe_data = {
            "name": "test-recipe",
            "description": "Full migration test fixture",
            "summary": "Fixture recipe at old version",
            AUTOSKILLIT_VERSION_KEY: "0.0.0",
            "steps": [],
        }
        recipe_path = recipes_dir / "test-recipe.yaml"
        dump_yaml(recipe_data, recipe_path)

        # Fake migration from 0.0.0 to current version
        fake_mig_dir = tmp_path / "migrations"
        fake_mig_dir.mkdir()
        migration_yaml = (
            "from_version: '0.0.0'\n"
            f"to_version: '{installed_ver}'\n"
            "description: Upgrade scripts\n"
            "changes:\n"
            "  - id: add-summary-field\n"
            "    description: Scripts now require a summary field\n"
            "    instruction: Add summary field to your script\n"
        )
        (fake_mig_dir / "0.0.0-migration.yaml").write_text(migration_yaml)
        monkeypatch.setattr(ml, "_migrations_dir", lambda: fake_mig_dir)

        # Create temp output file so RecipeMigrationAdapter finds migrated content
        temp_mig_dir = tmp_path / ".autoskillit" / "temp" / "migrations"
        temp_mig_dir.mkdir(parents=True)
        migrated_content = (
            f"name: test-recipe\nsteps: []\nautoskillit_version: '{installed_ver}'\n"
        )
        (temp_mig_dir / "test-recipe.yaml").write_text(migrated_content)

        success_result = SkillResult(
            success=True,
            result="ok",
            session_id="",
            subtype="success",
            is_error=False,
            exit_code=0,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
        )
        service = DefaultMigrationService(default_migration_engine())
        service._run_headless = AsyncMock(return_value=success_result)

        monkeypatch.setattr("autoskillit.recipe.load_recipe_card", lambda *a, **kw: None)
        monkeypatch.setattr("autoskillit.recipe.generate_recipe_card", lambda *a, **kw: {})

        result = await service.migrate(recipe_path)

        assert "contracts_regenerated" in result
        assert result["contracts_regenerated"] == ["test-recipe"]


def test_default_migration_service_accepts_run_headless_at_construction() -> None:
    """REQ-P12-001: DefaultMigrationService.__init__ accepts run_headless kwarg."""
    from unittest.mock import AsyncMock

    from autoskillit.migration import DefaultMigrationService, default_migration_engine

    sentinel = AsyncMock()
    service = DefaultMigrationService(default_migration_engine(), run_headless=sentinel)
    assert service._run_headless is sentinel


def test_default_migration_service_has_no_bind_headless() -> None:
    """REQ-P12-001: bind_headless is removed — constructor injection is the only wiring path."""
    from autoskillit.migration import DefaultMigrationService, default_migration_engine

    service = DefaultMigrationService(default_migration_engine())
    assert not hasattr(service, "bind_headless"), (
        "bind_headless must be removed from DefaultMigrationService. "
        "Pass run_headless at construction time instead."
    )
