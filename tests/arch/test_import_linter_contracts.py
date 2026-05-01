"""Tests verifying import-linter contract documentation.

REQ-ARCH-007: IL-003 must document the pipeline → config exception inline.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.arch._helpers import SRC_ROOT

# IL contract forbidden imports: package -> set of packages it CANNOT import at runtime.
_FORBIDDEN_BY_CONTRACT: dict[str, frozenset[str]] = {
    "core": frozenset(
        {"config", "pipeline", "execution", "workspace", "recipe", "migration", "server", "cli"}
    ),
    "config": frozenset(
        {"pipeline", "execution", "workspace", "recipe", "migration", "server", "cli"}
    ),
    "pipeline": frozenset({"execution", "workspace", "recipe", "migration", "server", "cli"}),
    "execution": frozenset(
        {"config", "pipeline", "workspace", "recipe", "migration", "server", "cli"}
    ),
    "workspace": frozenset(
        {"config", "pipeline", "execution", "recipe", "migration", "server", "cli"}
    ),
}

EXPECTED_CROSS_LAYER_GUARDS: dict[str, frozenset[str]] = {
    "core/_type_protocols_recipe.py": frozenset({"recipe"}),
    "execution/headless.py": frozenset({"config", "pipeline"}),
    "execution/linux_tracing.py": frozenset({"config"}),
    "execution/process.py": frozenset({"config"}),
    "execution/testing.py": frozenset({"config"}),
    "workspace/session_skills.py": frozenset({"config"}),
}


def _is_type_checking_guard(node: ast.If) -> bool:
    """Return True if the node is ``if TYPE_CHECKING:``."""
    test = node.test
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _collect_type_checking_modules(tree: ast.AST) -> set[str]:
    """Return all module paths imported under any TYPE_CHECKING guard in *tree*."""
    modules: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not _is_type_checking_guard(node):
            continue
        for child in ast.walk(ast.Module(body=node.body, type_ignores=[])):
            if isinstance(child, ast.ImportFrom) and child.module:
                modules.add(child.module)
    return modules


def _find_cross_layer_type_checking_imports(
    pkg_dir: Path,
    pkg_name: str,
) -> dict[str, frozenset[str]]:
    """Walk *pkg_dir* and return files with contract-violating TYPE_CHECKING imports.

    Returns ``{relative_path: frozenset[imported_package]}`` where each
    imported_package would violate the source package's IL contract if it
    were a runtime import (i.e. it is in _FORBIDDEN_BY_CONTRACT[pkg_name]).
    """
    result: dict[str, frozenset[str]] = {}
    forbidden = _FORBIDDEN_BY_CONTRACT.get(pkg_name)
    if forbidden is None:
        return result

    for py_file in sorted(pkg_dir.rglob("*.py")):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except SyntaxError:
            continue

        tc_modules = _collect_type_checking_modules(tree)
        cross_pkgs = frozenset(
            parts[1]
            for m in tc_modules
            if (parts := m.split("."))[0] == "autoskillit"
            and len(parts) >= 2
            and parts[1] in forbidden
        )

        if cross_pkgs:
            rel = f"{pkg_name}/{py_file.relative_to(pkg_dir)}"
            result[rel] = cross_pkgs

    return result


def test_il003_pipeline_config_exception_documented() -> None:
    """REQ-ARCH-007: IL-003 must contain an inline `# EXCEPTION` comment
    explaining why `autoskillit.config` is omitted from its forbidden_modules
    list (pipeline.context holds AutomationConfig as the DI wiring point).
    Validates that the exception is captured in source rather than tribal."""
    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    raw = pyproject_path.read_text()
    lines = raw.splitlines()
    in_block = False
    block_lines: list[str] = []
    for line in lines:
        if 'name = "IL-1 pipeline does not import IL-2 or IL-3"' in line:
            in_block = True
        if in_block:
            block_lines.append(line)
            if line.strip().startswith("[[") and len(block_lines) > 1:
                block_lines.pop()
                break
    block = "\n".join(block_lines)
    assert "# EXCEPTION" in block or "# Exception" in block, (
        "IL-003 must inline-document the pipeline → config exception. "
        "Add a `# EXCEPTION: pipeline.context owns AutomationConfig` "
        "comment above forbidden_modules in the IL-003 contract."
    )
    fm_section = block.split("forbidden_modules", 1)[1].split("]", 1)[0]
    assert '"autoskillit.config"' not in fm_section, (
        "IL-003 forbidden_modules must continue to omit autoskillit.config."
    )


def test_il_contract_count_is_guarded() -> None:
    """All 9 IL-* contracts must be present in pyproject.toml.

    Silently removing a contract from pyproject.toml would cause lint-imports
    to stop enforcing that layer boundary with no pytest signal. This test
    catches that drift.

    If you add a new contract: update the expected_count below and add its
    IL-NNN comment tag. If you remove a contract: restore it or obtain explicit
    sign-off and update this test.
    """
    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    raw = pyproject_path.read_text()

    expected_count = 9
    actual_count = raw.count("[[tool.importlinter.contracts]]")
    assert actual_count == expected_count, (
        f"Expected {expected_count} importlinter contracts in pyproject.toml, "
        f"found {actual_count}. Update this count when adding/removing contracts."
    )

    expected_ids = [f"IL-{str(i).zfill(3)}" for i in range(1, expected_count + 1)]
    missing = [il_id for il_id in expected_ids if il_id not in raw]
    assert not missing, (
        f"Import-linter contract ID tags missing from pyproject.toml: {missing}. "
        "Each contract block must carry its IL-NNN comment tag."
    )


def test_type_checking_cross_layer_guard_inventory() -> None:
    """Pin the set of files with TYPE_CHECKING-guarded cross-layer imports.

    import-linter's exclude_type_checking_imports=true makes these imports
    invisible to contract enforcement.  This AST test provides defense-in-depth:
    if a guard is removed (runtime violation) or a new guarded import is added
    (surface expansion), the test fails and forces explicit review.
    """
    scan_packages = ["core", "config", "execution", "workspace", "pipeline"]
    actual: dict[str, frozenset[str]] = {}
    for pkg_name in scan_packages:
        pkg_dir = SRC_ROOT / pkg_name
        if pkg_dir.is_dir():
            actual.update(_find_cross_layer_type_checking_imports(pkg_dir, pkg_name))

    assert actual == EXPECTED_CROSS_LAYER_GUARDS, (
        f"TYPE_CHECKING cross-layer guard inventory drifted.\n"
        f"  Expected: {EXPECTED_CROSS_LAYER_GUARDS}\n"
        f"  Actual:   {actual}\n"
        f"If a guard was intentionally added or removed, update "
        f"EXPECTED_CROSS_LAYER_GUARDS in this file."
    )


def test_core_recipe_guard_is_sole_exception() -> None:
    """Only _type_protocols_recipe.py may import from autoskillit.recipe in core/.

    IL-001 forbids core/ from importing recipe/ at runtime.  The single
    TYPE_CHECKING-guarded exception in _type_protocols_recipe.py is documented
    and intentional.  This test catches any second file that introduces a
    recipe dependency -- guarded or not.
    """
    core_dir = SRC_ROOT / "core"
    files_with_recipe_import: list[str] = []

    for py_file in sorted(core_dir.glob("*.py")):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("autoskillit.recipe")
            ):
                files_with_recipe_import.append(py_file.name)
                break

    assert files_with_recipe_import == ["_type_protocols_recipe.py"], (
        f"Expected only _type_protocols_recipe.py to import from autoskillit.recipe "
        f"in core/, but found: {files_with_recipe_import}. "
        f"IL-001 forbids core/ from depending on recipe/."
    )


def test_execution_forbidden_imports_are_guarded() -> None:
    """All config/pipeline imports in execution/ must be TYPE_CHECKING-guarded.

    IL-004 forbids execution/ from importing config or pipeline at runtime.
    import-linter enforces this via exclude_type_checking_imports=true, but
    provides a generic 'contract broken' message.  This AST test catches
    unguarded imports with a precise file:line error.
    """
    execution_dir = SRC_ROOT / "execution"
    forbidden_pkgs = {"autoskillit.config", "autoskillit.pipeline"}
    violations: list[str] = []

    for py_file in sorted(execution_dir.rglob("*.py")):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except SyntaxError:
            continue

        tc_import_modules = _collect_type_checking_modules(tree)
        tc_top_pkgs = {".".join(m.split(".")[:2]) for m in tc_import_modules}

        rel = py_file.relative_to(execution_dir)
        for top_node in tree.body:
            if isinstance(top_node, ast.If) and _is_type_checking_guard(top_node):
                continue
            for node in ast.walk(top_node):
                if isinstance(node, ast.ImportFrom) and node.module:
                    top_pkg = ".".join(node.module.split(".")[:2])
                    if top_pkg in forbidden_pkgs and top_pkg not in tc_top_pkgs:
                        violations.append(
                            f"{rel}:{node.lineno} imports {node.module} "
                            f"at module level without TYPE_CHECKING guard"
                        )

    assert not violations, "Unguarded config/pipeline imports found in execution/:\n" + "\n".join(
        f"  - {v}" for v in violations
    )
