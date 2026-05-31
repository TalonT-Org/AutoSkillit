"""Discover architectural constraint tests by module docstring convention.

Tests that enforce project-wide invariants follow a docstring prefix convention:
their module docstring starts with one of ARCH_CONSTRAINT_PREFIXES. This module
provides a discovery function used by staleness guards to validate catalog
completeness without hardcoded file lists.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS_ROOT = Path(__file__).parent

ARCH_CONSTRAINT_PREFIXES: tuple[str, ...] = (
    "Architectural invariant",
    "Architectural guard",
    "Architectural enforcement",
    "Structural guard",
    "Structural enforcement",
    "AST guard",
)


def discover_constraint_tests() -> dict[str, Path]:
    """Return {filename: path} for all test files whose module docstring
    starts with a recognized constraint prefix.

    Uses basename as key for catalog lookup compatibility.  If two files
    share a basename, the one with the longer (deeper) relative path wins
    so the collision is deterministic and logged via ValueError.
    """
    results: dict[str, Path] = {}
    for test_file in sorted(TESTS_ROOT.rglob("test_*.py")):
        try:
            tree = ast.parse(test_file.read_text())
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        docstring = ast.get_docstring(tree)
        if docstring and docstring.startswith(ARCH_CONSTRAINT_PREFIXES):
            name = test_file.name
            if name in results and results[name] != test_file:
                raise ValueError(
                    f"Basename collision: {name} found at both "
                    f"{results[name].relative_to(TESTS_ROOT)} and "
                    f"{test_file.relative_to(TESTS_ROOT)}"
                )
            results[name] = test_file
    return results
