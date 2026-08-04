"""Durable retry planning and restart recovery for GitHub review publication."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from autoskillit.core import (
    GitHubReviewFindingDisposition,
    GitHubReviewPostResult,
    GitHubReviewRequest,
    ReviewOperationState,
    ReviewResponseClass,
)

from . import _poster_support
from .ledger import MutationSlot, ReviewAttemptRecord

if TYPE_CHECKING:
    from .poster import DefaultGitHubReviewPoster


def attempt_material(
    *,
    request: GitHubReviewRequest,
    operation_key: str,
    findings: tuple[_poster_support.CanonicalFinding, ...],
    effective_event: str,
) -> tuple[dict[str, Any], bytes, str]:
    """Build the exact payload and digest persisted for one attempt."""

    payload = _poster_support.payload(
        request=request,
        operation_key=operation_key,
        findings=findings,
        event=effective_event,
    )
    payload_json = _poster_support.canonical_json(payload)
    attempt_digest = hashlib.sha256(
        b"autoskillit:github-review-attempt:v1\0" + payload_json
    ).hexdigest()
    return payload, payload_json, attempt_digest


def schedule_retry(
    poster: DefaultGitHubReviewPoster,
    *,
    request: GitHubReviewRequest,
    operation_key: str,
    completed_attempt_number: int,
    response_class: ReviewResponseClass,
    status_code: int | None,
    error: str | None,
    retry_attempt_number: int,
    retry_findings: tuple[_poster_support.CanonicalFinding, ...],
    retry_effective_event: str,
    retry_omitted: tuple[GitHubReviewFindingDisposition, ...],
) -> None:
    """Atomically persist the precise next attempt before it is claimed."""

    retry_payload, retry_payload_json, retry_attempt_digest = attempt_material(
        request=request,
        operation_key=operation_key,
        findings=retry_findings,
        effective_event=retry_effective_event,
    )
    poster.ledger.complete_attempt_and_schedule_retry(
        operation_key=operation_key,
        completed_attempt_number=completed_attempt_number,
        response_class=response_class,
        status_code=status_code,
        error=error,
        retry_attempt_number=retry_attempt_number,
        retry_attempt_digest=retry_attempt_digest,
        retry_payload_json=retry_payload_json,
        retry_canonical_indexes=tuple(finding.canonical_index for finding in retry_findings),
        retry_omitted_dispositions=retry_omitted,
        retry_effective_event=retry_effective_event,
        retry_effective_body_digest=_poster_support.text_digest(str(retry_payload["body"])),
    )


async def resume_pending_retry(
    poster: DefaultGitHubReviewPoster,
    *,
    request: GitHubReviewRequest,
    operation_key: str,
    findings: tuple[_poster_support.CanonicalFinding, ...],
    attempt: ReviewAttemptRecord,
) -> GitHubReviewPostResult:
    """Validate and claim a retry plan recovered from the durable ledger."""

    preflight = await poster._preflight_new_operation(request)
    if isinstance(preflight, GitHubReviewPostResult):
        poster.ledger.complete_attempt(
            operation_key=operation_key,
            attempt_number=attempt.attempt_number,
            state=preflight.state,
            response_class=preflight.response_class,
            status_code=None,
            error=preflight.error,
        )
        return replace(preflight, operation_key=operation_key)
    scope_material, authenticated_login, pr_author_login = preflight
    retry_indexes = set(attempt.canonical_indexes)
    retry_findings = tuple(
        finding for finding in findings if finding.canonical_index in retry_indexes
    )
    if tuple(finding.canonical_index for finding in retry_findings) != (attempt.canonical_indexes):
        error = "persisted review retry references unknown canonical findings"
        poster.ledger.complete_attempt(
            operation_key=operation_key,
            attempt_number=attempt.attempt_number,
            state=ReviewOperationState.TERMINAL,
            response_class=ReviewResponseClass.NONE,
            status_code=None,
            error=error,
        )
        return _poster_support.nonfinal_result(
            request,
            ReviewOperationState.TERMINAL,
            ReviewResponseClass.NONE,
            error,
            operation_key=operation_key,
        )
    scope_id = poster.ledger.rate_scope_id(
        credential=scope_material.credential,
        api_origin=scope_material.api_origin,
    )
    return await poster._attempt(
        request=request,
        operation_key=operation_key,
        scope_id=scope_id,
        findings=retry_findings,
        effective_event=attempt.effective_event,
        attempt_number=attempt.attempt_number,
        omitted=attempt.omitted_dispositions,
        authenticated_login=authenticated_login,
        pr_author_login=pr_author_login,
        resume_pending=True,
    )


def release_slot(
    poster: DefaultGitHubReviewPoster,
    scope_id: str,
    slot: MutationSlot,
    operation_key: str,
    *,
    keep_in_flight: bool,
) -> None:
    """Release the durable mutation lease held by a posting attempt."""

    poster.ledger.finish_mutation(
        scope_id=scope_id,
        lease_owner=slot.lease_owner,
        lease_generation=slot.lease_generation,
        operation_key=operation_key,
        keep_in_flight=keep_in_flight,
    )
