"""Composition root for the migration domain.

Wires the bundled adapters into a ``MigrationEngine``. This module exists so
``engine.py`` stays a leaf: the adapters import the ABCs from ``engine``, and
only this module imports the adapters.
"""

from __future__ import annotations

from autoskillit.migration.adapters_contract import ContractMigrationAdapter
from autoskillit.migration.adapters_diagram import DiagramMigrationAdapter
from autoskillit.migration.adapters_recipe import RecipeMigrationAdapter
from autoskillit.migration.adapters_skill import SkillMigrationAdapter
from autoskillit.migration.engine import MigrationEngine


def default_migration_engine() -> MigrationEngine:
    """Create a MigrationEngine with all bundled adapters registered."""
    return MigrationEngine(
        [
            RecipeMigrationAdapter(),
            ContractMigrationAdapter(),
            DiagramMigrationAdapter(),
            SkillMigrationAdapter(),
        ]
    )


__all__ = ["default_migration_engine"]
