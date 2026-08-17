"""Module-level `_reconcile_payload` and `_finalize` helpers for the poster.

Extracted from `poster.py`. These are post-attempt helpers that finalize
attempt + receipt publication; they are NOT mutation authorities (the
ledger retains all mutation authority). Moving them to module-level
functions keeps `poster.py` focused on the state machine.

`_EXPECTED_REMOTE_STATES` and `_FINAL_STATES` also live here because
only these helpers consume them.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from autoskillit.core import (
    GitHubReviewFindingDisposition,
    GitHubReviewPostResult,
    GitHubReviewReceipt,
    GitHubReviewRequest,
    ReviewFindingDispositionKind,
    ReviewOperationState,
    ReviewReconciliationResult,
)

from . import _poster_support

_EXPECTED_REMOTE_STATES = {
    "APPROVE": "APPROVED",
    "REQUEST_CHANGES": "CHANGES_REQUESTED",
    "COMMENT": "COMMENTED",
}
_FINAL_STATES = frozenset({ReviewOperationState.SUCCEEDED, ReviewOperationState.RECONCILED})


async def reconcile_payload(
    *,
    gateway,
    request: GitHubReviewRequest,
    operation_key: str,
    payload: Mapping[str, Any],
    findings: tuple[_poster_support.CanonicalFinding, ...],
    authenticated_login: str,
) -> _poster_support.Reconciliation:
    reviews = await gateway.list_reviews(request.repository, request.pr_number)
    if not reviews.succeeded or not isinstance(reviews.data, list):
        return _poster_support.Reconciliation(
            ReviewReconciliationResult.UNCERTAIN,
            error=reviews.error or "could not read pull-request reviews",
        )
    marker = _poster_support.OPERATION_MARKER.format(key=operation_key)
    expected_body = str(payload["body"])
    expected_event = str(payload["event"])
    expected_state = _EXPECTED_REMOTE_STATES[expected_event]
    matches = [
        item
        for item in reviews.data
        if isinstance(item, Mapping)
        and item.get("body") == expected_body
        and marker in str(item.get("body", ""))
        and item.get("commit_id") == request.head_sha
        and item.get("state") == expected_state
        and _poster_support.nested_string(item, "user", "login") == authenticated_login
        and _poster_support.positive_int(item.get("id")) is not None
    ]
    if not matches:
        return _poster_support.Reconciliation(ReviewReconciliationResult.NOT_FOUND)
    if len(matches) != 1:
        return _poster_support.Reconciliation(
            ReviewReconciliationResult.UNCERTAIN,
            error="multiple reviews matched one operation marker",
        )
    review_id = _poster_support.positive_int(matches[0].get("id"))
    if review_id is None:
        return _poster_support.Reconciliation(
            ReviewReconciliationResult.UNCERTAIN,
            error="matched review omitted a valid id",
        )
    comments = await gateway.list_review_comments(
        request.repository,
        request.pr_number,
        review_id,
    )
    if not comments.succeeded or not isinstance(comments.data, list):
        return _poster_support.Reconciliation(
            ReviewReconciliationResult.UNCERTAIN,
            error=comments.error or "could not read review comments",
        )
    expected_comments = payload.get("comments")
    if not isinstance(expected_comments, list) or len(expected_comments) != len(findings):
        return _poster_support.Reconciliation(
            ReviewReconciliationResult.UNCERTAIN,
            error="persisted attempt comments do not match canonical findings",
        )
    matched_ids: list[tuple[int, int]] = []
    used_remote_ids: set[int] = set()
    for finding, expected in zip(findings, expected_comments, strict=True):
        if not isinstance(expected, Mapping):
            return _poster_support.Reconciliation(
                ReviewReconciliationResult.UNCERTAIN,
                error="persisted attempt comment is malformed",
            )
        candidates = [
            item
            for item in comments.data
            if isinstance(item, Mapping)
            and _poster_support.remote_comment_matches(item, expected, finding.digest)
        ]
        if len(candidates) != 1:
            return _poster_support.Reconciliation(
                ReviewReconciliationResult.UNCERTAIN,
                error="review comments did not exactly match the attempted finding set",
            )
        comment_id = _poster_support.positive_int(candidates[0].get("id"))
        if comment_id is None or comment_id in used_remote_ids:
            return _poster_support.Reconciliation(
                ReviewReconciliationResult.UNCERTAIN,
                error="review comment ids were missing or duplicated",
            )
        used_remote_ids.add(comment_id)
        matched_ids.append((finding.canonical_index, comment_id))
    marker_count = sum(
        isinstance(item, Mapping)
        and _poster_support.FINDING_MARKER.split("{digest}", 1)[0] in str(item.get("body", ""))
        for item in comments.data
    )
    if marker_count != len(findings):
        return _poster_support.Reconciliation(
            ReviewReconciliationResult.UNCERTAIN,
            error="review contained an unexpected AutoSkillit finding marker set",
        )
    return _poster_support.Reconciliation(
        ReviewReconciliationResult.MATCHED,
        review_id=review_id,
        comment_ids=tuple(matched_ids),
    )


def finalize(
    *,
    ledger,
    wall_clock,
    request: GitHubReviewRequest,
    operation_key: str,
    findings: tuple[_poster_support.CanonicalFinding, ...],
    omitted: tuple[GitHubReviewFindingDisposition, ...],
    effective_event: str,
    attempt_digest: str,
    response_class,
    state: ReviewOperationState,
    reconciliation: _poster_support.Reconciliation,
    executed_mutations: int,
) -> GitHubReviewPostResult:
    if state not in _FINAL_STATES or reconciliation.review_id is None:
        raise ValueError("cannot finalize an unverified review operation")
    ids_by_index = dict(reconciliation.comment_ids)
    dispositions = list(omitted)
    for finding in findings:
        remote_comment_id = ids_by_index.get(finding.canonical_index)
        if remote_comment_id is None:
            raise ValueError("verified review omitted a posted comment id")
        dispositions.append(
            GitHubReviewFindingDisposition(
                original_index=finding.original_index,
                canonical_index=finding.canonical_index,
                kind=ReviewFindingDispositionKind.POSTED,
                remote_comment_id=remote_comment_id,
            )
        )
    dispositions.sort(key=lambda item: item.original_index)
    if len(dispositions) != len(request.comments):
        raise ValueError("review finding accounting is not exhaustive")
    now = wall_clock()
    all_findings = _poster_support.canonical_findings(request)
    receipt = GitHubReviewReceipt(
        schema_version=1,
        operation_key=operation_key,
        repository=request.repository,
        pr_number=request.pr_number,
        head_sha=request.head_sha,
        logical_iteration=request.logical_iteration,
        requested_event=request.event,
        effective_event=effective_event,
        requested_body_digest=_poster_support.text_digest(request.body),
        effective_body_digest=_poster_support.effective_body_digest(
            request, operation_key, findings, effective_event
        ),
        canonical_finding_digest=_poster_support.finding_set_digest(all_findings),
        state=state,
        response_class=response_class,
        review_id=reconciliation.review_id,
        comment_ids=tuple(
            item.remote_comment_id
            for item in dispositions
            if item.remote_comment_id is not None
        ),
        canonical_finding_count=len(all_findings),
        reconciliation_result=reconciliation.result,
        finding_dispositions=tuple(dispositions),
        created_at=now,
        updated_at=now,
        final_attempt_digest=attempt_digest,
    )
    ledger.save_receipt(receipt)
    return GitHubReviewPostResult(
        operation_key=operation_key,
        head_sha=request.head_sha,
        state=state,
        response_class=response_class,
        reconciliation_result=reconciliation.result,
        review_id=reconciliation.review_id,
        comment_ids=receipt.comment_ids,
        planned_mutation_count=1,
        planned_comment_count=len(all_findings),
        executed_mutation_count=executed_mutations,
        executed_comment_count=sum(
            item.kind is ReviewFindingDispositionKind.POSTED for item in dispositions
        ),
        receipt=receipt,
    )


__all__ = ["reconcile_payload", "finalize"]