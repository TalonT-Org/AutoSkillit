"""Structural guard: src/ must use `import regex as re`, not bare `import re`.

Hooks (src/autoskillit/hooks/) and hook_registry.py are stdlib-only and exempt.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_EXEMPT_PATHS = {"hooks", "hook_registry.py"}


def _is_exempt(path: Path, pkg_root: Path) -> bool:
    rel = path.relative_to(pkg_root)
    parts = rel.parts
    return parts[0] in _EXEMPT_PATHS or rel.name in _EXEMPT_PATHS


def test_src_uses_regex_not_bare_re() -> None:
    """All non-hook src/ modules must use `import regex as re`, not bare `import re`."""
    from autoskillit.core.paths import pkg_root

    src_root = pkg_root()
    violations: list[str] = []

    for py_file in src_root.rglob("*.py"):
        if _is_exempt(py_file, src_root):
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Import):
                continue
            for alias in node.names:
                if alias.name == "re":
                    asname_suffix = f" as {alias.asname}" if alias.asname else ""
                    rel = py_file.relative_to(src_root.parent.parent)
                    violations.append(f"  {rel}:{node.lineno}  →  import re{asname_suffix}")

    assert not violations, (
        "Found 'import re' (stdlib) in src/ (outside hooks/). "
        "Use 'import regex as re' (or 'import regex as <alias>') instead:\n"
        + "\n".join(violations)
    )
