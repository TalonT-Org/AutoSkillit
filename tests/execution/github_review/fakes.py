"""Stateful GitHub review fakes with deterministic clocks and fault injection."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autoskillit.core import (
    GitHubReviewComment,
    GitHubReviewRequest,
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
        self.create_timestamps.append(self.clock())
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
            response_class=_classify(outcome.status_code),
            error=outcome.error,
        )

    async def list_reviews(self, repository: str, pr_number: int) -> GatewayResult:
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


def _classify(status_code: int | None) -> ReviewResponseClass:
    if status_code is None:
        return ReviewResponseClass.TRANSPORT_ERROR
    if 200 <= status_code < 300:
        return ReviewResponseClass.SUCCESS
    if status_code >= 500:
        return ReviewResponseClass.SERVER_ERROR
    if status_code in {403, 429}:
        return ReviewResponseClass.SECONDARY_RATE_LIMIT
    if status_code == 422:
        return ReviewResponseClass.VALIDATION_ERROR
    return ReviewResponseClass.CLIENT_ERROR


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
