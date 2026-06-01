#!/usr/bin/env python3
"""Validate __init__.pyi stub files export all public symbols from their submodules.

AST-scans __init__.pyi files, maps each re-exported symbol to its source submodule,
then checks that every public symbol in the submodule appears in the stub.

A symbol added to a submodule but missing from the stub is invisible to
lazy_loader.attach_stub() and will cause ImportError at runtime.

Exit 0 if all stubs are complete. Exit 1 with details on missing symbols.
"""

import ast
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "autoskillit"

_PRIVATE_REEXPORTS = frozenset(
    {
        "_InstallLock",
        "_is_release_tag",
        "_is_stable_track",
        "_retire_old_versions",
        "_collect_disabled_feature_tags",
        "_AUTOSKILLIT_GITIGNORE_ENTRIES",
        "_COMMITTED_BY_DESIGN",
    }
)


def _extract_all(tree: ast.Module) -> set[str] | None:
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


def check_file(pyi_path: Path) -> list[str]:
    violations: list[str] = []
    try:
        pyi_tree = ast.parse(pyi_path.read_text(encoding="utf-8"), filename=str(pyi_path))
    except (SyntaxError, UnicodeDecodeError, OSError) as exc:
        violations.append(f"{pyi_path.name}: could not parse — {exc}")
        return violations

    pkg_dir = pyi_path.parent

    stub_by_submod: dict[str, set[str]] = {}
    for node in pyi_tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            names = {alias.name for alias in node.names}
            stub_by_submod.setdefault(node.module, set()).update(names)

    for submod_rel, stub_names in sorted(stub_by_submod.items()):
        parts = submod_rel.split(".")
        submod_path = pkg_dir.joinpath(*parts)

        if submod_path.is_dir():
            py_file = submod_path / "__init__.py"
        else:
            py_file = submod_path.with_suffix(".py")

        if not py_file.exists():
            continue

        try:
            submod_tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue

        submod_all = _extract_all(submod_tree)
        if submod_all is None:
            continue
        public_names = submod_all

        for name in sorted(public_names - stub_names):
            if name.startswith("_"):
                continue
            if name in _PRIVATE_REEXPORTS:
                continue
            violations.append(
                f"{pyi_path.name}: {submod_rel}.{name} defined in submodule "
                f"but missing from stub re-exports"
            )

    return violations


def check() -> list[str]:
    violations: list[str] = []
    for path in sorted(SRC_ROOT.rglob("__init__.pyi")):
        violations.extend(check_file(path))
    return violations


def main() -> int:
    violations = check()
    if violations:
        print("__init__.pyi stub symbol completeness violations:\n")
        for v in violations:
            print(f"  {v}")
        print(
            "\nEvery public symbol in a submodule referenced by __init__.pyi must appear\n"
            "as a re-export in the stub. Add: from .module import Name as Name"
        )
        return 1
    print("All __init__.pyi stubs complete: no missing symbols.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
