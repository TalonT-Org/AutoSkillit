"""Happy-path, dry-run, replay, and verification behavior for the review poster."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from autoskillit.core import (
    GitHubReviewComment,
    ReviewFindingDispositionKind,
    ReviewOperationState,
)
from autoskillit.execution import GitHubReviewLedger

from .fakes import (
    CreateOutcome,
    ManualClock,
    StatefulReviewGateway,
    _poster,
    _request,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


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
