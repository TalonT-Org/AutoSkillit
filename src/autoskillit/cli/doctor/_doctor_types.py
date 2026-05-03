"""Shared DoctorResult type — imported by all _doctor_* sub-modules."""

from __future__ import annotations

from dataclasses import dataclass

from autoskillit.core import Severity


@dataclass
class DoctorResult:
    """Outcome of a single doctor check."""

    severity: Severity
    check: str
    message: str


_NON_PROBLEM: frozenset[Severity] = frozenset({Severity.OK, Severity.INFO})
