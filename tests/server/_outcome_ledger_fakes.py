"""Shared fake-ledger scaffolding for outcome-accounting tool tests.

Hoisted from ``tests/server/test_tools_test_check.py`` and
``tests/server/test_tools_commit_files.py`` so the two paired test modules
stay symmetric. When the ``WorkspaceOutcomeLedger`` Protocol gains methods,
update them here once and both test modules pick up the change.
"""

from __future__ import annotations


class _RecordingLedger:
    def __init__(self) -> None:
        self.records: list = []

    def record(self, record) -> None:
        self.records.append(record)

    def read(self, workspace: str, *, since: str, until: str) -> list:
        return []


class _FailingLedger:
    def __init__(self) -> None:
        self.calls = 0

    def record(self, record) -> None:
        self.calls += 1
        raise OSError("ledger unavailable")

    def read(self, workspace: str, *, since: str, until: str) -> list:
        return []


__all__ = ["_FailingLedger", "_RecordingLedger"]
