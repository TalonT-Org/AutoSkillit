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
import re as _re
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


_DISPATCH_READY_TEST = (
    Path(__file__).resolve().parents[1] / "recipe" / "test_bundled_recipes_dispatch_ready.py"
)

_ALLOWLIST_CAP = 4


def _collect_allowlist_rule_names() -> set[str]:
    """Extract all rule-name string values from _KNOWN_NON_CONFORMING_RULES."""
    tree = ast.parse(_DISPATCH_READY_TEST.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            target = node.target
            value = node.value
        elif isinstance(node, ast.Assign):
            target = node.targets[0] if node.targets else None
            value = node.value
        else:
            continue
        if not isinstance(target, ast.Name) or target.id != "_KNOWN_NON_CONFORMING_RULES":
            continue
        if not isinstance(value, ast.Dict):
            continue
        for v in value.values:
            if isinstance(v, ast.Set):
                for elt in v.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        names.add(elt.value)
            elif isinstance(v, ast.Dict):
                for inner_v in v.values:
                    if isinstance(inner_v, ast.Set):
                        for elt in inner_v.elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                names.add(elt.value)
    return names


def test_dispatch_readiness_allowlist_size_cap() -> None:
    """Dispatch-readiness allowlists must not grow beyond current size.

    If this test fails, a new exemption was added. This forces the developer
    to either: (a) fix the recipe, (b) revert the severity promotion, or
    (c) explicitly increase the cap with justification.
    """
    rule_names = _collect_allowlist_rule_names()
    assert len(rule_names) <= _ALLOWLIST_CAP, (
        f"Dispatch-readiness allowlist has {len(rule_names)} entries "
        f"(cap: {_ALLOWLIST_CAP}): {sorted(rule_names)}. "
        "Fix the recipe or revert the severity promotion instead of adding exemptions."
    )


def test_known_non_conforming_entries_have_tracking_comments() -> None:
    """Every _KNOWN_NON_CONFORMING_RULES entry must reference a tracking issue."""
    source = _DISPATCH_READY_TEST.read_text()
    lines = source.splitlines()
    tree = ast.parse(source)

    missing: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            target = node.target
            value = node.value
        elif isinstance(node, ast.Assign):
            target = node.targets[0] if node.targets else None
            value = node.value
        else:
            continue
        if not isinstance(target, ast.Name) or target.id != "_KNOWN_NON_CONFORMING_RULES":
            continue
        if not isinstance(value, ast.Dict):
            continue
        for key in value.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                line = lines[key.lineno - 1]
                if not _re.search(r"#\s*tracking:\s*#\d+", line):
                    missing.append(f"{key.value!r} (line {key.lineno})")

    assert not missing, (
        "Entries in _KNOWN_NON_CONFORMING_RULES missing tracking comments: "
        + ", ".join(missing)
        + ". Add '# tracking: #NNNN' with the relevant GitHub issue number."
    )


def _decorator_rule_name(dec: ast.Call) -> str | None:
    """Extract the ``name=`` keyword string value from a decorator call."""
    for kw in dec.keywords:
        if (
            kw.arg == "name"
            and isinstance(kw.value, ast.Constant)
            and isinstance(kw.value.value, str)
        ):
            return kw.value.value
    return None


def _collect_error_severity_rules() -> set[str]:
    """Extract rule names whose @semantic_rule decorator has severity=Severity.ERROR."""
    error_rules: set[str] = set()
    for path in _iter_rule_modules():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            dec = _find_semantic_rule_decorator(node)
            if dec is None:
                continue
            sev = _decorator_severity(dec)
            if sev is not None and "ERROR" in sev:
                rule_name = _decorator_rule_name(dec)
                if rule_name is not None:
                    error_rules.add(rule_name)
    return error_rules


@pytest.mark.xfail(
    strict=True,
    reason=(
        "2 ERROR-severity rules still in allowlist — fix agent-eval and skill-eval "
        "before removing xfail"
    ),
)
def test_error_severity_rules_have_no_dispatch_ready_exemptions() -> None:
    """Every ERROR-severity rule must have zero entries in the dispatch-ready allowlist.

    This enforces the policy: fix all recipes FIRST, then promote to ERROR.
    A severity promotion paired with an allowlist entry is a contradiction —
    the rule claims to be blocking but the test infrastructure lets it pass.
    """
    error_rules = _collect_error_severity_rules()
    allowlist_rules = _collect_allowlist_rule_names()
    overlap = error_rules & allowlist_rules
    assert not overlap, (
        f"ERROR-severity rules appear in dispatch-ready allowlist: {sorted(overlap)}. "
        "Fix all bundled recipes for these rules BEFORE promoting to ERROR severity."
    )


def _is_xfail_strict_false(node: ast.AST) -> bool:
    """Check if an AST node is pytest.mark.xfail(strict=False) or pytest.xfail(strict=False)."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    is_xfail = False
    if isinstance(func, ast.Name) and func.id == "xfail":
        is_xfail = True
    elif isinstance(func, ast.Attribute) and func.attr == "xfail":
        is_xfail = True
    if not is_xfail:
        return False
    for kw in node.keywords:
        if kw.arg == "strict" and isinstance(kw.value, ast.Constant) and kw.value.value is False:
            return True
    return False


def test_no_strict_false_xfail_in_recipe_and_contract_tests() -> None:
    """Recipe and contract tests must not use xfail(strict=False).

    strict=False makes a test incapable of ever causing CI failure,
    regardless of whether the underlying condition is met or not.
    Use strict=True or remove the xfail entirely.
    """
    tests_dir = Path(__file__).resolve().parents[1]
    excluded = tests_dir / "hooks" / "test_write_guard.py"
    violations: list[str] = []
    for subdir in ("recipe", "contracts"):
        scan_dir = tests_dir / subdir
        for py_file in sorted(scan_dir.rglob("*.py")):
            if py_file.resolve() == excluded.resolve():
                continue
            if py_file.name.startswith("test_"):
                tree = ast.parse(py_file.read_text())
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        for dec in node.decorator_list:
                            if _is_xfail_strict_false(dec):
                                rel = py_file.relative_to(tests_dir.parent)
                                violations.append(f"{rel}:{dec.lineno} — {node.name}")
    assert not violations, (
        "xfail(strict=False) found in recipe/contract tests:\n"
        + "\n".join(violations)
        + "\nUse strict=True or remove the xfail entirely."
    )
