"""Reconciliation of persisted review operations against live GitHub state.

Owns the three read-side steps of the publication state machine: the
pre-flight identity/head validation a new operation must clear, the
reconciliation of an operation that already carries a persisted attempt, and
the remote finding-marker scan used for deduplication. None of them is a
mutation authority — the ledger retains all mutation authority; these helpers
read remote state and hand a decision back to the poster.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING

from autoskillit.core import (
    GitHubReviewPostResult,
    GitHubReviewRequest,
    ReviewOperationState,
    ReviewReconciliationResult,
    ReviewResponseClass,
)

from . import _poster_retry, _poster_support
from .gateway import CredentialScopeMaterial
from .ledger import ReviewAttemptRecord

if TYPE_CHECKING:
    from .poster import DefaultGitHubReviewPoster


async def preflight_new_operation(
    poster: DefaultGitHubReviewPoster,
    request: GitHubReviewRequest,
) -> tuple[CredentialScopeMaterial, str, str] | GitHubReviewPostResult:
    """Validate identity and PR head before starting a brand-new review operation.

    Returns ``(scope_material, authenticated_login, pr_author_login)`` on success. On
    failure — authentication fails, the live PR head no longer matches
    ``request.head_sha``, or the PR response omits required identity fields — returns a
    terminal ``GitHubReviewPostResult`` instead.
    """
    scope_material = await poster.gateway.scope_material()
    authenticated = await poster.gateway.get_authenticated_user()
    if not authenticated.succeeded:
        return _poster_support.nonfinal_result(
            request,
            ReviewOperationState.TERMINAL,
            authenticated.response_class,
            authenticated.error or "could not resolve authenticated GitHub user",
        )
    authenticated_login = _poster_support.login(authenticated.data)
    if authenticated_login is None:
        return _poster_support.nonfinal_result(
            request,
            ReviewOperationState.TERMINAL,
            ReviewResponseClass.CLIENT_ERROR,
            "authenticated GitHub response omitted login",
        )
    pull = await poster.gateway.get_pull(request.repository, request.pr_number)
    if not pull.succeeded:
        return _poster_support.nonfinal_result(
            request,
            ReviewOperationState.TERMINAL,
            pull.response_class,
            pull.error or "could not validate pull-request head",
        )
    head_sha = _poster_support.nested_string(pull.data, "head", "sha")
    pr_author_login = _poster_support.nested_string(pull.data, "user", "login")
    if head_sha != request.head_sha:
        return _poster_support.nonfinal_result(
            request,
            ReviewOperationState.TERMINAL,
            ReviewResponseClass.CLIENT_ERROR,
            "requested review head is not the live pull-request head",
        )
    if pr_author_login is None:
        return _poster_support.nonfinal_result(
            request,
            ReviewOperationState.TERMINAL,
            ReviewResponseClass.CLIENT_ERROR,
            "pull-request response omitted author identity",
        )
    return scope_material, authenticated_login, pr_author_login


async def reconcile_existing(
    poster: DefaultGitHubReviewPoster,
    *,
    request: GitHubReviewRequest,
    operation_key: str,
    findings: tuple[_poster_support.CanonicalFinding, ...],
    attempt: ReviewAttemptRecord,
) -> GitHubReviewPostResult:
    """Reconcile a persisted review attempt against live GitHub state.

    Resumes a pending retry directly when ``attempt.state`` is ``RETRY_PENDING``.
    Otherwise re-authenticates and re-reconciles the persisted payload: returns a
    non-final result (``AMBIGUOUS`` or ``COMMITTED_PENDING_VERIFICATION``) when the
    remote review id cannot be resolved, or a final result (``SUCCEEDED`` or
    ``RECONCILED``) once reconciliation confirms the remote review.
    """
    if attempt.state == ReviewOperationState.RETRY_PENDING.value:
        return await _poster_retry.resume_pending_retry(
            poster,
            request=request,
            operation_key=operation_key,
            findings=findings,
            attempt=attempt,
        )
    authenticated = await poster.gateway.get_authenticated_user()
    authenticated_login = (
        _poster_support.login(authenticated.data) if authenticated.succeeded else None
    )
    if authenticated_login is None:
        return _poster_support.nonfinal_result(
            request,
            ReviewOperationState.AMBIGUOUS,
            authenticated.response_class,
            authenticated.error or "could not authenticate for review reconciliation",
            operation_key=operation_key,
            reconciliation=ReviewReconciliationResult.UNCERTAIN,
        )
    payload = json.loads(attempt.payload_json)
    attempted = tuple(
        finding
        for finding in findings
        if finding.canonical_index in set(attempt.canonical_indexes)
    )
    reconciliation = await poster._reconcile_payload(
        request=request,
        operation_key=operation_key,
        payload=payload,
        findings=attempted,
        authenticated_login=authenticated_login,
    )
    if reconciliation.review_id is None:
        state = (
            ReviewOperationState.COMMITTED_PENDING_VERIFICATION
            if attempt.response_class is ReviewResponseClass.SUCCESS
            else ReviewOperationState.AMBIGUOUS
        )
        poster.ledger.set_operation_state(operation_key, state)
        return _poster_support.nonfinal_result(
            request,
            state,
            attempt.response_class,
            reconciliation.error or "persisted review attempt remains unresolved",
            operation_key=operation_key,
            reconciliation=reconciliation.result,
        )
    state = (
        ReviewOperationState.SUCCEEDED
        if attempt.response_class is ReviewResponseClass.SUCCESS
        else ReviewOperationState.RECONCILED
    )
    return poster._finalize(
        request=request,
        operation_key=operation_key,
        findings=attempted,
        omitted=attempt.omitted_dispositions,
        effective_event=attempt.effective_event,
        attempt_digest=attempt.attempt_digest,
        response_class=attempt.response_class,
        state=state,
        reconciliation=reconciliation,
        executed_mutations=0,
    )


async def scan_remote_findings(
    poster: DefaultGitHubReviewPoster,
    *,
    request: GitHubReviewRequest,
    authenticated_login: str,
) -> _poster_support.RemoteFindingScan:
    """Scan the PR's existing reviews for finding markers left by ``authenticated_login``.

    Returns a ``RemoteFindingScan`` mapping each finding-marker digest to the GitHub
    comment id that carries it, for remote-side deduplication. ``ok`` is False (with
    ``error`` set) if listing reviews/comments fails, a review has an invalid id, or the
    same digest is attached to two different comment ids (ambiguous dedup).
    """
    reviews = await poster.gateway.list_reviews(request.repository, request.pr_number)
    if not reviews.succeeded or not isinstance(reviews.data, list):
        return _poster_support.RemoteFindingScan(
            False,
            {},
            reviews.error or "could not list reviews for remote deduplication",
        )
    found: dict[str, int] = {}
    for review in reviews.data:
        if (
            not isinstance(review, Mapping)
            or _poster_support.nested_string(review, "user", "login") != authenticated_login
        ):
            continue
        review_id = _poster_support.positive_int(review.get("id"))
        if review_id is None:
            return _poster_support.RemoteFindingScan(False, found, "review id was invalid")
        comments = await poster.gateway.list_review_comments(
            request.repository,
            request.pr_number,
            review_id,
        )
        if not comments.succeeded or not isinstance(comments.data, list):
            return _poster_support.RemoteFindingScan(
                False,
                found,
                comments.error or "could not list comments for remote deduplication",
            )
        for comment in comments.data:
            if not isinstance(comment, Mapping):
                continue
            digest = _poster_support.finding_marker_digest(str(comment.get("body", "")))
            comment_id = _poster_support.positive_int(comment.get("id"))
            if digest is None or comment_id is None:
                continue
            previous = found.setdefault(digest, comment_id)
            if previous != comment_id:
                return _poster_support.RemoteFindingScan(
                    False,
                    found,
                    "duplicate remote finding markers made deduplication ambiguous",
                )
    return _poster_support.RemoteFindingScan(True, found)
