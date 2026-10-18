"""Truth dialects used by recipe guards and ingredient pruning."""

from __future__ import annotations

__all__ = [
    "DeclaredTruthUnresolved",
    "DeclaredTruthUnsupported",
    "FALSY_STRINGS",
    "is_ingredient_truthy",
    "normalize_declared_truth",
]


class DeclaredTruthUnresolved(ValueError):
    """Raised when a declared guard value was not supplied."""


class DeclaredTruthUnsupported(ValueError):
    """Raised when a declared guard value is outside the recipe dialect."""


_TRUTHY_VALUES = frozenset({"true", "1", "yes", "on"})
_FALSY_VALUES = frozenset({"false", "0", "no", "off", ""})
FALSY_STRINGS = frozenset({"false", "0", "no", ""})


def is_ingredient_truthy(value: str) -> bool:
    """Resolve recipe ingredient values used by skip_when_false."""
    return bool(value) and value.lower() not in FALSY_STRINGS


def normalize_declared_truth(value: object) -> bool:
    """Normalize the closed recipe truth dialect or raise a precise exception.

    ``t``/``f`` and ``y``/``n`` aliases deliberately remain unsupported: recipe
    values use the existing full-word/numeric vocabulary, and accepting extra
    spellings would silently expand the execution contract.
    """
    if value is None:
        raise DeclaredTruthUnresolved("declared truth value is required")
    if isinstance(value, bool):
        return value
    if not isinstance(value, str):
        raise DeclaredTruthUnsupported(f"unsupported declared truth value: {value!r}")

    normalized = value.strip().lower()
    if "${{" in normalized:
        raise DeclaredTruthUnsupported("declared truth value contains an unresolved template")
    if normalized in _TRUTHY_VALUES:
        return True
    if normalized in _FALSY_VALUES:
        return False
    raise DeclaredTruthUnsupported(f"unsupported declared truth value: {value!r}")
