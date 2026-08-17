"""Receipt-first review publication state machine and persisted pacing."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from autoskillit.core import (
    GitHubReviewFindingDisposition,
    GitHubReviewPostResult,
    GitHubReviewReceipt,
    GitHubReviewRequest,
    ReviewFindingDispositionKind,
    ReviewOperationState,
    ReviewReconciliationResult,
    ReviewResponseClass,
)
from autoskillit.execution._github_http import (
    github_error_message,
    retry_after_seconds,
)

from . import _poster_boundary, _poster_finalize, _poster_retry, _poster_support
from ._mutation_coordinator import GitHubReviewMutationCoordinator
from .canonical import (
    canonicalize_review_request,
    compute_review_operation_key,
    normalize_review_repository,
)
from .gateway import CredentialScopeMaterial, DefaultGitHubReviewGateway
from .ledger import GitHubReviewLedger, ReviewAttemptRecord


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

    async def _post(self, request: GitHubReviewRequest) -> GitHubReviewPostResult:
        request = replace(
            request,
            repository=normalize_review_repository(request.repository),
        )
        canonical_request = canonicalize_review_request(request)
        operation_key = compute_review_operation_key(request)
        findings = _poster_support.canonical_findings(request)
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
            return _poster_support.result_from_receipt(receipt, replayed=True)

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
            attempts = self.ledger.load_attempts(operation_key)
            if attempts:
                return await self._reconcile_existing(
                    request=request,
                    operation_key=operation_key,
                    findings=findings,
                    attempt=attempts[-1],
                )
            if operation.state is not ReviewOperationState.PREPARED:
                return replace(
                    base,
                    state=ReviewOperationState.AMBIGUOUS,
                    error="persisted review operation has no reconcilable attempt",
                )

        preflight = await self._preflight_new_operation(request)
        if isinstance(preflight, GitHubReviewPostResult):
            return replace(preflight, operation_key=operation_key)
        scope_material, authenticated_login, pr_author_login = preflight
        persisted_findings = tuple(
            (
                finding.canonical_index,
                finding.original_index,
                finding.digest,
                _poster_support.canonical_json(finding.wire),
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
            attempts = self.ledger.load_attempts(operation_key)
            if attempts:
                return await self._reconcile_existing(
                    request=request,
                    operation_key=operation_key,
                    findings=findings,
                    attempt=attempts[-1],
                )
        if not self.ledger.claim_operation(operation_key):
            attempts = self.ledger.load_attempts(operation_key)
            if attempts:
                return await self._reconcile_existing(
                    request=request,
                    operation_key=operation_key,
                    findings=findings,
                    attempt=attempts[-1],
                )
            return replace(
                base,
                state=ReviewOperationState.AMBIGUOUS,
                error="matching review operation is already claimed",
            )
        scope_id = self.ledger.rate_scope_id(
            credential=scope_material.credential,
            api_origin=scope_material.api_origin,
        )
        return await self._attempt(
            request=request,
            operation_key=operation_key,
            scope_id=scope_id,
            findings=findings,
            effective_event=request.event,
            attempt_number=1,
            omitted=(),
            authenticated_login=authenticated_login,
            pr_author_login=pr_author_login,
        )

    async def _preflight_new_operation(
        self,
        request: GitHubReviewRequest,
    ) -> tuple[CredentialScopeMaterial, str, str] | GitHubReviewPostResult:
        scope_material = await self.gateway.scope_material()
        authenticated = await self.gateway.get_authenticated_user()
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
        pull = await self.gateway.get_pull(request.repository, request.pr_number)
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

    async def _attempt(
        self,
        *,
        request: GitHubReviewRequest,
        operation_key: str,
        scope_id: str,
        findings: tuple[_poster_support.CanonicalFinding, ...],
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
        if resume_pending:
            if attempt.state != ReviewOperationState.RETRY_PENDING.value:
                return _poster_support.nonfinal_result(
                    request,
                    ReviewOperationState.AMBIGUOUS,
                    ReviewResponseClass.NONE,
                    "persisted review retry is no longer pending",
                    operation_key=operation_key,
                )
            if not self.ledger.claim_retry_attempt(
                operation_key=operation_key,
                attempt_number=attempt_number,
            ):
                return _poster_support.nonfinal_result(
                    request,
                    ReviewOperationState.AMBIGUOUS,
                    ReviewResponseClass.NONE,
                    "persisted review retry was claimed by another poster",
                    operation_key=operation_key,
                )
        lease_owner = secrets.token_hex(24)
        slot = await self.coordinator.acquire(
            scope_id=scope_id,
            operation_key=operation_key,
            lease_owner=lease_owner,
        )
        if slot.blocked_operation_key is not None:
            self.ledger.complete_attempt(
                operation_key=operation_key,
                attempt_number=attempt_number,
                state=ReviewOperationState.AMBIGUOUS,
                response_class=ReviewResponseClass.NONE,
                status_code=None,
                error="another unresolved review mutation blocks this rate scope",
            )
            return _poster_support.nonfinal_result(
                request,
                ReviewOperationState.AMBIGUOUS,
                ReviewResponseClass.NONE,
                "another unresolved review mutation blocks this rate scope",
                operation_key=operation_key,
            )

        response = await self.gateway.create_review(
            request.repository,
            request.pr_number,
            payload,
        )
        response_class = response.response_class
        if response.succeeded:
            self.ledger.complete_attempt(
                operation_key=operation_key,
                attempt_number=attempt_number,
                state=ReviewOperationState.COMMITTED_PENDING_VERIFICATION,
                response_class=response_class,
                status_code=response.status_code,
                error=None,
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
                    state=ReviewOperationState.SUCCEEDED,
                    reconciliation=reconciliation,
                    executed_mutations=attempt_number,
                )
                _poster_retry.release_slot(
                    self,
                    scope_id,
                    slot,
                    operation_key,
                    keep_in_flight=False,
                )
                return result
            self.ledger.set_operation_state(
                operation_key,
                ReviewOperationState.COMMITTED_PENDING_VERIFICATION,
            )
            _poster_retry.release_slot(
                self,
                scope_id,
                slot,
                operation_key,
                keep_in_flight=True,
            )
            return _poster_support.nonfinal_result(
                request,
                ReviewOperationState.COMMITTED_PENDING_VERIFICATION,
                response_class,
                reconciliation.error or "created review could not be verified",
                operation_key=operation_key,
                reconciliation=reconciliation.result,
                executed_mutations=attempt_number,
            )

        if response_class in {
            ReviewResponseClass.TRANSPORT_ERROR,
            ReviewResponseClass.SERVER_ERROR,
        }:
            self.ledger.complete_attempt(
                operation_key=operation_key,
                attempt_number=attempt_number,
                state=ReviewOperationState.AMBIGUOUS,
                response_class=response_class,
                status_code=response.status_code,
                error=response.error or github_error_message(response.data),
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
                    state=ReviewOperationState.RECONCILED,
                    reconciliation=reconciliation,
                    executed_mutations=attempt_number,
                )
                _poster_retry.release_slot(
                    self,
                    scope_id,
                    slot,
                    operation_key,
                    keep_in_flight=False,
                )
                return result
            _poster_retry.release_slot(
                self,
                scope_id,
                slot,
                operation_key,
                keep_in_flight=True,
            )
            return _poster_support.nonfinal_result(
                request,
                ReviewOperationState.AMBIGUOUS,
                response_class,
                reconciliation.error or response.error or "review mutation outcome is ambiguous",
                operation_key=operation_key,
                reconciliation=reconciliation.result,
                executed_mutations=attempt_number,
            )

        if response_class is ReviewResponseClass.SECONDARY_RATE_LIMIT:
            delay = retry_after_seconds(
                response.headers,
                wall_time=self.wall_clock(),
            )
            self.ledger.set_backoff(
                scope_id=scope_id,
                until=self.coordinator.clock() + delay,
            )
            self.ledger.complete_attempt(
                operation_key=operation_key,
                attempt_number=attempt_number,
                state=ReviewOperationState.THROTTLED,
                response_class=response_class,
                status_code=response.status_code,
                error=github_error_message(response.data) or response.error,
            )
            _poster_retry.release_slot(
                self,
                scope_id,
                slot,
                operation_key,
                keep_in_flight=False,
            )
            return _poster_support.nonfinal_result(
                request,
                ReviewOperationState.THROTTLED,
                response_class,
                github_error_message(response.data) or "GitHub secondary rate limit",
                operation_key=operation_key,
                executed_mutations=attempt_number,
            )

        invalid_index = _poster_support.structured_invalid_comment_index(response, len(findings))
        if invalid_index is not None and attempt_number == 1:
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
                    state=ReviewOperationState.RECONCILED,
                    reconciliation=reconciliation,
                    executed_mutations=attempt_number,
                )
                _poster_retry.release_slot(
                    self,
                    scope_id,
                    slot,
                    operation_key,
                    keep_in_flight=False,
                )
                return result
            if reconciliation.result is ReviewReconciliationResult.UNCERTAIN:
                self.ledger.complete_attempt(
                    operation_key=operation_key,
                    attempt_number=attempt_number,
                    state=ReviewOperationState.AMBIGUOUS,
                    response_class=response_class,
                    status_code=response.status_code,
                    error=reconciliation.error,
                )
                _poster_retry.release_slot(
                    self,
                    scope_id,
                    slot,
                    operation_key,
                    keep_in_flight=True,
                )
                return _poster_support.nonfinal_result(
                    request,
                    ReviewOperationState.AMBIGUOUS,
                    response_class,
                    reconciliation.error or "validation fallback reconciliation was uncertain",
                    operation_key=operation_key,
                    reconciliation=reconciliation.result,
                    executed_mutations=attempt_number,
                )
            scan = await self._scan_remote_findings(
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
                    self,
                    scope_id,
                    slot,
                    operation_key,
                    keep_in_flight=True,
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
            next_omitted = list(omitted)
            next_omitted.append(
                GitHubReviewFindingDisposition(
                    original_index=rejected.original_index,
                    canonical_index=rejected.canonical_index,
                    kind=ReviewFindingDispositionKind.OMITTED_INVALID,
                    reason=(
                        _poster_support.structured_error_message(response) or "invalid diff anchor"
                    ),
                )
            )
            reduced: list[_poster_support.CanonicalFinding] = []
            for finding in findings:
                if finding.canonical_index == rejected.canonical_index:
                    continue
                existing_id = scan.ids_by_digest.get(finding.digest)
                if existing_id is None:
                    reduced.append(finding)
                else:
                    next_omitted.append(
                        GitHubReviewFindingDisposition(
                            original_index=finding.original_index,
                            canonical_index=finding.canonical_index,
                            kind=ReviewFindingDispositionKind.ALREADY_PRESENT,
                            remote_comment_id=existing_id,
                        )
                    )
            if not reduced or len(reduced) >= len(findings):
                self.ledger.complete_attempt(
                    operation_key=operation_key,
                    attempt_number=attempt_number,
                    state=ReviewOperationState.TERMINAL,
                    response_class=response_class,
                    status_code=response.status_code,
                    error="validation fallback did not produce a nonempty strict subset",
                )
                _poster_retry.release_slot(
                    self,
                    scope_id,
                    slot,
                    operation_key,
                    keep_in_flight=False,
                )
                return _poster_support.nonfinal_result(
                    request,
                    ReviewOperationState.TERMINAL,
                    response_class,
                    "validation fallback did not produce a nonempty strict subset",
                    operation_key=operation_key,
                    executed_mutations=attempt_number,
                )
            next_findings = tuple(reduced)
            next_omitted_tuple = tuple(next_omitted)
            _poster_retry.schedule_retry(
                self,
                request=request,
                operation_key=operation_key,
                completed_attempt_number=attempt_number,
                response_class=response_class,
                status_code=response.status_code,
                error=_poster_support.structured_error_message(response),
                retry_attempt_number=2,
                retry_findings=next_findings,
                retry_effective_event=effective_event,
                retry_omitted=next_omitted_tuple,
            )
            _poster_retry.release_slot(
                self,
                scope_id,
                slot,
                operation_key,
                keep_in_flight=False,
            )
            return await self._attempt(
                request=request,
                operation_key=operation_key,
                scope_id=scope_id,
                findings=next_findings,
                effective_event=effective_event,
                attempt_number=2,
                omitted=next_omitted_tuple,
                authenticated_login=authenticated_login,
                pr_author_login=pr_author_login,
                resume_pending=True,
            )

        if (
            attempt_number == 1
            and effective_event == "REQUEST_CHANGES"
            and authenticated_login == pr_author_login
            and _poster_support.is_structured_self_review(response)
        ):
            _poster_retry.schedule_retry(
                self,
                request=request,
                operation_key=operation_key,
                completed_attempt_number=attempt_number,
                response_class=response_class,
                status_code=response.status_code,
                error=_poster_support.structured_error_message(response),
                retry_attempt_number=2,
                retry_findings=findings,
                retry_effective_event="COMMENT",
                retry_omitted=omitted,
            )
            _poster_retry.release_slot(
                self,
                scope_id,
                slot,
                operation_key,
                keep_in_flight=False,
            )
            return await self._attempt(
                request=request,
                operation_key=operation_key,
                scope_id=scope_id,
                findings=findings,
                effective_event="COMMENT",
                attempt_number=2,
                omitted=omitted,
                authenticated_login=authenticated_login,
                pr_author_login=pr_author_login,
                resume_pending=True,
            )

        self.ledger.complete_attempt(
            operation_key=operation_key,
            attempt_number=attempt_number,
            state=ReviewOperationState.TERMINAL,
            response_class=response_class,
            status_code=response.status_code,
            error=github_error_message(response.data) or response.error,
        )
        _poster_retry.release_slot(
            self,
            scope_id,
            slot,
            operation_key,
            keep_in_flight=False,
        )
        return _poster_support.nonfinal_result(
            request,
            ReviewOperationState.TERMINAL,
            response_class,
            github_error_message(response.data)
            or response.error
            or f"GitHub rejected review ({response.status_code})",
            operation_key=operation_key,
            executed_mutations=attempt_number,
        )

    async def _reconcile_existing(
        self,
        *,
        request: GitHubReviewRequest,
        operation_key: str,
        findings: tuple[_poster_support.CanonicalFinding, ...],
        attempt: ReviewAttemptRecord,
    ) -> GitHubReviewPostResult:
        if attempt.state == ReviewOperationState.RETRY_PENDING.value:
            return await _poster_retry.resume_pending_retry(
                self,
                request=request,
                operation_key=operation_key,
                findings=findings,
                attempt=attempt,
            )
        authenticated = await self.gateway.get_authenticated_user()
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
        reconciliation = await self._reconcile_payload(
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
            self.ledger.set_operation_state(operation_key, state)
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
        return self._finalize(
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


    async def _reconcile_payload(
        self,
        *,
        request: GitHubReviewRequest,
        operation_key: str,
        payload: Mapping[str, Any],
        findings: tuple[_poster_support.CanonicalFinding, ...],
        authenticated_login: str,
    ) -> _poster_support.Reconciliation:
        return await _poster_finalize.reconcile_payload(
            gateway=self.gateway,
            request=request,
            operation_key=operation_key,
            payload=payload,
            findings=findings,
            authenticated_login=authenticated_login,
        )

    async def _scan_remote_findings(
        self,
        *,
        request: GitHubReviewRequest,
        authenticated_login: str,
    ) -> _poster_support.RemoteFindingScan:
        reviews = await self.gateway.list_reviews(request.repository, request.pr_number)
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
            comments = await self.gateway.list_review_comments(
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

    def _finalize(
        self,
        *,
        request: GitHubReviewRequest,
        operation_key: str,
        findings: tuple[_poster_support.CanonicalFinding, ...],
        omitted: tuple[GitHubReviewFindingDisposition, ...],
        effective_event: str,
        attempt_digest: str,
        response_class: ReviewResponseClass,
        state: ReviewOperationState,
        reconciliation: _poster_support.Reconciliation,
        executed_mutations: int,
    ) -> GitHubReviewPostResult:
        return _poster_finalize.finalize(
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
