"""B4: StoreCapacityExhaustedError is a typed infrastructure fault."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import InfrastructureFaultError, StoreCapacityExhaustedError

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_store_capacity_exhausted_is_an_infrastructure_fault() -> None:
    exc = StoreCapacityExhaustedError(
        path=Path("/dev/shm"),
        free_bytes=100,
        total_bytes=21_000_000_000,
        remedy="run `task cleanup-shm` to reclaim stale pytest generations",
    )

    assert isinstance(exc, InfrastructureFaultError)
    assert isinstance(exc, RuntimeError)
    message = str(exc)
    assert "/dev/shm" in message
    assert "100" in message
    assert "21000000000" in message
    assert "cleanup-shm" in message
