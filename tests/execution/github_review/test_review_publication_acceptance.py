"""End-to-end acceptance matrix for authoritative batched review publication.

The rectify plan named ``tests/skills`` for this matrix, but that directory's
AGENTS.md restricts it to SKILL.md content compliance. Runtime gateway and ledger
acceptance belongs beside the implementation under ``tests/execution``.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from autoskillit.core import DiffAnchorAuthority, GitHubReviewComment, ReviewOperationState

from .fakes import CreateOutcome, ManualClock, StatefulReviewGateway, _poster, _request

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _anchor_error(index: int) -> dict[str, object]:
    return {
        "message": "Validation Failed",
        "errors": [
            {
                "resource": "PullRequestReviewComment",
                "field": f"comments[{index}].line",
                "code": "invalid",
                "message": "line must be part of the diff",
            }
        ],
    }


def _assert_batch_only(gateway: StatefulReviewGateway) -> None:
    assert gateway.call_trace.count("create_review") == len(gateway.create_calls)
    assert "create_review_comment" not in gateway.call_trace


@pytest.mark.anyio
async def test_commit_then_error_reconciles_committed_review(tmp_path: Path) -> None:
    database_path = tmp_path / "ledger.sqlite3"
    request = _request(tmp_path)
    clock = ManualClock()
    gateway = StatefulReviewGateway(
        clock=clock,
        outcomes=[CreateOutcome(502, data={"message": "Bad Gateway"}, commit=True)],
    )

    first = await _poster(database_path, gateway, clock).post(request)
    repeated = await _poster(database_path, gateway, clock).post(request)

    assert first.state is repeated.state is ReviewOperationState.RECONCILED
    assert repeated.replayed is True
    assert len(gateway.create_calls) == 1
    _assert_batch_only(gateway)


@pytest.mark.anyio
async def test_invalid_anchor_422_makes_one_corrected_strict_subset_retry(
    tmp_path: Path,
) -> None:
    request = _request(
        tmp_path,
        comments=(
            GitHubReviewComment(path="src/a.py", line=10, body="Drop this"),
            GitHubReviewComment(path="src/b.py", line=20, body="Keep this"),
        ),
    )
    clock = ManualClock()
    gateway = StatefulReviewGateway(
        clock=clock,
        outcomes=[
            CreateOutcome(422, data=_anchor_error(0)),
            CreateOutcome(200, commit=True),
        ],
    )

    result = await _poster(tmp_path / "ledger.sqlite3", gateway, clock).post(request)

    assert result.state is ReviewOperationState.SUCCEEDED
    assert [len(call["comments"]) for call in gateway.create_calls] == [2, 1]
    assert "Drop this" in gateway.create_calls[-1]["body"]
    assert len(gateway.create_calls) == 2
    _assert_batch_only(gateway)


@pytest.mark.anyio
async def test_force_push_drift_before_publication_returns_stale_snapshot(
    tmp_path: Path,
) -> None:
    clock = ManualClock()
    gateway = StatefulReviewGateway(clock=clock, head_sha="b" * 40)

    result = await _poster(tmp_path / "ledger.sqlite3", gateway, clock).post(_request(tmp_path))

    assert result.state is ReviewOperationState.TERMINAL
    assert "head" in result.error.casefold()
    assert gateway.create_calls == []
    _assert_batch_only(gateway)


@pytest.mark.anyio
async def test_repeated_session_against_same_operation_key_reuses_reconciled_receipt(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "ledger.sqlite3"
    request = _request(tmp_path)
    clock = ManualClock()
    gateway = StatefulReviewGateway(clock=clock)

    first = await _poster(database_path, gateway, clock).post(request)
    repeated = await _poster(database_path, gateway, clock).post(request)

    assert first.operation_key == repeated.operation_key
    assert repeated.replayed is True
    assert len(gateway.create_calls) == 1
    _assert_batch_only(gateway)


@pytest.mark.anyio
async def test_duplicate_concurrent_submission_produces_one_remote_post(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "ledger.sqlite3"
    request = _request(tmp_path)
    clock = ManualClock()
    gateway = StatefulReviewGateway(clock=clock)

    left, right = await asyncio.gather(
        _poster(database_path, gateway, clock).post(request),
        _poster(database_path, gateway, clock).post(request),
    )

    assert left.operation_key == right.operation_key
    assert {left.replayed, right.replayed} == {False, True}
    assert len(gateway.create_calls) == 1
    _assert_batch_only(gateway)


@pytest.mark.anyio
@pytest.mark.parametrize("authority_available", [True, False])
async def test_initially_admissible_or_missing_authority_body_only_makes_one_post(
    tmp_path: Path,
    authority_available: bool,
) -> None:
    request = _request(tmp_path)
    if not authority_available:
        request = replace(
            request,
            anchor_authority=DiffAnchorAuthority.unavailable(
                repository=request.repository,
                pr_number=request.pr_number,
                head_sha=request.head_sha,
            ),
        )
    clock = ManualClock()
    gateway = StatefulReviewGateway(clock=clock)

    result = await _poster(tmp_path / "ledger.sqlite3", gateway, clock).post(request)

    assert result.state is ReviewOperationState.SUCCEEDED
    assert len(gateway.create_calls) == 1
    if authority_available:
        assert len(gateway.create_calls[0]["comments"]) == 1
    else:
        assert gateway.create_calls[0]["comments"] == []
        assert "Normalize this value." in gateway.create_calls[0]["body"]
    _assert_batch_only(gateway)
