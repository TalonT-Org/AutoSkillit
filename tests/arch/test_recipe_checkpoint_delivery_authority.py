"""Keep recipe checkpoint carrier preparation and attachment in one module."""

from __future__ import annotations

import ast

import pytest

from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_DELIVERY_AUTHORITY_MODULE = SRC_ROOT / "server" / "recipe" / "_recipe_segment_delivery.py"
_DELIVERY_NAMES = frozenset({"prepare_recipe_segment_delivery", "attach_recipe_segment"})


def _referenced_delivery_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    if isinstance(node, ast.Name):
        names.add(node.id)
    elif isinstance(node, ast.Attribute):
        names.add(node.attr)
    elif isinstance(node, ast.ImportFrom | ast.Import):
        for alias in node.names:
            names.add(alias.name.rsplit(".", maxsplit=1)[-1])
            if alias.asname is not None:
                names.add(alias.asname)
    return names


def _scan_for_alternate_delivery_authorities() -> list[str]:
    violations: list[str] = []
    for source_path in sorted(SRC_ROOT.rglob("*.py")):
        if source_path == _DELIVERY_AUTHORITY_MODULE:
            continue
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(source_path))
        relative_path = source_path.relative_to(SRC_ROOT)
        for node in ast.walk(tree):
            matched = sorted(_referenced_delivery_names(node) & _DELIVERY_NAMES)
            violations.extend(f"{relative_path}:{node.lineno} {name}" for name in matched)
    return violations


def test_only_the_delivery_module_prepares_or_attaches_carriers() -> None:
    violations = _scan_for_alternate_delivery_authorities()

    assert not violations, (
        "Recipe checkpoint carrier preparation and attachment must stay in "
        "server/recipe/_recipe_segment_delivery.py:\n" + "\n".join(violations)
    )
