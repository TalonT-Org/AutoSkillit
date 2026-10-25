"""Composed production tests for server-owned review receipts."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

import autoskillit.server.tools.tools_pr_ops as tools_pr_ops
from autoskillit.server.tools.tools_pr_ops import post_pr_review, verify_review_receipt
from autoskillit.smoke_utils import (
    prepare_experimental_review_publication,
    publish_experimental_review_artifacts,
)
from tests.execution.github_review.fakes import (
    CreateOutcome,
    ManualClock,
    StatefulReviewGateway,
    _poster,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.anyio, pytest.mark.medium]

_HEAD_SHA = "a" * 40
_BASE_SHA = "b" * 40
_LOGICAL_ITERATION = "review-pr:2"


def _review_paths(tmp_path: Path) -> tuple[Path, Path]:
    output_dir = tmp_path / ".autoskillit" / "temp" / "review-pr" / "iter_2"
    return output_dir, output_dir / "batch_review_response_42.json"


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
) -> dict[str, Any]:
    anchor_authority_path = tmp_path / ".autoskillit" / "temp" / "anchor_authority_42.json"
    return json.loads(
        await post_pr_review(
            cwd=str(tmp_path),
            receipt_path=str(receipt_path),
            anchor_authority_path=str(anchor_authority_path),
            repository="octo/example",
            pr_number=42,
            head_sha=_HEAD_SHA,
            logical_iteration=logical_iteration,
            event="COMMENT",
            body="Review body.",
            comments=[],
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


def _publish(
    output_dir: Path,
    mode: str,
    *,
    written_at: str,
    fresh: bool = True,
) -> dict[str, Any]:
    publication = prepare_experimental_review_publication(
        raw_ledger={"candidate_records": []},
        survivors=[],
        snapshot={"head_sha": _HEAD_SHA, "base_sha": _BASE_SHA},
        annotation_generation_id="g" * 64,
        mode=mode,
        snapshot_is_fresh=fresh,
        handoff_metadata={"written_at": written_at, "schema_version": 2},
    )
    return publish_experimental_review_artifacts(
        publication=publication,
        output_dir=str(output_dir),
        pr_number="42",
    )


def _handoff_error(output_dir: Path) -> str | None:
    from autoskillit.smoke_utils import github_review_handoff_error

    return github_review_handoff_error(output_dir=str(output_dir), pr_number="42")


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
