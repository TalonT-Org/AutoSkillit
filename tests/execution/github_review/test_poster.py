"""Happy-path, dry-run, replay, and verification behavior for the review poster."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from autoskillit.core import (
    GitHubReviewComment,
    ReviewFindingDispositionKind,
    ReviewOperationState,
)
from autoskillit.execution import GitHubReviewLedger
from tests.fixtures.github_rest import (
    pulls_comments_outdated_right,
    pulls_comments_relocated_left,
    pulls_comments_relocated_right,
)

from .fakes import (
    CreateOutcome,
    ManualClock,
    StatefulReviewGateway,
    _poster,
    _request,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "capture",
    [pulls_comments_relocated_right, pulls_comments_outdated_right, pulls_comments_relocated_left],
)
async def test_recovery_uses_observed_original_anchor(
    tmp_path: Path, capture: Callable[[], dict[str, Any]]
) -> None:
    remote = capture()
    request = _request(
        tmp_path,
        head_sha=remote["original_commit_id"],
        comments=(
            GitHubReviewComment(
                path=remote["path"],
                line=remote["original_line"],
                side=remote["side"],
                start_line=remote["original_start_line"],
                start_side=remote["start_side"],
                body="Finding",
            ),
        ),
    )
    clock = ManualClock()
    gateway = StatefulReviewGateway(
        clock=clock,
        head_sha=request.head_sha,
        outcomes=[CreateOutcome(200, commit=True, fail_reads_after=True)],
    )
    gateway._next_review_id = int(remote["pull_request_review_id"])
    gateway._next_comment_id = int(remote["id"])
    poster = _poster(tmp_path / "ledger.sqlite3", gateway, clock)
    first = await poster.post(request)
    assert first.state is ReviewOperationState.COMMITTED_PENDING_VERIFICATION
    assert first.receipt is None
    # Only the finding body is synthetic; captured coordinates remain intact.
    remote["body"] = gateway.create_calls[0]["comments"][0]["body"]
    gateway.comments_by_review[int(remote["pull_request_review_id"])] = [remote]
    gateway.head_sha = str(remote["commit_id"])
    gateway.fail_reads = False
    pull_calls = gateway.pull_calls

    recovered = await poster.post(request)

    assert recovered.state is ReviewOperationState.SUCCEEDED
    assert recovered.operation_key == first.operation_key
    assert recovered.comment_ids == (remote["id"],)
    assert recovered.receipt is not None
    assert recovered.receipt.head_sha == request.head_sha
    assert recovered.executed_mutation_count == 0
    assert len(gateway.create_calls) == 1
    assert gateway.pull_calls == pull_calls


@pytest.mark.anyio
@pytest.mark.parametrize(
    "capture,field,value",
    [
        (pulls_comments_relocated_right, "original_commit_id", "b" * 40),
        (pulls_comments_relocated_right, "original_line", None),
        (pulls_comments_relocated_right, "original_line", True),
        (pulls_comments_relocated_right, "original_start_line", None),
        (pulls_comments_relocated_right, "original_start_line", 123),
        (pulls_comments_relocated_right, "side", None),
        (pulls_comments_relocated_right, "side", "LEFT"),
        (pulls_comments_relocated_right, "start_side", "RIGHT"),
        (pulls_comments_relocated_left, "original_start_line", None),
        (pulls_comments_relocated_left, "original_start_line", 12),
        (pulls_comments_relocated_left, "original_start_line", False),
        (pulls_comments_relocated_left, "start_side", None),
        (pulls_comments_relocated_left, "start_side", "RIGHT"),
    ],
)
async def test_incomplete_historical_anchor_stays_pending(
    tmp_path: Path,
    capture: Callable[[], dict[str, Any]],
    field: str,
    value: object,
) -> None:
    remote = capture()
    request = _request(
        tmp_path,
        head_sha=remote["original_commit_id"],
        comments=(
            GitHubReviewComment(
                path=remote["path"],
                line=remote["original_line"],
                side=remote["side"],
                start_line=remote["original_start_line"],
                start_side=remote["start_side"],
                body="Finding",
            ),
        ),
    )
    clock = ManualClock()
    gateway = StatefulReviewGateway(
        clock=clock,
        head_sha=request.head_sha,
        outcomes=[CreateOutcome(200, commit=True, fail_reads_after=True)],
    )
    poster = _poster(tmp_path / "ledger.sqlite3", gateway, clock)
    first = await poster.post(request)
    # Synthetic negatives make current coordinates coincide with the submission.
    remote.update(
        body=gateway.create_calls[0]["comments"][0]["body"],
        pull_request_review_id=700,
        line=remote["original_line"],
        start_line=remote["original_start_line"],
    )
    if value is None:
        del remote[field]
    else:
        remote[field] = value
    gateway.comments_by_review[700] = [remote]
    gateway.fail_reads = False

    recovered = await poster.post(request)

    assert recovered.state is ReviewOperationState.COMMITTED_PENDING_VERIFICATION
    assert recovered.operation_key == first.operation_key
    assert recovered.receipt is None
    assert recovered.executed_mutation_count == 0
    assert len(gateway.create_calls) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("unscopable_marker", [False, True])
async def test_exact_readback_scopes_review_before_marker_accounting(
    tmp_path: Path, unscopable_marker: bool
) -> None:
    clock = ManualClock()
    gateway = StatefulReviewGateway(
        clock=clock, outcomes=[CreateOutcome(200, commit=True, fail_reads_after=True)]
    )
    poster = _poster(tmp_path / "ledger.sqlite3", gateway, clock)
    request = _request(tmp_path)
    first = await poster.post(request)
    remote = gateway.comments_by_review[700][0]
    remote["pull_request_review_id"] = 700
    # Synthetic associations isolate scoping from otherwise identical anchors.
    gateway.comments_by_review[700].extend(
        [
            {**remote, "id": 901, "pull_request_review_id": 701},
            {"id": 902, "pull_request_review_id": None, "body": "Unrelated"},
        ]
    )
    if unscopable_marker:
        gateway.comments_by_review[700].append(
            {"id": 903, "pull_request_review_id": None, "body": remote["body"]}
        )
    gateway.fail_reads = False

    recovered = await poster.post(request)

    assert recovered.operation_key == first.operation_key
    assert len(gateway.create_calls) == 1
    if unscopable_marker:
        assert recovered.state is ReviewOperationState.COMMITTED_PENDING_VERIFICATION
        assert recovered.receipt is None
    else:
        assert recovered.state is ReviewOperationState.SUCCEEDED
        assert recovered.comment_ids == (900,)


@pytest.mark.anyio
async def test_unexpected_exception_returns_ambiguous_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    clock = ManualClock()
    poster = _poster(
        tmp_path / "ledger.sqlite3",
        StatefulReviewGateway(clock=clock),
        clock,
    )
    monkeypatch.setattr(
        poster,
        "_post",
        AsyncMock(side_effect=RuntimeError("unexpected failure")),
    )

    result = await poster.post(_request(tmp_path))

    assert result.state is ReviewOperationState.AMBIGUOUS
    assert result.error == "RuntimeError: unexpected failure"


@pytest.mark.anyio
@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, SystemExit])
async def test_process_control_exceptions_propagate(
    exception_type: type[BaseException],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    clock = ManualClock()
    poster = _poster(
        tmp_path / "ledger.sqlite3",
        StatefulReviewGateway(clock=clock),
        clock,
    )
    monkeypatch.setattr(
        poster,
        "_post",
        AsyncMock(side_effect=exception_type("stop")),
    )

    with pytest.raises(exception_type, match="stop"):
        await poster.post(_request(tmp_path))


@pytest.mark.anyio
async def test_dry_run_has_no_network_storage_token_or_receipt_side_effects(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "fresh-state" / "ledger.sqlite3"
    request = _request(tmp_path, dry_run=True)
    clock = ManualClock()
    gateway = StatefulReviewGateway(clock=clock)

    result = await _poster(database_path, gateway, clock).post(request)

    assert result.state is ReviewOperationState.DRY_RUN
    assert result.planned_mutation_count == 1
    assert result.planned_comment_count == 1
    assert result.executed_mutation_count == 0
    assert result.receipt is None
    assert gateway.scope_calls == 0
    assert gateway.authenticated_user_calls == 0
    assert gateway.pull_calls == 0
    assert gateway.create_calls == []
    assert not database_path.exists()
    assert request.receipt_path is not None
    assert not request.receipt_path.exists()


@pytest.mark.anyio
async def test_comment_cap_rejects_oversized_batch_before_any_side_effect(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state" / "ledger.sqlite3"
    request = _request(
        tmp_path,
        comments=(
            GitHubReviewComment(path="src/a.py", line=10, body="A"),
            GitHubReviewComment(path="src/b.py", line=20, body="B"),
        ),
    )
    clock = ManualClock()
    gateway = StatefulReviewGateway(clock=clock)

    result = await _poster(
        database_path,
        gateway,
        clock,
        review_comment_cap=1,
    ).post(request)

    assert result.state is ReviewOperationState.TERMINAL
    assert result.planned_comment_count == 2
    assert result.executed_mutation_count == 0
    assert result.error and "cap" in result.error.casefold()
    assert gateway.scope_calls == 0
    assert gateway.create_calls == []
    assert not database_path.exists()


@pytest.mark.anyio
async def test_success_requires_review_and_comment_readback_with_remote_ids(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state" / "ledger.sqlite3"
    request = _request(tmp_path)
    clock = ManualClock()
    gateway = StatefulReviewGateway(clock=clock)

    result = await _poster(database_path, gateway, clock).post(request)

    assert result.state is ReviewOperationState.SUCCEEDED
    assert result.review_id == 700
    assert result.comment_ids == (900,)
    assert result.executed_mutation_count == 1
    assert result.executed_comment_count == 1
    assert result.receipt is not None
    disposition = result.receipt.finding_dispositions[0]
    assert disposition.kind is ReviewFindingDispositionKind.POSTED
    assert disposition.remote_comment_id == 900
    assert gateway.review_read_calls == 1
    assert gateway.comment_read_calls == 1
    assert len(gateway.create_calls) == 1

    payload = gateway.create_calls[0]
    assert payload["commit_id"] == request.head_sha
    assert payload["event"] == "COMMENT"
    assert result.operation_key in payload["body"]
    assert "<!-- autoskillit-review-finding:" in payload["comments"][0]["body"]
    assert (
        result.receipt.requested_body_digest == hashlib.sha256(request.body.encode()).hexdigest()
    )
    assert (
        result.receipt.effective_body_digest
        == hashlib.sha256(payload["body"].encode()).hexdigest()
    )
    assert result.receipt.effective_body_digest != result.receipt.requested_body_digest
    attempt = GitHubReviewLedger(database_path).load_attempts(result.operation_key)[0]
    assert attempt.effective_body_digest == result.receipt.effective_body_digest
    assert request.receipt_path is not None
    assert not request.receipt_path.exists(), "the server handler owns receipt projection"
    json.dumps(result.to_dict())


@pytest.mark.anyio
async def test_http_success_without_readback_stays_pending_and_writes_no_receipt(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state" / "ledger.sqlite3"
    request = _request(tmp_path)
    clock = ManualClock()
    gateway = StatefulReviewGateway(
        clock=clock,
        outcomes=[CreateOutcome(200, commit=True, fail_reads_after=True)],
    )

    result = await _poster(database_path, gateway, clock).post(request)

    assert result.state is ReviewOperationState.COMMITTED_PENDING_VERIFICATION
    assert result.review_id is None
    assert result.comment_ids == ()
    assert result.receipt is None
    assert GitHubReviewLedger(database_path).load_receipt(result.operation_key) is None
    assert request.receipt_path is not None
    assert not request.receipt_path.exists()


@pytest.mark.anyio
async def test_receipt_first_replay_avoids_credentials_head_reads_and_network(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state" / "ledger.sqlite3"
    request = _request(tmp_path)
    clock = ManualClock()
    first_gateway = StatefulReviewGateway(clock=clock)
    original = await _poster(database_path, first_gateway, clock).post(request)

    replay_gateway = StatefulReviewGateway(clock=clock, head_sha="b" * 40)
    repeated = await _poster(database_path, replay_gateway, clock).post(request)

    assert original.replayed is False
    assert repeated.replayed is True
    assert repeated.state is ReviewOperationState.SUCCEEDED
    assert repeated.operation_key == original.operation_key
    assert repeated.review_id == original.review_id
    assert replay_gateway.scope_calls == 0
    assert replay_gateway.authenticated_user_calls == 0
    assert replay_gateway.pull_calls == 0
    assert replay_gateway.create_calls == []


@pytest.mark.anyio
async def test_new_operation_with_stale_requested_head_is_rejected_before_storage(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state" / "ledger.sqlite3"
    request = _request(tmp_path)
    clock = ManualClock()
    gateway = StatefulReviewGateway(clock=clock, head_sha="b" * 40)

    result = await _poster(database_path, gateway, clock).post(request)

    assert result.state is ReviewOperationState.TERMINAL
    assert result.executed_mutation_count == 0
    assert gateway.create_calls == []
    assert not database_path.exists()


@pytest.mark.anyio
async def test_separate_poster_instances_share_durable_claim_and_only_one_posts(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state" / "ledger.sqlite3"
    request = _request(tmp_path)
    clock = ManualClock()
    gateway = StatefulReviewGateway(clock=clock)
    first = _poster(database_path, gateway, clock)
    second = _poster(database_path, gateway, clock)

    left, right = await asyncio.gather(first.post(request), second.post(request))

    assert left.operation_key == right.operation_key
    assert {left.replayed, right.replayed} == {False, True}
    assert left.state is ReviewOperationState.SUCCEEDED
    assert right.state is ReviewOperationState.SUCCEEDED
    assert len(gateway.create_calls) == 1
