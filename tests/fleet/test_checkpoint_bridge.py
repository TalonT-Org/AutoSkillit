"""T1 (cont.): checkpoint_from_sidecar bridge."""

from __future__ import annotations

import pytest

from autoskillit.fleet._checkpoint_bridge import checkpoint_from_sidecar
from autoskillit.fleet.sidecar import IssueSidecarEntry

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


class TestCheckpointFromSidecar:
    def test_extracts_completed_urls(self) -> None:
        entries = [
            IssueSidecarEntry(
                issue_url="https://github.com/o/r/issues/1", status="completed", ts="t1"
            ),
            IssueSidecarEntry(
                issue_url="https://github.com/o/r/issues/2", status="failed", ts="t2"
            ),
            IssueSidecarEntry(
                issue_url="https://github.com/o/r/issues/3", status="completed", ts="t3"
            ),
        ]
        cp = checkpoint_from_sidecar(entries)
        assert cp.completed_items == [
            "https://github.com/o/r/issues/1",
            "https://github.com/o/r/issues/3",
        ]
        assert cp.step_name == "fleet_dispatch"

    def test_empty_entries(self) -> None:
        cp = checkpoint_from_sidecar([])
        assert cp.completed_items == []
        assert cp.ts == ""

    def test_uses_last_entry_ts(self) -> None:
        entries = [
            IssueSidecarEntry(issue_url="u1", status="completed", ts="2026-01-01"),
            IssueSidecarEntry(issue_url="u2", status="completed", ts="2026-05-04"),
        ]
        cp = checkpoint_from_sidecar(entries)
        assert cp.ts == "2026-05-04"

    def test_produces_valid_checkpoint(self) -> None:
        entries = [
            IssueSidecarEntry(issue_url="u1", status="completed", ts="t"),
        ]
        cp = checkpoint_from_sidecar(entries)
        d = cp.to_dict()
        from autoskillit.core.types._type_checkpoint import SessionCheckpoint

        restored = SessionCheckpoint.from_dict(d)
        assert restored == cp
