"""Tests for the DefaultMigrationService (post-decomposition).

The service was extracted from engine.py into its own module but remains
importable through the engine re-export gateway for backward compatibility.
These tests exercise both wiring paths and the recipe+contract+diagram
orchestration logic.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from autoskillit.migration.engine import (
    DefaultMigrationService,
    default_migration_engine,
)
from autoskillit.migration.service import (
    DefaultMigrationService as DefaultMigrationServiceFromService,
)

from .conftest import make_migration_note, make_skill_result

pytestmark = [pytest.mark.layer("migration"), pytest.mark.small]


class TestDefaultMigrationServiceIdentity:
    def test_engine_and_service_resolve_to_same_class(self) -> None:
        assert DefaultMigrationService is DefaultMigrationServiceFromService

    def test_service_class_module_is_engine(self) -> None:
        # __module__ reassignment in engine.py must keep the relocated class
        # reporting autoskillit.migration.engine as its home.
        assert DefaultMigrationService.__module__ == "autoskillit.migration.engine"


class TestDefaultMigrationServiceBehaviour:
    @pytest.mark.anyio
    async def test_up_to_date_when_no_migration_needed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recipe_path = tmp_path / ".autoskillit" / "recipes" / "myrecipe.yaml"
        recipe_path.parent.mkdir(parents=True)
        recipe_path.write_text("name: myrecipe\nautoskillit_version: '99.0.0'\n")

        monkeypatch.setattr(
            "autoskillit.migration.adapters_recipe.applicable_migrations",
            lambda *a, **kw: [],
        )
        # Skip the contract + diagram regeneration paths so the service returns
        # the up_to_date branch — without this, the contract adapter treats a
        # missing contract card as stale and the service reports "migrated".
        engine = default_migration_engine()
        monkeypatch.setattr(
            engine.get_adapter("contract"), "needs_migration", lambda *a, **kw: False
        )
        monkeypatch.setattr(
            engine.get_adapter("diagram"), "needs_migration", lambda *a, **kw: False
        )

        service = DefaultMigrationService(engine)
        result = await service.migrate(recipe_path)

        assert result["status"] == "up_to_date"
        assert result["name"] == "myrecipe"

    @pytest.mark.anyio
    async def test_no_headless_runner_returns_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recipe_path = tmp_path / ".autoskillit" / "recipes" / "myrecipe.yaml"
        recipe_path.parent.mkdir(parents=True)
        recipe_path.write_text("name: myrecipe\nautoskillit_version: '0.0.1'\n")

        monkeypatch.setattr(
            "autoskillit.migration.adapters_recipe.applicable_migrations",
            lambda *a, **kw: [make_migration_note()],
        )

        service = DefaultMigrationService(default_migration_engine())
        result = await service.migrate(recipe_path)

        assert "error" in result
        assert "headless runner" in result["error"]
        assert result["name"] == "myrecipe"

    @pytest.mark.anyio
    async def test_successful_migration_with_headless(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recipe_path = tmp_path / ".autoskillit" / "recipes" / "myrecipe.yaml"
        recipe_path.parent.mkdir(parents=True)
        recipe_path.write_text("name: myrecipe\nautoskillit_version: '0.0.1'\n")

        # Pre-create temp output so RecipeMigrationAdapter finds migrated content
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_out = temp_dir / "migrations" / "myrecipe.yaml"
        temp_out.parent.mkdir(parents=True)
        temp_out.write_text("name: myrecipe\n# migrated\nautoskillit_version: '1.0.0'\n")

        monkeypatch.setattr(
            "autoskillit.migration.adapters_recipe.applicable_migrations",
            lambda *a, **kw: [make_migration_note()],
        )

        mock_headless = AsyncMock(return_value=make_skill_result(True))
        service = DefaultMigrationService(
            default_migration_engine(), run_headless=mock_headless, temp_dir=temp_dir
        )
        result = await service.migrate(recipe_path)

        assert result["status"] == "migrated"
        assert result["name"] == "myrecipe"
        assert mock_headless.await_count == 1
