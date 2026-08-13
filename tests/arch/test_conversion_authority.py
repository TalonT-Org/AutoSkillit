"""Arch guard: no module outside core/ may multiply/divide a token-limit value by
a numeric bytes-per-token literal — all conversions route through core policies.
"""

from __future__ import annotations

import ast

import pytest

from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_TOKEN_LIMIT_PATTERNS = {"token_limit", "_tokens", "token_count", "result_limit"}


def _is_token_limit_name(name: str) -> bool:
    name_lower = name.lower()
    return any(pat in name_lower for pat in _TOKEN_LIMIT_PATTERNS)


def _scan_for_bare_conversions(
    *,
    exclude_dirs: tuple[str, ...] = ("core", "hooks"),
) -> list[str]:
    """Find BinOp nodes multiplying token-limit names by numeric constants."""
    violations: list[str] = []
    for py_file in SRC_ROOT.rglob("*.py"):
        rel = py_file.relative_to(SRC_ROOT)
        if any(part in exclude_dirs for part in rel.parts):
            continue
        try:
            tree = ast.parse(py_file.read_text(), filename=str(py_file))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.BinOp):
                continue
            if not isinstance(node.op, ast.Mult | ast.FloorDiv | ast.Div):
                continue
            # Check if one operand is a name matching token-limit patterns
            # and the other is a numeric constant
            left_is_name = isinstance(node.left, ast.Name) and _is_token_limit_name(node.left.id)
            right_is_name = isinstance(node.right, ast.Name) and _is_token_limit_name(
                node.right.id
            )
            left_is_num = isinstance(node.left, ast.Constant) and isinstance(
                node.left.value, int | float
            )
            right_is_num = isinstance(node.right, ast.Constant) and isinstance(
                node.right.value, int | float
            )
            if (left_is_name and right_is_num) or (right_is_name and left_is_num):
                violations.append(f"{rel}:{node.lineno}")
    return violations


def test_no_bare_token_byte_conversion_outside_core() -> None:
    """Token-limit × numeric-constant conversions must use core policies."""
    violations = _scan_for_bare_conversions()
    assert not violations, (
        "Bare token-limit × numeric-constant conversions found outside core/:\n"
        + "\n".join(f"  {v}" for v in violations)
        + "\nRoute conversions through core/types/_type_dimensions.py policies."
    )
