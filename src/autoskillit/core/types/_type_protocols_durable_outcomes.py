"""Durable cross-process ledger protocols.

The ``WorkspaceOutcomeLedger`` protocol is a read-write, cross-process
persistence concern rather than a logging concern (the surrounding
``_type_protocols_logging.py`` shard's other protocols are
record-only). Splitting it into its own module keeps each shard's
filename aligned with its actual interface shape.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ._type_results import WorkspaceOutcomeRecord

__all__ = ["WorkspaceOutcomeLedger"]


@runtime_checkable
class WorkspaceOutcomeLedger(Protocol):
    """Durable cross-process history for workspace test and commit outcomes."""

    def record(self, record: WorkspaceOutcomeRecord) -> None: ...

    def read(
        self,
        workspace: str,
        *,
        since: str,
        until: str,
    ) -> list[WorkspaceOutcomeRecord]: ...
