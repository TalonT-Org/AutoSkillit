"""Validation, aggregation (with verdict derivation), and publication (with
rendering and atomic writes) helpers for proof-only PR review auditors.
"""

from __future__ import annotations

from autoskillit.smoke_utils.review._aggregation import (
    aggregate_combined_review_candidates,
    determine_experimental_review_verdict,
)
from autoskillit.smoke_utils.review._audit_collection import (
    collect_review_audit,
    evaluate_review_audit_slots,
)
from autoskillit.smoke_utils.review._audit_finalize import (
    REVIEW_DISPOSITION_REASON_CODES,
    finalize_review_audit,
)
from autoskillit.smoke_utils.review._audit_manifest import (
    REVIEW_AUDIT_MAX_ATTEMPTS,
    REVIEW_AUDIT_SLOT_FIELD,
    ReviewAuditInputError,
    load_review_audit_manifest,
    plan_review_audit,
    plan_review_audit_slots,
)
from autoskillit.smoke_utils.review._auditor_output import (
    AUDITOR_FINDINGS_MAX_BYTES,
    parse_auditor_findings_output,
)
from autoskillit.smoke_utils.review._publication import (
    normalize_local_review_finding,
    prepare_experimental_review_publication,
    publish_experimental_review_artifacts,
    render_review_finding_body,
    render_unpostable_review_section,
)
from autoskillit.smoke_utils.review._validation import (
    build_malformed_review_envelope,
    deletion_regression_is_eligible,
    validate_experimental_auditor_outputs,
)

__all__ = [
    "AUDITOR_FINDINGS_MAX_BYTES",
    "REVIEW_AUDIT_MAX_ATTEMPTS",
    "REVIEW_AUDIT_SLOT_FIELD",
    "REVIEW_DISPOSITION_REASON_CODES",
    "ReviewAuditInputError",
    "aggregate_combined_review_candidates",
    "build_malformed_review_envelope",
    "collect_review_audit",
    "deletion_regression_is_eligible",
    "determine_experimental_review_verdict",
    "evaluate_review_audit_slots",
    "finalize_review_audit",
    "load_review_audit_manifest",
    "normalize_local_review_finding",
    "parse_auditor_findings_output",
    "plan_review_audit",
    "plan_review_audit_slots",
    "prepare_experimental_review_publication",
    "publish_experimental_review_artifacts",
    "render_review_finding_body",
    "render_unpostable_review_section",
    "validate_experimental_auditor_outputs",
]
