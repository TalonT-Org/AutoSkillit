"""Stub-symbol completeness tests for core/__init__.pyi.

Verifies that every public symbol defined in a core submodule appears in the
__init__.pyi stub. This catches the case where a developer adds a public symbol
to a submodule but forgets to add it to the stub — the symbol becomes invisible
to lazy_loader and causes ImportError at runtime.
"""

from __future__ import annotations

import ast

import pytest

from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_CORE_DIR = SRC_ROOT / "core"


def test_pyi_stub_covers_submodule_public_symbols() -> None:
    """Every public symbol in a core submodule must appear in __init__.pyi."""
    import autoskillit.core as core

    pyi_path = _CORE_DIR / "__init__.pyi"
    assert pyi_path.exists(), "core/__init__.pyi must exist"

    pyi_tree = ast.parse(pyi_path.read_text(encoding="utf-8"), filename=str(pyi_path))
    stub_by_submod: dict[str, set[str]] = {}
    for node in pyi_tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            names = {alias.name for alias in node.names}
            stub_by_submod.setdefault(node.module, set()).update(names)

    private_reexports: frozenset[str] = getattr(core, "_PRIVATE_REEXPORTS")

    missing: list[str] = []
    for submod_rel, stub_names in sorted(stub_by_submod.items()):
        parts = submod_rel.split(".")
        submod_path = _CORE_DIR.joinpath(*parts)

        if submod_path.is_dir():
            py_file = submod_path / "__init__.py"
        else:
            py_file = submod_path.with_suffix(".py")

        if not py_file.exists():
            continue

        submod_tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))

        submod_all = _extract_all(submod_tree)
        if submod_all is None:
            continue

        for name in sorted(submod_all - stub_names):
            if name.startswith("_"):
                continue
            if name in private_reexports:
                continue
            missing.append(f"  {submod_rel}: {name} defined but not in stub")

    assert not missing, (
        "Public symbols in core submodules missing from __init__.pyi:\n" + "\n".join(missing)
    )


def _extract_all(tree: ast.Module) -> set[str] | None:
    """Extract __all__ list if defined as a simple assignment."""
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    if isinstance(stmt.value, (ast.List, ast.Tuple)):
                        return {
                            elt.value
                            for elt in stmt.value.elts
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                        }
    return None
