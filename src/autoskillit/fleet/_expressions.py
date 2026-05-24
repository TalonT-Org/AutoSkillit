"""Campaign expression evaluation."""

from __future__ import annotations

import regex as re

from autoskillit.core import FleetErrorCode

_CAMPAIGN_REF_RE = re.compile(r"\$\{\{\s*campaign\.(\w+)\s*\}\}")
_INPUTS_REF_RE = re.compile(r"\$\{\{\s*inputs\.(\w+)\s*\}\}")


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
        ref for ref in _CAMPAIGN_REF_RE.findall(skip_when) if ref not in accumulated_captures
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
        ref for ref in _INPUTS_REF_RE.findall(skip_when) if ref not in ingredients
    ]
    if missing_inputs_refs:
        return (
            FleetErrorCode.FLEET_RECIPE_INVALID,
            f"skip_when references ingredient keys that were not passed: "
            f"{missing_inputs_refs!r}. Available ingredients: {sorted(ingredients)}",
            False,
        )

    resolved = _CAMPAIGN_REF_RE.sub(lambda m: accumulated_captures[m.group(1)], skip_when)
    resolved = _INPUTS_REF_RE.sub(lambda m: ingredients[m.group(1)], resolved).strip()

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


def _interpolate_campaign_refs(
    ingredients: dict[str, str],
    captured: dict[str, str],
) -> dict[str, str]:
    """Resolve ``${{ campaign.key }}`` references in ingredient values.

    Raises ValueError if a campaign reference cannot be resolved or resolves to
    an empty string (which may indicate an invalid capture from a prior dispatch).
    Non-campaign values are returned unchanged.
    """
    out: dict[str, str] = {}
    for k, v in ingredients.items():

        def _replace(m: re.Match, _k: str = k) -> str:
            ref = m.group(1)
            if ref not in captured:
                raise ValueError(
                    f"Ingredient '{_k}' references ${{{{ campaign.{ref} }}}} "
                    f"but '{ref}' has not been captured by any prior dispatch. "
                    f"Available: {sorted(captured)}"
                )
            resolved = captured[ref]
            if resolved == "":
                raise ValueError(
                    f"Ingredient '{_k}' campaign ref '{ref}' resolved to empty string — "
                    f"the capturing dispatch may have emitted an empty value"
                )
            return resolved

        out[k] = _CAMPAIGN_REF_RE.sub(_replace, v)
    return out
