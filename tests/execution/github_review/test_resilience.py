"""Ambiguity, reconciliation, validation reduction, and throttle behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import (
    GitHubReviewComment,
    ReviewFindingDispositionKind,
    ReviewOperationState,
    ReviewReconciliationResult,
)
from autoskillit.execution import GitHubReviewLedger
from autoskillit.execution.github_review import _poster_support

from .fakes import (
    CreateOutcome,
    ManualClock,
    StatefulReviewGateway,
    _poster,
    _request,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _validation_error(index: int) -> dict[str, object]:
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


def _self_review_error() -> dict[str, object]:
    return {
        "message": "Validation Failed",
        "errors": [
            {
                "resource": "PullRequestReview",
                "field": "event",
                "code": "invalid",
                "message": "Can not request changes on your own pull request",
            }
        ],
    }


@pytest.mark.anyio
async def test_commit_then_502_is_reconciled_without_second_post(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    clock = ManualClock()
    gateway = StatefulReviewGateway(
        clock=clock,
        outcomes=[
            CreateOutcome(
                502,
                data={"message": "Bad Gateway"},
                commit=True,
            )
        ],
    )

    result = await _poster(tmp_path / "ledger.sqlite3", gateway, clock).post(request)

    assert result.state is ReviewOperationState.RECONCILED
    assert result.review_id == 700
    assert result.comment_ids == (900,)
    assert result.executed_mutation_count == 1
    assert result.receipt is not None
    assert result.reconciliation_result is ReviewReconciliationResult.MATCHED
    assert len(gateway.create_calls) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("failure_kind", ["server", "transport"])
async def test_unverifiable_5xx_or_transport_outcome_stays_ambiguous(
    tmp_path: Path,
    failure_kind: str,
) -> None:
    clock = ManualClock()
    outcome = (
        CreateOutcome(503, data={"message": "Service Unavailable"})
        if failure_kind == "server"
        else CreateOutcome(None, error="response lost after upload", fail_reads_after=True)
    )
    gateway = StatefulReviewGateway(clock=clock, outcomes=[outcome])
    request = _request(tmp_path)
    poster = _poster(tmp_path / f"{failure_kind}.sqlite3", gateway, clock)

    result = await poster.post(request)
    repeated = await poster.post(request)

    assert result.state is ReviewOperationState.AMBIGUOUS
    assert repeated.state is ReviewOperationState.AMBIGUOUS
    assert result.review_id is None
    assert result.executed_mutation_count == 1
    assert result.receipt is None
    assert len(gateway.create_calls) == 1


@pytest.mark.anyio
async def test_exact_structured_422_retries_one_strict_subset_and_accounts_findings(
    tmp_path: Path,
) -> None:
    request = _request(
        tmp_path,
        comments=(
            GitHubReviewComment(path="src/a.py", line=10, body="Keep A"),
            GitHubReviewComment(path="src/b.py", line=20, body="Reject B"),
            GitHubReviewComment(path="src/c.py", line=30, body="Keep C"),
        ),
    )
    clock = ManualClock()
    gateway = StatefulReviewGateway(
        clock=clock,
        outcomes=[
            CreateOutcome(422, data=_validation_error(1)),
            CreateOutcome(200, commit=True),
        ],
    )

    result = await _poster(tmp_path / "ledger.sqlite3", gateway, clock).post(request)

    assert result.state is ReviewOperationState.SUCCEEDED
    assert result.planned_comment_count == 3
    assert result.executed_mutation_count == 2
    assert result.executed_comment_count == 2
    assert len(gateway.create_calls) == 2
    assert gateway.create_timestamps[1] - gateway.create_timestamps[0] >= 1.0
    first_bodies = [
        item["body"].split("\n\n", 1)[0] for item in gateway.create_calls[0]["comments"]
    ]
    second_bodies = [
        item["body"].split("\n\n", 1)[0] for item in gateway.create_calls[1]["comments"]
    ]
    assert first_bodies == ["Keep A", "Reject B", "Keep C"]
    assert second_bodies == ["Keep A", "Keep C"]
    assert result.receipt is not None
    assert len(result.receipt.finding_dispositions) == 3
    rejected = next(
        item for item in result.receipt.finding_dispositions if item.original_index == 1
    )
    assert rejected.kind is ReviewFindingDispositionKind.OMITTED_INVALID
    assert rejected.reason and "diff" in rejected.reason
    posted = [
        item
        for item in result.receipt.finding_dispositions
        if item.kind is ReviewFindingDispositionKind.POSTED
    ]
    assert {item.remote_comment_id for item in posted} == {900, 901}


@pytest.mark.anyio
async def test_crash_after_reduced_batch_reconciles_persisted_subset_without_repost(
    tmp_path: Path,
) -> None:
    request = _request(
        tmp_path,
        comments=(
            GitHubReviewComment(path="src/a.py", line=10, body="Already present"),
            GitHubReviewComment(path="src/b.py", line=20, body="Reject"),
            GitHubReviewComment(path="src/c.py", line=30, body="Keep"),
        ),
    )
    database_path = tmp_path / "ledger.sqlite3"
    clock = ManualClock()
    gateway = StatefulReviewGateway(
        clock=clock,
        outcomes=[
            CreateOutcome(422, data=_validation_error(1)),
            CreateOutcome(
                None,
                error="process lost response",
                commit=True,
                fail_reads_after=True,
            ),
        ],
    )
    findings = _poster_support.canonical_findings(request)
    gateway._commit(
        _poster_support.payload(
            request=request,
            operation_key="preexisting-operation",
            findings=(findings[0],),
            event="COMMENT",
        )
    )
    first = await _poster(database_path, gateway, clock).post(request)
    gateway.fail_reads = False

    recovered = await _poster(database_path, gateway, clock).post(request)

    assert first.state is ReviewOperationState.AMBIGUOUS
    assert recovered.state is ReviewOperationState.RECONCILED
    assert recovered.review_id == 701
    assert len(gateway.create_calls) == 2
    assert recovered.receipt is not None
    dispositions = {item.original_index: item for item in recovered.receipt.finding_dispositions}
    assert dispositions[0].kind is ReviewFindingDispositionKind.ALREADY_PRESENT
    assert dispositions[0].remote_comment_id == 900
    assert dispositions[1].kind is ReviewFindingDispositionKind.OMITTED_INVALID
    assert dispositions[1].reason and "diff" in dispositions[1].reason
    assert dispositions[2].kind is ReviewFindingDispositionKind.POSTED
    assert dispositions[2].remote_comment_id == 901
    final_attempt = GitHubReviewLedger(database_path).load_attempts(recovered.operation_key)[-1]
    assert final_attempt.state == ReviewOperationState.RECONCILED.value
    assert final_attempt.omitted_dispositions == (
        dispositions[1],
        dispositions[0],
    )


@pytest.mark.anyio
async def test_http_200_pending_verification_is_enriched_without_repost(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    database_path = tmp_path / "ledger.sqlite3"
    clock = ManualClock()
    gateway = StatefulReviewGateway(
        clock=clock,
        outcomes=[CreateOutcome(200, commit=True, fail_reads_after=True)],
    )
    first = await _poster(database_path, gateway, clock).post(request)
    gateway.fail_reads = False

    enriched = await _poster(database_path, gateway, clock).post(request)

    assert first.state is ReviewOperationState.COMMITTED_PENDING_VERIFICATION
    assert enriched.state is ReviewOperationState.SUCCEEDED
    assert enriched.review_id == 700
    assert len(gateway.create_calls) == 1
    assert GitHubReviewLedger(database_path).load_receipt(enriched.operation_key) is not None


@pytest.mark.anyio
async def test_old_head_ambiguous_attempt_reconciles_read_only_after_head_advance(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    database_path = tmp_path / "ledger.sqlite3"
    clock = ManualClock()
    gateway = StatefulReviewGateway(
        clock=clock,
        outcomes=[
            CreateOutcome(
                502,
                data={"message": "Bad Gateway"},
                commit=True,
                fail_reads_after=True,
            )
        ],
    )
    first = await _poster(database_path, gateway, clock).post(request)
    gateway.fail_reads = False
    gateway.head_sha = "b" * 40
    pull_calls_before = gateway.pull_calls

    reconciled = await _poster(database_path, gateway, clock).post(request)

    assert first.state is ReviewOperationState.AMBIGUOUS
    assert reconciled.state is ReviewOperationState.RECONCILED
    assert gateway.pull_calls == pull_calls_before
    assert len(gateway.create_calls) == 1


@pytest.mark.anyio
async def test_unstructured_or_second_422_is_terminal_without_further_fallback(
    tmp_path: Path,
) -> None:
    clock = ManualClock()
    gateway = StatefulReviewGateway(
        clock=clock,
        outcomes=[
            CreateOutcome(422, data=_validation_error(0)),
            CreateOutcome(422, data=_validation_error(0)),
        ],
    )

    result = await _poster(tmp_path / "ledger.sqlite3", gateway, clock).post(
        _request(
            tmp_path,
            comments=(
                GitHubReviewComment(path="src/a.py", line=10, body="Reject A"),
                GitHubReviewComment(path="src/b.py", line=20, body="Reject B"),
            ),
        )
    )

    assert result.state is ReviewOperationState.TERMINAL
    assert len(gateway.create_calls) == 2


@pytest.mark.anyio
async def test_self_review_422_transforms_request_changes_to_comment_once(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path, event="REQUEST_CHANGES")
    clock = ManualClock()
    gateway = StatefulReviewGateway(
        clock=clock,
        authenticated_login="pull-author",
        pr_author_login="pull-author",
        outcomes=[
            CreateOutcome(422, data=_self_review_error()),
            CreateOutcome(200, commit=True),
        ],
    )

    result = await _poster(tmp_path / "ledger.sqlite3", gateway, clock).post(request)

    assert [payload["event"] for payload in gateway.create_calls] == [
        "REQUEST_CHANGES",
        "COMMENT",
    ]
    assert gateway.create_timestamps[1] - gateway.create_timestamps[0] >= 1.0
    assert result.state is ReviewOperationState.SUCCEEDED
    assert result.receipt is not None
    assert result.receipt.requested_event == "REQUEST_CHANGES"
    assert result.receipt.effective_event == "COMMENT"


@pytest.mark.anyio
async def test_self_review_text_without_identity_proof_does_not_transform(
    tmp_path: Path,
) -> None:
    clock = ManualClock()
    gateway = StatefulReviewGateway(
        clock=clock,
        authenticated_login="review-bot",
        pr_author_login="pull-author",
        outcomes=[CreateOutcome(422, data=_self_review_error())],
    )

    result = await _poster(tmp_path / "ledger.sqlite3", gateway, clock).post(
        _request(tmp_path, event="REQUEST_CHANGES")
    )

    assert result.state is ReviewOperationState.TERMINAL
    assert len(gateway.create_calls) == 1


@pytest.mark.anyio
async def test_secondary_rate_limit_persists_backpressure_without_retry_or_receipt(
    tmp_path: Path,
) -> None:
    clock = ManualClock()
    gateway = StatefulReviewGateway(
        clock=clock,
        outcomes=[
            CreateOutcome(
                403,
                data={"message": "You have exceeded a secondary rate limit."},
                headers={"retry-after": "7"},
            )
        ],
    )
    database_path = tmp_path / "ledger.sqlite3"

    result = await _poster(database_path, gateway, clock).post(_request(tmp_path))

    assert result.state is ReviewOperationState.THROTTLED
    assert result.executed_mutation_count == 1
    assert result.receipt is None
    assert GitHubReviewLedger(database_path).load_receipt(result.operation_key) is None
    assert len(gateway.create_calls) == 1
