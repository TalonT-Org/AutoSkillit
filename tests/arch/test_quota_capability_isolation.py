"""AST guard: quota modules must not reference BackendCapabilities fields.

Quota provider gates must use config-string comparison (resolve_provider),
never backend capability fields. This prevents accidental coupling between
the quota system and the backend capabilities system.
"""

from __future__ import annotations

import ast

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_QUOTA_MODULES: tuple[str, ...] = (
    "execution/quota.py",
    "server/_misc.py",
    "hooks/guards/quota_guard.py",
    "hooks/quota_post_hook.py",
)

_FORBIDDEN_NAMES: frozenset[str] = frozenset(
    {
        "BackendCapabilities",
        "anthropic_provider_capable",
        "CLAUDE_CODE_CAPABILITIES",
    }
)


class _CapabilityRefVisitor(ast.NodeVisitor):
    """Find references to BackendCapabilities or its fields in quota modules."""

    def __init__(self) -> None:
        self.violations: list[tuple[int, str]] = []

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in _FORBIDDEN_NAMES:
            self.violations.append((node.lineno, node.id))
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in _FORBIDDEN_NAMES:
            self.violations.append((node.lineno, node.attr))
        if node.attr == "capabilities":
            self.violations.append((node.lineno, ".capabilities"))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.names:
            for alias in node.names:
                if alias.name in _FORBIDDEN_NAMES:
                    self.violations.append((node.lineno, alias.name))
        self.generic_visit(node)


def test_quota_modules_do_not_reference_backend_capabilities() -> None:
    """Quota code paths must gate on provider string, never BackendCapabilities fields."""
    from autoskillit.core import paths

    src_root = paths.pkg_root()
    violations: list[str] = []

    for relpath in _QUOTA_MODULES:
        filepath = src_root / relpath
        if not filepath.exists():
            continue
        tree = ast.parse(filepath.read_text())
        visitor = _CapabilityRefVisitor()
        visitor.visit(tree)
        for lineno, name in visitor.violations:
            violations.append(f"{relpath}:{lineno}: references {name}")

    assert not violations, (
        "Quota modules must not reference BackendCapabilities or its fields "
        "(use resolve_provider config string instead):\n"
        + "\n".join(f"  {v}" for v in sorted(violations))
    )
