"""B7: doctor reports pytest-generation temp-root capacity, read-only."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.cli.doctor._doctor_runtime import _check_pytest_temp_capacity
from autoskillit.core import Severity

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def _low_space_probe(path: Path) -> tuple[int, int, int]:
    return (21_000_000_000, 20_999_999_000, 1_000)


def _plenty_space_probe(path: Path) -> tuple[int, int, int]:
    return (21_000_000_000, 1_000_000_000, 20_000_000_000)


def test_check_reports_usage_and_generation_count() -> None:
    result = _check_pytest_temp_capacity()

    assert result.check == "pytest_temp_capacity"
    assert result.severity in (Severity.OK, Severity.WARNING)
    assert "bytes free" in result.message
    assert "pytest generations under" in result.message


def test_check_reports_warning_below_threshold() -> None:
    result = _check_pytest_temp_capacity(space_probe=_low_space_probe)

    assert result.severity is Severity.WARNING
    assert "1000 bytes free of 21000000000 total" in result.message
    assert "below" in result.message and "threshold" in result.message


def test_check_reports_ok_with_plenty_of_space() -> None:
    result = _check_pytest_temp_capacity(space_probe=_plenty_space_probe)

    assert result.severity is Severity.OK
    assert "20000000000 bytes free of 21000000000 total" in result.message
