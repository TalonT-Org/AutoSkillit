"""Behavior tests for DefaultRecipeRepository and DefaultMigrationService.

REQ-ARCH-006: DefaultRecipeRepository observable behavior.
REQ-ARCH-007: DefaultMigrationService observable behavior.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

import autoskillit

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.fixture
def isolated_recipe_discovery_cache() -> Iterator[None]:
    """Keep the centralized discovery counters local to one wrapper regression."""
    import autoskillit.recipe.io as recipe_io

    recipe_io._clear_recipe_discovery_caches()
    yield
    recipe_io._clear_recipe_discovery_caches()


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
        assert isinstance(result["recipes"], list)
        assert len(result["recipes"]) >= 1

    def test_load_and_validate_returns_content_and_valid(self, tmp_path: Path) -> None:
        """load_and_validate() for a bundled recipe returns 'content' and 'valid' keys."""
        result = self.repo.load_and_validate("implementation", tmp_path)
        assert isinstance(result, dict), "load_and_validate() must return a dict"
        assert "content" in result, f"Expected 'content' key in result, got: {list(result)}"
        assert "valid" in result, f"Expected 'valid' key in result, got: {list(result)}"
        assert isinstance(result["valid"], bool)
        assert result["content"]

    def test_validate_from_path_returns_findings(self) -> None:
        """validate_from_path() returns a dict with 'valid' and 'findings' keys."""
        recipe_path = self._recipes_dir / "implementation.yaml"
        assert recipe_path.exists(), f"Bundled recipe not found: {recipe_path}"

        result = self.repo.validate_from_path(recipe_path)
        assert isinstance(result, dict), "validate_from_path() must return a dict"
        assert "valid" in result, f"Expected 'valid' key in result, got: {list(result)}"
        assert "findings" in result, f"Expected 'findings' key in result, got: {list(result)}"
        assert isinstance(result["valid"], bool)
        assert isinstance(result["findings"], list)


class TestDefaultMigrationService:
    @pytest.mark.anyio
    async def test_migrate_up_to_date_for_current_version(
        self, tmp_path: Path, monkeypatch
    ) -> None:  # SW-UPD-1
        """A recipe whose autoskillit_version matches the installed version returns up_to_date."""
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
        recipe_path.write_text(
            yaml.dump(recipe_data, default_flow_style=False, allow_unicode=True)
        )

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

    @pytest.mark.anyio
    async def test_migrate_result_has_standard_structure(
        self, tmp_path: Path, monkeypatch
    ) -> None:  # SW-UPD-2
        """migrate() always returns a dict with 'name' and either 'status' or 'error'."""
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
        recipe_path.write_text(
            yaml.dump(recipe_data, default_flow_style=False, allow_unicode=True)
        )

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

    @pytest.mark.anyio
    async def test_migrate_stale_contract_no_version_migration_returns_migrated(  # SW-NEW-1
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Recipe at current version with a stale contract returns status=migrated."""
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
        recipe_path.write_text(
            yaml.dump(recipe_data, default_flow_style=False, allow_unicode=True)
        )

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

    @pytest.mark.anyio
    async def test_migrate_fresh_contract_and_no_version_migration_returns_up_to_date(  # SW-NEW-2
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Recipe at current version with a fresh contract returns status=up_to_date."""
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
        recipe_path.write_text(
            yaml.dump(recipe_data, default_flow_style=False, allow_unicode=True)
        )

        monkeypatch.setattr(
            "autoskillit.recipe.load_recipe_card",
            lambda *a, **kw: {"skill_hashes": {}},
        )
        monkeypatch.setattr("autoskillit.recipe.check_contract_staleness", lambda *a, **kw: [])

        service = DefaultMigrationService(default_migration_engine())
        result = await service.migrate(recipe_path)

        assert result == {"status": "up_to_date", "name": "test-recipe"}

    @pytest.mark.anyio
    async def test_migrate_contract_regeneration_failure_is_nonfatal(  # SW-NEW-3
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Contract regeneration failure is non-fatal; migrate() does not raise."""
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
        recipe_path.write_text(
            yaml.dump(recipe_data, default_flow_style=False, allow_unicode=True)
        )

        monkeypatch.setattr("autoskillit.recipe.load_recipe_card", lambda *a, **kw: None)

        def _raise(*a, **kw):
            raise OSError("disk error")

        monkeypatch.setattr("autoskillit.recipe.generate_recipe_card", _raise)

        service = DefaultMigrationService(default_migration_engine())
        result = await service.migrate(recipe_path)

        assert "status" in result
        assert result["status"] == "up_to_date"

    @pytest.mark.anyio
    async def test_migrate_contracts_regenerated_included_in_migrated_result(  # SW-NEW-4
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Full migration: version migration + stale contract both reflected in result."""
        import autoskillit.migration.loader as ml
        from autoskillit.core import RetryReason, SkillResult
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
        recipe_path.write_text(
            yaml.dump(recipe_data, default_flow_style=False, allow_unicode=True)
        )

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
        service = DefaultMigrationService(
            default_migration_engine(), run_headless=AsyncMock(return_value=success_result)
        )

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


# --- SW-B helpers and tests: DefaultRecipeRepository in-memory index ---


def _write_valid_recipe(path: Path) -> str:
    """Write a minimal valid recipe to path, return raw YAML text."""
    raw = (
        f"name: {path.stem}\n"
        "description: Test recipe\n"
        "steps:\n"
        "  done:\n"
        "    action: stop\n"
        "    message: Done\n"
    )
    path.write_text(raw)
    return raw


# SW-B1: load_and_validate does NOT call Path.read_text when info.content is available
def test_load_and_validate_skips_file_read_when_content_in_recipe_info(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Patching Path.read_text to raise ensures the content is never re-read."""
    import autoskillit.recipe.io as recipe_io
    from autoskillit.recipe._api import load_and_validate

    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    recipe_path = recipes_dir / "my-recipe.yaml"
    _write_valid_recipe(recipe_path)

    # Pre-compute the result while Path.read_text is still real; content is
    # already populated by Phase 2 (recipe discovery stores raw text).
    pre_result = recipe_io.list_recipes(tmp_path)
    assert any(r.content is not None for r in pre_result.items if r.name == "my-recipe")

    # Replace list_recipes with a lambda returning the pre-computed result so
    # the lambda itself never calls read_text after the patch below.
    monkeypatch.setattr(recipe_io, "list_recipes", lambda _: pre_result)

    # Guard only the recipe path: semantic validation may still read bundled
    # skill frontmatter when the process-local skill cache is cold.
    original_read_text = Path.read_text

    def guarded_read_text(
        path: Path,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> str:
        if path == recipe_path:
            raise AssertionError("recipe content should not be re-read when cached")
        return original_read_text(path, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)

    result = load_and_validate("my-recipe", tmp_path)
    assert "content" in result


# SW-B2: recipe discovery work is centrally cached, while callers get fresh results
def test_list_recipes_reuses_discovery_cache_with_fresh_containers(
    isolated_recipe_discovery_cache: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Warm discovery avoids uncached work without sharing result containers."""
    import autoskillit.recipe.io as recipe_io

    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    _write_valid_recipe(recipes_dir / "my-recipe.yaml")

    calls = {"enumerate": 0, "collect": 0}
    enumerate_uncached = recipe_io._enumerate_recipe_candidates_uncached
    collect_recipes = recipe_io.collect_recipes_from_candidates

    def counting_enumerate(*args: object, **kwargs: object) -> object:
        calls["enumerate"] += 1
        return enumerate_uncached(*args, **kwargs)

    def counting_collect(*args: object, **kwargs: object) -> object:
        calls["collect"] += 1
        return collect_recipes(*args, **kwargs)

    monkeypatch.setattr(recipe_io, "_enumerate_recipe_candidates_uncached", counting_enumerate)
    monkeypatch.setattr(recipe_io, "collect_recipes_from_candidates", counting_collect)

    first = recipe_io.list_recipes(tmp_path)
    warm = recipe_io.list_recipes(tmp_path)

    assert calls == {"enumerate": 2, "collect": 1}
    assert first is not warm
    assert first.items is not warm.items
    assert first.errors is not warm.errors
    assert first.items == warm.items
    assert first.errors == warm.errors


# SW-B3: project updates invalidate only the affected discovery tier
def test_list_recipes_reenumerates_only_project_candidates_after_recipe_added(
    isolated_recipe_discovery_cache: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Adding a project recipe re-enumerates and recollects without rescanning bundled recipes."""
    import autoskillit.recipe.io as recipe_io

    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    _write_valid_recipe(recipes_dir / "r1.yaml")

    calls = {"enumerate": 0, "collect": 0}
    enumerate_uncached = recipe_io._enumerate_recipe_candidates_uncached
    collect_recipes = recipe_io.collect_recipes_from_candidates

    def counting_enumerate(*args: object, **kwargs: object) -> object:
        calls["enumerate"] += 1
        return enumerate_uncached(*args, **kwargs)

    def counting_collect(*args: object, **kwargs: object) -> object:
        calls["collect"] += 1
        return collect_recipes(*args, **kwargs)

    monkeypatch.setattr(recipe_io, "_enumerate_recipe_candidates_uncached", counting_enumerate)
    monkeypatch.setattr(recipe_io, "collect_recipes_from_candidates", counting_collect)

    recipe_io.list_recipes(tmp_path)
    _write_valid_recipe(recipes_dir / "r2.yaml")
    updated = recipe_io.list_recipes(tmp_path)

    assert calls == {"enumerate": 3, "collect": 2}
    assert {recipe.name for recipe in updated.items} >= {"r1", "r2"}


# SW-B4: DefaultRecipeRepository delegates discovery to the public list_recipes API
def test_default_recipe_repository_find_delegates_to_list_recipes(
    isolated_recipe_discovery_cache: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """list() and find() both delegate so discovery cache ownership stays in recipe I/O."""
    import autoskillit.recipe.io as recipe_io
    from autoskillit.recipe.repository import DefaultRecipeRepository

    call_count = 0
    discovery_calls = {"enumerate": 0, "collect": 0}
    enumerate_uncached = recipe_io._enumerate_recipe_candidates_uncached
    collect_recipes = recipe_io.collect_recipes_from_candidates

    def counting_list(project_dir: Path) -> object:
        nonlocal call_count
        call_count += 1
        return recipe_io.list_recipes(project_dir)

    def counting_enumerate(*args: object, **kwargs: object) -> object:
        discovery_calls["enumerate"] += 1
        return enumerate_uncached(*args, **kwargs)

    def counting_collect(*args: object, **kwargs: object) -> object:
        discovery_calls["collect"] += 1
        return collect_recipes(*args, **kwargs)

    monkeypatch.setattr("autoskillit.recipe.repository.list_recipes", counting_list)
    monkeypatch.setattr(recipe_io, "_enumerate_recipe_candidates_uncached", counting_enumerate)
    monkeypatch.setattr(recipe_io, "collect_recipes_from_candidates", counting_collect)

    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    _write_valid_recipe(recipes_dir / "r.yaml")

    repo = DefaultRecipeRepository()
    repo.list(tmp_path)
    assert repo.find("r", tmp_path) is not None
    assert call_count == 2
    assert discovery_calls == {"enumerate": 2, "collect": 1}
