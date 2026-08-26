"""Dataclasses and Protocols owned by the exploration broker.

``_CapabilityLease`` is the in-memory owner-bound capability record
produced by the store; ``ExplorationLaunchBinding`` is the public
material returned to launch adapters; ``ExplorationContext`` is the
immutable evidence generation; ``ExplorationServiceProtocol`` is the
injectable deterministic collector gateway.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Generic, Literal, Protocol, TypeVar, runtime_checkable

from autoskillit.core import (
    CompletenessReport,
    ContinuationCursor,
    EvidencePage,
    EvidenceRecord,
    ExplorationQuerySpec,
    RepositorySnapshot,
)
from autoskillit.pipeline.exploration_context_durable import (
    EXPLORATION_AUTHORITY_PATH_ENV,
    EXPLORATION_CAPABILITY_ENV,
    EXPLORATION_ROLE_ENV,
    EXPLORATION_SESSION_ENV,
)

_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class ExplorationLaunchBinding:
    """Server-issued material for one explorer child launch.

    The capability is opaque; role and session are trusted launch metadata, not
    authority accepted back from a child MCP call.
    """

    capability: str
    role: str
    session_id: str
    authority_path: Path

    def provider_extras(self) -> dict[str, str]:
        return {
            EXPLORATION_CAPABILITY_ENV: self.capability,
            EXPLORATION_ROLE_ENV: self.role,
            EXPLORATION_SESSION_ENV: self.session_id,
            EXPLORATION_AUTHORITY_PATH_ENV: str(self.authority_path),
        }


@dataclass(frozen=True, slots=True)
class _CapabilityLease(Generic[_T]):
    """In-memory owner-bound capability over a typed value and snapshot scope."""

    owner_id: str
    role: str
    session_id: str
    expires_at: float
    value: _T
    origin: Literal["session", "launch"]
    cwd: Path | None = None
    repository_root: Path | None = None
    source_identity: str = ""
    snapshot_digest: str = ""


@dataclass(frozen=True, slots=True)
class ExplorationContext:
    """One server-owned immutable evidence generation for a broker capability."""

    query: ExplorationQuerySpec
    snapshot: RepositorySnapshot
    evidence: tuple[EvidenceRecord, ...]
    completeness: CompletenessReport


@runtime_checkable
class ExplorationServiceProtocol(Protocol):
    """Injectable deterministic collector gateway; handlers never call collectors."""

    def capture_snapshot(self, root: Path) -> RepositorySnapshot: ...

    def collect(self, query: ExplorationQuerySpec, *, root: Path) -> ExplorationContext: ...

    def page(
        self,
        context: ExplorationContext,
        *,
        page_size: int,
        cursor: ContinuationCursor | None = None,
    ) -> EvidencePage: ...
