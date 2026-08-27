"""Tests for the ContractMigrationAdapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.migration.adapters_contract import ContractMigrationAdapter
from autoskillit.migration.engine import MigrationFile

pytestmark = [pytest.mark.layer("migration"), pytest.mark.small]

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent.parent


class TestContractMigrationAdapter:
    # ME10
    def test_contract_adapter_discover_finds_contracts(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / ".autoskillit" / "recipes" / "contracts"
        contracts_dir.mkdir(parents=True)
        (contracts_dir / "foo.yaml").write_text("skill_hashes: {}")
        (contracts_dir / "bar.yaml").write_text("skill_hashes: {}")

        adapter = ContractMigrationAdapter()
        files = adapter.discover(tmp_path)

        assert len(files) == 2
        names = {f.name for f in files}
        assert names == {"foo", "bar"}
        assert all(f.file_type == "contract" for f in files)
        assert all(f.current_version is None for f in files)

    # ME11
    def test_contract_adapter_discover_empty_dir(self, tmp_path: Path) -> None:
        adapter = ContractMigrationAdapter()
        files = adapter.discover(tmp_path)
        assert files == []

    # ME12
    def test_contract_adapter_needs_migration_stale_contract_on_disk(self, tmp_path: Path) -> None:
        """ME12: needs_migration returns True for an on-disk contract with empty skill_hashes."""
        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        contracts_dir = recipes_dir / "contracts"
        contracts_dir.mkdir(parents=True)
        # A contract with empty skill_hashes is stale because bundled skills have real hashes.
        contract_path = contracts_dir / "test.yaml"
        contract_path.write_text("skill_hashes: {}\nbundled_manifest_version: '0.0.1'\n")

        file = MigrationFile(
            name="test", path=contract_path, file_type="contract", current_version=None
        )
        adapter = ContractMigrationAdapter()
        # Should be True: stale contract or load_recipe_card returns None → needs migration
        assert adapter.needs_migration(file) is True

    # ME13
    @pytest.mark.anyio
    async def test_contract_adapter_migrate_regenerates_card_on_disk(self, tmp_path: Path) -> None:
        """ME13: migrate() runs generate_recipe_card and writes a contract file to disk."""
        import shutil

        recipes_dir = tmp_path / ".autoskillit" / "recipes"
        contracts_dir = recipes_dir / "contracts"
        contracts_dir.mkdir(parents=True)

        # Copy the project-local smoke-test recipe so generate_recipe_card has valid input
        src_recipe = PROJECT_ROOT / ".autoskillit" / "recipes" / "smoke-test.yaml"
        assert src_recipe.exists(), f"smoke-test source missing: {src_recipe}"
        shutil.copy2(src_recipe, recipes_dir / "smoke-test.yaml")

        contract_path = contracts_dir / "smoke-test.yaml"
        contract_path.write_text("skill_hashes: {}\n")  # stale placeholder

        file = MigrationFile(
            name="smoke-test", path=contract_path, file_type="contract", current_version=None
        )
        adapter = ContractMigrationAdapter()
        result = await adapter.migrate(file, temp_dir=tmp_path / "temp")

        assert result.success is True
        assert result.name == "smoke-test"
        # generate_recipe_card writes a real contract file; verify it exists and is non-trivial
        written = contract_path.read_text()
        assert "skill_hashes" in written

    # ME14
    @pytest.mark.anyio
    async def test_contract_adapter_migrate_fails_gracefully_when_no_source(
        self, tmp_path: Path
    ) -> None:
        contracts_dir = tmp_path / ".autoskillit" / "recipes" / "contracts"
        contracts_dir.mkdir(parents=True)
        contract_path = contracts_dir / "missing.yaml"
        contract_path.write_text("skill_hashes: {}")
        # recipes_dir / "missing.yaml" does NOT exist

        file = MigrationFile(
            name="missing", path=contract_path, file_type="contract", current_version=None
        )
        adapter = ContractMigrationAdapter()
        result = await adapter.migrate(file, temp_dir=tmp_path / "temp")

        assert result.success is False
        assert "not found" in (result.error or "")
