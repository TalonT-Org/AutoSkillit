"""Tests for the MigrationEngine default registration and MIGRATE_RECIPES_MAX_RETRIES."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from autoskillit.migration.adapters_contract import ContractMigrationAdapter
from autoskillit.migration.adapters_recipe import RecipeMigrationAdapter
from autoskillit.migration.adapters_skill import SkillMigrationAdapter
from autoskillit.migration.engine import (
    MigrationEngine,
    MigrationFile,
    MigrationResult,
    default_migration_engine,
)

from .conftest import make_migration_note, make_skill_result

pytestmark = [pytest.mark.layer("migration"), pytest.mark.small]


class TestMigrationEngine:
    # ME15
    def test_engine_get_adapter_returns_correct_type(self) -> None:
        engine = default_migration_engine()
        assert isinstance(engine.get_adapter("recipe"), RecipeMigrationAdapter)
        assert isinstance(engine.get_adapter("contract"), ContractMigrationAdapter)

    # ME16
    def test_engine_get_adapter_returns_none_for_unknown(self) -> None:
        engine = default_migration_engine()
        assert engine.get_adapter("unknown") is None

    # ME17
    @pytest.mark.anyio
    async def test_engine_skips_migration_when_not_needed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "autoskillit.migration.adapters_recipe.applicable_migrations",
            lambda *a, **kw: [],
        )
        mock_headless = AsyncMock()
        file = MigrationFile(
            name="test", path=tmp_path / "test.yaml", file_type="recipe", current_version="99.0.0"
        )
        engine = default_migration_engine()
        result = await engine.migrate_file(
            file, run_headless=mock_headless, temp_dir=tmp_path / "temp"
        )

        assert result.success is True
        assert result.name == "test"
        mock_headless.assert_not_awaited()

    # ME18
    @pytest.mark.anyio
    async def test_engine_writes_back_on_successful_headless_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recipe_path = tmp_path / ".autoskillit" / "recipes" / "mypipe.yaml"
        recipe_path.parent.mkdir(parents=True)
        original_content = "name: mypipe\n"
        recipe_path.write_text(original_content)

        new_content = "name: mypipe\n# migrated\nautoskillit_version: '1.0.0'\n"
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_out = temp_dir / "migrations" / "mypipe.yaml"
        temp_out.parent.mkdir(parents=True)
        temp_out.write_text(new_content)

        monkeypatch.setattr(
            "autoskillit.migration.adapters_recipe.applicable_migrations",
            lambda *a, **kw: [make_migration_note()],
        )
        mock_headless = AsyncMock(return_value=make_skill_result(True))

        file = MigrationFile(
            name="mypipe", path=recipe_path, file_type="recipe", current_version="0.0.1"
        )
        engine = default_migration_engine()
        result = await engine.migrate_file(file, run_headless=mock_headless, temp_dir=temp_dir)

        assert result.success is True
        assert recipe_path.read_text() == new_content
        mock_headless.assert_awaited_once()

    # ME19
    @pytest.mark.anyio
    async def test_engine_returns_failure_when_headless_errors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recipe_path = tmp_path / ".autoskillit" / "recipes" / "test.yaml"
        recipe_path.parent.mkdir(parents=True)
        recipe_path.write_text("name: test\n")

        monkeypatch.setattr(
            "autoskillit.migration.adapters_recipe.applicable_migrations",
            lambda *a, **kw: [make_migration_note()],
        )
        mock_headless = AsyncMock(return_value=make_skill_result(False, "headless session failed"))

        file = MigrationFile(
            name="test", path=recipe_path, file_type="recipe", current_version="0.0.1"
        )
        engine = default_migration_engine()
        result = await engine.migrate_file(
            file, run_headless=mock_headless, temp_dir=tmp_path / "temp"
        )

        assert result.success is False
        assert "headless session failed" in (result.error or "")
        mock_headless.assert_awaited_once()

    # ME20
    @pytest.mark.anyio
    async def test_engine_returns_failure_when_temp_output_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recipe_path = tmp_path / ".autoskillit" / "recipes" / "test.yaml"
        recipe_path.parent.mkdir(parents=True)
        recipe_path.write_text("name: test\n")
        # temp output file intentionally NOT created

        monkeypatch.setattr(
            "autoskillit.migration.adapters_recipe.applicable_migrations",
            lambda *a, **kw: [make_migration_note()],
        )
        mock_headless = AsyncMock(return_value=make_skill_result(True))

        file = MigrationFile(
            name="test", path=recipe_path, file_type="recipe", current_version="0.0.1"
        )
        engine = default_migration_engine()
        result = await engine.migrate_file(
            file, run_headless=mock_headless, temp_dir=tmp_path / "temp"
        )

        assert result.success is False
        assert result.error is not None
        assert "output" in result.error.lower()

    @pytest.mark.anyio
    async def test_engine_reports_post_write_validation_failure(self, tmp_path: Path) -> None:
        class InvalidOutputAdapter(SkillMigrationAdapter):
            file_type = "invalid-output"

            def discover(self, project_dir: Path) -> list[MigrationFile]:
                return []

            def needs_migration(self, file: MigrationFile) -> bool:
                return True

            async def migrate(self, file: MigrationFile, *, temp_dir: Path) -> MigrationResult:
                return MigrationResult(
                    success=True,
                    name=file.name,
                    migrated_content="invalid migrated content\n",
                )

            def validate(self, path: Path) -> tuple[bool, str]:
                return False, "contract remains invalid"

        source = tmp_path / "artifact.txt"
        source.write_text("original\n")
        file = MigrationFile(
            name="artifact",
            path=source,
            file_type="invalid-output",
            current_version=None,
        )
        engine = MigrationEngine([InvalidOutputAdapter()])

        result = await engine.migrate_file(
            file,
            run_headless=AsyncMock(),
            temp_dir=tmp_path / "temp",
        )

        assert result.success is False
        assert result.error == "post-migration validation failed: contract remains invalid"
        assert source.read_text() == "invalid migrated content\n"
