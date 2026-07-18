"""Contract-field parser and outcome invariant evaluator.

Extracts declared output fields from session result text as ``KEY = value``
lines, typed per the contract's output declarations. Evaluates outcome
invariants and success qualifiers against parsed fields.
"""

from __future__ import annotations

import operator
import re
from typing import Any

from autoskillit.core import get_logger
from autoskillit.recipe._contracts_types import (
    OutcomeInvariantEntry,
    SkillContract,
    SuccessQualifierEntry,
)

logger = get_logger(__name__)

_FIELD_RE = re.compile(r"^(\w+)\s*=\s*(.+)$", re.MULTILINE)

_OPS: dict[str, Any] = {
    ">": operator.gt,
    ">=": operator.ge,
    "==": operator.eq,
    "!=": operator.ne,
    "<=": operator.le,
    "<": operator.lt,
}

_EXPR_RE = re.compile(r"^(\w+)\s*(>=|<=|!=|==|>|<)\s*(\d+)$")


def parse_outcome_fields(
    result_text: str,
    contract: SkillContract,
) -> dict[str, int | str]:
    """Extract declared output fields from session result text.

    Only fields declared in the contract's ``outputs`` are extracted.
    Integer-typed fields are parsed to int; malformed integer values are
    recorded as the raw string (the invariant evaluator treats missing/
    malformed fields as violations when referenced by a ``require``).
    """
    declared = {o.name: o.type for o in contract.outputs}
    parsed: dict[str, int | str] = {}
    for match in _FIELD_RE.finditer(result_text):
        name, raw_value = match.group(1), match.group(2).strip()
        if name not in declared:
            continue
        if declared[name] == "integer":
            try:
                parsed[name] = int(raw_value)
            except ValueError:
                parsed[name] = raw_value
        else:
            parsed[name] = raw_value
    return parsed


def _eval_expr(expr: str, fields: dict[str, int | str]) -> bool | None:
    """Evaluate a simple comparison expression against parsed fields.

    Returns True/False on successful evaluation, None if the referenced
    field is missing or non-numeric.
    """
    m = _EXPR_RE.match(expr)
    if not m:
        return None
    field_name, op_str, literal = m.group(1), m.group(2), int(m.group(3))
    value = fields.get(field_name)
    if not isinstance(value, int):
        return None
    return _OPS[op_str](value, literal)


def _eval_compound_expr(expr: str, fields: dict[str, int | str]) -> bool | None:
    """Evaluate a compound expression with ``and`` connectives.

    Each sub-expression is a simple comparison. All must be true for the
    compound to be true. Returns None if any sub-expression references a
    missing/non-numeric field.
    """
    parts = [p.strip() for p in expr.split(" and ")]
    results = [_eval_expr(p, fields) for p in parts]
    if any(r is None for r in results):
        return None
    return all(results)


def evaluate_outcome_invariants(
    fields: dict[str, int | str],
    invariants: list[OutcomeInvariantEntry],
) -> tuple[bool, str]:
    """Evaluate outcome invariants against parsed fields.

    Returns (violated, detail). ``violated`` is True if any invariant's
    ``when`` predicate is satisfied but its ``require`` condition is not.
    A missing ``require`` field when ``when`` is true is a violation
    (fail-closed). A missing ``when`` field causes the invariant to be
    skipped (the field was never emitted — legitimate no-PR-found exit).
    """
    for inv in invariants:
        when_result = _eval_compound_expr(inv.when, fields)
        if when_result is None or not when_result:
            continue
        require_result = _eval_compound_expr(inv.require, fields)
        if require_result is None or not require_result:
            return True, f"invariant violated: when '{inv.when}' require '{inv.require}'"
    return False, ""


def evaluate_success_qualifier(
    fields: dict[str, int | str],
    qualifiers: list[SuccessQualifierEntry],
) -> str | None:
    """Evaluate success qualifiers against parsed fields.

    Returns the qualifier string if any qualifier's ``when`` predicate
    matches, or None if no qualifier applies.
    """
    for sq in qualifiers:
        when_result = _eval_compound_expr(sq.when, fields)
        if when_result is not None and when_result:
            return sq.qualifier
    return None
