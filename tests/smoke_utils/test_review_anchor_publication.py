from __future__ import annotations

from typing import Any

import pytest

from autoskillit.smoke_utils import prepare_experimental_review_publication

pytestmark = [pytest.mark.medium]


def _prepared_local_experimental_publication() -> dict[str, Any]:
    return prepare_experimental_review_publication(
        raw_ledger={"candidate_records": [{"candidate_id": "candidate-1"}]},
        survivors=[
            {
                "candidate_id": "candidate-1",
                "disposition_id": "disposition-1",
                "file": "src/app.py",
                "line": 42,
            }
        ],
        snapshot={
            "head_sha": "head",
            "base_sha": "base",
            "merge_base_sha": "merge",
            "diff_sha256": "diff",
        },
        annotation_generation_id="annotation-generation",
        mode="local",
        snapshot_is_fresh=True,
    )


def test_experimental_publication_emits_incomplete_v1_context_without_a_digest() -> None:
    """Only enrichment may create the content digest used to validate an anchor."""
    with pytest.raises(ValueError, match="anchor_digest"):
        prepare_experimental_review_publication(
            raw_ledger={"candidate_records": [{"candidate_id": "candidate-1"}]},
            survivors=[
                {
                    "candidate_id": "candidate-1",
                    "disposition_id": "disposition-1",
                    "file": "src/app.py",
                    "line": 42,
                    "anchor_digest": "untrusted-caller-value",
                }
            ],
            snapshot={
                "head_sha": "head",
                "base_sha": "base",
                "merge_base_sha": "merge",
                "diff_sha256": "diff",
            },
            annotation_generation_id="annotation-generation",
            mode="local",
            snapshot_is_fresh=True,
        )

    context = _prepared_local_experimental_publication()["artifacts"]["diff_context"]
    entry = context["context_entries"][0]
    assert context["schema_version"] == 1
    assert entry["path"] == "src/app.py"
    assert entry["line"] == 42
    assert "anchor_digest" not in entry
