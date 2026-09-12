"""Shared doctor-collection helpers for the split doctor test files."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from autoskillit.cli import doctor as doctor_mod
from autoskillit.cli.doctor._doctor_types import DoctorResult, _check_display_name


def select_doctor_checks(
    monkeypatch: pytest.MonkeyPatch, selected_check_names: set[str]
) -> list[str]:
    """Run only the named checks through the real ``_run_check`` wrapper.

    Every check that ``_collect_doctor_results`` offers still reaches the boundary,
    where its name is resolved exactly as production does. An unselected check
    contributes no results and its body never executes. A selected check is
    forwarded to the ``_run_check`` bound at call time, so exception isolation and
    result normalisation stay real. Returns the offered names in dispatch order.
    """
    real_run_check = doctor_mod._run_check
    offered: list[str] = []

    def run_selected(
        fn: Callable[[], object], *, check_name: str | None = None
    ) -> list[DoctorResult]:
        resolved_name = check_name or _check_display_name(fn)
        offered.append(resolved_name)
        if resolved_name not in selected_check_names:
            return []
        return real_run_check(fn, check_name=check_name)

    monkeypatch.setattr(doctor_mod, "_run_check", run_selected)
    return offered
