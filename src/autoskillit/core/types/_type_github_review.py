"""Immutable contracts for authoritative GitHub pull-request reviews."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

__all__ = [
    "GitHubReviewAttempt",
    "GitHubReviewComment",
    "GitHubReviewFindingDisposition",
    "GitHubReviewPostResult",
    "GitHubReviewReceipt",
    "GitHubReviewRequest",
    "ReviewFindingDispositionKind",
    "ReviewOperationState",
    "ReviewReconciliationResult",
    "ReviewResponseClass",
]


class ReviewOperationState(StrEnum):
    DRY_RUN = "DRY_RUN"
    PREPARED = "PREPARED"
    POSTING = "POSTING"
    COMMITTED_PENDING_VERIFICATION = "COMMITTED_PENDING_VERIFICATION"
    SUCCEEDED = "SUCCEEDED"
    RECONCILED = "RECONCILED"
    AMBIGUOUS = "AMBIGUOUS"
    THROTTLED = "THROTTLED"
    TERMINAL = "TERMINAL"


class ReviewResponseClass(StrEnum):
    NONE = "NONE"
    SUCCESS = "SUCCESS"
    TRANSPORT_ERROR = "TRANSPORT_ERROR"
    SERVER_ERROR = "SERVER_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    SECONDARY_RATE_LIMIT = "SECONDARY_RATE_LIMIT"
    CLIENT_ERROR = "CLIENT_ERROR"


class ReviewReconciliationResult(StrEnum):
    NOT_NEEDED = "NOT_NEEDED"
    MATCHED = "MATCHED"
    ENRICHED = "ENRICHED"
    NOT_FOUND = "NOT_FOUND"
    UNCERTAIN = "UNCERTAIN"


class ReviewFindingDispositionKind(StrEnum):
    POSTED = "POSTED"
    ALREADY_PRESENT = "ALREADY_PRESENT"
    OMITTED_INVALID = "OMITTED_INVALID"


@dataclass(frozen=True, slots=True)
class GitHubReviewComment:
    path: str
    line: int
    body: str
    side: str = "RIGHT"
    start_line: int | None = None
    start_side: str | None = None

    @classmethod
    def from_wire(cls, item: Mapping[str, object]) -> GitHubReviewComment:
        """Construct a typed comment from the MCP wire representation."""
        return cls(
            path=str(item["path"]),
            line=_required_int(item, "line"),
            body=str(item["body"]),
            side=str(item.get("side", "RIGHT")),
            start_line=(
                _required_int(item, "start_line") if item.get("start_line") is not None else None
            ),
            start_side=(str(item["start_side"]) if item.get("start_side") is not None else None),
        )


@dataclass(frozen=True, slots=True)
class GitHubReviewRequest:
    repository: str
    pr_number: int
    head_sha: str
    logical_iteration: str
    event: str
    body: str
    comments: tuple[GitHubReviewComment, ...] = ()
    cwd: str = ""
    receipt_path: Path | str | None = None
    dry_run: bool = False


@dataclass(frozen=True, slots=True)
class GitHubReviewAttempt:
    attempt_number: int
    response_class: ReviewResponseClass
    attempt_digest: str = ""
    effective_event: str = ""
    effective_body_digest: str = ""
    canonical_indexes: tuple[int, ...] = ()
    status_code: int | None = None
    error: str | None = None
    retry_after_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class GitHubReviewFindingDisposition:
    original_index: int
    kind: ReviewFindingDispositionKind
    canonical_index: int | None = None
    remote_comment_id: int | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class GitHubReviewReceipt:
    schema_version: int
    operation_key: str
    repository: str
    pr_number: int
    head_sha: str
    logical_iteration: str
    requested_event: str
    effective_event: str
    requested_body_digest: str
    effective_body_digest: str
    canonical_finding_digest: str
    state: ReviewOperationState
    response_class: ReviewResponseClass
    review_id: int | None
    comment_ids: tuple[int, ...]
    canonical_finding_count: int
    reconciliation_result: ReviewReconciliationResult
    finding_dispositions: tuple[GitHubReviewFindingDisposition, ...]
    created_at: float
    updated_at: float
    final_attempt_digest: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _wire(asdict(self))


@dataclass(frozen=True, slots=True)
class GitHubReviewPostResult:
    operation_key: str
    head_sha: str
    state: ReviewOperationState
    response_class: ReviewResponseClass = ReviewResponseClass.NONE
    reconciliation_result: ReviewReconciliationResult = ReviewReconciliationResult.NOT_NEEDED
    review_id: int | None = None
    comment_ids: tuple[int, ...] = ()
    planned_mutation_count: int = 1
    planned_comment_count: int = 0
    executed_mutation_count: int = 0
    executed_comment_count: int = 0
    receipt_path: Path | None = None
    receipt: GitHubReviewReceipt | None = None
    replayed: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _wire(asdict(self))


def _wire(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _wire(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_wire(item) for item in value]
    return value


def _required_int(item: Mapping[str, object], key: str) -> int:
    value = item[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{key} must be an integer")
    return value
