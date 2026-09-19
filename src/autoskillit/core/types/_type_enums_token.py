"""Token-accounting enum discriminators. Zero autoskillit imports."""

from __future__ import annotations

from enum import StrEnum, unique

__all__ = ["TokenMeasureState"]


@unique
class TokenMeasureState(StrEnum):
    """Whether an accounting token measure was observed by its producer."""

    MEASURED = "measured"
    MEASURED_ZERO = "measured_zero"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"
