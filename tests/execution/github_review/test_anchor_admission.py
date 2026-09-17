"""The publication boundary admits every inline anchor and preserves rejected findings."""

from dataclasses import replace
from pathlib import Path

import pytest

from autoskillit.core import (
    DiffAnchorAuthority,
    GitHubReviewComment,
    ReviewFindingDispositionKind,
    ReviewOperationState,
)
from autoskillit.execution.github_review._poster_support import payload
from autoskillit.execution.github_review.canonical import canonical_findings

from .fakes import ManualClock, StatefulReviewGateway, _poster, _request

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


@pytest.mark.anyio
async def test_empty_authority_publishes_body_only_before_inline_cap(tmp_path: Path) -> None:
    authority = DiffAnchorAuthority.authoritative(
        repository="octo/example",
        pr_number=42,
        head_sha="a" * 40,
        generation_id="empty",
        right_side_lines={},
        left_side_lines={},
    )
    clock = ManualClock()
    gateway = StatefulReviewGateway(clock=clock)
    result = await _poster(tmp_path / "ledger.sqlite3", gateway, clock, review_comment_cap=0).post(
        _request(tmp_path, anchor_authority=authority)
    )
    assert result.state is ReviewOperationState.SUCCEEDED
    assert result.planned_comment_count == 0
    assert gateway.create_calls[0]["comments"] == []
    assert "Normalize this value." in gateway.create_calls[0]["body"]


def test_payload_cannot_be_built_from_unadmitted_findings(tmp_path: Path) -> None:
    request = _request(tmp_path)
    with pytest.raises(TypeError, match="admitted"):
        payload(
            request=request,
            operation_key="test",
            findings=canonical_findings(request),
            omitted=(),
            event="COMMENT",
        )


@pytest.mark.anyio
async def test_post_refuses_request_without_anchor_authority(tmp_path: Path) -> None:
    clock = ManualClock()
    gateway = StatefulReviewGateway(clock=clock)
    with pytest.raises(TypeError, match="anchor_authority"):
        request = replace(_request(tmp_path), anchor_authority=None)
        await _poster(tmp_path / "ledger.sqlite3", gateway, clock).post(request)
    assert gateway.create_calls == []


@pytest.mark.anyio
async def test_out_of_authority_comment_is_never_posted_and_appears_in_body(
    tmp_path: Path,
) -> None:
    authority = DiffAnchorAuthority.authoritative(
        repository="octo/example",
        pr_number=42,
        head_sha="a" * 40,
        generation_id="test",
        right_side_lines={"src/a.py": {10, 11}},
        left_side_lines={},
    )
    request = _request(
        tmp_path,
        anchor_authority=authority,
        comments=(
            GitHubReviewComment(path="src/a.py", line=10, body="First"),
            GitHubReviewComment(path="src/a.py", line=99, body="Outside finding"),
            GitHubReviewComment(path="src/a.py", line=11, body="Second"),
        ),
    )
    clock = ManualClock()
    gateway = StatefulReviewGateway(clock=clock)
    result = await _poster(tmp_path / "ledger.sqlite3", gateway, clock, review_comment_cap=2).post(
        request
    )
    assert result.state is ReviewOperationState.SUCCEEDED
    assert len(gateway.create_calls) == 1
    assert result.planned_comment_count == 2
    assert [item["line"] for item in gateway.create_calls[0]["comments"]] == [10, 11]
    assert "Outside Diff Range" in gateway.create_calls[0]["body"]
    assert "Outside finding" in gateway.create_calls[0]["body"]
    assert result.receipt is not None
    assert (
        result.receipt.finding_dispositions[1].kind is ReviewFindingDispositionKind.OMITTED_INVALID
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "line,start_line,start_side,admitted",
    [
        (12, None, None, True),
        (12, 10, "LEFT", True),
        (12, 9, "LEFT", False),
        (12, 10, "RIGHT", False),
    ],
)
async def test_left_and_multiline_anchors_are_admitted_or_rejected_at_the_boundary(
    tmp_path: Path,
    line: int,
    start_line: int | None,
    start_side: str | None,
    admitted: bool,
) -> None:
    authority = DiffAnchorAuthority.authoritative(
        repository="octo/example",
        pr_number=42,
        head_sha="a" * 40,
        generation_id="test",
        right_side_lines={},
        left_side_lines={"src/a.py": {10, 11, 12}},
    )
    request = _request(
        tmp_path,
        anchor_authority=authority,
        comments=(
            GitHubReviewComment(
                path="src/a.py",
                line=line,
                side="LEFT",
                start_line=start_line,
                start_side=start_side,
                body="Left finding",
            ),
        ),
    )
    clock = ManualClock()
    gateway = StatefulReviewGateway(clock=clock)
    result = await _poster(tmp_path / "ledger.sqlite3", gateway, clock).post(request)
    assert result.state is ReviewOperationState.SUCCEEDED
    assert len(gateway.create_calls[0]["comments"]) == int(admitted)
    if not admitted:
        assert "Left finding" in gateway.create_calls[0]["body"]


@pytest.mark.anyio
async def test_body_only_receipt_does_not_shadow_later_authoritative_resubmission(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    unavailable = DiffAnchorAuthority.unavailable(
        repository=request.repository,
        pr_number=request.pr_number,
        head_sha=request.head_sha,
    )
    clock = ManualClock()
    from .fakes import CreateOutcome

    gateway = StatefulReviewGateway(clock=clock, outcomes=[CreateOutcome(200, commit=True)] * 2)
    poster = _poster(tmp_path / "ledger.sqlite3", gateway, clock)
    first = await poster.post(replace(request, anchor_authority=unavailable))
    second = await poster.post(request)
    assert first.state is second.state is ReviewOperationState.SUCCEEDED
    assert first.operation_key != second.operation_key
    assert len(gateway.create_calls) == 2
    assert gateway.create_calls[0]["comments"] == []
    assert len(gateway.create_calls[1]["comments"]) == 1
