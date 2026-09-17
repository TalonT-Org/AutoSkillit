"""Receipt-first review publication state machine and persisted pacing."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from enum import StrEnum, auto
from typing import Any, assert_never

from autoskillit.core import (
    AdmittedAnchor,
    GitHubReviewFindingDisposition,
    GitHubReviewPostResult,
    GitHubReviewReceipt,
    GitHubReviewRequest,
    ReviewFindingDispositionKind,
    ReviewOperationState,
    ReviewReconciliationResult,
    ReviewResponseClass,
)
from autoskillit.execution.github_ops._github_http import (
    github_error_message,
    retry_after_seconds,
)

from . import (
    _poster_boundary,
    _poster_post_attempt,
    _poster_reconcile,
    _poster_retry,
    _poster_support,
)
from ._mutation_coordinator import GitHubReviewMutationCoordinator
from .canonical import (
    AdmittedFinding,
    admit_findings,
    canonical_findings,
    canonicalize_review_request,
    compute_review_operation_key,
    normalize_review_repository,
)
from .gateway import CredentialScopeMaterial, DefaultGitHubReviewGateway, GatewayResult
from .ledger import GitHubReviewLedger


class ReviewAttemptOutcome(StrEnum):
    RETRY_STALE = auto()
    RETRY_CONTENDED = auto()
    SLOT_BLOCKED = auto()
    SUCCESS = auto()
    TRANSPORT_ERROR = auto()
    SERVER_ERROR = auto()
    SECONDARY_RATE_LIMIT = auto()
    ANCHOR_422 = auto()
    SELF_REVIEW_422 = auto()
    COMBINED_422 = auto()
    GENERIC_422 = auto()
    SECOND_422 = auto()
    CLIENT_ERROR = auto()


def classify_attempt_outcome(
    response: GatewayResult,
    *,
    attempt_number: int,
    comment_count: int,
    effective_event: str,
    authenticated_login: str,
    pr_author_login: str,
) -> tuple[ReviewAttemptOutcome, int | None]:
    if response.succeeded:
        return ReviewAttemptOutcome.SUCCESS, None
    if response.response_class is ReviewResponseClass.TRANSPORT_ERROR:
        return ReviewAttemptOutcome.TRANSPORT_ERROR, None
    if response.response_class is ReviewResponseClass.SERVER_ERROR:
        return ReviewAttemptOutcome.SERVER_ERROR, None
    if response.response_class is ReviewResponseClass.SECONDARY_RATE_LIMIT:
        return ReviewAttemptOutcome.SECONDARY_RATE_LIMIT, None
    if response.status_code == 422:
        if attempt_number != 1:
            return ReviewAttemptOutcome.SECOND_422, None
        invalid_index = _poster_support.structured_invalid_comment_index(response, comment_count)
        self_review = (
            effective_event == "REQUEST_CHANGES"
            and authenticated_login == pr_author_login
            and _poster_support.is_structured_self_review(response)
        )
        if invalid_index is not None:
            return (
                ReviewAttemptOutcome.COMBINED_422
                if self_review
                else ReviewAttemptOutcome.ANCHOR_422
            ), invalid_index
        if self_review:
            # A second error whose anchor is not identifiable must not cause a full-set retry.
            errors = response.data.get("errors", []) if isinstance(response.data, Mapping) else []
            if len(errors) == 1:
                return ReviewAttemptOutcome.SELF_REVIEW_422, None
        return ReviewAttemptOutcome.GENERIC_422, None
    return ReviewAttemptOutcome.CLIENT_ERROR, None


class DefaultGitHubReviewPoster:
    def __init__(
        self,
        *,
        ledger: GitHubReviewLedger,
        coordinator: GitHubReviewMutationCoordinator,
        gateway: DefaultGitHubReviewGateway,
        review_comment_cap: int = 50,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self.ledger = ledger
        self.coordinator = coordinator
        self.gateway = gateway
        self.review_comment_cap = review_comment_cap
        self.wall_clock = wall_clock
        self._instance_lock = asyncio.Lock()

    async def post(self, request: GitHubReviewRequest) -> GitHubReviewPostResult:
        return await _poster_boundary.post(self, request)

    def verify_receipt(self, operation_key: str) -> GitHubReviewReceipt | None:
        return self.ledger.load_receipt(operation_key)

    async def _post(self, request: GitHubReviewRequest) -> GitHubReviewPostResult:
        request = replace(
            request,
            repository=normalize_review_repository(request.repository),
        )
        canonical_request = canonicalize_review_request(request)
        operation_key = compute_review_operation_key(request)
        admission = admit_findings(canonical_findings(request), request.anchor_authority)
        findings = admission.admitted
        omitted = tuple(
            GitHubReviewFindingDisposition(
                original_index=item.finding.original_index,
                canonical_index=item.finding.canonical_index,
                kind=ReviewFindingDispositionKind.OMITTED_INVALID,
                reason=item.reason.value,
            )
            for item in admission.rejected
        )
        base = GitHubReviewPostResult(
            operation_key=operation_key,
            head_sha=request.head_sha,
            state=ReviewOperationState.PREPARED,
            planned_mutation_count=1,
            planned_comment_count=len(findings),
        )
        if len(findings) > self.review_comment_cap:
            return replace(
                base,
                state=ReviewOperationState.TERMINAL,
                error=(
                    f"review comment cap exceeded: {len(findings)} > {self.review_comment_cap}"
                ),
            )
        if request.dry_run:
            return replace(base, state=ReviewOperationState.DRY_RUN)

        receipt = self.ledger.load_receipt(operation_key)
        if receipt is not None:
            return replace(
                _poster_support.result_from_receipt(receipt, replayed=True),
                planned_comment_count=len(findings),
            )

        operation = self.ledger.load_operation(operation_key)
        if operation is not None:
            if (
                operation.request_digest != hashlib.sha256(canonical_request).hexdigest()
                or operation.request_json != canonical_request
            ):
                return replace(
                    base,
                    state=ReviewOperationState.TERMINAL,
                    error="review operation identity conflicts with persisted intent",
                )
            reconciled = await _poster_reconcile.reconcile_latest_attempt(
                self,
                request=request,
                operation_key=operation_key,
                findings=findings,
            )
            if reconciled is not None:
                return replace(reconciled, planned_comment_count=len(findings))
            if operation.state is not ReviewOperationState.PREPARED:
                return replace(
                    base,
                    state=ReviewOperationState.AMBIGUOUS,
                    error="persisted review operation has no reconcilable attempt",
                )

        preflight = await self._preflight_new_operation(request)
        if isinstance(preflight, GitHubReviewPostResult):
            return replace(
                preflight, operation_key=operation_key, planned_comment_count=len(findings)
            )
        scope_material, authenticated_login, pr_author_login = preflight
        persisted_findings = tuple(
            (
                finding.canonical_index,
                finding.original_index,
                finding.digest,
                _poster_support.canonical_json(dict(finding.wire)),
            )
            for finding in findings
        )
        state = self.ledger.prepare(
            operation_key=operation_key,
            request_digest=hashlib.sha256(canonical_request).hexdigest(),
            request_json=canonical_request,
            findings=persisted_findings,
        )
        if state is not ReviewOperationState.PREPARED:
            reconciled = await _poster_reconcile.reconcile_latest_attempt(
                self,
                request=request,
                operation_key=operation_key,
                findings=findings,
            )
            if reconciled is not None:
                return replace(reconciled, planned_comment_count=len(findings))
        if not self.ledger.claim_operation(operation_key):
            reconciled = await _poster_reconcile.reconcile_latest_attempt(
                self,
                request=request,
                operation_key=operation_key,
                findings=findings,
            )
            if reconciled is not None:
                return replace(reconciled, planned_comment_count=len(findings))
            return replace(
                base,
                state=ReviewOperationState.AMBIGUOUS,
                error="matching review operation is already claimed",
            )
        scope_id = self.ledger.rate_scope_id(
            credential=scope_material.credential,
            api_origin=scope_material.api_origin,
        )
        result = await self._attempt(
            request=request,
            operation_key=operation_key,
            scope_id=scope_id,
            findings=findings,
            effective_event=request.event,
            attempt_number=1,
            omitted=omitted,
            authenticated_login=authenticated_login,
            pr_author_login=pr_author_login,
        )
        return replace(result, planned_comment_count=len(findings))

    async def _preflight_new_operation(
        self,
        request: GitHubReviewRequest,
    ) -> tuple[CredentialScopeMaterial, str, str] | GitHubReviewPostResult:
        return await _poster_reconcile.preflight_new_operation(self, request)

    async def _attempt(
        self,
        *,
        request: GitHubReviewRequest,
        operation_key: str,
        scope_id: str,
        findings: tuple[AdmittedFinding, ...],
        effective_event: str,
        attempt_number: int,
        omitted: tuple[GitHubReviewFindingDisposition, ...],
        authenticated_login: str,
        pr_author_login: str,
        resume_pending: bool = False,
    ) -> GitHubReviewPostResult:
        payload, payload_json, attempt_digest = _poster_retry.attempt_material(
            request=request,
            operation_key=operation_key,
            findings=findings,
            omitted=omitted,
            effective_event=effective_event,
        )
        attempt = self.ledger.begin_attempt(
            operation_key=operation_key,
            attempt_number=attempt_number,
            attempt_digest=attempt_digest,
            payload_json=payload_json,
            canonical_indexes=tuple(item.canonical_index for item in findings),
            omitted_dispositions=omitted,
            effective_event=effective_event,
            effective_body_digest=_poster_support.text_digest(str(payload["body"])),
        )
        outcome: ReviewAttemptOutcome | None = None
        if resume_pending:
            if attempt.state != ReviewOperationState.RETRY_PENDING.value:
                outcome = ReviewAttemptOutcome.RETRY_STALE
            elif not self.ledger.claim_retry_attempt(
                operation_key=operation_key,
                attempt_number=attempt_number,
            ):
                outcome = ReviewAttemptOutcome.RETRY_CONTENDED
        slot = None
        response = None
        invalid_index = None
        if outcome is None:
            slot = await self.coordinator.acquire(
                scope_id=scope_id,
                operation_key=operation_key,
                lease_owner=secrets.token_hex(24),
            )
            if slot.blocked_operation_key is not None:
                outcome = ReviewAttemptOutcome.SLOT_BLOCKED
            else:
                assert all(
                    isinstance(finding.anchor, AdmittedAnchor)
                    and finding.anchor.authority_digest
                    == request.anchor_authority.authority_digest
                    for finding in findings
                )
                response = await self.gateway.create_review(
                    request.repository,
                    request.pr_number,
                    payload,
                )
                outcome, invalid_index = classify_attempt_outcome(
                    response,
                    attempt_number=attempt_number,
                    comment_count=len(findings),
                    effective_event=effective_event,
                    authenticated_login=authenticated_login,
                    pr_author_login=pr_author_login,
                )
        retry = False
        next_event = effective_event
        match outcome:
            case (
                ReviewAttemptOutcome.RETRY_STALE
                | ReviewAttemptOutcome.RETRY_CONTENDED
                | ReviewAttemptOutcome.SLOT_BLOCKED
            ):
                guard_error = {
                    ReviewAttemptOutcome.RETRY_STALE: (
                        "persisted review retry is no longer pending"
                    ),
                    ReviewAttemptOutcome.RETRY_CONTENDED: (
                        "persisted review retry was claimed by another poster"
                    ),
                    ReviewAttemptOutcome.SLOT_BLOCKED: (
                        "another unresolved review mutation blocks this rate scope"
                    ),
                }[outcome]
                if outcome is ReviewAttemptOutcome.SLOT_BLOCKED:
                    self.ledger.complete_attempt(
                        operation_key=operation_key,
                        attempt_number=attempt_number,
                        state=ReviewOperationState.AMBIGUOUS,
                        response_class=ReviewResponseClass.NONE,
                        status_code=None,
                        error=guard_error,
                    )
                return _poster_support.nonfinal_result(
                    request,
                    ReviewOperationState.AMBIGUOUS,
                    ReviewResponseClass.NONE,
                    guard_error,
                    operation_key=operation_key,
                )
            case ReviewAttemptOutcome.SECONDARY_RATE_LIMIT:
                assert response is not None and slot is not None
                delay = retry_after_seconds(response.headers, wall_time=self.wall_clock())
                self.ledger.set_backoff(scope_id=scope_id, until=self.coordinator.clock() + delay)
                self.ledger.complete_attempt(
                    operation_key=operation_key,
                    attempt_number=attempt_number,
                    state=ReviewOperationState.THROTTLED,
                    response_class=response.response_class,
                    status_code=response.status_code,
                    error=github_error_message(response.data) or response.error,
                )
                _poster_retry.release_slot(
                    self, scope_id, slot, operation_key, keep_in_flight=False
                )
                return _poster_support.nonfinal_result(
                    request,
                    ReviewOperationState.THROTTLED,
                    response.response_class,
                    github_error_message(response.data) or "GitHub secondary rate limit",
                    operation_key=operation_key,
                    executed_mutations=attempt_number,
                )
            case ReviewAttemptOutcome.SUCCESS:
                attempt_state = ReviewOperationState.COMMITTED_PENDING_VERIFICATION
            case ReviewAttemptOutcome.TRANSPORT_ERROR | ReviewAttemptOutcome.SERVER_ERROR:
                attempt_state = ReviewOperationState.AMBIGUOUS
            case (
                ReviewAttemptOutcome.ANCHOR_422
                | ReviewAttemptOutcome.SELF_REVIEW_422
                | ReviewAttemptOutcome.COMBINED_422
            ):
                attempt_state = ReviewOperationState.AMBIGUOUS
                retry = True
                if outcome in {
                    ReviewAttemptOutcome.SELF_REVIEW_422,
                    ReviewAttemptOutcome.COMBINED_422,
                }:
                    next_event = "COMMENT"
            case (
                ReviewAttemptOutcome.GENERIC_422
                | ReviewAttemptOutcome.SECOND_422
                | ReviewAttemptOutcome.CLIENT_ERROR
            ):
                attempt_state = ReviewOperationState.AMBIGUOUS
            case _:
                assert_never(outcome)
        assert response is not None and slot is not None
        response_class = response.response_class
        error = github_error_message(response.data) or response.error
        if not retry:
            self.ledger.complete_attempt(
                operation_key=operation_key,
                attempt_number=attempt_number,
                state=attempt_state,
                response_class=response_class,
                status_code=response.status_code,
                error=error,
            )
        reconciliation = await self._reconcile_payload(
            request=request,
            operation_key=operation_key,
            payload=payload,
            findings=findings,
            authenticated_login=authenticated_login,
        )
        if reconciliation.review_id is not None:
            result = self._finalize(
                request=request,
                operation_key=operation_key,
                findings=findings,
                omitted=omitted,
                effective_event=effective_event,
                attempt_digest=attempt_digest,
                response_class=response_class,
                state=(
                    ReviewOperationState.SUCCEEDED
                    if outcome is ReviewAttemptOutcome.SUCCESS
                    else ReviewOperationState.RECONCILED
                ),
                reconciliation=reconciliation,
                executed_mutations=attempt_number,
            )
            _poster_retry.release_slot(self, scope_id, slot, operation_key, keep_in_flight=False)
            return result
        uncertain = reconciliation.result is ReviewReconciliationResult.UNCERTAIN
        if uncertain or outcome in {
            ReviewAttemptOutcome.SUCCESS,
            ReviewAttemptOutcome.TRANSPORT_ERROR,
            ReviewAttemptOutcome.SERVER_ERROR,
        }:
            self.ledger.complete_attempt(
                operation_key=operation_key,
                attempt_number=attempt_number,
                state=attempt_state,
                response_class=response_class,
                status_code=response.status_code,
                error=reconciliation.error or error,
            )
            _poster_retry.release_slot(self, scope_id, slot, operation_key, keep_in_flight=True)
            return _poster_support.nonfinal_result(
                request,
                attempt_state,
                response_class,
                reconciliation.error or error or "review mutation outcome is ambiguous",
                operation_key=operation_key,
                reconciliation=reconciliation.result,
                executed_mutations=attempt_number,
            )
        if not retry:
            self.ledger.complete_attempt(
                operation_key=operation_key,
                attempt_number=attempt_number,
                state=ReviewOperationState.TERMINAL,
                response_class=response_class,
                status_code=response.status_code,
                error=error,
            )
            _poster_retry.release_slot(self, scope_id, slot, operation_key, keep_in_flight=False)
            return _poster_support.nonfinal_result(
                request,
                ReviewOperationState.TERMINAL,
                response_class,
                error or f"GitHub rejected review ({response.status_code})",
                operation_key=operation_key,
                reconciliation=reconciliation.result,
                executed_mutations=attempt_number,
            )
        next_findings = findings
        next_omitted = list(omitted)
        if invalid_index is not None:
            scan = await _poster_reconcile.scan_remote_findings(
                self,
                request=request,
                authenticated_login=authenticated_login,
            )
            if not scan.certain:
                self.ledger.complete_attempt(
                    operation_key=operation_key,
                    attempt_number=attempt_number,
                    state=ReviewOperationState.AMBIGUOUS,
                    response_class=response_class,
                    status_code=response.status_code,
                    error=scan.error,
                )
                _poster_retry.release_slot(
                    self, scope_id, slot, operation_key, keep_in_flight=True
                )
                return _poster_support.nonfinal_result(
                    request,
                    ReviewOperationState.AMBIGUOUS,
                    response_class,
                    scan.error or "remote finding deduplication was uncertain",
                    operation_key=operation_key,
                    executed_mutations=attempt_number,
                )
            rejected = findings[invalid_index]
            reduced: list[AdmittedFinding] = []
            for finding in findings:
                if finding.canonical_index == rejected.canonical_index:
                    next_omitted.append(
                        GitHubReviewFindingDisposition(
                            original_index=finding.original_index,
                            canonical_index=finding.canonical_index,
                            kind=ReviewFindingDispositionKind.OMITTED_INVALID,
                            reason=_poster_support.structured_error_message(response)
                            or "invalid diff anchor",
                        )
                    )
                elif (remote_id := scan.ids_by_digest.get(finding.digest)) is not None:
                    next_omitted.append(
                        GitHubReviewFindingDisposition(
                            original_index=finding.original_index,
                            canonical_index=finding.canonical_index,
                            kind=ReviewFindingDispositionKind.ALREADY_PRESENT,
                            remote_comment_id=remote_id,
                        )
                    )
                else:
                    reduced.append(finding)
            assert len(reduced) < len(findings)
            next_findings = tuple(reduced)
            if not next_findings:
                next_omitted = list(omitted) + [
                    GitHubReviewFindingDisposition(
                        original_index=finding.original_index,
                        canonical_index=finding.canonical_index,
                        kind=ReviewFindingDispositionKind.OMITTED_INVALID,
                        reason="validation correction exhausted inline findings",
                    )
                    for finding in findings
                ]
        next_omitted_tuple = tuple(next_omitted)
        _poster_retry.schedule_retry(
            self,
            request=request,
            operation_key=operation_key,
            completed_attempt_number=attempt_number,
            response_class=response_class,
            status_code=response.status_code,
            error=error,
            retry_attempt_number=2,
            retry_findings=next_findings,
            retry_effective_event=next_event,
            retry_omitted=next_omitted_tuple,
        )
        _poster_retry.release_slot(self, scope_id, slot, operation_key, keep_in_flight=False)
        return await self._attempt(
            request=request,
            operation_key=operation_key,
            scope_id=scope_id,
            findings=next_findings,
            effective_event=next_event,
            attempt_number=2,
            omitted=next_omitted_tuple,
            authenticated_login=authenticated_login,
            pr_author_login=pr_author_login,
            resume_pending=True,
        )

    async def _reconcile_payload(
        self,
        *,
        request: GitHubReviewRequest,
        operation_key: str,
        payload: Mapping[str, Any],
        findings: tuple[AdmittedFinding, ...],
        authenticated_login: str,
    ) -> _poster_support.Reconciliation:
        return await _poster_post_attempt.reconcile_payload(
            gateway=self.gateway,
            request=request,
            operation_key=operation_key,
            payload=payload,
            findings=findings,
            authenticated_login=authenticated_login,
        )

    def _finalize(
        self,
        *,
        request: GitHubReviewRequest,
        operation_key: str,
        findings: tuple[AdmittedFinding, ...],
        omitted: tuple[GitHubReviewFindingDisposition, ...],
        effective_event: str,
        attempt_digest: str,
        response_class: ReviewResponseClass,
        state: ReviewOperationState,
        reconciliation: _poster_support.Reconciliation,
        executed_mutations: int,
    ) -> GitHubReviewPostResult:
        return _poster_post_attempt.finalize(
            ledger=self.ledger,
            wall_clock=self.wall_clock,
            request=request,
            operation_key=operation_key,
            findings=findings,
            omitted=omitted,
            effective_event=effective_event,
            attempt_digest=attempt_digest,
            response_class=response_class,
            state=state,
            reconciliation=reconciliation,
            executed_mutations=executed_mutations,
        )
