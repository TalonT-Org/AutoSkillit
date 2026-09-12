"""Tests for the shared select_doctor_checks boundary helper."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from autoskillit import cli
from tests.cli._doctor_helpers import select_doctor_checks

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


# SEL1: every check is offered, only the target runs, and it runs through the real wrapper
def test_selection_runs_only_script_version_health_through_real_wrapper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """select_doctor_checks forwards only the selected check to the genuine _run_check."""
    from autoskillit.cli import doctor as doctor_mod
    from autoskillit.cli.doctor import _doctor_types

    assert doctor_mod._run_check is _doctor_types._run_check
    real_run_check = doctor_mod._run_check
    reached_real_wrapper: list[str] = []

    def spy_run_check(
        fn: Callable[[], object], *, check_name: str | None = None
    ) -> list[_doctor_types.DoctorResult]:
        reached_real_wrapper.append(check_name or _doctor_types._check_display_name(fn))
        return real_run_check(fn, check_name=check_name)

    monkeypatch.setattr(doctor_mod, "_run_check", spy_run_check)
    offered = select_doctor_checks(monkeypatch, {"script_version_health"})
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    # No .autoskillit/recipes/ directory created
    cli.doctor_cmd(output_json=True)
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    script_checks = [r for r in data["results"] if r["check"] == "script_version_health"]
    assert len(script_checks) == 1
    assert script_checks[0]["severity"] == "ok"
    assert script_checks[0]["message"] == "No pipeline scripts found"
    assert reached_real_wrapper == ["script_version_health"]
    assert "script_version_health" in offered
    assert any(name != "script_version_health" for name in offered), (
        "expected the doctor registry to offer more than one check; if it has "
        "shrunk to just script_version_health, this selection-boundary test no "
        "longer proves anything and needs re-examination, not just a passing run"
    )
