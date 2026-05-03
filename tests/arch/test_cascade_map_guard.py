"""
REQ-GUARD-001..003, 005: CI guard validating cascade maps against the AST-derived
reverse import graph.  Zero runtime cost — pure static analysis.
"""

from __future__ import annotations

import ast
import warnings
from collections import defaultdict
from pathlib import Path

import pytest

from tests._test_filter import (
    LAYER_CASCADE_CONSERVATIVE,
    MODULE_CASCADE_CORE,
    MODULE_CASCADE_EXECUTION,
    MODULE_CASCADE_RECIPE,
    _file_to_package,
)

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_SRC_ROOT = Path(__file__).parent.parent.parent / "src" / "autoskillit"


def _all_src_files() -> list[Path]:
    return sorted(_SRC_ROOT.rglob("*.py"))


def _build_reexport_map(pkg_name: str) -> dict[str, str]:
    """Parse {pkg_name}/__init__.py (or .pyi stub) relative imports → submodule stem map.

    `from .feature_flags import is_feature_enabled` → {"is_feature_enabled": "feature_flags"}

    When __init__.py uses PEP 562 lazy loading (no relative imports in .py),
    the .pyi stub serves as the canonical source of re-export mappings.
    """
    reexport_map: dict[str, str] = {}

    for suffix in ("__init__.py", "__init__.pyi"):
        path = _SRC_ROOT / pkg_name / suffix
        if not path.exists():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            warnings.warn(f"SyntaxError parsing {path}: {exc} — skipping", stacklevel=2)
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
                stem = node.module
                for alias in node.names:
                    name = alias.asname if alias.asname else alias.name
                    reexport_map[name] = stem

    return reexport_map


def _build_core_reexport_map() -> dict[str, str]:
    reexport_map = _build_reexport_map("core")
    if not reexport_map:
        pytest.skip(
            "core/__init__.py and .pyi contain no relative imports — guard would pass vacuously"
        )
    return reexport_map


_AUTOSKILLIT_DUNDER_STEMS: frozenset[str] = frozenset({"__init__", "__main__"})


def _build_package_reverse_graph() -> dict[str, set[str]]:
    """
    REQ-GUARD-001 (package level).

    Scans all src files for `from autoskillit.{pkg}[.anything] import ...`.
    Returns {source_pkg: set[consuming_pkg]}.
    """
    graph: defaultdict[str, set[str]] = defaultdict(set)
    for filepath in _all_src_files():
        consumer_pkg = _file_to_package(str(filepath))
        if consumer_pkg is None:
            continue
        try:
            tree = ast.parse(filepath.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            warnings.warn(
                f"SyntaxError parsing {filepath}: {exc} — skipping file in package reverse graph",
                stacklevel=2,
            )
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.ImportFrom) and node.module):
                continue
            parts = node.module.split(".")
            if parts[0] == "autoskillit" and len(parts) >= 2:
                graph[parts[1]].add(consumer_pkg)
    return dict(graph)


def _build_pkg_module_reverse_graph(
    pkg_name: str, reexport_map: dict[str, str]
) -> dict[str, set[str]]:
    """
    Build a reverse import graph for submodules of a given autoskillit package.

    Tracks direct `from autoskillit.{pkg_name}.{stem} import ...` and
    re-exported names `from autoskillit.{pkg_name} import {name}` resolved
    through {pkg_name}/__init__.py.

    Returns {module_stem: set[consuming_pkg]}.

    Limitation: only captures relative imports in {pkg_name}/__init__.py via the
    reexport_map. Absolute submodule imports (`from autoskillit.{pkg_name} import X`
    at level=0) are invisible to this function; callers must handle them separately.
    """
    graph: defaultdict[str, set[str]] = defaultdict(set)
    for filepath in _all_src_files():
        consumer_pkg = _file_to_package(str(filepath))
        if consumer_pkg is None:
            continue
        try:
            tree = ast.parse(filepath.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            warnings.warn(
                f"SyntaxError parsing {filepath}: {exc} — skipping file in module reverse graph",
                stacklevel=2,
            )
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.ImportFrom) and node.module):
                continue
            parts = node.module.split(".")
            if parts[0] != "autoskillit":
                continue
            # Direct: from autoskillit.{pkg_name}.{stem} import ...
            if len(parts) >= 3 and parts[1] == pkg_name:
                graph[parts[2]].add(consumer_pkg)
            # Via re-export: from autoskillit.{pkg_name} import {name}
            elif len(parts) == 2 and parts[1] == pkg_name:
                for alias in node.names:
                    stem = reexport_map.get(alias.name)
                    if stem:
                        graph[stem].add(consumer_pkg)
    return dict(graph)


def _build_module_reverse_graph() -> dict[str, set[str]]:
    """REQ-GUARD-001 (module level, core). Returns {core_module_stem: set[consuming_pkg]}."""
    return _build_pkg_module_reverse_graph("core", _build_core_reexport_map())


def _build_execution_module_reverse_graph() -> dict[str, set[str]]:
    """REQ-GUARD-001 (module level, execution). Returns {stem: set[consuming_pkg]}."""
    return _build_pkg_module_reverse_graph("execution", _build_reexport_map("execution"))


def _build_recipe_module_reverse_graph() -> dict[str, set[str]]:
    """REQ-GUARD-001 (module level, recipe). Returns {stem: set[consuming_pkg]}."""
    graph = _build_pkg_module_reverse_graph("recipe", _build_reexport_map("recipe"))
    recipe_init = _SRC_ROOT / "recipe" / "__init__.py"
    if recipe_init.exists():
        try:
            tree = ast.parse(recipe_init.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            warnings.warn(
                f"SyntaxError parsing recipe/__init__.py: {exc}"
                " — absolute-import graph may be incomplete",
                stacklevel=2,
            )
            return graph
        for node in ast.walk(tree):
            if not (isinstance(node, ast.ImportFrom) and node.level == 0 and node.module):
                continue
            if node.module == "autoskillit.recipe":
                for alias in node.names:
                    stem = alias.name
                    if (recipe_init.parent / f"{stem}.py").exists():
                        graph.setdefault(stem, set()).add("recipe")
            elif node.module.startswith("autoskillit.recipe."):
                subpkg = node.module.split(".")[2]
                subpkg_dir = recipe_init.parent / subpkg
                if subpkg_dir.is_dir():
                    for alias in node.names:
                        name = alias.name
                        if (subpkg_dir / f"{name}.py").exists():
                            graph.setdefault(name, set()).add("recipe")
    return graph


class TestModuleCascadeCoreGuard:
    """REQ-GUARD-002: MODULE_CASCADE_CORE declared sets must be supersets of actual consumers."""

    def test_module_cascade_core_is_superset_of_ast_consumers(self) -> None:
        graph = _build_module_reverse_graph()
        violations: dict[str, dict[str, list[str]]] = {}
        for stem, declared in MODULE_CASCADE_CORE.items():
            actual = graph.get(stem, set())
            missing = actual - declared
            if missing:
                violations[stem] = {
                    "declared": sorted(declared),
                    "actual": sorted(actual),
                    "missing": sorted(missing),
                }
        assert not violations, (
            "MODULE_CASCADE_CORE entries are too narrow — update tests/_test_filter.py:\n"
            + "\n".join(
                f"  {stem}: add {v['missing']} (declared={v['declared']}, actual={v['actual']})"
                for stem, v in sorted(violations.items())
            )
        )

    def test_module_cascade_core_has_no_phantom_stems(self) -> None:
        graph = _build_module_reverse_graph()
        phantoms = [stem for stem in MODULE_CASCADE_CORE if not graph.get(stem)]
        assert not phantoms, (
            "MODULE_CASCADE_CORE contains stems with zero AST consumers — "
            "the source file may have been renamed or deleted:\n"
            f"  {sorted(phantoms)}\n"
            "Remove the stale entry or rename it to match the current module."
        )


class TestModuleCascadeExecutionGuard:
    """
    REQ-EXEC-004: Validate MODULE_CASCADE_EXECUTION against actual AST imports.
    Mirrors TestModuleCascadeCoreGuard.
    """

    def test_module_cascade_execution_is_superset_of_ast_consumers(self) -> None:
        graph = _build_execution_module_reverse_graph()
        violations: dict[str, dict[str, list[str]]] = {}
        for stem, declared in MODULE_CASCADE_EXECUTION.items():
            actual = graph.get(stem, set())
            missing = actual - declared
            if missing:
                violations[stem] = {
                    "declared": sorted(declared),
                    "actual": sorted(actual),
                    "missing": sorted(missing),
                }
        assert not violations, (
            "MODULE_CASCADE_EXECUTION entries are too narrow — update tests/_test_filter.py:\n"
            + "\n".join(
                f"  {stem}: add {v['missing']} (declared={v['declared']}, actual={v['actual']})"
                for stem, v in sorted(violations.items())
            )
        )

    def test_module_cascade_execution_has_no_phantom_stems(self) -> None:
        graph = _build_execution_module_reverse_graph()
        phantoms = [stem for stem in MODULE_CASCADE_EXECUTION if not graph.get(stem)]
        assert not phantoms, (
            "MODULE_CASCADE_EXECUTION contains stems with zero AST consumers — "
            "the source file may have been renamed or deleted:\n"
            f"  {sorted(phantoms)}\n"
            "Remove the stale entry or rename it to match the current module."
        )


class TestModuleCascadeRecipeGuard:
    """REQ-RECIPE-001: Validate MODULE_CASCADE_RECIPE against actual AST imports."""

    def test_module_cascade_recipe_is_superset_of_ast_consumers(self) -> None:
        graph = _build_recipe_module_reverse_graph()
        violations: dict[str, dict[str, list[str]]] = {}
        for stem, declared in MODULE_CASCADE_RECIPE.items():
            actual = graph.get(stem, set())
            declared_dirs = {d for d in declared if "/" not in d}
            file_prefixes = {d.split("/", 1)[0] for d in declared if "/" in d}
            covered = declared_dirs | file_prefixes
            missing = actual - covered
            if missing:
                violations[stem] = {
                    "declared": sorted(declared),
                    "actual": sorted(actual),
                    "missing": sorted(missing),
                }
        assert not violations, (
            "MODULE_CASCADE_RECIPE entries are too narrow — update tests/_test_filter.py:\n"
            + "\n".join(
                f"  {stem}: add {v['missing']} (declared={v['declared']}, actual={v['actual']})"
                for stem, v in sorted(violations.items())
            )
        )

    def test_module_cascade_recipe_has_no_phantom_stems(self) -> None:
        graph = _build_recipe_module_reverse_graph()
        phantoms = [stem for stem in MODULE_CASCADE_RECIPE if not graph.get(stem)]
        assert not phantoms, (
            "MODULE_CASCADE_RECIPE contains stems with zero AST consumers — "
            "the source file may have been renamed or deleted:\n"
            f"  {sorted(phantoms)}\n"
            "Remove the stale entry or rename it to match the current module."
        )


_TESTS_ROOT = Path(__file__).parent.parent


class TestModuleCascadeRecipeNarrowing:
    """Validate that MODULE_CASCADE_RECIPE actually narrows scope in build_test_scope."""

    def test_rules_module_narrows_to_recipe_dir_only(self) -> None:
        from tests._test_filter import FilterMode, build_test_scope

        scope = build_test_scope(
            changed_files={"src/autoskillit/recipe/rules_actions.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=_TESTS_ROOT,
        )
        assert not isinstance(scope, str)  # not a FullRunReason
        assert any("recipe" in str(p) for p in scope)
        layer_only = {"server", "cli", "fleet", "migration"}
        dir_scope_names = {p.name for p in scope if p.is_dir()}
        assert not (layer_only & dir_scope_names), (
            f"rules_actions.py should narrow to recipe-only but got dirs: {dir_scope_names}"
        )

    def test_recipe_init_change_fails_open_to_layer_cascade(self) -> None:
        from tests._test_filter import FilterMode, build_test_scope

        scope = build_test_scope(
            changed_files={"src/autoskillit/recipe/__init__.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=_TESTS_ROOT,
        )
        assert not isinstance(scope, str)
        assert any("recipe" in str(p) for p in scope)
        # recipe/__init__ is not in MODULE_CASCADE_RECIPE — fails open to full recipe cascade.
        # The full cascade includes cross-layer test files as specific file entries, not full
        # layer directories — confirm recipe/ dir is in scope.
        dir_scope_names = {p.name for p in scope if p.is_dir()}
        assert "recipe" in dir_scope_names, (
            f"recipe/__init__ change should include recipe/ dir but got dirs: {dir_scope_names}"
        )

    def test_unmapped_recipe_stem_fails_open_to_layer_cascade(self) -> None:
        from tests._test_filter import (
            LAYER_CASCADE_CONSERVATIVE,
            FilterMode,
            build_test_scope,
        )

        scope = build_test_scope(
            changed_files={"src/autoskillit/recipe/some_future_module.py"},
            mode=FilterMode.CONSERVATIVE,
            tests_root=_TESTS_ROOT,
        )
        assert not isinstance(scope, str)
        scope_strs = {str(p) for p in scope}
        layer_dirs = {
            entry
            for entry in LAYER_CASCADE_CONSERVATIVE["recipe"]
            if "/" not in entry and (_TESTS_ROOT / entry).is_dir()
        }
        for entry in layer_dirs:
            assert any(entry in s for s in scope_strs), (
                f"Fail-open should include '{entry}' from LAYER_CASCADE"
            )

    def test_mixed_mapped_and_unmapped_recipe_stems_fail_open_init(self) -> None:
        from tests._test_filter import (
            LAYER_CASCADE_CONSERVATIVE,
            FilterMode,
            build_test_scope,
        )

        scope = build_test_scope(
            changed_files={
                "src/autoskillit/recipe/rules_actions.py",
                "src/autoskillit/recipe/some_future_module.py",
            },
            mode=FilterMode.CONSERVATIVE,
            tests_root=_TESTS_ROOT,
        )
        assert not isinstance(scope, str)
        scope_strs = {str(p) for p in scope}
        layer_dirs = {
            entry
            for entry in LAYER_CASCADE_CONSERVATIVE["recipe"]
            if "/" not in entry and (_TESTS_ROOT / entry).is_dir()
        }
        for entry in layer_dirs:
            assert any(entry in s for s in scope_strs), (
                f"Mixed stems should fail-open; missing '{entry}'"
            )


class TestLayerCascadeConservativeGuard:
    """REQ-GUARD-002 (extended): LAYER_CASCADE_CONSERVATIVE values must cover actual consumers."""

    def test_layer_cascade_conservative_is_superset_of_ast_consumers(self) -> None:
        graph = _build_package_reverse_graph()
        violations: dict[str, dict[str, list[str]]] = {}
        for pkg, declared in LAYER_CASCADE_CONSERVATIVE.items():
            actual_pkgs = graph.get(pkg, set())
            declared_dirs = {d for d in declared if "/" not in d}
            file_prefixes = {d.split("/", 1)[0] for d in declared if "/" in d}
            covered = declared_dirs | file_prefixes
            missing = actual_pkgs - covered
            if missing:
                violations[pkg] = {
                    "declared": sorted(declared),
                    "actual": sorted(actual_pkgs),
                    "missing": sorted(missing),
                }
        assert not violations, (
            "LAYER_CASCADE_CONSERVATIVE entries are too narrow — update tests/_test_filter.py:\n"
            + "\n".join(f"  {pkg}: add {v['missing']}" for pkg, v in sorted(violations.items()))
        )


class TestFileLevelCascadeDriftGuard:
    """REQ-GUARD-005: File-level cascade entries must cover all test-file importers."""

    def test_no_importing_test_file_missing_from_file_level_entries(self) -> None:
        by_dir: dict[str, dict[str, set[str]]] = {}
        for pkg, cascade_set in LAYER_CASCADE_CONSERVATIVE.items():
            dir_entries = {e for e in cascade_set if "/" not in e}
            for entry in cascade_set:
                if "/" not in entry:
                    continue
                dir_name, fname = entry.split("/", 1)
                if dir_name not in dir_entries:
                    by_dir.setdefault(dir_name, {}).setdefault(pkg, set()).add(fname)

        tests_root = Path(__file__).parent.parent
        violations: list[str] = []

        for dir_name, pkg_map in sorted(by_dir.items()):
            dir_path = tests_root / dir_name
            if not dir_path.is_dir():
                continue
            for test_file in sorted(dir_path.glob("test_*.py")):
                try:
                    tree = ast.parse(test_file.read_text(encoding="utf-8"))
                except SyntaxError:
                    continue
                imported_pkgs: set[str] = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.module:
                        if node.module.startswith("autoskillit."):
                            parts = node.module.split(".")
                            if len(parts) >= 2:
                                imported_pkgs.add(parts[1])
                    elif isinstance(node, ast.Import):
                        for alias in node.names:
                            if alias.name.startswith("autoskillit."):
                                parts = alias.name.split(".")
                                if len(parts) >= 2:
                                    imported_pkgs.add(parts[1])

                fname = test_file.name
                for pkg, declared_files in pkg_map.items():
                    if pkg in imported_pkgs and fname not in declared_files:
                        violations.append(f"{dir_name}/{fname} imports autoskillit.{pkg}")

        assert not violations, (
            "Test files import from packages in directories with partial "
            "(file-level) cascade entries but are not declared in "
            "LAYER_CASCADE_CONSERVATIVE:\n"
            + "\n".join(f"  {v}" for v in violations)
            + "\nAdd the missing file-level entries to tests/_test_filter.py."
        )

    def test_every_file_level_entry_references_existing_test(self) -> None:
        tests_root = Path(__file__).parent.parent
        stale: list[str] = []
        for pkg, cascade_set in sorted(LAYER_CASCADE_CONSERVATIVE.items()):
            for entry in sorted(cascade_set):
                if "/" not in entry:
                    continue
                if not (tests_root / entry).is_file():
                    stale.append(f"{pkg}: {entry} (not found at tests/{entry})")
        assert not stale, (
            "File-level cascade entries reference nonexistent test files:\n"
            + "\n".join(f"  {s}" for s in stale)
            + "\nRemove the stale entry or update the filename in tests/_test_filter.py."
        )


class TestUnmappedPackageGuard:
    """REQ-GUARD-003: Every package found in src/ must be a key in LAYER_CASCADE_CONSERVATIVE."""

    def test_no_src_package_missing_from_conservative_cascade(self) -> None:
        all_files = _all_src_files()
        src_packages = {
            pkg
            for f in all_files
            if (pkg := _file_to_package(str(f))) is not None
            and pkg not in _AUTOSKILLIT_DUNDER_STEMS
        }
        missing = src_packages - set(LAYER_CASCADE_CONSERVATIVE.keys())
        assert not missing, (
            "New packages found in src/autoskillit/ with no LAYER_CASCADE_CONSERVATIVE entry.\n"
            f"  Unmapped: {sorted(missing)}\n"
            "Add an entry for each to LAYER_CASCADE_CONSERVATIVE in tests/_test_filter.py."
        )
