"""Composed production tests for server-owned review receipts."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

import autoskillit.server.tools.tools_pr_ops as tools_pr_ops
from autoskillit.core import DiffAnchorAuthority, github_review_receipt_filename
from autoskillit.execution import (
    DefaultGitHubReviewPoster,
    GitHubReviewLedger,
    GitHubReviewMutationCoordinator,
)
from autoskillit.execution.github_review.gateway import DefaultGitHubReviewGateway
from autoskillit.server.tools.tools_pr_ops import post_pr_review, verify_review_receipt
from autoskillit.smoke_utils import github_review_handoff_error
from tests.execution.github_review.fakes import (
    AdvancingSleeper,
    CreateOutcome,
    ManualClock,
    StatefulReviewGateway,
    _poster,
)
from tests.fixtures.github_rest import pulls_comments_relocated_left
from tests.smoke_utils._review_publication_fixtures import _publish

pytestmark = [pytest.mark.layer("server"), pytest.mark.anyio, pytest.mark.medium]

_HEAD_SHA = "a" * 40
_LOGICAL_ITERATION = "review-pr:2"


def _review_paths(tmp_path: Path) -> tuple[Path, Path]:
    output_dir = tmp_path / ".autoskillit" / "temp" / "review-pr" / "iter_2"
    return output_dir, output_dir / github_review_receipt_filename(42)


def _prepare_review_context(
    tmp_path: Path,
    tool_ctx_kitchen_open: Any,
    monkeypatch: pytest.MonkeyPatch,
    outcomes: list[CreateOutcome],
) -> tuple[ManualClock, Path, Path]:
    clock = ManualClock()
    gateway = StatefulReviewGateway(clock=clock, outcomes=outcomes)
    poster = _poster(tmp_path / "ledger.sqlite3", gateway, clock)
    monkeypatch.setattr(tool_ctx_kitchen_open, "github_review_poster", poster)
    monkeypatch.setattr(tools_pr_ops, "_get_ctx", lambda: tool_ctx_kitchen_open)
    monkeypatch.setattr(tools_pr_ops, "_notify", AsyncMock())
    return (clock, *_review_paths(tmp_path))


async def _post_review(
    tmp_path: Path,
    receipt_path: Path,
    *,
    logical_iteration: str = _LOGICAL_ITERATION,
    head_sha: str = _HEAD_SHA,
    comments: list[dict[str, object]] | None = None,
) -> dict[str, Any]:
    anchor_authority_path = tmp_path / ".autoskillit" / "temp" / "anchor_authority_42.json"
    return json.loads(
        await post_pr_review(
            cwd=str(tmp_path),
            receipt_path=str(receipt_path),
            anchor_authority_path=str(anchor_authority_path),
            repository="octo/example",
            pr_number=42,
            head_sha=head_sha,
            logical_iteration=logical_iteration,
            event="COMMENT",
            body="Review body.",
            comments=comments or [],
            dry_run=False,
            ctx=Mock(),
        )
    )


async def _verify_review(
    tmp_path: Path,
    receipt_path: Path,
    **overrides: object,
) -> dict[str, Any]:
    arguments: dict[str, object] = {
        "cwd": str(tmp_path),
        "receipt_path": str(receipt_path),
        "repository": "octo/example",
        "pr_number": 42,
        "head_sha": _HEAD_SHA,
        "logical_iteration": _LOGICAL_ITERATION,
        "mode": "github",
        "post_state": "SUCCEEDED",
        "ctx": Mock(),
    }
    arguments.update(overrides)
    return json.loads(await verify_review_receipt(**arguments))  # type: ignore[arg-type]


def _handoff_error(output_dir: Path) -> str | None:
    return github_review_handoff_error(output_dir=str(output_dir), pr_number="42")


@pytest.mark.parametrize(
    "first_read,initial_comment_reads", [("missing_originals", 1), ("bad_link", 0)]
)
async def test_real_gateway_recovers_pending_relocated_left_multiline_review(
    first_read: str,
    initial_comment_reads: int,
    tool_ctx_kitchen_open: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed = pulls_comments_relocated_left()
    comment_id = observed["id"]
    review_id = observed["pull_request_review_id"]
    path = observed["path"]
    old_head = observed["original_commit_id"]
    later_head = observed["commit_id"]
    original_line = observed["original_line"]
    original_start_line = observed["original_start_line"]
    assert observed["side"] == observed["start_side"] == "LEFT"
    assert observed["line"] != original_line
    assert observed["start_line"] != original_start_line
    assert later_head != old_head

    authority = DiffAnchorAuthority.authoritative(
        repository="octo/example",
        pr_number=42,
        head_sha=old_head,
        generation_id="synthetic-production-seam",
        right_side_lines={},
        left_side_lines={path: range(original_start_line, original_line + 1)},
    )
    authority_path = tmp_path / ".autoskillit" / "temp" / "anchor_authority_42.json"
    authority_path.parent.mkdir(parents=True)
    authority_path.write_text(
        json.dumps(authority.to_wire(), sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    comments = [
        {
            "path": path,
            "line": original_line,
            "body": "Captured comment coordinates must survive reconciliation.",
            "side": "LEFT",
            "start_line": original_start_line,
            "start_side": "LEFT",
        }
    ]

    calls: list[tuple[str, str]] = []
    unexpected_calls: list[tuple[str, str]] = []
    posted_payload: dict[str, Any] | None = None
    review_reads = 0
    comment_reads = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal posted_payload, review_reads, comment_reads
        calls.append((request.method, str(request.url)))
        route = request.url.path
        if request.method == "GET" and route == "/user":
            return httpx.Response(200, json={"login": "synthetic-review-author"})
        if request.method == "GET" and route == "/repos/octo/example/pulls/42":
            return httpx.Response(
                200,
                json={
                    "head": {"sha": old_head},
                    "user": {"login": "synthetic-pr-author"},
                },
            )
        if request.method == "POST" and route == "/repos/octo/example/pulls/42/reviews":
            posted_payload = json.loads(request.content)
            return httpx.Response(201, json={"id": review_id})
        if request.method == "GET" and route == "/repos/octo/example/pulls/42/reviews":
            review_reads += 1
            assert posted_payload is not None
            headers = {}
            if first_read == "bad_link" and review_reads == 1:
                headers["link"] = (
                    "<https://api.github.com/repos/octo/example/pulls/42/reviews?page=2; "
                    'rel="next"'
                )
            return httpx.Response(
                200,
                headers=headers,
                json=[
                    {
                        "id": review_id,
                        # Synthetic identity/body substitutions bind the captured comment to
                        # this hermetic seam; all captured coordinate and commit fields below
                        # remain untouched.
                        "body": posted_payload["body"],
                        "commit_id": old_head,
                        "state": "COMMENTED",
                        "user": {"login": "synthetic-review-author"},
                    }
                ],
            )
        if request.method == "GET" and route == "/repos/octo/example/pulls/42/comments":
            comment_reads += 1
            assert posted_payload is not None
            remote_comment = {
                **observed,
                # GitHub supplies the finding marker from the submitted body, not the
                # public capture used for the coordinate evidence.
                "body": posted_payload["comments"][0]["body"],
            }
            if first_read == "missing_originals" and comment_reads == 1:
                remote_comment.pop("original_line")
                remote_comment.pop("original_start_line")
            return httpx.Response(200, json=[remote_comment])
        unexpected_calls.append((request.method, str(request.url)))
        raise AssertionError(f"unexpected GitHub request: {request.method} {request.url}")

    transport = httpx.MockTransport(handle)
    clock = ManualClock()
    database_path = tmp_path / "real-gateway-ledger.sqlite3"

    def fresh_poster() -> DefaultGitHubReviewPoster:
        gateway = DefaultGitHubReviewGateway(
            token_factory=lambda: "synthetic-token",
            api_log=None,
            client_factory=lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs),
        )
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
            wall_clock=clock,
        )

    monkeypatch.setattr(tools_pr_ops, "_get_ctx", lambda: tool_ctx_kitchen_open)
    monkeypatch.setattr(tools_pr_ops, "_notify", AsyncMock())
    _, receipt_path = _review_paths(tmp_path)
    monkeypatch.setattr(tool_ctx_kitchen_open, "github_review_poster", fresh_poster())

    pending = await _post_review(
        tmp_path,
        receipt_path,
        head_sha=old_head,
        comments=comments,
    )

    reviews_url = "https://api.github.com/repos/octo/example/pulls/42/reviews?per_page=100"
    comments_url = "https://api.github.com/repos/octo/example/pulls/42/comments?per_page=100"
    expected_calls = [
        ("GET", "https://api.github.com/user"),
        ("GET", "https://api.github.com/repos/octo/example/pulls/42"),
        ("POST", "https://api.github.com/repos/octo/example/pulls/42/reviews"),
        ("GET", reviews_url),
    ]
    expected_calls.extend([("GET", comments_url)] * initial_comment_reads)
    assert calls == expected_calls
    assert unexpected_calls == []
    assert pending["state"] == "COMMITTED_PENDING_VERIFICATION"
    assert pending["operation_key"]
    assert pending["receipt"] is None
    assert not receipt_path.exists()

    recovery_poster = fresh_poster()
    monkeypatch.setattr(tool_ctx_kitchen_open, "github_review_poster", recovery_poster)
    recovered = await _post_review(
        tmp_path,
        receipt_path,
        head_sha=old_head,
        comments=comments,
    )

    expected_calls.extend(
        [
            ("GET", "https://api.github.com/user"),
            ("GET", reviews_url),
            ("GET", comments_url),
        ]
    )
    assert calls == expected_calls
    assert unexpected_calls == []
    assert recovered["state"] == "SUCCEEDED"
    assert recovered["operation_key"] == pending["operation_key"]
    assert recovered["head_sha"] == old_head
    assert recovered["review_id"] == review_id
    assert recovered["comment_ids"] == [comment_id]
    assert recovered["executed_mutation_count"] == 0
    assert recovered["receipt"]["head_sha"] == old_head
    assert recovered["receipt"]["review_id"] == review_id
    assert recovered["receipt"]["comment_ids"] == [comment_id]
    assert recovered["receipt"]["finding_dispositions"] == [
        {
            "original_index": 0,
            "canonical_index": 0,
            "kind": "POSTED",
            "reason": None,
            "remote_comment_id": comment_id,
        }
    ]

    verifier_poster = fresh_poster()
    monkeypatch.setattr(tool_ctx_kitchen_open, "github_review_poster", verifier_poster)
    canonical_receipt = verifier_poster.verify_receipt(pending["operation_key"])
    assert canonical_receipt is not None
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == canonical_receipt.to_dict()
    assert await _verify_review(
        tmp_path,
        receipt_path,
        head_sha=old_head,
        post_state="SUCCEEDED",
    ) == {"reviews_posted": "true"}

    assert sum(method == "POST" for method, _ in calls) == 1
    assert sum(url.endswith("/pulls/42") for _, url in calls) == 1


@pytest.mark.parametrize(
    ("outcomes", "expected_state"),
    [
        ([CreateOutcome(200, commit=True)], "SUCCEEDED"),
        (
            [CreateOutcome(502, data={"message": "Bad Gateway"}, commit=True)],
            "RECONCILED",
        ),
    ],
)
async def test_server_receipt_survives_github_publication_and_verifies(
    outcomes: list[CreateOutcome],
    expected_state: str,
    tool_ctx_kitchen_open: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, output_dir, receipt_path = _prepare_review_context(
        tmp_path, tool_ctx_kitchen_open, monkeypatch, outcomes
    )

    posted = await _post_review(tmp_path, receipt_path)

    assert posted["state"] == expected_state
    assert receipt_path.is_file()
    assert receipt_path.stat().st_mode & 0o777 == 0o600
    receipt_bytes = receipt_path.read_bytes()
    receipt_stat = receipt_path.stat()
    publication = _publish(output_dir, "github", written_at="t0")

    assert receipt_path.read_bytes() == receipt_bytes
    assert receipt_path.stat().st_ino == receipt_stat.st_ino
    assert receipt_path.stat().st_mtime_ns == receipt_stat.st_mtime_ns
    assert await _verify_review(tmp_path, receipt_path, post_state=expected_state) == {
        "reviews_posted": "true"
    }
    assert _handoff_error(output_dir) is None
    assert publication["publication_records"][-1]["artifact"] == "github_review_binding"


async def test_replayed_post_after_publication_still_verifies_and_pairs(
    tool_ctx_kitchen_open: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, output_dir, receipt_path = _prepare_review_context(
        tmp_path,
        tool_ctx_kitchen_open,
        monkeypatch,
        [CreateOutcome(200, commit=True)],
    )
    await _post_review(tmp_path, receipt_path)
    _publish(output_dir, "github", written_at="t0")

    replayed = await _post_review(tmp_path, receipt_path)

    assert replayed["replayed"] is True
    assert await _verify_review(tmp_path, receipt_path) == {"reviews_posted": "true"}
    assert _handoff_error(output_dir) is None


async def test_new_post_without_republication_is_rejected_by_consumer(
    tool_ctx_kitchen_open: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, output_dir, receipt_path = _prepare_review_context(
        tmp_path,
        tool_ctx_kitchen_open,
        monkeypatch,
        [CreateOutcome(200, commit=True), CreateOutcome(200, commit=True)],
    )
    await _post_review(tmp_path, receipt_path)
    _publish(output_dir, "github", written_at="t0")

    second_post = await _post_review(tmp_path, receipt_path, logical_iteration="review-pr:3")

    assert second_post["state"] == "SUCCEEDED"
    assert _handoff_error(output_dir) == (
        "receipt operation key does not match the github review binding"
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"cwd": "relative"},
        {"repository": "invalid"},
        {"pr_number": True},
        {"pr_number": 0},
        {"head_sha": "invalid"},
        {"logical_iteration": "review-pr"},
        {"post_state": "PREPARED"},
    ],
)
async def test_verifier_rejects_malformed_request_fields(
    overrides: dict[str, object],
    tool_ctx_kitchen_open: Any,
    tmp_path: Path,
) -> None:
    _, receipt_path = _review_paths(tmp_path)

    assert await _verify_review(tmp_path, receipt_path, **overrides) == {
        "reviews_posted": "false",
        "rejection_reason": "invalid_request",
    }


def _configure_verifier_rejection(
    case: str,
    tmp_path: Path,
    receipt_path: Path,
    clock: ManualClock,
    tool_ctx_kitchen_open: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, dict[str, object]]:
    overrides: dict[str, object] = {}
    if case == "binding_identity":
        receipt = json.loads(receipt_path.read_text())
        binding = json.loads((receipt_path.parent / "github_review_binding_42.json").read_text())
        identity = {key: value for key, value in binding.items() if key != "receipt_operation_key"}
        receipt_path.write_text(
            json.dumps({**receipt, **identity}, sort_keys=True, separators=(",", ":"))
        )
        receipt_path.chmod(0o600)
    elif case == "review_id":
        receipt = json.loads(receipt_path.read_text())
        receipt["review_id"] += 1
        receipt_path.write_text(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
        receipt_path.chmod(0o600)
    elif case == "head_sha":
        overrides["head_sha"] = "c" * 40
    elif case == "empty_ledger":
        empty_poster = _poster(
            tmp_path / "empty-ledger.sqlite3",
            StatefulReviewGateway(clock=clock),
            clock,
        )
        monkeypatch.setattr(tool_ctx_kitchen_open, "github_review_poster", empty_poster)
    elif case == "wrong_pr_filename":
        wrong_path = receipt_path.with_name("batch_review_response_43.json")
        wrong_path.write_bytes(receipt_path.read_bytes())
        wrong_path.chmod(0o600)
        receipt_path = wrong_path
    elif case == "outside_root":
        outside_path = tmp_path / "outside" / receipt_path.name
        outside_path.parent.mkdir(parents=True)
        outside_path.write_bytes(receipt_path.read_bytes())
        outside_path.chmod(0o600)
        receipt_path = outside_path
    elif case == "deleted":
        receipt_path.unlink()
    else:
        receipt_path.write_bytes(b'{"operation_key":"x","operation_key":"y"}')
        receipt_path.chmod(0o600)
    return receipt_path, overrides


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("binding_identity", "receipt_wire_mismatch"),
        ("review_id", "receipt_wire_mismatch"),
        ("head_sha", "identity_mismatch"),
        ("empty_ledger", "ledger_receipt_missing"),
        ("wrong_pr_filename", "invalid_request"),
        ("outside_root", "receipt_unreadable"),
        ("deleted", "receipt_unreadable"),
        ("malformed", "receipt_malformed"),
    ],
)
async def test_verifier_rejects_with_exact_reason(
    case: str,
    reason: str,
    tool_ctx_kitchen_open: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    clock, output_dir, receipt_path = _prepare_review_context(
        tmp_path,
        tool_ctx_kitchen_open,
        monkeypatch,
        [CreateOutcome(200, commit=True)],
    )
    await _post_review(tmp_path, receipt_path)
    _publish(output_dir, "github", written_at="t0")
    verify_path, overrides = _configure_verifier_rejection(
        case, tmp_path, receipt_path, clock, tool_ctx_kitchen_open, monkeypatch
    )

    assert await _verify_review(tmp_path, verify_path, **overrides) == {
        "reviews_posted": "false",
        "rejection_reason": reason,
    }


async def test_mixed_generation_handoff_is_rejected(
    tool_ctx_kitchen_open: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, output_dir, receipt_path = _prepare_review_context(
        tmp_path,
        tool_ctx_kitchen_open,
        monkeypatch,
        [CreateOutcome(200, commit=True)],
    )
    await _post_review(tmp_path, receipt_path)
    _publish(output_dir, "github", written_at="t0")
    diff_context = output_dir / "diff_context_42.json"
    generation_a = diff_context.read_bytes()
    _publish(output_dir, "github", written_at="t1")
    diff_context.write_bytes(generation_a)

    assert _handoff_error(output_dir) == (
        "paired review artifacts have mismatched review_generation_id"
    )
    assert await _verify_review(tmp_path, receipt_path) == {"reviews_posted": "true"}


async def test_github_publish_without_server_receipt_fails_closed(
    tool_ctx_kitchen_open: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, output_dir, _ = _prepare_review_context(
        tmp_path,
        tool_ctx_kitchen_open,
        monkeypatch,
        [CreateOutcome(200, commit=True)],
    )
    import autoskillit.smoke_utils.review._publication as publication_module

    write_spy = Mock(wraps=publication_module._write_temp_bytes)
    replace_spy = Mock(wraps=os.replace)
    monkeypatch.setattr(publication_module, "_write_temp_bytes", write_spy)
    monkeypatch.setattr(publication_module.os, "replace", replace_spy)

    with pytest.raises(ValueError, match=r"^receipt unreadable"):
        _publish(output_dir, "github", written_at="t0")

    write_spy.assert_not_called()
    replace_spy.assert_not_called()
    assert not output_dir.exists() or not any(output_dir.iterdir())


async def test_stale_publication_after_post_preserves_server_receipt(
    tool_ctx_kitchen_open: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, output_dir, receipt_path = _prepare_review_context(
        tmp_path,
        tool_ctx_kitchen_open,
        monkeypatch,
        [CreateOutcome(200, commit=True)],
    )
    await _post_review(tmp_path, receipt_path)
    _publish(output_dir, "github", written_at="t0")
    receipt_bytes = receipt_path.read_bytes()
    receipt_inode = receipt_path.stat().st_ino

    _publish(output_dir, "github", written_at="t1", fresh=False)

    assert receipt_path.read_bytes() == receipt_bytes
    assert receipt_path.stat().st_ino == receipt_inode
    assert not (output_dir / "diff_context_42.json").exists()
    assert not (output_dir / "github_review_binding_42.json").exists()
    assert await _verify_review(tmp_path, receipt_path) == {"reviews_posted": "true"}


async def test_local_mode_never_touches_receipt_and_stays_explicit(
    tool_ctx_kitchen_open: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, output_dir, receipt_path = _prepare_review_context(
        tmp_path,
        tool_ctx_kitchen_open,
        monkeypatch,
        [CreateOutcome(200, commit=True)],
    )
    await _post_review(tmp_path, receipt_path)
    receipt_bytes = receipt_path.read_bytes()

    _publish(output_dir, "local", written_at="t0")

    assert receipt_path.read_bytes() == receipt_bytes
    assert await _verify_review(tmp_path, receipt_path, mode="local", post_state="LOCAL") == {
        "reviews_posted": "true"
    }
    assert await _verify_review(tmp_path, receipt_path, mode="local", post_state="SUCCEEDED") == {
        "reviews_posted": "false",
        "rejection_reason": "invalid_request",
    }
