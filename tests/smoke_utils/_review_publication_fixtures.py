"""Shared fixture builders for experimental review publication tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autoskillit.core import GitHubReviewReceipt
from autoskillit.smoke_utils import (
    prepare_experimental_review_publication,
    publish_experimental_review_artifacts,
)
from tests._github_review_fixtures import authoritative_receipt

_REVIEW_HEAD = "a" * 40
_REVIEW_BASE = "b" * 40
_GENERATION_IDENTITY_KEYS = (
    "_head_sha",
    "_base_sha",
    "_merge_base_sha",
    "annotation_generation_id",
    "review_generation_id",
)


def _write_server_receipt(
    output_dir: Path,
    *,
    pr_number: int = 42,
    head_sha: str = _REVIEW_HEAD,
    **overrides: object,
) -> tuple[GitHubReviewReceipt, Path]:
    receipt = authoritative_receipt(pr_number=pr_number, head_sha=head_sha, **overrides)
    receipt_path = output_dir / f"batch_review_response_{pr_number}.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt.to_dict(), sort_keys=True, separators=(",", ":")))
    receipt_path.chmod(0o600)
    return receipt, receipt_path


def _prepared_publication(
    mode: str,
    *,
    written_at: str = "t0",
    fresh: bool = True,
    head_sha: str = _REVIEW_HEAD,
) -> dict[str, Any]:
    return prepare_experimental_review_publication(
        raw_ledger={"candidate_records": []},
        survivors=[],
        snapshot={"head_sha": head_sha, "base_sha": _REVIEW_BASE},
        annotation_generation_id="g" * 64,
        mode=mode,
        snapshot_is_fresh=fresh,
        handoff_metadata={"written_at": written_at, "schema_version": 2},
    )


def _publish(
    output_dir: Path,
    mode: str,
    *,
    written_at: str = "t0",
    fresh: bool = True,
    pr_number: str = "42",
    head_sha: str = _REVIEW_HEAD,
) -> dict[str, Any]:
    return publish_experimental_review_artifacts(
        publication=_prepared_publication(
            mode,
            written_at=written_at,
            fresh=fresh,
            head_sha=head_sha,
        ),
        output_dir=str(output_dir),
        pr_number=pr_number,
    )
