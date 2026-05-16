"""skip_when expression evaluation for conditional dispatch skipping."""

from __future__ import annotations

import re
from typing import Final

from autoskillit.core import FleetErrorCode

_SKIP_CAMPAIGN_REF_RE: Final = re.compile(r"\$\{\{\s*campaign\.(\w+)\s*\}\}")
_SKIP_INPUTS_REF_RE: Final = re.compile(r"\$\{\{\s*inputs\.(\w+)\s*\}\}")


def _strip_quotes(s: str) -> str:
    if (s.startswith("'") and s.endswith("'")) or (s.startswith('"') and s.endswith('"')):
        return s[1:-1]
    return s


def evaluate_skip_when(
    skip_when: str,
    accumulated_captures: dict[str, str],
    ingredients: dict[str, str] | None = None,
) -> tuple[FleetErrorCode | None, str | None, bool]:
    """Evaluate a skip_when expression against campaign captures and ingredients.

    Returns ``(error_code, error_message, should_skip)``.
    ``error_code`` is ``None`` when evaluation succeeds; ``should_skip`` is only
    meaningful in that case.
    """
    ingredients = ingredients or {}

    missing_campaign_refs = [
        ref for ref in _SKIP_CAMPAIGN_REF_RE.findall(skip_when) if ref not in accumulated_captures
    ]
    if missing_campaign_refs:
        return (
            FleetErrorCode.FLEET_UNKNOWN_INGREDIENT,
            f"skip_when references campaign captures that have not been produced "
            f"by any prior dispatch: {missing_campaign_refs!r}. "
            f"Available captures: {sorted(accumulated_captures)}",
            False,
        )

    missing_inputs_refs = [
        ref for ref in _SKIP_INPUTS_REF_RE.findall(skip_when) if ref not in ingredients
    ]
    if missing_inputs_refs:
        return (
            FleetErrorCode.FLEET_RECIPE_INVALID,
            f"skip_when references ingredient keys that were not passed: "
            f"{missing_inputs_refs!r}. Available ingredients: {sorted(ingredients)}",
            False,
        )

    resolved = _SKIP_CAMPAIGN_REF_RE.sub(lambda m: accumulated_captures[m.group(1)], skip_when)
    resolved = _SKIP_INPUTS_REF_RE.sub(lambda m: ingredients[m.group(1)], resolved).strip()

    if not resolved:
        return (
            FleetErrorCode.FLEET_RECIPE_INVALID,
            "skip_when resolved to an empty expression after substitution",
            False,
        )

    op_count = resolved.count(" == ") + resolved.count(" != ")
    if op_count != 1:
        return (
            FleetErrorCode.FLEET_RECIPE_INVALID,
            f"skip_when resolved to a malformed expression: {resolved!r}. "
            f"Expected exactly one '==' or '!=' comparison operator.",
            False,
        )

    should_skip = False
    if " == " in resolved:
        lhs, rhs = resolved.split(" == ", 1)
        should_skip = _strip_quotes(lhs.strip()) == _strip_quotes(rhs.strip())
    elif " != " in resolved:
        lhs, rhs = resolved.split(" != ", 1)
        should_skip = _strip_quotes(lhs.strip()) != _strip_quotes(rhs.strip())

    return (None, None, should_skip)
