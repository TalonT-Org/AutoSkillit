"""Tests for server/tools_pr_ops.py."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

import autoskillit.server.tools.tools_pr_ops as tools_pr_ops
from autoskillit.core.types import (
    DiffAnchorAuthority,
    GitHubReviewComment,
    GitHubReviewPostResult,
    GitHubReviewReceipt,
    GitHubReviewRequest,
    ReviewOperationState,
    ReviewReconciliationResult,
    ReviewResponseClass,
)
from autoskillit.pipeline.gate import DefaultGateState
from autoskillit.server.tools.tools_pr_ops import (
    _close_issues_sequentially,
    _map_api_reviews,
    _map_pr_view_reviews,
    bulk_close_issues,
    get_pr_reviews,
    post_pr_review,
    verify_review_receipt,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class _FakeReviewPoster:
    def __init__(self, result: GitHubReviewPostResult | None = None) -> None:
        self.result = result
        self.requests: list[GitHubReviewRequest] = []
        self.receipts: dict[str, GitHubReviewReceipt] = {}
        self.receipt_lookups: list[str] = []
        self.error: Exception | None = None

    async def post(self, request: GitHubReviewRequest) -> GitHubReviewPostResult:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result

    def verify_receipt(self, operation_key: str) -> GitHubReviewReceipt | None:
        self.receipt_lookups.append(operation_key)
        return self.receipts.get(operation_key)


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------


def test_map_api_reviews_extracts_user_login() -> None:
    """raw=[{"user": {"login": "alice"}, "state": "APPROVED", "body": ""}] → author="alice"."""
    raw = [{"user": {"login": "alice"}, "state": "APPROVED", "body": "LGTM"}]
    result = _map_api_reviews(raw)
    assert result == [{"author": "alice", "state": "APPROVED", "body": "LGTM"}]


def test_map_api_reviews_missing_user() -> None:
    """user=None → author=''."""
    raw = [{"user": None, "state": "COMMENTED", "body": ""}]
    result = _map_api_reviews(raw)
    assert result[0]["author"] == ""


def test_map_pr_view_reviews_extracts_author_login() -> None:
    """gh pr view format (author.login) → mapped correctly."""
    data = {
        "reviews": [{"author": {"login": "bob"}, "state": "CHANGES_REQUESTED", "body": "Fix this"}]
    }
    result = _map_pr_view_reviews(data)
    assert result == [{"author": "bob", "state": "CHANGES_REQUESTED", "body": "Fix this"}]


def test_map_pr_view_reviews_empty() -> None:
    """Empty data or missing 'reviews' key → []."""
    assert _map_pr_view_reviews({}) == []
    assert _map_pr_view_reviews({"reviews": []}) == []


@pytest.mark.anyio
async def test_close_issues_sequentially_all_succeed() -> None:
    """All gh calls return rc=0 → closed=[1,2], failed=[]."""
    with patch.object(
        tools_pr_ops,
        "_run_subprocess",
        new=AsyncMock(return_value=(0, "", "")),
    ):
        with patch(
            "autoskillit.server.tools.tools_pr_ops.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            closed, failed = await _close_issues_sequentially([1, 2], "closing", "/tmp")

    assert closed == [1, 2]
    assert failed == []


@pytest.mark.anyio
async def test_close_issues_sequentially_partial_failure() -> None:
    """First rc=0, second rc=1 → closed=[1], failed=[2]."""
    call_count = {"n": 0}

    async def _mock_subprocess(cmd, *, cwd, timeout):
        n = call_count["n"]
        call_count["n"] += 1
        return (0, "", "") if n == 0 else (1, "", "not found")

    with patch.object(
        tools_pr_ops,
        "_run_subprocess",
        new=_mock_subprocess,
    ):
        with patch(
            "autoskillit.server.tools.tools_pr_ops.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            closed, failed = await _close_issues_sequentially([1, 2], "", "/tmp")

    assert closed == [1]
    assert failed == [2]


# ---------------------------------------------------------------------------
# T5 — _close_issues_sequentially inserts inter-call delay
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_close_issues_sequentially_delays_between_calls() -> None:
    """Each gh issue close call is preceded by asyncio.sleep(1) after the first."""
    with patch.object(
        tools_pr_ops,
        "_run_subprocess",
        new=AsyncMock(return_value=(0, "", "")),
    ):
        with patch(
            "autoskillit.server.tools.tools_pr_ops.asyncio.sleep",
            new_callable=AsyncMock,
        ) as mock_sleep:
            closed, failed = await _close_issues_sequentially([1, 2, 3], "", "/tmp")

    assert closed == [1, 2, 3]
    assert failed == []
    assert mock_sleep.call_count == 2
    for call in mock_sleep.call_args_list:
        assert call[0][0] == 1


# ---------------------------------------------------------------------------
# MCP tool handlers
# ---------------------------------------------------------------------------


def _authoritative_receipt() -> GitHubReviewReceipt:
    return GitHubReviewReceipt(
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


def _write_receipt(cwd: Path, payload: dict[str, object]) -> Path:
    path = cwd / ".autoskillit" / "temp" / "batch_review_response_7.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return path


async def _verify_receipt(cwd: Path, receipt_path: Path, **overrides: object) -> dict[str, str]:
    arguments: dict[str, object] = {
        "cwd": str(cwd.resolve()),
        "receipt_path": str(receipt_path),
        "repository": "owner/repo",
        "pr_number": 7,
        "head_sha": "a" * 40,
        "logical_iteration": "review-pr:1",
        "mode": "github",
        "post_state": "SUCCEEDED",
        "ctx": Mock(),
    }
    arguments.update(overrides)
    return json.loads(await verify_review_receipt(**arguments))  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_ledger_receipt_is_authoritative(
    tool_ctx_kitchen_open,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    receipt = _authoritative_receipt()
    poster = _FakeReviewPoster()
    poster.receipts[receipt.operation_key] = receipt
    monkeypatch.setattr(tool_ctx_kitchen_open, "github_review_poster", poster)
    monkeypatch.setattr(tools_pr_ops, "_get_ctx", lambda: tool_ctx_kitchen_open)
    receipt_path = _write_receipt(tmp_path, receipt.to_dict())

    result = await _verify_receipt(tmp_path, receipt_path)

    assert result == {"reviews_posted": "true"}
    assert poster.receipt_lookups == [receipt.operation_key]


@pytest.mark.anyio
async def test_handcrafted_receipt_without_ledger_row_is_rejected(
    tool_ctx_kitchen_open,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(tool_ctx_kitchen_open, "github_review_poster", _FakeReviewPoster())
    monkeypatch.setattr(tools_pr_ops, "_get_ctx", lambda: tool_ctx_kitchen_open)
    receipt_path = _write_receipt(tmp_path, _authoritative_receipt().to_dict())

    result = await _verify_receipt(tmp_path, receipt_path)

    assert result == {"reviews_posted": "false"}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("parameter", "value"),
    [
        ("repository", "other/repo"),
        ("head_sha", "b" * 40),
        ("logical_iteration", "review-pr:2"),
        ("post_state", "RECONCILED"),
    ],
)
async def test_receipt_identity_must_match_independently_supplied_values(
    tool_ctx_kitchen_open,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    parameter: str,
    value: object,
) -> None:
    receipt = _authoritative_receipt()
    poster = _FakeReviewPoster()
    poster.receipts[receipt.operation_key] = receipt
    monkeypatch.setattr(tool_ctx_kitchen_open, "github_review_poster", poster)
    monkeypatch.setattr(tools_pr_ops, "_get_ctx", lambda: tool_ctx_kitchen_open)
    receipt_path = _write_receipt(tmp_path, receipt.to_dict())

    result = await _verify_receipt(tmp_path, receipt_path, **{parameter: value})

    assert result == {"reviews_posted": "false"}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("review_id", 902),
        ("review_id", True),
        ("comment_ids", [903]),
        ("reconciliation_result", "MATCHED"),
    ],
)
async def test_receipt_contents_must_match_ledger_row_field_for_field(
    tool_ctx_kitchen_open,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    receipt = _authoritative_receipt()
    poster = _FakeReviewPoster()
    poster.receipts[receipt.operation_key] = receipt
    monkeypatch.setattr(tool_ctx_kitchen_open, "github_review_poster", poster)
    monkeypatch.setattr(tools_pr_ops, "_get_ctx", lambda: tool_ctx_kitchen_open)
    payload = receipt.to_dict()
    payload[field] = value
    receipt_path = _write_receipt(tmp_path, payload)

    result = await _verify_receipt(tmp_path, receipt_path)

    assert result == {"reviews_posted": "false"}


@pytest.mark.anyio
@pytest.mark.parametrize(("post_state", "expected"), [("LOCAL", "true"), ("", "false")])
async def test_local_mode_gate_requires_explicit_local_marker(
    tool_ctx_kitchen_open,
    tmp_path: Path,
    post_state: str,
    expected: str,
) -> None:
    result = await _verify_receipt(
        tmp_path,
        tmp_path / "missing.json",
        mode="local",
        post_state=post_state,
    )

    assert result == {"reviews_posted": expected}


@pytest.mark.anyio
async def test_verify_review_receipt_gate_closed(tool_ctx, tmp_path: Path) -> None:
    tool_ctx.gate = DefaultGateState(enabled=False)

    result = await _verify_receipt(
        tmp_path, tmp_path / "missing.json", mode="local", post_state="LOCAL"
    )

    assert result["success"] is False
    assert result["subtype"] == "gate_error"


@pytest.mark.anyio
async def test_get_pr_reviews_gate_closed(tool_ctx) -> None:
    """Gate disabled → gate error JSON."""
    tool_ctx.gate = DefaultGateState(enabled=False)
    result = json.loads(await get_pr_reviews(42, "/tmp"))
    assert result["success"] is False
    assert result["subtype"] == "gate_error"


@pytest.mark.anyio
async def test_get_pr_reviews_with_repo_success(
    tool_ctx_kitchen_open, monkeypatch: pytest.MonkeyPatch
) -> None:
    """repo provided → gh api repos/{repo}/pulls/123/reviews path used."""
    api_response = json.dumps([{"user": {"login": "alice"}, "state": "APPROVED", "body": ""}])
    with patch.object(
        tools_pr_ops,
        "_run_subprocess",
        new=AsyncMock(return_value=(0, api_response, "")),
    ):
        result = json.loads(await get_pr_reviews(123, "/tmp", repo="owner/repo"))

    assert "reviews" in result
    assert result["reviews"][0]["author"] == "alice"


@pytest.mark.anyio
async def test_get_pr_reviews_without_repo_success(
    tool_ctx_kitchen_open, monkeypatch: pytest.MonkeyPatch
) -> None:
    """repo='' → gh pr view 123 --json reviews path used."""
    pr_view_response = json.dumps(
        {"reviews": [{"author": {"login": "bob"}, "state": "CHANGES_REQUESTED", "body": ""}]}
    )
    with patch.object(
        tools_pr_ops,
        "_run_subprocess",
        new=AsyncMock(return_value=(0, pr_view_response, "")),
    ):
        result = json.loads(await get_pr_reviews(123, "/tmp", repo=""))

    assert result["reviews"][0]["author"] == "bob"


@pytest.mark.anyio
async def test_get_pr_reviews_gh_failure(
    tool_ctx_kitchen_open, monkeypatch: pytest.MonkeyPatch
) -> None:
    """gh returns rc=1 → {"success": False, "error": ...}."""
    with patch.object(
        tools_pr_ops,
        "_run_subprocess",
        new=AsyncMock(return_value=(1, "", "repository not found")),
    ):
        result = json.loads(await get_pr_reviews(123, "/tmp", repo="owner/repo"))

    assert result["success"] is False
    assert "repository not found" in result["error"]


@pytest.mark.anyio
async def test_bulk_close_issues_gate_closed(tool_ctx) -> None:
    """Gate disabled → gate error JSON."""
    tool_ctx.gate = DefaultGateState(enabled=False)
    result = json.loads(await bulk_close_issues([1, 2], "", "/tmp"))
    assert result["success"] is False
    assert result["subtype"] == "gate_error"


@pytest.mark.anyio
async def test_bulk_close_issues_all_closed(
    tool_ctx_kitchen_open, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All succeed → {"closed": [1, 2, 3], "failed": []}."""
    with patch.object(
        tools_pr_ops,
        "_run_subprocess",
        new=AsyncMock(return_value=(0, "", "")),
    ):
        with patch(
            "autoskillit.server.tools.tools_pr_ops.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            result = json.loads(await bulk_close_issues([1, 2, 3], "", "/tmp"))

    assert result["closed"] == [1, 2, 3]
    assert result["failed"] == []


@pytest.mark.anyio
async def test_bulk_close_issues_partial_failure(
    tool_ctx_kitchen_open, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mixed outcomes → correct closed/failed split."""
    call_count = {"n": 0}

    async def _mock_subprocess(cmd, *, cwd, timeout):
        n = call_count["n"]
        call_count["n"] += 1
        return (0, "", "") if n % 2 == 0 else (1, "", "error")

    with patch.object(
        tools_pr_ops,
        "_run_subprocess",
        new=_mock_subprocess,
    ):
        with patch(
            "autoskillit.server.tools.tools_pr_ops.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            result = json.loads(await bulk_close_issues([1, 2, 3], "", "/tmp"))

    assert 1 in result["closed"]
    assert 2 in result["failed"]
    assert 3 in result["closed"]


def _successful_review_result(receipt_path: str) -> GitHubReviewPostResult:
    return GitHubReviewPostResult(
        operation_key="f" * 64,
        head_sha="a" * 40,
        state=ReviewOperationState.COMMITTED_PENDING_VERIFICATION,
        response_class=ReviewResponseClass.SUCCESS,
        review_id=901,
        comment_ids=(902,),
        planned_comment_count=1,
        executed_mutation_count=1,
        executed_comment_count=1,
        receipt_path=Path(receipt_path),
    )


@pytest.mark.anyio
async def test_post_pr_review_uses_injected_poster_with_exact_typed_request(
    tool_ctx,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """The headless tool delegates through _get_ctx without opening the kitchen gate."""
    receipt_path = str(tmp_path / ".autoskillit" / "temp" / "batch_review_response_7.json")
    authority_path = tmp_path / ".autoskillit" / "temp" / "anchor_authority_7.json"
    authority_path.parent.mkdir(parents=True, exist_ok=True)
    authority = DiffAnchorAuthority.authoritative(
        repository="o/r",
        pr_number=7,
        head_sha="a" * 40,
        generation_id="server-ingress-test",
        right_side_lines={"src/example.py": [9, 10, 11]},
        left_side_lines={},
    )
    authority_path.write_text(json.dumps(authority.to_wire()))
    expected = _successful_review_result(receipt_path)
    poster = _FakeReviewPoster(expected)
    monkeypatch.setattr(tool_ctx, "github_review_poster", poster)
    get_ctx = Mock(return_value=tool_ctx)
    notify = AsyncMock()
    monkeypatch.setattr(
        tools_pr_ops,
        "_get_ctx",
        get_ctx,
    )
    monkeypatch.setattr(
        tools_pr_ops,
        "_notify",
        notify,
    )
    fastmcp_ctx = Mock()
    comments = [
        {
            "path": "src/example.py",
            "body": "This branch is unreachable.",
            "line": 11,
            "side": "RIGHT",
            "start_line": 9,
            "start_side": "RIGHT",
        }
    ]

    result = json.loads(
        await post_pr_review(
            cwd=str(tmp_path),
            receipt_path=receipt_path,
            anchor_authority_path=str(authority_path),
            repository="o/r",
            pr_number=7,
            head_sha="a" * 40,
            logical_iteration="review-pr:3",
            event="REQUEST_CHANGES",
            body="One blocking finding.",
            comments=comments,
            dry_run=False,
            ctx=fastmcp_ctx,
        )
    )

    assert result == expected.to_dict()
    get_ctx.assert_called_once_with()
    assert len(poster.requests) == 1
    request = poster.requests[0]
    assert isinstance(request, GitHubReviewRequest)
    assert request.cwd == str(tmp_path)
    assert request.receipt_path == receipt_path
    assert request.repository == "o/r"
    assert request.pr_number == 7
    assert request.head_sha == "a" * 40
    assert request.logical_iteration == "review-pr:3"
    assert request.anchor_authority == authority
    assert request.event == "REQUEST_CHANGES"
    assert request.body == "One blocking finding."
    assert request.dry_run is False
    assert isinstance(request.comments, tuple)
    assert len(request.comments) == 1
    comment = request.comments[0]
    assert isinstance(comment, GitHubReviewComment)
    assert comment.path == "src/example.py"
    assert comment.body == "This branch is unreachable."
    assert comment.line == 11
    assert comment.side == "RIGHT"
    assert comment.start_line == 9
    assert comment.start_side == "RIGHT"
    assert notify.await_count >= 1
    assert all(call.args[0] is fastmcp_ctx for call in notify.await_args_list)
    assert tool_ctx.gate.enabled is False


@pytest.mark.anyio
async def test_post_pr_review_dry_run_is_delegated_without_gate(
    tool_ctx,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    receipt_path = str(tmp_path / ".autoskillit" / "temp" / "batch_review_response_7.json")
    expected = GitHubReviewPostResult(
        operation_key="f" * 64,
        head_sha="a" * 40,
        state=ReviewOperationState.DRY_RUN,
        planned_comment_count=0,
    )
    poster = _FakeReviewPoster(expected)
    monkeypatch.setattr(tool_ctx, "github_review_poster", poster)
    monkeypatch.setattr(
        tools_pr_ops,
        "_get_ctx",
        lambda: tool_ctx,
    )
    monkeypatch.setattr(
        tools_pr_ops,
        "_notify",
        AsyncMock(),
    )

    result = json.loads(
        await post_pr_review(
            cwd=str(tmp_path),
            receipt_path=receipt_path,
            anchor_authority_path=str(
                tmp_path / ".autoskillit" / "temp" / "anchor_authority_7.json"
            ),
            repository="o/r",
            pr_number=7,
            head_sha="a" * 40,
            logical_iteration="review-pr:3",
            event="COMMENT",
            body="Preview",
            comments=[],
            dry_run=True,
            ctx=Mock(),
        )
    )

    assert result == expected.to_dict()
    assert poster.requests[0].dry_run is True
    assert poster.requests[0].comments == ()
    assert tool_ctx.gate.enabled is False


@pytest.mark.anyio
async def test_post_pr_review_missing_poster_returns_structured_error(
    tool_ctx,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(tool_ctx, "github_review_poster", None)
    monkeypatch.setattr(
        tools_pr_ops,
        "_get_ctx",
        lambda: tool_ctx,
    )
    monkeypatch.setattr(
        tools_pr_ops,
        "_notify",
        AsyncMock(),
    )

    result = json.loads(
        await post_pr_review(
            cwd=str(tmp_path),
            receipt_path=str(tmp_path / ".autoskillit" / "temp" / "batch_review_response_7.json"),
            anchor_authority_path=str(
                tmp_path / ".autoskillit" / "temp" / "anchor_authority_7.json"
            ),
            repository="o/r",
            pr_number=7,
            head_sha="a" * 40,
            logical_iteration="review-pr:1",
            event="COMMENT",
            body="",
            comments=[],
            dry_run=False,
            ctx=Mock(),
        )
    )

    assert "success" not in result
    assert result["state"] == ReviewOperationState.AMBIGUOUS.value
    assert result["response_class"] == ReviewResponseClass.SERVER_ERROR.value
    assert "github_review_poster" in result["error"]


@pytest.mark.anyio
async def test_post_pr_review_never_raises_when_poster_fails(
    tool_ctx,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    poster = _FakeReviewPoster()
    poster.error = RuntimeError("poster exploded")
    monkeypatch.setattr(tool_ctx, "github_review_poster", poster)
    monkeypatch.setattr(
        tools_pr_ops,
        "_get_ctx",
        lambda: tool_ctx,
    )
    monkeypatch.setattr(
        tools_pr_ops,
        "_notify",
        AsyncMock(),
    )

    result = json.loads(
        await post_pr_review(
            cwd=str(tmp_path),
            receipt_path=str(tmp_path / ".autoskillit" / "temp" / "batch_review_response_7.json"),
            anchor_authority_path=str(
                tmp_path / ".autoskillit" / "temp" / "anchor_authority_7.json"
            ),
            repository="o/r",
            pr_number=7,
            head_sha="a" * 40,
            logical_iteration="review-pr:1",
            event="COMMENT",
            body="",
            comments=[],
            dry_run=False,
            ctx=Mock(),
        )
    )

    assert "success" not in result
    assert result["state"] == ReviewOperationState.AMBIGUOUS.value
    assert result["response_class"] == ReviewResponseClass.SERVER_ERROR.value
    assert result["error"] == "RuntimeError: poster exploded"


@pytest.mark.anyio
async def test_post_pr_review_never_raises_on_invalid_comment_shape(
    tool_ctx,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    poster = _FakeReviewPoster(
        _successful_review_result(
            str(tmp_path / ".autoskillit" / "temp" / "batch_review_response_7.json")
        )
    )
    monkeypatch.setattr(tool_ctx, "github_review_poster", poster)
    monkeypatch.setattr(
        tools_pr_ops,
        "_get_ctx",
        lambda: tool_ctx,
    )
    monkeypatch.setattr(
        tools_pr_ops,
        "_notify",
        AsyncMock(),
    )

    result = json.loads(
        await post_pr_review(
            cwd=str(tmp_path),
            receipt_path=str(tmp_path / ".autoskillit" / "temp" / "batch_review_response_7.json"),
            anchor_authority_path=str(
                tmp_path / ".autoskillit" / "temp" / "anchor_authority_7.json"
            ),
            repository="o/r",
            pr_number=7,
            head_sha="a" * 40,
            logical_iteration="review-pr:1",
            event="COMMENT",
            body="",
            comments=[{"path": "src/example.py"}],
            dry_run=False,
            ctx=Mock(),
        )
    )

    assert "success" not in result
    assert result["state"] == ReviewOperationState.TERMINAL.value
    assert result["response_class"] == ReviewResponseClass.CLIENT_ERROR.value
    assert result["error"]
    assert poster.requests == []
