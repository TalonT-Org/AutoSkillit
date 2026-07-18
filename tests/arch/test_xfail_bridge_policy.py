"""Architectural guard: xfail(strict=True) bridges must cite a tracking issue.

Every ``pytest.mark.xfail(strict=True, reason=...)`` decorator — both on function
definitions and inside ``pytest.param(..., marks=...)`` — must include a ``#NNNN``
issue reference in its ``reason`` string so bridge exit conditions are trackable.
Files in ``_XFAIL_POLICY_EXEMPT_FILES`` are excluded when they have an alternative
self-governance mechanism (e.g. a companion meta-test that forces the count
monotonically non-increasing).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_TESTS_ROOT = Path(__file__).resolve().parent.parent

_XFAIL_POLICY_EXEMPT_FILES: frozenset[str] = frozenset(
    {
        "arch/test_recipe_diagram_freshness.py",  # permanent: shrink meta-test
    }
)

_EXEMPT_CAP = 2


def _is_xfail_strict_true(node: ast.AST) -> bool:
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
        if kw.arg == "strict" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
            return True
    return False


def _extract_reason(node: ast.Call) -> str | None:
    for kw in node.keywords:
        if kw.arg == "reason":
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                return kw.value.value
            if isinstance(kw.value, ast.JoinedStr):
                parts = []
                for v in kw.value.values:
                    if isinstance(v, ast.Constant) and isinstance(v.value, str):
                        parts.append(v.value)
                return "".join(parts) if parts else None
            return None
    return None


def _collect_xfail_strict_true_nodes(tree: ast.Module) -> list[ast.Call]:
    results: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and _is_xfail_strict_true(dec):
                    results.append(dec)
                elif isinstance(dec, ast.Call):
                    for arg_node in ast.walk(dec):
                        if (
                            isinstance(arg_node, ast.Call)
                            and arg_node is not dec
                            and _is_xfail_strict_true(arg_node)
                        ):
                            results.append(arg_node)
        if isinstance(node, ast.Call):
            func = node.func
            is_param = False
            if isinstance(func, ast.Attribute) and func.attr == "param":
                is_param = True
            elif isinstance(func, ast.Name) and func.id == "param":
                is_param = True
            if is_param:
                for kw in node.keywords:
                    if kw.arg == "marks":
                        for marks_node in ast.walk(kw.value):
                            if isinstance(marks_node, ast.Call) and _is_xfail_strict_true(
                                marks_node
                            ):
                                results.append(marks_node)
    return results


def test_strict_xfail_reasons_cite_tracking_issue() -> None:
    """Every xfail(strict=True) reason must contain a #NNNN issue reference."""
    violations: list[str] = []
    for py_file in sorted(_TESTS_ROOT.rglob("test_*.py")):
        rel = py_file.relative_to(_TESTS_ROOT).as_posix()
        if rel in _XFAIL_POLICY_EXEMPT_FILES:
            continue
        try:
            tree = ast.parse(py_file.read_text())
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        for xfail_node in _collect_xfail_strict_true_nodes(tree):
            reason = _extract_reason(xfail_node)
            if reason is None:
                violations.append(
                    f"{rel}:{xfail_node.lineno} — reason must be a string literal "
                    "so policy can be checked"
                )
            elif not re.search(r"#\d+", reason):
                violations.append(
                    f"{rel}:{xfail_node.lineno} — reason={reason!r} does not cite "
                    "a tracking issue; add #NNNN or resolve the defect and remove the xfail"
                )
    assert not violations, "xfail(strict=True) without tracking issue reference:\n" + "\n".join(
        f"  {v}" for v in violations
    )


def test_exemption_entries_have_rationale_comments() -> None:
    """Every _XFAIL_POLICY_EXEMPT_FILES entry must have a rationale comment."""
    src = Path(__file__).read_text()
    tree = ast.parse(src)
    lines = src.splitlines()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "_XFAIL_POLICY_EXEMPT_FILES":
                if isinstance(node.value, ast.Call) and node.value.args:
                    set_node = node.value.args[0]
                    if isinstance(set_node, ast.Set):
                        for elt in set_node.elts:
                            if isinstance(elt, ast.Constant):
                                line = lines[elt.lineno - 1]
                                assert re.search(r"#\s*(permanent|tracking):", line), (
                                    f"Exempt entry {elt.value!r} on line {elt.lineno} "
                                    "must have a '# permanent:' or '# tracking:' comment"
                                )


def test_exemption_registry_size_cap() -> None:
    """Exemption registry must not exceed _EXEMPT_CAP."""
    assert len(_XFAIL_POLICY_EXEMPT_FILES) <= _EXEMPT_CAP, (
        f"_XFAIL_POLICY_EXEMPT_FILES has {len(_XFAIL_POLICY_EXEMPT_FILES)} entries "
        f"but cap is {_EXEMPT_CAP}. Review whether all entries are still needed."
    )


def test_exempt_files_exist() -> None:
    """Every exemption registry entry must resolve to an existing file."""
    missing = [
        entry
        for entry in sorted(_XFAIL_POLICY_EXEMPT_FILES)
        if not (_TESTS_ROOT / entry).is_file()
    ]
    assert not missing, f"Exempt entries point to non-existent files: {missing}"
