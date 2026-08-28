"""Override value coercion and type-validation gate for typed recipe ingredients."""

from __future__ import annotations

import json
from typing import Any, Final

__all__ = [
    "OverrideCoercionError",
    "_validate_override_types",
    "coerce_override_value",
]

_ALLOWED_BOOLEAN_STRINGS: Final[frozenset[str]] = frozenset(
    {"true", "false", "1", "0", "yes", "no"}
)


class OverrideCoercionError(ValueError):
    """Caller override cannot be coerced to the declared ingredient type."""


def coerce_override_value(value: object, declared_type: str | None) -> str:
    """Validate ``value`` can be coerced to ``declared_type``.

    Returns ``value`` on success. When ``declared_type`` is ``None``, returns
    ``value`` without type validation (the caller passes anything as a string).
    Raises :class:`OverrideCoercionError` on coercion failure or non-string input.

    Non-string inputs are rejected explicitly rather than silently stringified
    — callers see a clear error instead of ambiguous coerced behavior. The
    final ``unknown declared type`` raise is defense-in-depth: ``__post_init__``
    rejects unknown types at parse time, but ``RecipeIngredient`` instances
    constructed at runtime (e.g., in unit tests, in recipe analysis code) can
    bypass parse-time validation, so the branch remains reachable.
    """
    # Reject non-string inputs explicitly (do not coerce silently).
    if not isinstance(value, str):
        raise OverrideCoercionError(f"override value must be a string, got {type(value).__name__}")
    if declared_type is None:
        return value
    if declared_type == "string":
        return value
    if declared_type == "optional_string":
        return value  # any string including empty is valid
    if declared_type == "path":
        if not value:
            raise OverrideCoercionError(f"path value must be non-empty, got {value!r}")
        return value
    if declared_type == "absolute_path":
        if not value:
            raise OverrideCoercionError(f"absolute_path value must be non-empty, got {value!r}")
        return value
    if declared_type == "worktree_relative_path":
        return value  # empty allowed (worktree root may be project root)
    if declared_type == "integer":
        try:
            int(value)
        except ValueError as e:
            raise OverrideCoercionError(f"integer value must parse as int, got {value!r}") from e
        return value
    if declared_type == "boolean":
        if value.lower() not in _ALLOWED_BOOLEAN_STRINGS:
            raise OverrideCoercionError(
                f"boolean value must be one of {sorted(_ALLOWED_BOOLEAN_STRINGS)} "
                f"(case-insensitive), got {value!r}"
            )
        return value
    if declared_type == "list":
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as e:
            raise OverrideCoercionError(
                f"list value must be valid JSON array, got {value!r}"
            ) from e
        if not isinstance(parsed, list):
            raise OverrideCoercionError(
                f"list value must decode to a JSON array, got {type(parsed).__name__}"
            )
        return value
    if declared_type == "dict":
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as e:
            raise OverrideCoercionError(
                f"dict value must be valid JSON object, got {value!r}"
            ) from e
        if not isinstance(parsed, dict):
            raise OverrideCoercionError(
                f"dict value must decode to a JSON object, got {type(parsed).__name__}"
            )
        return value
    # Defense in depth — __post_init__ rejects unknown types at parse time.
    raise OverrideCoercionError(f"unknown declared type {declared_type!r}")


_RecipeLike = Any  # any object exposing ``ingredients.get(key)`` returning a ``.type`` attribute


def _validate_override_types(
    overrides: dict[str, str] | None,
    recipe_obj: _RecipeLike,
) -> str | None:
    """Tier-2 type gate: enforce that caller-supplied override values are
    coercible to the recipe's declared ingredient types.

    Returns ``None`` on success; returns a JSON error envelope string on
    failure. Shared by ``open_kitchen`` and ``load_recipe`` to guarantee
    identical rejection behavior across both surfaces.

    The unknown-key check is delegated to :func:`_check_override_keys` — this
    helper only enforces type coercion for keys present in the recipe's
    declared ingredients. Caller is expected to invoke this helper after the
    recipe object is loaded but before any side effect (hook config writes,
    ``tool_ctx`` mutations).
    """
    if not overrides:
        return None
    for key, value in overrides.items():
        ing = recipe_obj.ingredients.get(key)
        if ing is None:
            continue  # unknown-key check handled by _check_override_keys
        ing_type = getattr(ing, "type", None)
        # Treat anything that isn't a string as untyped — covers ``type=None``
        # (declared untyped) and ``MagicMock()`` auto-spec attrs used in tests,
        # which would otherwise trigger the unknown-type branch below for
        # ingredients that production code never typed.
        if not isinstance(ing_type, str):
            ing_type = None
        try:
            coerce_override_value(value, ing_type)
        except OverrideCoercionError as e:
            return json.dumps(
                {
                    "success": False,
                    "error": f"Override for {key!r} failed type validation: {e}",
                    "stage": "ingredient_type_validation",
                    "retriable": False,
                    "user_visible_message": (
                        f"Override for ingredient {key!r} cannot be coerced to "
                        f"declared type {ing_type!r}: {e}. Adjust the override "
                        f"value to match the declared type."
                    ),
                }
            )
    return None
