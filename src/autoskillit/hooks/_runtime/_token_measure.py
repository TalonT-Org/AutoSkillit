"""Stdlib-only stand-in for ``TokenMeasure`` used by hook scripts.

Hook scripts run under any Python interpreter and cannot import the
``autoskillit`` package. This module mirrors the dict-level surface of
``autoskillit.core.types._type_token.TokenMeasure`` so the
``token_summary_hook`` can decode durable measure records and combine
or take the maximum of two without duplicating core's logic. Any
semantic change here must be reflected in ``TokenMeasure`` (and the
two canonical helpers used by Python-side callers).
"""

from __future__ import annotations

from typing import Any


def measure_decode(raw: Any, *, legacy: bool = False) -> dict[str, Any]:
    """Decode one raw value into the canonical {state, value} dict shape."""
    if isinstance(raw, dict) and set(raw) == {"state", "value"}:
        state, value = raw.get("state"), raw.get("value")
        if isinstance(state, str) and state in {
            "measured",
            "measured_zero",
            "unavailable",
            "unknown",
            "not_applicable",
        }:
            value_is_int = isinstance(value, int) and not isinstance(value, bool)
            if state in {"measured", "measured_zero"} and value_is_int:
                return {"state": state, "value": value}
            if state in {"unavailable", "unknown", "not_applicable"} and value is None:
                return {"state": state, "value": None}
    if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
        if legacy and raw == 0:
            return {"state": "unknown", "value": None}
        return {"state": "measured_zero" if raw == 0 else "measured", "value": raw}
    return {"state": "unknown", "value": None}


def combine_measures(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Combine two measures, downgrading to unknown on incompatible states."""
    if isinstance(left.get("value"), int) and isinstance(right.get("value"), int):
        value = left["value"] + right["value"]
        return {"state": "measured_zero" if value == 0 else "measured", "value": value}
    if left.get("state") == right.get("state"):
        return dict(left)
    return {"state": "unknown", "value": None}


def maximum_measures(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Return the maximum of two measures, downgrading to unknown on conflict."""
    if isinstance(left.get("value"), int) and isinstance(right.get("value"), int):
        value = max(left["value"], right["value"])
        return {"state": "measured_zero" if value == 0 else "measured", "value": value}
    if left.get("state") == right.get("state"):
        return dict(left)
    return {"state": "unknown", "value": None}
