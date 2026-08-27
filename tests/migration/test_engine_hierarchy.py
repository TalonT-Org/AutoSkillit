"""Tests for the MigrationAdapter abstract-base-class hierarchy."""

from __future__ import annotations

import inspect

import pytest

from autoskillit.migration.adapters_contract import ContractMigrationAdapter
from autoskillit.migration.adapters_diagram import DiagramMigrationAdapter
from autoskillit.migration.adapters_recipe import RecipeMigrationAdapter
from autoskillit.migration.engine import (
    AdvisoryMigrationAdapter,
    DeterministicMigrationAdapter,
    HeadlessMigrationAdapter,
    MigrationAdapter,
)

pytestmark = [pytest.mark.layer("migration"), pytest.mark.small]


class TestAdapterHierarchy:
    # ME-ADP1
    def test_adapter_abcs_are_importable(self) -> None:
        from abc import ABC

        assert issubclass(HeadlessMigrationAdapter, MigrationAdapter)
        assert issubclass(DeterministicMigrationAdapter, MigrationAdapter)
        assert issubclass(MigrationAdapter, ABC)

    # ME-ADP2
    def test_recipe_adapter_is_headless(self) -> None:
        assert isinstance(RecipeMigrationAdapter(), HeadlessMigrationAdapter)

    # ME-ADP3
    def test_contract_adapter_is_deterministic(self) -> None:
        assert isinstance(ContractMigrationAdapter(), DeterministicMigrationAdapter)

    # ME-ADP4
    def test_contract_migrate_has_no_run_headless_param(self) -> None:
        sig = inspect.signature(ContractMigrationAdapter.migrate)
        assert "run_headless" not in sig.parameters

    # ME-RT1
    def test_incomplete_adapter_raises_type_error(self) -> None:
        class BrokenAdapter(HeadlessMigrationAdapter):
            file_type = "broken"
            # missing: discover, needs_migration, validate, migrate

        with pytest.raises(TypeError):
            BrokenAdapter()

    # ME-ADP5
    def test_diagram_adapter_is_advisory_not_deterministic(self) -> None:
        assert isinstance(DiagramMigrationAdapter(), AdvisoryMigrationAdapter)
        assert not isinstance(DiagramMigrationAdapter(), DeterministicMigrationAdapter)
