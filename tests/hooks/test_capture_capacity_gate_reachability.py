"""Contract tests for the capacity gate-reachability declaration (C-T2)."""

from __future__ import annotations

import pytest

from autoskillit.hooks._capture._capacity import (
    CAPACITY_REASON_GATES,
    CapacityGate,
)
from autoskillit.hooks._capture._types import CaptureCapacityReason

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]


class TestGateReachabilityTotality:
    """C-T2 — every CaptureCapacityReason declares its gates."""

    def test_every_capacity_reason_declares_its_gates(self) -> None:
        assert set(CAPACITY_REASON_GATES) == set(CaptureCapacityReason)
        for reason, gates in CAPACITY_REASON_GATES.items():
            assert gates, f"empty gate set for {reason!r}"
            for gate in gates:
                assert isinstance(gate, CapacityGate)

    def test_registry_rejects_unregistered_reason(self) -> None:
        missing = {"FAKE_REASON"} - set(CAPACITY_REASON_GATES)
        assert missing, "a fake reason not in CAPACITY_REASON_GATES must be detected"
