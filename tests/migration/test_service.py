"""Tests for the DefaultMigrationService (post-decomposition).

The service was extracted from engine.py into its own module. These tests
exercise the recipe+contract+diagram orchestration logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from autoskillit.migration.engine import MigrationResult, default_migration_engine
from autoskillit.migration.service import DefaultMigrationService

from .conftest import make_migration_note, make_skill_result

pytestmark = [pytest.mark.layer("migration"), pytest.mark.small]


def _seed_recipe(tmp_path: Path, name: str = "myrecipe", version: str = "0.0.1") -> Path:
    recipe_path = tmp_path / ".autoskillit" / "recipes" / f"{name}.yaml"
    recipe_path.parent.mkdir(parents=True)
    recipe_path.write_text(f"name: {name}\nautoskillit_version: '{version}'\n")
    return recipe_path


class TestDefaultMigrationServiceBehaviour:
    @pytest.mark.anyio
    async def test_up_to_date_when_no_migration_needed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recipe_path = _seed_recipe(tmp_path, version="99.0.0")

        # Patch the late-import alias that DefaultMigrationService.migrate
        # actually resolves via its own ``from ... import`` inside migrate();
        # patching ``loader.applicable_migrations`` is too late because
        # service.py's local ``_applicable`` is already bound.
        monkeypatch.setattr(
            "autoskillit.migration.service._applicable",
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
        recipe_path = _seed_recipe(tmp_path)

        # Patch the late-import alias that DefaultMigrationService.migrate
        # actually resolves via its own ``from ... import`` inside migrate();
        # patching ``loader.applicable_migrations`` is too late because
        # service.py's local ``_applicable`` is already bound.
        monkeypatch.setattr(
            "autoskillit.migration.service._applicable",
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
        recipe_path = _seed_recipe(tmp_path)

        # Pre-create temp output so RecipeMigrationAdapter finds migrated content
        temp_dir = tmp_path / ".autoskillit" / "temp"
        temp_out = temp_dir / "migrations" / "myrecipe.yaml"
        temp_out.parent.mkdir(parents=True)
        temp_out.write_text("name: myrecipe\n# migrated\nautoskillit_version: '1.0.0'\n")

        # Patch the late-import alias that DefaultMigrationService.migrate
        # actually resolves via its own ``from ... import`` inside migrate();
        # patching ``loader.applicable_migrations`` is too late because
        # service.py's local ``_applicable`` is already bound.
        monkeypatch.setattr(
            "autoskillit.migration.service._applicable",
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

    @pytest.mark.anyio
    async def test_headless_runner_failure_records_to_failure_store(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recipe_path = _seed_recipe(tmp_path)

        # Patch the late-import alias that DefaultMigrationService.migrate
        # actually resolves via its own ``from ... import`` inside migrate().
        monkeypatch.setattr(
            "autoskillit.migration.service._applicable",
            lambda *a, **kw: [make_migration_note()],
        )

        mock_headless = AsyncMock(return_value=make_skill_result(False, result="boom"))
        service = DefaultMigrationService(
            default_migration_engine(), run_headless=mock_headless, temp_dir=tmp_path / "temp"
        )
        result = await service.migrate(recipe_path)

        # Migration error shape, populated before any contract/diagram work.
        assert "error" in result
        assert "Migration failed" in result["error"]
        assert "boom" in result["error"]
        assert result["name"] == "myrecipe"
        # Headless runner was invoked exactly once.
        assert mock_headless.await_count == 1

    @pytest.mark.anyio
    async def test_contract_regeneration_appends_to_contracts_regenerated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recipe_path = _seed_recipe(tmp_path)

        monkeypatch.setattr(
            "autoskillit.migration.service._applicable",
            lambda *a, **kw: [],
        )

        # Force the contract adapter to declare a stale card and to succeed.
        engine = default_migration_engine()
        contract_adapter = engine.get_adapter("contract")
        monkeypatch.setattr(contract_adapter, "needs_migration", lambda *a, **kw: True)
        monkeypatch.setattr(
            contract_adapter,
            "migrate",
            AsyncMock(return_value=MigrationResult(success=True, name="myrecipe")),
        )
        monkeypatch.setattr(
            engine.get_adapter("diagram"), "needs_migration", lambda *a, **kw: False
        )

        service = DefaultMigrationService(engine)
        result = await service.migrate(recipe_path)

        # Status flips to "migrated" because contracts_regenerated is non-empty,
        # even though no version migration ran.
        assert result["status"] == "migrated"
        assert result["name"] == "myrecipe"
        assert result["contracts_regenerated"] == ["myrecipe"]

    @pytest.mark.anyio
    async def test_diagram_advisory_populates_advisories_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recipe_path = _seed_recipe(tmp_path)

        monkeypatch.setattr(
            "autoskillit.migration.service._applicable",
            lambda *a, **kw: [],
        )

        engine = default_migration_engine()
        monkeypatch.setattr(
            engine.get_adapter("contract"), "needs_migration", lambda *a, **kw: False
        )
        diagram_adapter = engine.get_adapter("diagram")
        monkeypatch.setattr(diagram_adapter, "needs_migration", lambda *a, **kw: True)
        monkeypatch.setattr(
            diagram_adapter,
            "check_staleness",
            lambda f: _StubAdvisoryResult(name=f.name, suggestion="re-render with /render-recipe"),
        )

        service = DefaultMigrationService(engine)
        result = await service.migrate(recipe_path)

        # Status stays up_to_date but the advisory surfaces on the result.
        assert result["status"] == "up_to_date"
        assert result["name"] == "myrecipe"
        assert result["advisories"] == ["re-render with /render-recipe"]

    @pytest.mark.anyio
    async def test_status_migrated_carries_both_contracts_regenerated_and_advisories(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recipe_path = _seed_recipe(tmp_path)

        monkeypatch.setattr(
            "autoskillit.migration.service._applicable",
            lambda *a, **kw: [],
        )

        engine = default_migration_engine()
        contract_adapter = engine.get_adapter("contract")
        monkeypatch.setattr(contract_adapter, "needs_migration", lambda *a, **kw: True)
        monkeypatch.setattr(
            contract_adapter,
            "migrate",
            AsyncMock(return_value=MigrationResult(success=True, name="myrecipe")),
        )
        diagram_adapter = engine.get_adapter("diagram")
        monkeypatch.setattr(diagram_adapter, "needs_migration", lambda *a, **kw: True)
        monkeypatch.setattr(
            diagram_adapter,
            "check_staleness",
            lambda f: _StubAdvisoryResult(name=f.name, suggestion="re-render with /render-recipe"),
        )

        service = DefaultMigrationService(engine)
        result = await service.migrate(recipe_path)

        assert result["status"] == "migrated"
        assert result["name"] == "myrecipe"
        assert result["contracts_regenerated"] == ["myrecipe"]
        assert result["advisories"] == ["re-render with /render-recipe"]


@dataclass
class _StubAdvisoryResult:
    name: str
    suggestion: str
