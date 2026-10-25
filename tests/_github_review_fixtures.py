"""Core-only receipt fixtures shared across review test layers."""

from dataclasses import replace
from typing import Any

from autoskillit.core import (
    GitHubReviewReceipt,
    ReviewOperationState,
    ReviewReconciliationResult,
    ReviewResponseClass,
)


def authoritative_receipt(**overrides: Any) -> GitHubReviewReceipt:
    """Return the canonical server receipt used by verifier and publisher tests."""
    receipt = GitHubReviewReceipt(
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
    return replace(receipt, **overrides)
