"""
REQ-GUARD-001..003: CI guard validating cascade maps against the AST-derived
reverse import graph.  Zero runtime cost — pure static analysis.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

import pytest

from tests._test_filter import (
    LAYER_CASCADE_CONSERVATIVE,
    MODULE_CASCADE_CORE,
    _file_to_package,
)

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_SRC_ROOT = Path(__file__).parent.parent.parent / "src" / "autoskillit"


def _all_src_files() -> list[Path]:
    return sorted(_SRC_ROOT.rglob("*.py"))


def _build_core_reexport_map() -> dict[str, str]:
    """
    Parse core/__init__.py relative imports to map re-exported names → submodule stem.

    `from .feature_flags import is_feature_enabled` → {"is_feature_enabled": "feature_flags"}
    """
    init_path = _SRC_ROOT / "core" / "__init__.py"
    if not init_path.exists():
        return {}
    reexport_map: dict[str, str] = {}
    try:
        tree = ast.parse(init_path.read_text(encoding="utf-8"))
    except SyntaxError:
        return {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
            stem = node.module
            for alias in node.names:
                reexport_map[alias.name] = stem
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
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.ImportFrom) and node.module):
                continue
            parts = node.module.split(".")
            if parts[0] == "autoskillit" and len(parts) >= 2:
                graph[parts[1]].add(consumer_pkg)
    return dict(graph)


def _build_module_reverse_graph() -> dict[str, set[str]]:
    """
    REQ-GUARD-001 (module level, core submodules).

    Tracks direct `from autoskillit.core.{stem} import ...` and
    re-exported names `from autoskillit.core import {name}` resolved
    through core/__init__.py.

    Returns {core_module_stem: set[consuming_pkg]}.
    """
    core_reexports = _build_core_reexport_map()
    graph: defaultdict[str, set[str]] = defaultdict(set)
    for filepath in _all_src_files():
        consumer_pkg = _file_to_package(str(filepath))
        if consumer_pkg is None:
            continue
        try:
            tree = ast.parse(filepath.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.ImportFrom) and node.module):
                continue
            parts = node.module.split(".")
            if parts[0] != "autoskillit":
                continue
            # Direct: from autoskillit.core.{stem} import ...
            if len(parts) >= 3 and parts[1] == "core":
                graph[parts[2]].add(consumer_pkg)
            # Via re-export: from autoskillit.core import {name}
            elif len(parts) == 2 and parts[1] == "core":
                for alias in node.names:
                    stem = core_reexports.get(alias.name)
                    if stem:
                        graph[stem].add(consumer_pkg)
    return dict(graph)


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


class TestLayerCascadeConservativeGuard:
    """REQ-GUARD-002 (extended): LAYER_CASCADE_CONSERVATIVE values must cover actual consumers."""

    def test_layer_cascade_conservative_is_superset_of_ast_consumers(self) -> None:
        graph = _build_package_reverse_graph()
        violations: dict[str, dict[str, list[str]]] = {}
        for pkg, declared in LAYER_CASCADE_CONSERVATIVE.items():
            actual_all = graph.get(pkg, set())
            # Exclude standalone test-file values (entries like "test_smoke_utils.py")
            actual_pkgs = {c for c in actual_all if not c.endswith(".py")}
            missing = actual_pkgs - declared
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
