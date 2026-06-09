"""Structural guards for rule severity consistency.

1. Every RuleFinding emitted by a rule must use the same severity as the
   RuleDef registered by the @semantic_rule decorator.  compute_recipe_validity()
   reads RuleFinding.severity, not RuleDef.severity.  If these diverge, the
   decorator severity becomes misleading — a rule declared WARNING that emits
   ERROR findings will silently block dispatch.

2. No rule function (decorated with @semantic_rule or @block_rule) may
   construct RuleFinding(...) directly.  All findings must go through
   make_finding() / make_block_finding() to enforce single-source-of-truth
   severity from the decorator.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_SRC = Path(__file__).resolve().parents[2] / "src" / "autoskillit"
_RULES_ROOT = _SRC / "recipe" / "rules"


def _iter_rule_modules() -> list[Path]:
    """Yield every rules_*.py file under src/autoskillit/recipe/rules/."""
    return sorted(_RULES_ROOT.rglob("rules_*.py"))


def _find_semantic_rule_decorator(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
) -> ast.Call | None:
    """Return the @semantic_rule decorator call on *fn*, or None."""
    for dec in fn.decorator_list:
        if isinstance(dec, ast.Call) and (
            (isinstance(dec.func, ast.Name) and dec.func.id == "semantic_rule")
            or (isinstance(dec.func, ast.Attribute) and dec.func.attr == "semantic_rule")
        ):
            return dec
    return None


def _decorator_severity(dec: ast.Call) -> str | None:
    """Extract the ``severity=`` keyword value's string representation."""
    for kw in dec.keywords:
        if kw.arg == "severity":
            if isinstance(kw.value, ast.Attribute):
                return ast.dump(kw.value)
            if isinstance(kw.value, ast.Name):
                return kw.value.id
    return None


def _find_block_rule_decorator(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
) -> ast.Call | None:
    """Return the @block_rule decorator call on *fn*, or None."""
    for dec in fn.decorator_list:
        if isinstance(dec, ast.Call) and (
            (isinstance(dec.func, ast.Name) and dec.func.id == "block_rule")
            or (isinstance(dec.func, ast.Attribute) and dec.func.attr == "block_rule")
        ):
            return dec
    return None


def test_no_direct_rule_finding_construction_in_rules() -> None:
    """Rule functions must use make_finding()/make_block_finding() — never construct directly.

    Direct construction allows severity divergence between RuleDef and RuleFinding.
    make_finding()/make_block_finding() enforce single-source-of-truth severity from the decorator.
    """
    violations: list[str] = []
    for path in _iter_rule_modules():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            is_rule = _find_semantic_rule_decorator(node) is not None
            is_block = _find_block_rule_decorator(node) is not None
            if not is_rule and not is_block:
                continue
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    func = child.func
                    name = None
                    if isinstance(func, ast.Name):
                        name = func.id
                    elif isinstance(func, ast.Attribute):
                        name = func.attr
                    if name == "RuleFinding":
                        rel = path.relative_to(_SRC.parent)
                        violations.append(f"{rel}:{child.lineno} — {node.name}")
    assert not violations, (
        "Rule functions must use make_finding()/make_block_finding(), not direct "
        "RuleFinding() construction:\n" + "\n".join(violations)
    )


def test_rule_findings_match_rule_def_severity() -> None:
    """Every RuleFinding emitted by a rule must use the rule's registered severity.

    This AST check inspects rule bodies for any RuleFinding(severity=...) call
    and verifies the severity value matches the @semantic_rule decorator's
    severity= argument in the same function scope.  After the make_finding()
    migration this scan should find no RuleFinding(severity=...) calls at all
    in semantic-rule bodies; any remaining direct construction is a violation.
    """
    violations: list[str] = []
    for path in _iter_rule_modules():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            dec = _find_semantic_rule_decorator(node)
            if dec is None:
                continue
            expected = _decorator_severity(dec)
            for child in ast.walk(node):
                if not isinstance(child, ast.Call):
                    continue
                func = child.func
                name = None
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                if name != "RuleFinding":
                    continue
                actual: str | None = None
                for kw in child.keywords:
                    if kw.arg == "severity":
                        if isinstance(kw.value, ast.Attribute):
                            actual = ast.dump(kw.value)
                        elif isinstance(kw.value, ast.Name):
                            actual = kw.value.id
                rel = path.relative_to(_SRC.parent)
                if actual is None:
                    violations.append(
                        f"{rel}:{child.lineno} — missing severity= (expected {expected})"
                    )
                elif expected is not None and actual != expected:
                    violations.append(
                        f"{rel}:{child.lineno} — severity {actual} != decorator {expected}"
                    )
    assert not violations, (
        "RuleFinding severity must match the @semantic_rule decorator severity "
        "or go through make_finding():\n" + "\n".join(violations)
    )
