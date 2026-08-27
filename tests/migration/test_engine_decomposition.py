"""Structural tests pinning the engine.py decomposition contract.

These tests fail against the pre-decomposition monolithic engine.py and pass
once the file is split into focused adapter/service modules.

T1 — engine.py is below the 750-line ceiling after the split.
T2 — each extracted adapter module is below the 750-line ceiling.
T3 — adapter classes live in their new owning modules.
T4 — engine core still re-exports every public symbol with preserved
     __module__ identity for the relocated classes.
T6 — adapters_skill.py can call the legacy helpers _skill_project_dir and
     _normalize_legacy_child_spawn_cardinality from engine.py.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from autoskillit.migration.adapters_contract import ContractMigrationAdapter
from autoskillit.migration.adapters_diagram import AdvisoryResult, DiagramMigrationAdapter
from autoskillit.migration.adapters_recipe import RecipeMigrationAdapter
from autoskillit.migration.adapters_skill import SkillMigrationAdapter
from autoskillit.migration.engine import (
    MIGRATE_RECIPES_MAX_RETRIES,
    AdvisoryMigrationAdapter,
    DefaultMigrationService,
    DeterministicMigrationAdapter,
    HeadlessMigrationAdapter,
    MigrationAdapter,
    MigrationEngine,
    MigrationFile,
    MigrationResult,
)
from autoskillit.migration.engine import (
    AdvisoryResult as AdvisoryResultFromEngine,
)
from autoskillit.migration.engine import (
    ContractMigrationAdapter as ContractMigrationAdapterFromEngine,
)
from autoskillit.migration.engine import (
    DefaultMigrationService as DefaultMigrationServiceFromEngine,
)
from autoskillit.migration.engine import (
    DiagramMigrationAdapter as DiagramMigrationAdapterFromEngine,
)
from autoskillit.migration.engine import (
    RecipeMigrationAdapter as RecipeMigrationAdapterFromEngine,
)
from autoskillit.migration.engine import (
    SkillMigrationAdapter as SkillMigrationAdapterFromEngine,
)
from autoskillit.migration.service import (
    DefaultMigrationService as DefaultMigrationServiceFromService,
)

pytestmark = [pytest.mark.layer("migration"), pytest.mark.small]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_DIR = _REPO_ROOT / "src" / "autoskillit" / "migration"
_LINE_CEILING = 750


# ---------------------------------------------------------------------------
# T1 — engine.py is below the 750-line ceiling after the split
# ---------------------------------------------------------------------------


def test_engine_module_under_line_ceiling() -> None:
    engine_path = _MIGRATION_DIR / "engine.py"
    line_count = sum(1 for _ in engine_path.read_text(encoding="utf-8").splitlines())
    assert line_count <= _LINE_CEILING, (
        f"engine.py has {line_count} lines, exceeds ceiling of {_LINE_CEILING}"
    )


# ---------------------------------------------------------------------------
# T2 — each extracted adapter module is below the 750-line ceiling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename",
    [
        "adapters_recipe.py",
        "adapters_contract.py",
        "adapters_diagram.py",
        "adapters_skill.py",
        "service.py",
    ],
)
def test_extracted_modules_under_line_ceiling(filename: str) -> None:
    module_path = _MIGRATION_DIR / filename
    assert module_path.is_file(), f"missing extracted module: {module_path}"
    line_count = sum(1 for _ in module_path.read_text(encoding="utf-8").splitlines())
    assert line_count <= _LINE_CEILING, (
        f"{filename} has {line_count} lines, exceeds ceiling of {_LINE_CEILING}"
    )


# ---------------------------------------------------------------------------
# T3 — adapter classes live in their new owning modules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("class_name", "expected_module"),
    [
        ("RecipeMigrationAdapter", "adapters_recipe.py"),
        ("ContractMigrationAdapter", "adapters_contract.py"),
        ("DiagramMigrationAdapter", "adapters_diagram.py"),
        ("SkillMigrationAdapter", "adapters_skill.py"),
        ("DefaultMigrationService", "service.py"),
    ],
)
def test_adapter_classes_reside_in_new_modules(class_name: str, expected_module: str) -> None:
    src = (_MIGRATION_DIR / expected_module).read_text(encoding="utf-8")
    tree = ast.parse(src)
    class_names = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    assert class_name in class_names, (
        f"{class_name} not defined in {expected_module} (found: {sorted(class_names)})"
    )


# ---------------------------------------------------------------------------
# T4 — engine core still re-exports every public symbol with preserved
#       __module__ identity for the relocated classes.
# ---------------------------------------------------------------------------


def test_engine_module_reexports_public_symbols() -> None:
    # Every name must resolve to the same object regardless of import path.
    assert RecipeMigrationAdapter is RecipeMigrationAdapterFromEngine
    assert ContractMigrationAdapter is ContractMigrationAdapterFromEngine
    assert DiagramMigrationAdapter is DiagramMigrationAdapterFromEngine
    assert SkillMigrationAdapter is SkillMigrationAdapterFromEngine
    assert AdvisoryResult is AdvisoryResultFromEngine
    assert DefaultMigrationService is DefaultMigrationServiceFromService
    assert DefaultMigrationService is DefaultMigrationServiceFromEngine

    # Relocated classes report autoskillit.migration.engine as __module__.
    for cls in (
        RecipeMigrationAdapter,
        ContractMigrationAdapter,
        DiagramMigrationAdapter,
        SkillMigrationAdapter,
        AdvisoryResult,
        DefaultMigrationService,
    ):
        assert cls.__module__ == "autoskillit.migration.engine", (
            f"{cls.__name__}.__module__ == {cls.__module__!r}, "
            "expected autoskillit.migration.engine"
        )

    # Symbols that physically live in engine.py naturally report it.
    assert MigrationAdapter.__module__ == "autoskillit.migration.engine"
    assert HeadlessMigrationAdapter.__module__ == "autoskillit.migration.engine"
    assert DeterministicMigrationAdapter.__module__ == "autoskillit.migration.engine"
    assert AdvisoryMigrationAdapter.__module__ == "autoskillit.migration.engine"
    assert MigrationEngine.__module__ == "autoskillit.migration.engine"
    assert MigrationFile.__module__ == "autoskillit.migration.engine"
    assert MigrationResult.__module__ == "autoskillit.migration.engine"
    assert MIGRATE_RECIPES_MAX_RETRIES == 3


# ---------------------------------------------------------------------------
# T6 — adapters_skill.py can call the legacy helpers from engine.py
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_adapters_skill_can_call_legacy_helpers(tmp_path: Path) -> None:
    """adapters_skill.py must import _skill_project_dir and
    _normalize_legacy_child_spawn_cardinality from engine.py. The helpers
    are exercised by the SEMANTIC_CHILD_CARDINALITY_INVALID migration path;
    if either import fails at module load, SkillMigrationAdapter.migrate
    raises NameError when triggered with that invalidity kind.
    """
    skill_dir = tmp_path / ".claude" / "skills" / "legacy-child-spawn-cardinality"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(
        "---\n"
        "name: legacy-child-spawn-cardinality\n"
        "description: A previously valid skill whose child cardinality was implicit.\n"
        "semantic_version: 1\n"
        "semantic_requirements:\n"
        "  logical_roles:\n"
        "    - name: worker\n"
        "      purpose: Process one fixed task.\n"
        "  child_spawns:\n"
        "    - role: worker\n"
        "---\n"
        "# legacy-child-spawn-cardinality\n\n"
        "Delegates one fixed task and joins the result.\n",
        encoding="utf-8",
    )

    file = MigrationFile(
        name="legacy-child-spawn-cardinality",
        path=skill_path,
        file_type="skill",
        current_version=None,
    )

    # If adapters_skill.py fails to import either helper from engine.py,
    # the SEMANTIC_CHILD_CARDINALITY_INVALID branch raises NameError at
    # runtime when it calls _normalize_legacy_child_spawn_cardinality(data).
    adapter = SkillMigrationAdapter()
    result = await adapter.migrate(file, temp_dir=tmp_path / "temp")

    assert isinstance(result, MigrationResult)
    # Either success (legacy cardinality was migrated) or a controlled failure
    # from the helper returning an error string — both prove the helper
    # was reachable. What would NOT be acceptable is a NameError before
    # MigrationResult is constructed.
    assert result.name == "legacy-child-spawn-cardinality"
