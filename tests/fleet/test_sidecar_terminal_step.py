"""Tests for terminal_step field on IssueSidecarEntry."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.fleet.sidecar import IssueSidecarEntry, append_sidecar_entry, read_sidecar

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]

URL = "https://github.com/org/repo/issues/1"
TS = "2026-06-01T00:00:00Z"


class TestSidecarEntryTerminalStep:
    def test_roundtrip(self, tmp_path: Path) -> None:
        entry = IssueSidecarEntry(
            issue_url=URL,
            status="completed",
            ts=TS,
            pr_url="https://github.com/org/repo/pull/1",
            terminal_step="escalate_stop",
        )
        append_sidecar_entry("d1", entry, tmp_path)
        entries = read_sidecar("d1", tmp_path)
        assert len(entries) == 1
        assert entries[0].terminal_step == "escalate_stop"

    def test_none_by_default(self) -> None:
        entry = IssueSidecarEntry(issue_url=URL, status="completed", ts=TS)
        assert entry.terminal_step is None

    def test_not_in_dict_when_none(self) -> None:
        entry = IssueSidecarEntry(issue_url=URL, status="completed", ts=TS)
        from dataclasses import asdict

        payload = {k: v for k, v in asdict(entry).items() if v is not None}
        assert "terminal_step" not in payload
