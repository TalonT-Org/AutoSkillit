"""Schema constants and dataclasses for the GitHub review ledger.

The schema (CREATE TABLE statements), the file modes, and the three
dataclasses (`ReviewOperationRecord`, `ReviewAttemptRecord`, `MutationSlot`)
live here. `GitHubReviewLedger` imports them from this module.
"""

from __future__ import annotations

from dataclasses import dataclass

from autoskillit.core import (
    GitHubReviewFindingDisposition,
    ReviewOperationState,
    ReviewResponseClass,
)

_SCHEMA_VERSION = 1
_DIRECTORY_MODE = 0o700
_DATABASE_MODE = 0o600
_SCHEMA = """
CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;
CREATE TABLE operations (
    operation_key TEXT PRIMARY KEY,
    request_digest TEXT NOT NULL,
    request_json BLOB NOT NULL,
    state TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
) STRICT;
CREATE TABLE operation_findings (
    operation_key TEXT NOT NULL,
    canonical_index INTEGER NOT NULL,
    original_index INTEGER NOT NULL,
    finding_digest TEXT NOT NULL,
    payload_json BLOB NOT NULL,
    PRIMARY KEY (operation_key, canonical_index),
    UNIQUE (operation_key, original_index),
    UNIQUE (operation_key, finding_digest),
    FOREIGN KEY (operation_key) REFERENCES operations(operation_key)
) STRICT;
CREATE TABLE attempts (
    operation_key TEXT NOT NULL,
    attempt_number INTEGER NOT NULL,
    attempt_digest TEXT NOT NULL,
    payload_json BLOB NOT NULL,
    canonical_indexes_json BLOB NOT NULL,
    omitted_dispositions_json BLOB NOT NULL,
    effective_event TEXT NOT NULL,
    effective_body_digest TEXT NOT NULL,
    state TEXT NOT NULL,
    response_class TEXT NOT NULL,
    status_code INTEGER,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (operation_key, attempt_number),
    UNIQUE (operation_key, attempt_digest),
    FOREIGN KEY (operation_key) REFERENCES operations(operation_key)
) STRICT;
CREATE TABLE receipts (
    operation_key TEXT PRIMARY KEY,
    receipt_json BLOB NOT NULL,
    created_at REAL NOT NULL,
    FOREIGN KEY (operation_key) REFERENCES operations(operation_key)
) STRICT;
CREATE TABLE rate_scopes (
    scope_id TEXT PRIMARY KEY,
    lease_owner TEXT,
    lease_generation INTEGER NOT NULL DEFAULT 0,
    lease_expires_at REAL NOT NULL DEFAULT 0,
    next_mutation_not_before REAL NOT NULL DEFAULT 0,
    backoff_until REAL NOT NULL DEFAULT 0,
    in_flight_operation_key TEXT
) STRICT;
"""


@dataclass(frozen=True, slots=True)
class ReviewOperationRecord:
    operation_key: str
    request_digest: str
    request_json: bytes
    state: ReviewOperationState
    created_at: float
    updated_at: float


@dataclass(frozen=True, slots=True)
class ReviewAttemptRecord:
    operation_key: str
    attempt_number: int
    attempt_digest: str
    payload_json: bytes
    canonical_indexes: tuple[int, ...]
    omitted_dispositions: tuple[GitHubReviewFindingDisposition, ...]
    effective_event: str
    effective_body_digest: str
    state: str
    response_class: ReviewResponseClass
    status_code: int | None
    error: str | None
    created_at: float
    updated_at: float


@dataclass(frozen=True, slots=True)
class MutationSlot:
    ready: bool
    delay: float
    lease_owner: str
    lease_generation: int
    blocked_operation_key: str | None = None


__all__ = [
    "MutationSlot",
    "ReviewAttemptRecord",
    "ReviewOperationRecord",
    "_DIRECTORY_MODE",
    "_DATABASE_MODE",
    "_SCHEMA",
    "_SCHEMA_VERSION",
]
