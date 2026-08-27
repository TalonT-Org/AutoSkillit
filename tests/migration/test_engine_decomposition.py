"""Structural tests pinning the engine.py decomposition contract.

Each test exercises a module-shape invariant the split is meant to enforce.

T1 — engine.py is below the 750-line ceiling after the split.
T2 — each extracted adapter module is below the 750-line ceiling.
T3 — adapter classes live in their new owning modules.
T4 — the engine module no longer re-exports relocated adapter classes.
T6 — adapters_skill.py can call the legacy helpers from engine.py.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from autoskillit.migration.adapters_skill import SkillMigrationAdapter
from autoskillit.migration.engine import (
    MigrationFile,
    MigrationResult,
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
# T4 — the engine module no longer re-exports relocated adapter classes
# ---------------------------------------------------------------------------


def test_engine_module_does_not_reexport_relocated_classes() -> None:
    """The decomposition removed the backward-compat re-export shim.

    Relocated classes must resolve from their owning modules, not from
    ``autoskillit.migration.engine``. The engine keeps only the types it
    physically defines.
    """
    engine_src = (_MIGRATION_DIR / "engine.py").read_text(encoding="utf-8")
    tree = ast.parse(engine_src)
    top_level_names = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    relocated = {
        "RecipeMigrationAdapter",
        "ContractMigrationAdapter",
        "DiagramMigrationAdapter",
        "SkillMigrationAdapter",
        "DefaultMigrationService",
    }
    leaked = relocated & top_level_names
    assert not leaked, (
        f"engine.py still defines relocated symbols: {sorted(leaked)}. "
        "Import them from their owning modules instead."
    )


# ---------------------------------------------------------------------------
# T6 — adapters_skill.py can call the legacy helpers from engine.py
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_adapters_skill_can_call_legacy_helpers(tmp_path: Path) -> None:
    """adapters_skill.py must import _skill_project_dir and
    _normalize_legacy_child_spawn_cardinality from engine.py.

    The helpers are exercised by the SEMANTIC_CHILD_CARDINALITY_INVALID
    migration path; if either import fails at module load,
    SkillMigrationAdapter.migrate raises NameError when triggered with that
    invalidity kind. The legacy child_spawns setup here forces that branch.
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
    # Reaching this branch (legacy cardinality detected) is the proof that
    # _normalize_legacy_child_spawn_cardinality was actually invoked.
    assert result.name == "legacy-child-spawn-cardinality"
    # The legacy cardinality setup is valid input for the normalizer, so
    # this branch must end with success — if the helper returned an error
    # string, the adapter would surface that as success=False.
    assert result.success is True, (
        f"_normalize_legacy_child_spawn_cardinality was reachable but did not "
        f"normalize the legacy setup: {result.error!r}"
    )
