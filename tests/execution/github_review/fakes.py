"""Stateful GitHub review fakes with deterministic clocks and fault injection."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from autoskillit.core import (
    DiffAnchorAuthority,
    GitHubReviewComment,
    GitHubReviewReceipt,
    GitHubReviewRequest,
    ReviewOperationState,
    ReviewReconciliationResult,
    ReviewResponseClass,
)
from autoskillit.execution import (
    DefaultGitHubReviewPoster,
    GitHubReviewLedger,
    GitHubReviewMutationCoordinator,
)
from autoskillit.execution.github_review.gateway import (
    CredentialScopeMaterial,
    GatewayResult,
    _classify_response,
)
from tests.fixtures.github_rest import (
    reviews_create_422_author_approve,
    reviews_create_422_author_request_changes,
)

_REMOTE_STATES = {
    "APPROVE": "APPROVED",
    "REQUEST_CHANGES": "CHANGES_REQUESTED",
    "COMMENT": "COMMENTED",
}


class ManualClock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class AdvancingSleeper:
    def __init__(self, clock: ManualClock) -> None:
        self.clock = clock
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)
        self.clock.advance(seconds)
        await asyncio.sleep(0)


@dataclass(frozen=True, slots=True)
class CreateOutcome:
    status_code: int | None
    data: Any = None
    headers: dict[str, str] | None = None
    error: str | None = None
    commit: bool = False
    fail_reads_after: bool = False


class StatefulReviewGateway:
    def __init__(
        self,
        *,
        clock: ManualClock,
        outcomes: list[CreateOutcome] | None = None,
        head_sha: str = "a" * 40,
        authenticated_login: str = "autoskillit-bot",
        pr_author_login: str = "pull-author",
    ) -> None:
        self.clock = clock
        self.outcomes = list(outcomes or [CreateOutcome(200, commit=True)])
        self.head_sha = head_sha
        self.authenticated_login = authenticated_login
        self.pr_author_login = pr_author_login
        self.reviews: list[dict[str, Any]] = []
        self.comments_by_review: dict[int, list[dict[str, Any]]] = {}
        self.create_calls: list[dict[str, Any]] = []
        self.call_trace: list[str] = []
        self.create_timestamps: list[float] = []
        self.scope_calls = 0
        self.authenticated_user_calls = 0
        self.pull_calls = 0
        self.review_read_calls = 0
        self.comment_read_calls = 0
        self.fail_reads = False
        self._next_review_id = 700
        self._next_comment_id = 900

    async def scope_material(self) -> CredentialScopeMaterial:
        self.scope_calls += 1
        return CredentialScopeMaterial(
            credential="secret-token",
            api_origin="https://api.github.com:443",
        )

    async def get_authenticated_user(self) -> GatewayResult:
        self.authenticated_user_calls += 1
        return _success({"login": self.authenticated_login})

    async def get_pull(self, repository: str, pr_number: int) -> GatewayResult:
        del repository, pr_number
        self.pull_calls += 1
        return _success(
            {
                "head": {"sha": self.head_sha},
                "user": {"login": self.pr_author_login},
            }
        )

    async def create_review(
        self,
        repository: str,
        pr_number: int,
        payload: dict[str, Any],
    ) -> GatewayResult:
        del repository, pr_number
        self.create_calls.append(payload)
        self.call_trace.append("create_review")
        self.create_timestamps.append(self.clock())
        if self.authenticated_login.casefold() == self.pr_author_login.casefold() and payload[
            "event"
        ] in {"APPROVE", "REQUEST_CHANGES"}:
            body = (
                reviews_create_422_author_approve()
                if payload["event"] == "APPROVE"
                else reviews_create_422_author_request_changes()
            )
            return GatewayResult(
                422, data=body, headers={}, response_class=_classify_response(422, body, {})
            )
        outcome = self.outcomes.pop(0)
        review_id = self._commit(payload) if outcome.commit else None
        if outcome.fail_reads_after:
            self.fail_reads = True
        data = outcome.data
        if data is None and outcome.status_code is not None and 200 <= outcome.status_code < 300:
            data = {"id": review_id}
        return GatewayResult(
            status_code=outcome.status_code,
            data=data,
            headers=outcome.headers or {},
            response_class=(
                ReviewResponseClass.TRANSPORT_ERROR
                if outcome.status_code is None
                else _classify_response(outcome.status_code, data, outcome.headers or {})
            ),
            error=outcome.error,
        )

    async def list_reviews(self, repository: str, pr_number: int) -> GatewayResult:
        self.call_trace.append("list_reviews")
        del repository, pr_number
        self.review_read_calls += 1
        if self.fail_reads:
            return GatewayResult(
                status_code=None,
                data=None,
                headers={},
                response_class=ReviewResponseClass.TRANSPORT_ERROR,
                error="injected review read failure",
            )
        return _success(list(self.reviews))

    async def list_review_comments(
        self,
        repository: str,
        pr_number: int,
        review_id: int,
    ) -> GatewayResult:
        del repository, pr_number
        self.comment_read_calls += 1
        if self.fail_reads:
            return GatewayResult(
                status_code=None,
                data=None,
                headers={},
                response_class=ReviewResponseClass.TRANSPORT_ERROR,
                error="injected comment read failure",
            )
        return _success(list(self.comments_by_review.get(review_id, [])))

    async def create_review_comment(
        self,
        repository: str,
        pr_number: int,
        body: str,
        commit_id: str,
        path: str,
        line: int,
        side: str = "RIGHT",
    ) -> GatewayResult:
        del repository, pr_number, body, commit_id, path, line, side
        self.call_trace.append("create_review_comment")
        raise AssertionError(
            "review poster must publish via batched create_review; "
            "create_review_comment is the per-comment fallback that this fixture forbids"
        )

    def _commit(self, payload: dict[str, Any]) -> int:
        review_id = self._next_review_id
        self._next_review_id += 1
        self.reviews.append(
            {
                "id": review_id,
                "body": payload["body"],
                "commit_id": payload["commit_id"],
                "state": _REMOTE_STATES[payload["event"]],
                "user": {"login": self.authenticated_login},
            }
        )
        remote_comments: list[dict[str, Any]] = []
        for comment in payload["comments"]:
            remote_comments.append(
                {
                    **comment,
                    "id": self._next_comment_id,
                }
            )
            self._next_comment_id += 1
        self.comments_by_review[review_id] = remote_comments
        return review_id


def _success(data: Any) -> GatewayResult:
    return GatewayResult(
        status_code=200,
        data=data,
        headers={},
        response_class=ReviewResponseClass.SUCCESS,
    )


def _request(tmp_path: Path, **overrides: object) -> GitHubReviewRequest:
    values: dict[str, object] = {
        "repository": "octo/example",
        "pr_number": 42,
        "head_sha": "a" * 40,
        "logical_iteration": "review-pr:2",
        "event": "COMMENT",
        "body": "Automated review",
        "comments": (
            GitHubReviewComment(
                path="src/example.py",
                line=17,
                body="Normalize this value.",
            ),
        ),
        "receipt_path": tmp_path / "receipts" / "review.json",
    }
    values.update(overrides)
    if "anchor_authority" not in values:
        right: dict[str, set[int]] = {}
        left: dict[str, set[int]] = {}
        for comment in values["comments"]:
            lines = right if comment.side == "RIGHT" else left
            lines.setdefault(comment.path, set()).update(
                range(comment.start_line or comment.line, comment.line + 1)
            )
        values["anchor_authority"] = DiffAnchorAuthority.authoritative(
            repository=str(values["repository"]).casefold(),
            pr_number=values["pr_number"],
            head_sha=values["head_sha"],
            generation_id="test-generation",
            right_side_lines=right,
            left_side_lines=left,
        )
    return GitHubReviewRequest(**values)


def _poster(
    database_path: Path,
    gateway: StatefulReviewGateway,
    clock: ManualClock,
    *,
    review_comment_cap: int = 50,
) -> DefaultGitHubReviewPoster:
    ledger = GitHubReviewLedger(database_path)
    return DefaultGitHubReviewPoster(
        ledger=ledger,
        coordinator=GitHubReviewMutationCoordinator(
            ledger=ledger,
            clock=clock,
            sleeper=AdvancingSleeper(clock),
            minimum_interval_seconds=1.0,
            lease_ttl_seconds=60.0,
        ),
        gateway=gateway,
        review_comment_cap=review_comment_cap,
        wall_clock=clock,
    )


def authoritative_receipt(**overrides: Any) -> GitHubReviewReceipt:
    """Return the canonical server receipt used by verifier and publisher tests."""
    receipt = GitHubReviewReceipt(
        schema_version=1,
        operation_key="f" * 64,
        repository="owner/repo",
        pr_number=7,
        head_sha="a" * 40,
        logical_iteration="review-pr:1",
        requested_event="COMMENT",
        effective_event="COMMENT",
        requested_body_digest="b" * 64,
        effective_body_digest="b" * 64,
        canonical_finding_digest="c" * 64,
        state=ReviewOperationState.SUCCEEDED,
        response_class=ReviewResponseClass.SUCCESS,
        review_id=901,
        comment_ids=(),
        canonical_finding_count=0,
        reconciliation_result=ReviewReconciliationResult.NOT_NEEDED,
        finding_dispositions=(),
        created_at=1.0,
        updated_at=2.0,
        final_attempt_digest="d" * 64,
    )
    return replace(receipt, **overrides)
