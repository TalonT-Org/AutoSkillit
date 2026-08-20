"""Contract: every exploration failure "code" literal must be enum-registered.

AST-scans tools_exploration.py for every dict-literal `"code": <value>` pair
and every `_failure(...)` call argument. A bare string literal that is not a
registered ExplorationFailureCode value fails this test — the opaque
`except Exception: return _failure("exploration_provisioning_failed")`
catch-all this contract replaces (#4684) cannot silently reappear as a
hand-typed literal that bypasses the registry.

References that already flow through the enum (``ExplorationFailureCode.X``
or a module constant assigned from it) resolve as AST Attribute/Name nodes,
not Constant nodes, and are skipped — they are already typed by
construction: a drifted enum member would raise AttributeError at import
time, not silently produce an unregistered string.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from autoskillit.core import EXPLORATION_FAILURE_CODES

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_SRC = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "autoskillit"
    / "server"
    / "tools"
    / "tools_exploration.py"
)


def _code_dict_literal_values(tree: ast.Module) -> list[ast.expr]:
    """Return the value expression of every dict-literal `"code": <value>` pair."""
    found: list[ast.expr] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values, strict=True):
            if isinstance(key, ast.Constant) and key.value == "code" and value is not None:
                found.append(value)
    return found


def _failure_call_args(tree: ast.Module) -> list[ast.expr]:
    """Return the sole positional argument of every `_failure(...)` call."""
    found: list[ast.expr] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_failure"
            and len(node.args) == 1
        ):
            found.append(node.args[0])
    return found


def _literal_code_value(expr: ast.expr) -> str | None:
    """Return the bare string literal, or None if the expression isn't one."""
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    return None


def test_every_code_literal_is_a_registered_exploration_failure_code() -> None:
    tree = ast.parse(_SRC.read_text(encoding="utf-8"), filename=str(_SRC))
    candidates = [*_code_dict_literal_values(tree), *_failure_call_args(tree)]
    violations = [
        f"line {expr.lineno}: {literal!r} is not a registered ExplorationFailureCode"
        for expr in candidates
        if (literal := _literal_code_value(expr)) is not None
        if literal not in EXPLORATION_FAILURE_CODES
    ]
    assert not violations, (
        "Unregistered exploration failure code literal(s) in tools_exploration.py — "
        f"add the code to ExplorationFailureCode in _type_enums.py first: {violations}"
    )
