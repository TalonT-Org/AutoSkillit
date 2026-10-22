"""Ambiguity, reconciliation, validation reduction, and throttle behavior."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

import pytest

from autoskillit.core import (
    DiffAnchorAuthority,
    GitHubReviewComment,
    GitHubReviewRequest,
    ReviewFindingDispositionKind,
    ReviewOperationState,
    ReviewReconciliationResult,
    ReviewResponseClass,
)
from autoskillit.execution import GitHubReviewLedger, compute_review_operation_key
from autoskillit.execution.github_review import _poster_support
from autoskillit.execution.github_review.canonical import admit_findings, canonical_findings
from autoskillit.execution.github_review.gateway import GatewayResult
from tests.fixtures.github_rest import (
    reviews_create_422_author_approve,
    reviews_create_422_author_request_changes,
    reviews_create_422_line_unresolved,
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
    "guard,error",
    [
        ("RETRY_STALE", "no longer pending"),
        ("RETRY_CONTENDED", "claimed by another poster"),
        ("SLOT_BLOCKED", "blocks this rate scope"),
    ],
)
async def test_pre_gateway_guard_returns_are_classified_outcomes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    guard: str,
    error: str,
) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from autoskillit.execution.github_review.poster import ReviewAttemptOutcome

    outcome = ReviewAttemptOutcome[guard]
    request = _request(tmp_path)
    clock = ManualClock()
    gateway = StatefulReviewGateway(clock=clock)
    poster = _poster(tmp_path / "ledger.sqlite3", gateway, clock)
    monkeypatch.setattr(
        poster.ledger,
        "begin_attempt",
        lambda **kwargs: SimpleNamespace(
            state=(
                ReviewOperationState.SUCCEEDED.value
                if outcome is ReviewAttemptOutcome.RETRY_STALE
                else ReviewOperationState.RETRY_PENDING.value
            )
        ),
    )
    monkeypatch.setattr(poster.ledger, "claim_retry_attempt", lambda **kwargs: False)
    monkeypatch.setattr(poster.ledger, "complete_attempt", lambda **kwargs: None)
    monkeypatch.setattr(
        poster.coordinator,
        "acquire",
        AsyncMock(return_value=SimpleNamespace(blocked_operation_key="other")),
    )
    result = await poster._attempt(
        request=request,
        operation_key=compute_review_operation_key(request),
        scope_id="scope",
        findings=admit_findings(canonical_findings(request), request.anchor_authority).admitted,
        effective_event="COMMENT",
        attempt_number=2,
        omitted=(),
        authenticated_login="bot",
        resume_pending=outcome is not ReviewAttemptOutcome.SLOT_BLOCKED,
    )
    assert result.state is ReviewOperationState.AMBIGUOUS
    assert error in result.error
    assert gateway.create_calls == []


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


def _invalid_comment_response(field: str) -> GatewayResult:
    return GatewayResult(
        status_code=422,
        data={
            "message": "Validation Failed",
            "errors": [
                {
                    "resource": "PullRequestReviewComment",
                    "field": field,
                    "code": "invalid",
                    "message": "line must be part of the diff",
                }
            ],
        },
        headers={},
        response_class=ReviewResponseClass.VALIDATION_ERROR,
    )


@pytest.mark.parametrize("field", ["comments[0]", "comments[0].line"])
def test_structured_invalid_comment_index_accepts_optional_subfield(field: str) -> None:
    assert (
        _poster_support.structured_invalid_comment_index(
            _invalid_comment_response(field),
            1,
        )
        == 0
    )


@pytest.mark.parametrize(
    "field",
    ["comments[0].", "comments[0]line", "comments[].line", "comments[0"],
)
def test_structured_invalid_comment_index_rejects_malformed_suffix(field: str) -> None:
    assert (
        _poster_support.structured_invalid_comment_index(
            _invalid_comment_response(field),
            1,
        )
        is None
    )


@pytest.mark.anyio
@pytest.mark.parametrize("second,fail_reads", [(False, True), (True, True), (True, False)])
async def test_generic_and_second_422_reconcile_before_terminal(
    tmp_path: Path,
    second: bool,
    fail_reads: bool,
) -> None:
    clock = ManualClock()
    outcomes = [CreateOutcome(422, data=_validation_error(0))] if second else []
    outcomes.append(
        CreateOutcome(
            422,
            data=_validation_error(0) if second else {"message": "invalid"},
            fail_reads_after=fail_reads,
        )
    )
    gateway = StatefulReviewGateway(clock=clock, outcomes=outcomes)
    result = await _poster(tmp_path / "ledger.sqlite3", gateway, clock).post(_request(tmp_path))
    assert result.state is (
        ReviewOperationState.AMBIGUOUS if fail_reads else ReviewOperationState.TERMINAL
    )
    assert len(gateway.create_calls) == (2 if second else 1)
    assert gateway.call_trace[-2:] == ["create_review", "list_reviews"]


@pytest.mark.anyio
async def test_second_422_after_corrected_retry_terminates_without_third_attempt(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "ledger.sqlite3"
    request = _request(tmp_path)
    clock = ManualClock()
    gateway = StatefulReviewGateway(
        clock=clock,
        outcomes=[
            CreateOutcome(422, data=_validation_error(0)),
            CreateOutcome(422, data=_validation_error(0)),
        ],
    )

    result = await _poster(database_path, gateway, clock).post(request)

    assert result.state is ReviewOperationState.TERMINAL
    assert len(gateway.create_calls) == 2
    assert gateway.call_trace == [
        "create_review",
        "list_reviews",
        "list_reviews",
        "create_review",
        "list_reviews",
    ]
    assert (
        gateway.call_trace.index("create_review")
        < gateway.call_trace.index("list_reviews")
        < gateway.call_trace.index("create_review", 1)
    )
    attempts = GitHubReviewLedger(database_path).load_attempts(
        compute_review_operation_key(request)
    )
    assert [attempt.attempt_number for attempt in attempts] == [1, 2]


def _request_with_initial_and_retry_omissions(tmp_path: Path) -> GitHubReviewRequest:
    comments = (
        GitHubReviewComment(path="src/a.py", line=10, body="Keep A"),
        GitHubReviewComment(path="src/b.py", line=20, body="Dropped by retry"),
        GitHubReviewComment(path="src/c.py", line=30, body="Rejected before POST"),
    )
    authority = DiffAnchorAuthority.authoritative(
        repository="octo/example",
        pr_number=42,
        head_sha="a" * 40,
        generation_id="mixed-omissions",
        right_side_lines={"src/a.py": {10}, "src/b.py": {20}},
        left_side_lines={},
    )
    return _request(tmp_path, comments=comments, anchor_authority=authority)


@pytest.mark.anyio
async def test_corrected_retry_body_names_every_omitted_finding(tmp_path: Path) -> None:
    request = _request_with_initial_and_retry_omissions(tmp_path)
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
    retry_body = gateway.create_calls[-1]["body"]
    assert "Outside Diff Range" in retry_body
    assert "Dropped by retry" in retry_body
    assert "Rejected before POST" in retry_body


@pytest.mark.anyio
async def test_receipt_disposition_count_matches_body_entries(tmp_path: Path) -> None:
    request = _request_with_initial_and_retry_omissions(tmp_path)
    clock = ManualClock()
    gateway = StatefulReviewGateway(
        clock=clock,
        outcomes=[
            CreateOutcome(422, data=_validation_error(1)),
            CreateOutcome(200, commit=True),
        ],
    )

    result = await _poster(tmp_path / "ledger.sqlite3", gateway, clock).post(request)

    assert result.receipt is not None
    omitted_count = sum(
        item.kind is ReviewFindingDispositionKind.OMITTED_INVALID
        for item in result.receipt.finding_dispositions
    )
    outside_diff = gateway.create_calls[-1]["body"].split("## Outside Diff Range", 1)[1]
    body_count = outside_diff.count("\n- `")
    assert omitted_count == body_count, (
        f"disposition count {omitted_count} must match body entry count {body_count}"
    )
    # Fixture-encoded expectation: _request_with_initial_and_retry_omissions defines
    # exactly two omitted findings (one dropped by retry, one rejected before POST).
    # This guards against silent fixture growth that would otherwise pass the body
    # consistency check above.
    assert omitted_count == 2, f"expected exactly 2 omitted findings, got {omitted_count}"


@pytest.mark.anyio
async def test_over_reduction_publishes_body_only_rather_than_terminal(tmp_path: Path) -> None:
    clock = ManualClock()
    gateway = StatefulReviewGateway(
        clock=clock,
        outcomes=[CreateOutcome(422, data=_validation_error(0)), CreateOutcome(200, commit=True)],
    )
    result = await _poster(tmp_path / "ledger.sqlite3", gateway, clock).post(_request(tmp_path))
    assert result.state is ReviewOperationState.SUCCEEDED
    assert len(gateway.create_calls) == 2
    assert gateway.create_calls[1]["comments"] == []
    assert "Normalize this value." in gateway.create_calls[1]["body"]
    assert "Outside Diff Range" in gateway.create_calls[1]["body"]
    assert result.receipt is not None
    assert len(result.receipt.finding_dispositions) == 1
    assert (
        result.receipt.finding_dispositions[0].kind is ReviewFindingDispositionKind.OMITTED_INVALID
    )


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
    assert "Outside Diff Range" in gateway.create_calls[1]["body"]
    assert "Reject B" in gateway.create_calls[1]["body"]
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
async def test_crash_before_reduced_retry_claim_resumes_persisted_intent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(
        tmp_path,
        comments=(
            GitHubReviewComment(path="src/a.py", line=10, body="Keep A"),
            GitHubReviewComment(path="src/b.py", line=20, body="Reject B"),
            GitHubReviewComment(path="src/c.py", line=30, body="Keep C"),
        ),
    )
    database_path = tmp_path / "ledger.sqlite3"
    clock = ManualClock()
    gateway = StatefulReviewGateway(
        clock=clock,
        outcomes=[
            CreateOutcome(422, data=_validation_error(1)),
            CreateOutcome(200, commit=True),
        ],
    )
    poster = _poster(database_path, gateway, clock)

    def crash_after_retry_is_persisted(*, operation_key: str, attempt_number: int) -> bool:
        del operation_key, attempt_number
        raise RuntimeError("crash after retry scheduling")

    monkeypatch.setattr(
        poster.ledger,
        "claim_retry_attempt",
        crash_after_retry_is_persisted,
    )

    interrupted = await poster.post(request)

    operation_key = compute_review_operation_key(request)
    ledger = GitHubReviewLedger(database_path)
    operation = ledger.load_operation(operation_key)
    attempts = ledger.load_attempts(operation_key)
    assert interrupted.state is ReviewOperationState.AMBIGUOUS
    assert len(gateway.create_calls) == 1
    assert operation is not None
    assert operation.state is ReviewOperationState.RETRY_PENDING
    assert [attempt.state for attempt in attempts] == [
        ReviewOperationState.TERMINAL.value,
        ReviewOperationState.RETRY_PENDING.value,
    ]
    assert attempts[1].canonical_indexes == (0, 2)

    recovered = await _poster(database_path, gateway, clock).post(request)

    assert recovered.state is ReviewOperationState.SUCCEEDED
    assert recovered.executed_mutation_count == 2
    assert len(gateway.create_calls) == 2
    assert "Outside Diff Range" in gateway.create_calls[1]["body"]
    assert "Reject B" in gateway.create_calls[1]["body"]
    second_bodies = [
        item["body"].split("\n\n", 1)[0] for item in gateway.create_calls[1]["comments"]
    ]
    assert second_bodies == ["Keep A", "Keep C"]
    assert recovered.receipt is not None
    dispositions = {item.original_index: item for item in recovered.receipt.finding_dispositions}
    assert dispositions[1].kind is ReviewFindingDispositionKind.OMITTED_INVALID
    assert ledger.load_attempts(operation_key)[-1].state == (ReviewOperationState.SUCCEEDED.value)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "reviewer,resumed_reviewer",
    [("reviewer", "pull-author"), ("pull-author", "reviewer")],
)
async def test_resumed_retry_rejects_changed_author_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reviewer: str,
    resumed_reviewer: str,
) -> None:
    request = _request(
        tmp_path,
        event="APPROVE",
        comments=(
            GitHubReviewComment(path="src/a.py", line=10, body="Reject A"),
            GitHubReviewComment(path="src/b.py", line=20, body="Keep B"),
        ),
    )
    database_path = tmp_path / "ledger.sqlite3"
    clock = ManualClock()
    gateway = StatefulReviewGateway(
        clock=clock,
        authenticated_login=reviewer,
        pr_author_login="pull-author",
        outcomes=[CreateOutcome(422, data=_validation_error(0))],
    )
    poster = _poster(database_path, gateway, clock)

    def crash_after_retry_is_persisted(*, operation_key: str, attempt_number: int) -> bool:
        raise RuntimeError("crash after retry scheduling")

    monkeypatch.setattr(poster.ledger, "claim_retry_attempt", crash_after_retry_is_persisted)
    interrupted = await poster.post(request)
    assert interrupted.state is ReviewOperationState.AMBIGUOUS
    assert GitHubReviewLedger(database_path).load_attempts(interrupted.operation_key)[
        -1
    ].state == (ReviewOperationState.RETRY_PENDING.value)

    monkeypatch.setattr(gateway, "authenticated_login", resumed_reviewer)
    recovered = await _poster(database_path, gateway, clock).post(request)

    assert recovered.state is ReviewOperationState.TERMINAL
    assert "current reviewer identity" in recovered.error
    assert len(gateway.create_calls) == 1
    attempts = GitHubReviewLedger(database_path).load_attempts(interrupted.operation_key)
    assert len(attempts) == 2
    assert attempts[-1].state == ReviewOperationState.TERMINAL.value


@pytest.mark.anyio
async def test_release_failure_after_reduced_retry_preserves_retry_intent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(
        tmp_path,
        comments=(
            GitHubReviewComment(path="src/a.py", line=10, body="Keep A"),
            GitHubReviewComment(path="src/b.py", line=20, body="Reject B"),
            GitHubReviewComment(path="src/c.py", line=30, body="Keep C"),
        ),
    )
    database_path = tmp_path / "ledger.sqlite3"
    clock = ManualClock()
    gateway = StatefulReviewGateway(
        clock=clock,
        outcomes=[
            CreateOutcome(422, data=_validation_error(1)),
            CreateOutcome(200, commit=True),
        ],
    )
    poster = _poster(database_path, gateway, clock)

    def fail_release(**_kwargs: object) -> None:
        raise RuntimeError("lease release failed")

    monkeypatch.setattr(poster.ledger, "finish_mutation", fail_release)

    interrupted = await poster.post(request)

    operation_key = compute_review_operation_key(request)
    ledger = GitHubReviewLedger(database_path)
    operation = ledger.load_operation(operation_key)
    assert interrupted.state is ReviewOperationState.AMBIGUOUS
    assert operation is not None
    assert operation.state is ReviewOperationState.RETRY_PENDING
    assert [attempt.state for attempt in ledger.load_attempts(operation_key)] == [
        ReviewOperationState.TERMINAL.value,
        ReviewOperationState.RETRY_PENDING.value,
    ]

    recovered = await _poster(database_path, gateway, clock).post(request)

    assert recovered.state is ReviewOperationState.SUCCEEDED
    assert len(gateway.create_calls) == 2


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
    findings = admit_findings(canonical_findings(request), request.anchor_authority).admitted
    gateway._commit(
        _poster_support.payload(
            request=request,
            operation_key="preexisting-operation",
            findings=(findings[0],),
            omitted=(),
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
        dispositions[0],
        dispositions[1],
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


_AUTHOR_DISCLOSURES = {
    "APPROVE": (
        "Requested review event APPROVE was published as COMMENT because GitHub does not allow "
        "a pull request's author to approve it; this is not a GitHub approval."
    ),
    "REQUEST_CHANGES": (
        "Requested review event REQUEST_CHANGES was published as COMMENT because GitHub does "
        "not allow a pull request's author to request changes on it; this review does not "
        "block merging."
    ),
}


@pytest.mark.parametrize(
    "event,reviewer,author,expected",
    [
        ("APPROVE", "pull-author", "pull-author", "COMMENT"),
        ("REQUEST_CHANGES", "pull-author", "pull-author", "COMMENT"),
        ("COMMENT", "pull-author", "pull-author", "COMMENT"),
        ("APPROVE", "reviewer", "pull-author", "APPROVE"),
        ("REQUEST_CHANGES", "reviewer", "pull-author", "REQUEST_CHANGES"),
        ("APPROVE", "Pull-Author", "pull-author", "COMMENT"),
    ],
)
def test_author_effective_event_decision_table(
    event: str, reviewer: str, author: str, expected: str
) -> None:
    assert (
        _poster_support.author_effective_event(
            event, authenticated_login=reviewer, pr_author_login=author
        )
        == expected
    )


@pytest.mark.parametrize(
    "requested,event,body",
    [
        ("APPROVE", "COMMENT", "Automated review"),
        ("APPROVE", "COMMENT", ""),
        ("REQUEST_CHANGES", "COMMENT", "Automated review"),
        ("COMMENT", "COMMENT", "Automated review"),
        ("COMMENT", "APPROVE", "Automated review"),
        ("APPROVE", "REQUEST_CHANGES", "Automated review"),
    ],
)
def test_payload_discloses_only_author_comment_transformation(
    tmp_path: Path, requested: str, event: str, body: str
) -> None:
    request = _request(tmp_path, event=requested, body=body)
    arguments = dict(request=request, operation_key="key", findings=(), omitted=(), event=event)
    if event != requested and event != "COMMENT":
        with pytest.raises(
            ValueError, match="unsupported review event transformation"
        ) as exc_info:
            _poster_support.payload(**arguments)
        assert f"{requested!r} -> {event!r}" in str(exc_info.value)
        return
    pieces = [body] if body else []
    if event != requested:
        pieces.append(_AUTHOR_DISCLOSURES[requested])
    pieces.append("<!-- autoskillit-review-operation:key -->")
    assert _poster_support.payload(**arguments)["body"] == "\n\n".join(pieces)


@pytest.mark.anyio
@pytest.mark.parametrize("event", ["APPROVE", "REQUEST_CHANGES", "COMMENT"])
async def test_author_review_publishes_one_comment(tmp_path: Path, event: str) -> None:
    request = _request(
        tmp_path,
        event=event,
        comments=(
            GitHubReviewComment(path="src/a.py", line=10, body="First finding"),
            GitHubReviewComment(path="src/b.py", line=20, body="Second finding"),
        ),
    )
    clock = ManualClock()
    gateway = StatefulReviewGateway(
        clock=clock, authenticated_login="pull-author", pr_author_login="pull-author"
    )
    db = tmp_path / "ledger.sqlite3"
    result = await _poster(db, gateway, clock).post(request)

    assert result.state is ReviewOperationState.SUCCEEDED
    assert result.executed_mutation_count == 1
    assert [call["event"] for call in gateway.create_calls] == ["COMMENT"]
    assert len(gateway.create_calls[0]["comments"]) == len(request.comments)
    pieces = [request.body]
    if event != "COMMENT":
        pieces.append(_AUTHOR_DISCLOSURES[event])
    pieces.append(f"<!-- autoskillit-review-operation:{result.operation_key} -->")
    body = "\n\n".join(pieces)
    assert gateway.create_calls[0]["body"] == body
    assert result.receipt is not None
    assert result.receipt.requested_event == event
    assert result.receipt.effective_event == "COMMENT"
    assert result.receipt.effective_body_digest == hashlib.sha256(body.encode()).hexdigest()
    assert [review["state"] for review in gateway.reviews] == ["COMMENTED"]
    assert [
        attempt.effective_event
        for attempt in GitHubReviewLedger(db).load_attempts(result.operation_key)
    ] == ["COMMENT"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "event,state", [("APPROVE", "APPROVED"), ("REQUEST_CHANGES", "CHANGES_REQUESTED")]
)
async def test_third_party_review_keeps_requested_event(
    tmp_path: Path, event: str, state: str
) -> None:
    clock = ManualClock()
    gateway = StatefulReviewGateway(clock=clock)
    request = _request(tmp_path, event=event)
    result = await _poster(tmp_path / "ledger.sqlite3", gateway, clock).post(request)

    assert result.state is ReviewOperationState.SUCCEEDED
    assert [call["event"] for call in gateway.create_calls] == [event]
    assert [review["state"] for review in gateway.reviews] == [state]
    assert gateway.create_calls[0]["body"] == (
        f"{request.body}\n\n<!-- autoskillit-review-operation:{result.operation_key} -->"
    )
    assert result.receipt is not None
    assert result.receipt.requested_event == result.receipt.effective_event == event


@pytest.mark.anyio
@pytest.mark.parametrize(
    "capture,event,error",
    [
        (
            reviews_create_422_author_approve,
            "APPROVE",
            "Unprocessable Entity: Review Can not approve your own pull request",
        ),
        (
            reviews_create_422_author_request_changes,
            "REQUEST_CHANGES",
            "Unprocessable Entity: Review Can not request changes on your own pull request",
        ),
        (
            reviews_create_422_line_unresolved,
            "COMMENT",
            "Unprocessable Entity: Line could not be resolved",
        ),
    ],
)
async def test_captured_review_422_is_terminal_with_github_detail(
    tmp_path: Path, capture: Callable[[], dict[str, object]], event: str, error: str
) -> None:
    clock = ManualClock()
    gateway = StatefulReviewGateway(clock=clock, outcomes=[CreateOutcome(422, data=capture())])
    db = tmp_path / "ledger.sqlite3"
    result = await _poster(db, gateway, clock).post(_request(tmp_path, event=event))

    assert result.state is ReviewOperationState.TERMINAL
    assert result.executed_mutation_count == 1
    assert [call["event"] for call in gateway.create_calls] == [event]
    assert gateway.call_trace == ["create_review", "list_reviews"]
    assert result.error == error
    ledger = GitHubReviewLedger(db)
    assert ledger.load_attempts(result.operation_key)[0].error == error
    assert result.receipt is None
    assert ledger.load_receipt(result.operation_key) is None


@pytest.mark.anyio
async def test_persisted_terminal_author_approve_is_not_cleared(tmp_path: Path) -> None:
    clock = ManualClock()
    db = tmp_path / "ledger.sqlite3"
    request = _request(tmp_path, event="APPROVE")
    gateway = StatefulReviewGateway(
        clock=clock, outcomes=[CreateOutcome(422, data=reviews_create_422_author_approve())]
    )
    first = await _poster(db, gateway, clock).post(request)
    gateway2 = StatefulReviewGateway(
        clock=clock, authenticated_login="pull-author", pr_author_login="pull-author"
    )
    result = await _poster(db, gateway2, clock).post(request)

    assert first.state is ReviewOperationState.TERMINAL
    assert result.state is ReviewOperationState.AMBIGUOUS
    assert gateway2.create_calls == []
    assert len(GitHubReviewLedger(db).load_attempts(first.operation_key)) == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "event,own_author,length", [("APPROVE", True, 59990), ("COMMENT", False, 60001)]
)
async def test_effective_body_overflow_rejected_before_ledger_state(
    tmp_path: Path, event: str, own_author: bool, length: int
) -> None:
    clock = ManualClock()
    gateway = StatefulReviewGateway(
        clock=clock,
        authenticated_login="pull-author" if own_author else "reviewer",
        pr_author_login="pull-author",
    )
    db = tmp_path / "ledger.sqlite3"
    request = _request(tmp_path, event=event, body="x" * length)
    result = await _poster(db, gateway, clock).post(request)

    assert result.state is ReviewOperationState.TERMINAL
    assert "60000" in result.error
    assert gateway.create_calls == []
    assert GitHubReviewLedger(db).load_operation(compute_review_operation_key(request)) is None


@pytest.mark.anyio
async def test_author_event_anchor_retry_keeps_disclosed_comment(tmp_path: Path) -> None:
    clock = ManualClock()
    gateway = StatefulReviewGateway(
        clock=clock,
        authenticated_login="pull-author",
        pr_author_login="pull-author",
        outcomes=[CreateOutcome(422, data=_validation_error(0)), CreateOutcome(200, commit=True)],
    )
    request = _request(
        tmp_path,
        event="REQUEST_CHANGES",
        comments=(
            GitHubReviewComment(path="src/a.py", line=10, body="Reject"),
            GitHubReviewComment(path="src/b.py", line=20, body="Keep"),
        ),
    )
    result = await _poster(tmp_path / "ledger.sqlite3", gateway, clock).post(request)

    assert result.state is ReviewOperationState.SUCCEEDED
    assert [call["event"] for call in gateway.create_calls] == ["COMMENT", "COMMENT"]
    assert [len(call["comments"]) for call in gateway.create_calls] == [2, 1]
    assert all(
        _AUTHOR_DISCLOSURES["REQUEST_CHANGES"] in call["body"] for call in gateway.create_calls
    )
    assert result.receipt is not None
    assert result.receipt.requested_event == "REQUEST_CHANGES"
    assert result.receipt.effective_event == "COMMENT"
    assert gateway.create_timestamps[1] - gateway.create_timestamps[0] >= 1.0
