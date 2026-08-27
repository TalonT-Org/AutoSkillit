"""Structural tests pinning the migration package's module-shape contract.

Each test exercises one module-shape invariant: the per-module line ceiling,
the module each adapter class is defined in, the absence of adapter
re-exports from engine.py, and the skill adapter's use of the engine helpers.
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
# engine.py is below the line ceiling
# ---------------------------------------------------------------------------


def test_engine_module_under_line_ceiling() -> None:
    engine_path = _MIGRATION_DIR / "engine.py"
    line_count = sum(1 for _ in engine_path.read_text(encoding="utf-8").splitlines())
    assert line_count <= _LINE_CEILING, (
        f"engine.py has {line_count} lines, exceeds ceiling of {_LINE_CEILING}"
    )


# ---------------------------------------------------------------------------
# each adapter module is below the line ceiling
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
# adapter classes live in their owning modules
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
# engine.py does not re-export adapter classes
# ---------------------------------------------------------------------------


def test_engine_module_does_not_reexport_relocated_classes() -> None:
    """Adapter classes must resolve from their owning modules.

    ``autoskillit.migration.engine`` keeps only the types it physically
    defines.
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
# adapters_skill.py can call the engine helpers
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_adapters_skill_can_call_legacy_helpers(tmp_path: Path) -> None:
    """The child-cardinality branch reaches the engine helpers and normalizes.

    The skill below declares a ``child_spawns`` entry with no explicit
    ``count``, which drives ``SkillMigrationAdapter.migrate`` through the
    ``SEMANTIC_CHILD_CARDINALITY_INVALID`` branch — the only caller of
    ``_normalize_legacy_child_spawn_cardinality``.
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

    adapter = SkillMigrationAdapter()
    result = await adapter.migrate(file, temp_dir=tmp_path / "temp")

    assert isinstance(result, MigrationResult)
    assert result.name == "legacy-child-spawn-cardinality"
    assert result.success is True, f"migration failed: {result.error!r}"
    # An early return on the "nothing to migrate" path leaves migrated_content
    # as None, so this pins that the normalizer actually ran and rewrote the
    # implicit cardinality into an explicit ``count: 1``.
    assert result.migrated_content is not None, (
        "migrate() returned success without producing content — the "
        "child-cardinality branch was never reached"
    )
    assert "count: 1" in result.migrated_content
