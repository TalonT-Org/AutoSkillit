"""Cross-layer agreement between infrastructure exceptions and fleet categories."""

from __future__ import annotations

import pytest

from autoskillit.core import (
    FaultDomain,
    FleetErrorCode,
    InfrastructureFaultError,
    ProcessStaleError,
)
from autoskillit.fleet.state_types import get_error_category

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_process_stale_error_agrees_with_fleet_categorization() -> None:
    """The ProcessStaleError mapping in fleet/_api.py stays infrastructure-owned."""
    assert issubclass(ProcessStaleError, InfrastructureFaultError)
    assert get_error_category(FleetErrorCode.FLEET_PROCESS_STALE) is FaultDomain.INFRASTRUCTURE
