#!/usr/bin/env python3
"""Validate __init__.pyi stub files contain only relative re-export imports.

AST-scans __init__.pyi files and rejects any top-level statement that is not
an ast.ImportFrom with level == 1 (relative) and alias.asname == alias.name
(explicit re-export form).

lazy_loader.attach_stub() uses _StubVisitor which only implements visit_ImportFrom.
Any other AST node type is silently ignored — the symbol is absent from __all__
and invisible at runtime.

Exit 0 if all stubs are valid. Exit 1 with details on violations.
"""

import ast
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "autoskillit"


def check_file(path: Path) -> list[str]:
    violations = []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError) as exc:
        violations.append(f"{path.name}: could not parse — {exc}")
        return violations
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            if node.level != 1:
                violations.append(
                    f"{path.name}:{node.lineno}: import level {node.level} "
                    f"(must be 1: from .X import Y as Y)"
                )
            for alias in node.names:
                if alias.asname != alias.name:
                    violations.append(
                        f"{path.name}:{node.lineno}: '{alias.name}' missing 'as' form "
                        f"(must be: from .{node.module} import {alias.name} as {alias.name})"
                    )
        else:
            violations.append(
                f"{path.name}:{node.lineno}: {type(node).__name__} not allowed. "
                f"Only 'from .module import Name as Name' lines are valid. "
                f"lazy_loader.attach_stub() silently ignores {type(node).__name__} statements. "
                f"Add: from .module import {_guess_name(node)} as {_guess_name(node)}"
            )
    return violations


def _guess_name(node: ast.stmt) -> str:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name
    return "<Name>"


def check() -> list[str]:
    violations = []
    for path in sorted(SRC_ROOT.rglob("__init__.pyi")):
        violations.extend(check_file(path))
    return violations


def main() -> int:
    violations = check()
    if violations:
        print("__init__.pyi stub format violations:\n")
        for v in violations:
            print(f"  {v}")
        print(
            "\n__init__.pyi stubs used with lazy_loader.attach_stub() must contain ONLY:\n"
            "  from .module import Name as Name\n\n"
            "def/class/assign statements are silently ignored by lazy_loader._StubVisitor."
        )
        return 1
    print("All __init__.pyi stubs valid: re-export only.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
