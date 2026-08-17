"""Decomposition of the legacy server.tools._evidence_reader module facade."""

from __future__ import annotations

from autoskillit.exploration import (
    ArtifactCaptureError,
    ArtifactCaptureStatus,
    StableArtifactCapture,
    capture_stable_artifact,
    resolve_repository_identity,
    stable_artifact_matches,
)
from autoskillit.server.tools._evidence_reader._invocation import (
    create_evidence_reader_invocation,
)
from autoskillit.server.tools._evidence_reader._reader import (
    evidence_reader_scope_digest,
    read_bound_evidence_reader_page,
    read_evidence_reader_page,
)
from autoskillit.server.tools._evidence_reader._startup import (
    EvidenceReaderError,
    EvidenceReaderInvocation,
    EvidenceReaderLimits,
    EvidenceReaderPage,
    EvidenceReaderReceipt,
    load_evidence_reader_receipts,
    revoke_evidence_reader_invocation,
    validate_evidence_reader_startup,
)

__all__ = [
    "ArtifactCaptureError",
    "ArtifactCaptureStatus",
    "EvidenceReaderError",
    "EvidenceReaderInvocation",
    "EvidenceReaderLimits",
    "EvidenceReaderPage",
    "EvidenceReaderReceipt",
    "StableArtifactCapture",
    "capture_stable_artifact",
    "create_evidence_reader_invocation",
    "evidence_reader_scope_digest",
    "load_evidence_reader_receipts",
    "read_bound_evidence_reader_page",
    "read_evidence_reader_page",
    "resolve_repository_identity",
    "revoke_evidence_reader_invocation",
    "stable_artifact_matches",
    "validate_evidence_reader_startup",
]
