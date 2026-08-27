"""Validation, aggregation (with verdict derivation), and publication (with
rendering and atomic writes) helpers for proof-only PR review auditors.
"""

from __future__ import annotations

from autoskillit.smoke_utils.review._aggregation import (
    aggregate_combined_review_candidates,
    determine_experimental_review_verdict,
)
from autoskillit.smoke_utils.review._publication import (
    normalize_local_review_finding,
    prepare_experimental_review_publication,
    publish_experimental_review_artifacts,
    render_review_finding_body,
)
from autoskillit.smoke_utils.review._validation import (
    build_malformed_review_envelope,
    deletion_regression_is_eligible,
    validate_experimental_auditor_outputs,
)

__all__ = [
    "aggregate_combined_review_candidates",
    "build_malformed_review_envelope",
    "deletion_regression_is_eligible",
    "determine_experimental_review_verdict",
    "normalize_local_review_finding",
    "prepare_experimental_review_publication",
    "publish_experimental_review_artifacts",
    "render_review_finding_body",
    "validate_experimental_auditor_outputs",
]
