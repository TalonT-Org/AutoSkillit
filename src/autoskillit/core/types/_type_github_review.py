"""Immutable contracts for authoritative GitHub pull-request reviews."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, TypeGuard

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
    "is_final_github_review_state",
    "is_valid_github_review_head_sha",
    "is_valid_github_review_logical_iteration",
    "is_valid_github_review_operation_key",
    "is_valid_github_review_repository",
    "review_receipt_validation_error",
]

_HEAD_SHA_RE = re.compile(r"[0-9a-f]{40}")
_LOGICAL_ITERATION_RE = re.compile(
    r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?:[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"
)
_OPERATION_KEY_RE = re.compile(r"[0-9a-f]{64}")
_REPOSITORY_RE = re.compile(
    r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?/"
    r"[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?"
)


def is_valid_github_review_head_sha(value: object) -> bool:
    """Return whether a value is a canonical full lowercase Git commit SHA."""
    return isinstance(value, str) and _HEAD_SHA_RE.fullmatch(value) is not None


def is_valid_github_review_logical_iteration(value: object) -> bool:
    """Return whether a value is a canonical namespaced review iteration."""
    return isinstance(value, str) and _LOGICAL_ITERATION_RE.fullmatch(value) is not None


def is_valid_github_review_operation_key(value: object) -> bool:
    """Return whether a value is the canonical SHA-256 review operation key."""
    return isinstance(value, str) and _OPERATION_KEY_RE.fullmatch(value) is not None


def is_valid_github_review_repository(value: object) -> bool:
    """Return whether a value is a canonical lowercase owner/repository identity."""
    return isinstance(value, str) and _REPOSITORY_RE.fullmatch(value) is not None


class ReviewOperationState(StrEnum):
    DRY_RUN = "DRY_RUN"
    PREPARED = "PREPARED"
    RETRY_PENDING = "RETRY_PENDING"
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


def is_final_github_review_state(value: object) -> bool:
    """Return whether a wire value represents a successfully finalized review."""
    return isinstance(value, str) and value in {
        ReviewOperationState.SUCCEEDED.value,
        ReviewOperationState.RECONCILED.value,
    }


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
            path=_required_str(item, "path"),
            line=_required_int(item, "line"),
            body=_required_str(item, "body"),
            side=_optional_str(item, "side", default="RIGHT"),
            start_line=(
                _required_int(item, "start_line") if item.get("start_line") is not None else None
            ),
            start_side=(
                _required_str(item, "start_side") if item.get("start_side") is not None else None
            ),
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

    @classmethod
    def from_wire(cls, item: object) -> GitHubReviewFindingDisposition:
        """Decode one finding disposition from its durable wire representation."""
        if not isinstance(item, Mapping):
            raise TypeError("finding disposition must be an object")
        return cls(
            original_index=_required_int(item, "original_index"),
            kind=ReviewFindingDispositionKind(_required_str(item, "kind")),
            canonical_index=_optional_int(item, "canonical_index"),
            remote_comment_id=_optional_int(item, "remote_comment_id"),
            reason=_optional_str_or_none(item, "reason"),
        )


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

    @classmethod
    def from_wire(cls, item: Mapping[str, object]) -> GitHubReviewReceipt:
        """Decode the complete durable receipt wire representation."""
        raw_dispositions = item["finding_dispositions"]
        if not isinstance(raw_dispositions, list):
            raise TypeError("finding_dispositions must be a list")
        return cls(
            schema_version=_required_int(item, "schema_version"),
            operation_key=_required_str(item, "operation_key"),
            repository=_required_str(item, "repository"),
            pr_number=_required_int(item, "pr_number"),
            head_sha=_required_str(item, "head_sha"),
            logical_iteration=_required_str(item, "logical_iteration"),
            requested_event=_required_str(item, "requested_event"),
            effective_event=_required_str(item, "effective_event"),
            requested_body_digest=_required_str(item, "requested_body_digest"),
            effective_body_digest=_required_str(item, "effective_body_digest"),
            canonical_finding_digest=_required_str(item, "canonical_finding_digest"),
            state=ReviewOperationState(_required_str(item, "state")),
            response_class=ReviewResponseClass(_required_str(item, "response_class")),
            review_id=_optional_int(item, "review_id"),
            comment_ids=_required_int_tuple(item, "comment_ids"),
            canonical_finding_count=_required_int(item, "canonical_finding_count"),
            reconciliation_result=ReviewReconciliationResult(
                _required_str(item, "reconciliation_result")
            ),
            finding_dispositions=tuple(
                GitHubReviewFindingDisposition.from_wire(disposition)
                for disposition in raw_dispositions
            ),
            created_at=_required_float(item, "created_at"),
            updated_at=_required_float(item, "updated_at"),
            final_attempt_digest=_optional_str_or_none(item, "final_attempt_digest"),
        )

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


def review_receipt_validation_error(
    payload: Mapping[str, object],
    *,
    operation_key: str,
    repository: str,
    pr_number: int,
    head_sha: str,
    logical_iteration: str,
    post_state: str,
) -> str | None:
    """Return the stable failure code for an authoritative receipt effect."""
    required_fields = {
        "schema_version",
        "operation_key",
        "repository",
        "pr_number",
        "head_sha",
        "logical_iteration",
        "state",
        "review_id",
        "comment_ids",
        "canonical_finding_count",
        "finding_dispositions",
        "reconciliation_result",
    }
    if not required_fields.issubset(payload):
        return "incomplete_receipt"
    if (
        payload.get("schema_version") != 1
        or isinstance(payload.get("schema_version"), bool)
        or payload.get("operation_key") != operation_key
        or payload.get("repository") != repository
        or payload.get("pr_number") != pr_number
        or payload.get("head_sha") != head_sha
        or payload.get("logical_iteration") != logical_iteration
        or payload.get("state") != post_state
        or not is_final_github_review_state(payload.get("state"))
        or payload.get("reconciliation_result")
        not in {
            ReviewReconciliationResult.NOT_NEEDED.value,
            ReviewReconciliationResult.MATCHED.value,
            ReviewReconciliationResult.ENRICHED.value,
        }
        or payload.get("dry_run", False) is not False
        or not _is_positive_int(payload.get("review_id"))
    ):
        return "receipt_identity_mismatch"
    comment_ids = payload.get("comment_ids")
    dispositions = payload.get("finding_dispositions")
    count = payload.get("canonical_finding_count")
    if (
        not isinstance(comment_ids, list)
        or any(not _is_positive_int(value) for value in comment_ids)
        or len(set(comment_ids)) != len(comment_ids)
        or not isinstance(count, int)
        or isinstance(count, bool)
        or count < 0
        or not isinstance(dispositions, list)
        or len(dispositions) != count
    ):
        return "incomplete_finding_accounting"

    indexes: set[int] = set()
    disposition_remote_ids: set[int] = set()
    remote_kinds = {
        ReviewFindingDispositionKind.POSTED.value,
        ReviewFindingDispositionKind.ALREADY_PRESENT.value,
    }
    for disposition in dispositions:
        if not isinstance(disposition, Mapping):
            return "incomplete_finding_accounting"
        original_index = disposition.get("original_index")
        kind = disposition.get("kind")
        if (
            not isinstance(original_index, int)
            or isinstance(original_index, bool)
            or original_index < 0
            or original_index >= count
            or original_index in indexes
            or not isinstance(kind, str)
            or kind not in remote_kinds | {ReviewFindingDispositionKind.OMITTED_INVALID.value}
        ):
            return "incomplete_finding_accounting"
        indexes.add(original_index)
        remote_comment_id = disposition.get("remote_comment_id")
        if kind in remote_kinds:
            if (
                not _is_positive_int(remote_comment_id)
                or remote_comment_id in disposition_remote_ids
            ):
                return "incomplete_finding_accounting"
            disposition_remote_ids.add(remote_comment_id)
        elif (
            remote_comment_id is not None
            or not isinstance(disposition.get("reason"), str)
            or not str(disposition["reason"]).strip()
        ):
            return "incomplete_finding_accounting"

    if indexes != set(range(count)) or disposition_remote_ids != set(comment_ids):
        return "incomplete_finding_accounting"
    return None


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


def _is_positive_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _optional_int(item: Mapping[str, object], key: str) -> int | None:
    value = item.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{key} must be an integer or null")
    return value


def _required_float(item: Mapping[str, object], key: str) -> float:
    value = item[key]
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"{key} must be numeric")
    return float(value)


def _required_int_tuple(item: Mapping[str, object], key: str) -> tuple[int, ...]:
    value = item[key]
    if not isinstance(value, list):
        raise TypeError(f"{key} must be a list")
    return tuple(_required_int({key: entry}, key) for entry in value)


def _required_str(item: Mapping[str, object], key: str) -> str:
    value = item[key]
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    return value


def _optional_str(item: Mapping[str, object], key: str, *, default: str) -> str:
    value = item.get(key, default)
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    return value


def _optional_str_or_none(item: Mapping[str, object], key: str) -> str | None:
    value = item.get(key)
    if value is not None and not isinstance(value, str):
        raise TypeError(f"{key} must be a string or null")
    return value
