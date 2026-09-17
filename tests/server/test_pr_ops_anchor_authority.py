"""Server ingress tests for immutable diff-anchor authority artifacts."""

from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

import autoskillit.server.tools.tools_pr_ops as tools_pr_ops
from autoskillit.core import (
    ContainmentError,
    DiffAnchorAuthority,
    GitHubReviewPostResult,
    GitHubReviewRequest,
    ReviewOperationState,
)
from autoskillit.server.tools.tools_pr_ops import post_pr_review

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class _RecordingPoster:
    def __init__(self) -> None:
        self.requests: list[GitHubReviewRequest] = []

    async def post(self, request: GitHubReviewRequest) -> GitHubReviewPostResult:
        self.requests.append(request)
        return GitHubReviewPostResult(
            operation_key="f" * 64,
            head_sha=request.head_sha,
            state=ReviewOperationState.DRY_RUN,
        )


def _authority(*, repository: str = "owner/repo", pr_number: int = 7, head_sha: str = "a" * 40):
    return DiffAnchorAuthority.authoritative(
        repository=repository,
        pr_number=pr_number,
        head_sha=head_sha,
        generation_id="test-generation",
        right_side_lines={"src/example.py": [10]},
        left_side_lines={},
    )


def _managed_paths(cwd: Path) -> tuple[Path, Path]:
    root = cwd / ".autoskillit" / "temp"
    root.mkdir(parents=True)
    return (
        root / "anchor_authority_7.json",
        root / "batch_review_response_7.json",
    )


async def _post(
    cwd: Path,
    authority_path: Path,
    receipt_path: Path,
    *,
    repository: str = "owner/repo",
) -> dict[str, object]:
    return json.loads(
        await post_pr_review(
            cwd=str(cwd.resolve()),
            receipt_path=str(receipt_path),
            anchor_authority_path=str(authority_path),
            repository=repository,
            pr_number=7,
            head_sha="a" * 40,
            logical_iteration="review-pr:1",
            event="COMMENT",
            body="",
            comments=[],
            dry_run=True,
            ctx=Mock(),
        )
    )


def test_post_pr_review_requires_anchor_authority_path() -> None:
    parameter = inspect.signature(post_pr_review).parameters["anchor_authority_path"]

    assert parameter.default is inspect.Parameter.empty


@pytest.mark.anyio
@pytest.mark.parametrize("unsafe_kind", ["outside", "symlink", "hardlink", "traversal"])
async def test_anchor_authority_path_is_containment_checked(
    tool_ctx,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    unsafe_kind: str,
) -> None:
    cwd = tmp_path / "repo"
    authority_path, receipt_path = _managed_paths(cwd)
    authority_path.write_text(json.dumps(_authority().to_wire()))
    if unsafe_kind == "outside":
        outside = tmp_path / "anchor_authority_7.json"
        outside.write_text(authority_path.read_text())
        unsafe_path = outside
    elif unsafe_kind == "symlink":
        linked = authority_path.with_name("source.json")
        authority_path.rename(linked)
        authority_path.symlink_to(linked)
        unsafe_path = authority_path
    elif unsafe_kind == "hardlink":
        linked = authority_path.with_name("source.json")
        authority_path.rename(linked)
        os.link(linked, authority_path)
        unsafe_path = authority_path
    else:
        nested = authority_path.parent / "nested"
        nested.mkdir()
        unsafe_path = nested / ".." / authority_path.name

    poster = _RecordingPoster()
    monkeypatch.setattr(tool_ctx, "github_review_poster", poster)
    monkeypatch.setattr(tools_pr_ops, "_get_ctx", lambda: tool_ctx)
    monkeypatch.setattr(tools_pr_ops, "_notify", AsyncMock())

    result = await _post(cwd, unsafe_path, receipt_path)

    assert result["state"] == ReviewOperationState.TERMINAL.value
    assert poster.requests == []


@pytest.mark.anyio
async def test_anchor_authority_changed_during_read_is_rejected(
    tool_ctx,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cwd = tmp_path / "repo"
    authority_path, receipt_path = _managed_paths(cwd)
    authority_path.write_text(json.dumps(_authority().to_wire()))
    poster = _RecordingPoster()
    monkeypatch.setattr(tool_ctx, "github_review_poster", poster)
    monkeypatch.setattr(tools_pr_ops, "_get_ctx", lambda: tool_ctx)
    monkeypatch.setattr(tools_pr_ops, "_notify", AsyncMock())
    monkeypatch.setattr(
        tools_pr_ops,
        "read_stable_contained_bytes",
        Mock(side_effect=ContainmentError("modified between reads (TOCTOU)")),
    )

    result = await _post(cwd, authority_path, receipt_path)

    assert result["state"] == ReviewOperationState.TERMINAL.value
    assert poster.requests == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "authority",
    [
        _authority(repository="other/repo"),
        _authority(pr_number=8),
        _authority(head_sha="b" * 40),
    ],
)
async def test_authority_identity_must_match_request(
    tool_ctx,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    authority: DiffAnchorAuthority,
) -> None:
    cwd = tmp_path / "repo"
    authority_path, receipt_path = _managed_paths(cwd)
    authority_path.write_text(json.dumps(authority.to_wire()))
    poster = _RecordingPoster()
    monkeypatch.setattr(tool_ctx, "github_review_poster", poster)
    monkeypatch.setattr(tools_pr_ops, "_get_ctx", lambda: tool_ctx)
    monkeypatch.setattr(tools_pr_ops, "_notify", AsyncMock())

    result = await _post(cwd, authority_path, receipt_path)

    assert result["state"] == ReviewOperationState.TERMINAL.value
    assert poster.requests == []


@pytest.mark.anyio
async def test_authority_repository_identity_is_case_insensitive(
    tool_ctx,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cwd = tmp_path / "repo"
    authority_path, receipt_path = _managed_paths(cwd)
    authority = _authority()
    authority_path.write_text(json.dumps(authority.to_wire()))
    poster = _RecordingPoster()
    monkeypatch.setattr(tool_ctx, "github_review_poster", poster)
    monkeypatch.setattr(tools_pr_ops, "_get_ctx", lambda: tool_ctx)
    monkeypatch.setattr(tools_pr_ops, "_notify", AsyncMock())

    result = await _post(cwd, authority_path, receipt_path, repository="Owner/Repo")

    assert result["state"] == ReviewOperationState.DRY_RUN.value
    assert poster.requests[0].anchor_authority == authority


@pytest.mark.anyio
async def test_malformed_anchor_authority_is_rejected_before_mutation(
    tool_ctx,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cwd = tmp_path / "repo"
    authority_path, receipt_path = _managed_paths(cwd)
    authority_path.write_text('{"repository":"owner/repo","repository":"forged/repo"}')
    poster = _RecordingPoster()
    monkeypatch.setattr(tool_ctx, "github_review_poster", poster)
    monkeypatch.setattr(tools_pr_ops, "_get_ctx", lambda: tool_ctx)
    monkeypatch.setattr(tools_pr_ops, "_notify", AsyncMock())

    result = await _post(cwd, authority_path, receipt_path)

    assert result["state"] == ReviewOperationState.TERMINAL.value
    assert poster.requests == []
