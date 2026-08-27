"""Tests for the RecipeMigrationAdapter."""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from autoskillit.core.paths import pkg_root
from autoskillit.migration.adapters_recipe import RecipeMigrationAdapter
from autoskillit.migration.engine import (
    MIGRATE_RECIPES_MAX_RETRIES,
    MigrationFile,
)

from .conftest import make_migration_note, make_skill_result

pytestmark = [pytest.mark.layer("migration"), pytest.mark.small]


class TestRecipeMigrationAdapter:
    # ME1
    def test_recipe_adapter_discover_finds_recipes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        recipes_dir.mkdir(parents=True)
        (recipes_dir / "alpha.yaml").write_text("name: alpha\n")
        (recipes_dir / "beta.yaml").write_text("name: beta\n")
        # contracts subdir — should NOT be picked up
        contracts_dir = recipes_dir / "contracts"
        contracts_dir.mkdir()
        (contracts_dir / "contract.yaml").write_text("skill_hashes: {}")

        adapter = RecipeMigrationAdapter()
        files = adapter.discover(tmp_path)

        assert len(files) == 2
        names = {f.name for f in files}
        assert names == {"alpha", "beta"}
        assert all(f.file_type == "recipe" for f in files)

    # ME2
    def test_recipe_adapter_discover_empty_dir(self, tmp_path: Path) -> None:
        adapter = RecipeMigrationAdapter()
        files = adapter.discover(tmp_path)
        assert files == []

    # ME3
    def test_recipe_adapter_needs_migration_when_outdated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "autoskillit.migration.adapters_recipe.applicable_migrations",
            lambda *a, **kw: [make_migration_note()],
        )
        file = MigrationFile(
            name="test", path=tmp_path / "test.yaml", file_type="recipe", current_version="0.0.1"
        )
        adapter = RecipeMigrationAdapter()
        assert adapter.needs_migration(file) is True

    # ME4
    def test_recipe_adapter_no_migration_when_current(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "autoskillit.migration.adapters_recipe.applicable_migrations",
            lambda *a, **kw: [],
        )
        file = MigrationFile(
            name="test", path=tmp_path / "test.yaml", file_type="recipe", current_version="99.0.0"
        )
        adapter = RecipeMigrationAdapter()
        assert adapter.needs_migration(file) is False

    # ME5
    def test_recipe_adapter_needs_migration_when_no_version(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # current_version=None is treated as 0.0.0 by applicable_migrations;
        # we return a non-empty list to verify that None still causes needs_migration=True
        monkeypatch.setattr(
            "autoskillit.migration.adapters_recipe.applicable_migrations",
            lambda *a, **kw: [make_migration_note()],
        )
        file = MigrationFile(
            name="test", path=tmp_path / "test.yaml", file_type="recipe", current_version=None
        )
        adapter = RecipeMigrationAdapter()
        assert adapter.needs_migration(file) is True

    # ME6
    @pytest.mark.anyio
    async def test_recipe_adapter_build_skill_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recipe_path = tmp_path / ".autoskillit" / "recipes" / "myrecipe.yaml"
        recipe_path.parent.mkdir(parents=True)
        recipe_path.write_text("name: myrecipe\n")

        # Pre-create temp output so migrate() doesn't return "no output" failure
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_out = temp_dir / "migrations" / "myrecipe.yaml"
        temp_out.parent.mkdir(parents=True)
        temp_out.write_text("name: myrecipe\n# migrated\n")

        monkeypatch.setattr(
            "autoskillit.migration.adapters_recipe.applicable_migrations",
            lambda *a, **kw: [make_migration_note()],
        )
        mock_headless = AsyncMock(return_value=make_skill_result(True))

        adapter = RecipeMigrationAdapter()
        file = MigrationFile(
            name="myrecipe", path=recipe_path, file_type="recipe", current_version="0.0.1"
        )
        await adapter.migrate(file, run_headless=mock_headless, temp_dir=temp_dir)

        assert mock_headless.await_count == 1
        call_kwargs = mock_headless.call_args.kwargs
        assert "skill_command" in call_kwargs
        skill_cmd: str = call_kwargs["skill_command"]
        assert "script_path=" in skill_cmd
        assert "script_content=" in skill_cmd
        assert "migration_notes=" in skill_cmd
        assert "target_version=" in skill_cmd

    # ME7
    def test_recipe_adapter_temp_output_path(self, tmp_path: Path) -> None:
        adapter = RecipeMigrationAdapter()
        file = MigrationFile(
            name="myscript",
            path=tmp_path / "myscript.yaml",
            file_type="recipe",
            current_version=None,
        )
        temp_dir = tmp_path / "temp"
        result = adapter.get_temp_output_path(file, temp_dir)
        assert result == temp_dir / "migrations" / "myscript.yaml"

    # ME8
    def test_recipe_adapter_validate_valid_bundled_recipe(self) -> None:
        recipe_path = pkg_root() / "recipes" / "implementation.yaml"

        adapter = RecipeMigrationAdapter()
        is_valid, error = adapter.validate(recipe_path)

        assert is_valid is True
        assert error == ""

    # ME9
    def test_recipe_adapter_validate_invalid_yaml_structure(self, tmp_path: Path) -> None:
        recipe_path = tmp_path / "broken.yaml"
        recipe_path.write_text("steps: 'not_a_dict'\n")

        adapter = RecipeMigrationAdapter()
        is_valid, error = adapter.validate(recipe_path)

        assert is_valid is False
        assert len(error) > 0
        assert "mapping" in error.lower() or "expected" in error.lower()

    # ME9b
    def test_recipe_adapter_validate_errors_non_empty_branch(self, tmp_path: Path) -> None:
        recipe_path = tmp_path / "no-kitchen-rules.yaml"
        recipe_path.write_text(
            textwrap.dedent("""\
                name: bad-recipe
                steps:
                  step1:
                    tool: run_skill
                    with:
                      skill_command: "/foo"
                    on_success: step1
            """)
        )

        adapter = RecipeMigrationAdapter()
        is_valid, error = adapter.validate(recipe_path)

        assert is_valid is False
        assert len(error) > 0
        assert "kitchen_rules" in error.lower()


class TestMigrateRecipesConstant:
    @pytest.mark.anyio
    async def test_failed_headless_retries_match_constant(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recipe_path = tmp_path / ".autoskillit" / "recipes" / "myrecipe.yaml"
        recipe_path.parent.mkdir(parents=True)
        recipe_path.write_text("name: myrecipe\n")
        monkeypatch.setattr(
            "autoskillit.migration.adapters_recipe.applicable_migrations",
            lambda *a, **kw: [make_migration_note()],
        )
        mock_rh = AsyncMock(return_value=make_skill_result(False, "boom"))
        adapter = RecipeMigrationAdapter()
        file = MigrationFile(
            name="myrecipe",
            path=recipe_path,
            file_type="recipe",
            current_version="0.0.1",
        )
        result = await adapter.migrate(file, run_headless=mock_rh, temp_dir=tmp_path)
        assert not result.success
        assert result.retries_attempted == MIGRATE_RECIPES_MAX_RETRIES
