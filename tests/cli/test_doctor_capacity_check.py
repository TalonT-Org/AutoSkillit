"""B7: doctor reports pytest-generation temp-root capacity, read-only."""

from __future__ import annotations

import pytest

from autoskillit.cli.doctor._doctor_runtime import _check_pytest_temp_capacity
from autoskillit.core import Severity

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def test_check_reports_usage_and_generation_count() -> None:
    result = _check_pytest_temp_capacity()

    assert result.check == "pytest_temp_capacity"
    assert result.severity in (Severity.OK, Severity.WARNING)
    assert "bytes free" in result.message
    assert "pytest generations under" in result.message
