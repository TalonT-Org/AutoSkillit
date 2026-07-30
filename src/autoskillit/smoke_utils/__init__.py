"""Utility callables for smoke-test pipeline run_python steps.

Known limitation: functions use hardcoded path conventions from the pipeline recipe.
"""

from autoskillit.smoke_utils._eval import (
    REQUIRED_CRITERION_KEYS,
    VALID_CRITERION_TYPES,
    build_agent_eval_context,
    build_eval_context,
    compile_eval_scorecard,
    parse_agent_eval_manifests,
    parse_eval_manifests,
)
from autoskillit.smoke_utils._experimental_review import (
    EXPERIMENTAL_REVIEW_AUDITORS,
    aggregate_experimental_review_candidates,
    build_malformed_review_envelope,
    deletion_regression_is_eligible,
    determine_experimental_review_verdict,
    prepare_experimental_review_publication,
    publish_experimental_review_artifacts,
    render_review_finding_body,
    validate_experimental_auditor_outputs,
)
from autoskillit.smoke_utils._git import (
    check_bug_report_non_empty,
    check_commits_ahead,
    check_ref_state,
    close_issue_already_done,
    compute_domain_partitions,
    detect_zero_changes,
    fetch_merge_queue_data,
    gate_backend_write,
)
from autoskillit.smoke_utils._helpers import try_load_json
from autoskillit.smoke_utils._investigation import extract_investigation
from autoskillit.smoke_utils._merge_gate_diagnosis import diagnose_merge_gate
from autoskillit.smoke_utils._review import (
    LOCAL_ROUND_EXEMPT_VERDICTS,
    aggregate_review_verdict,
    annotate_pr_diff,
    check_loop_iteration,
    check_loop_with_progress,
    check_review_loop,
    check_review_posted,
    clear_review_annotation_context,
    enrich_diff_context,
    init_counter,
    pre_iteration_cleanup,
    select_review_dimensions,
)
from autoskillit.smoke_utils._telemetry import consolidate_health_reports, patch_pr_token_summary

__all__ = [
    "EXPERIMENTAL_REVIEW_AUDITORS",
    "LOCAL_ROUND_EXEMPT_VERDICTS",
    "REQUIRED_CRITERION_KEYS",
    "VALID_CRITERION_TYPES",
    "aggregate_experimental_review_candidates",
    "aggregate_review_verdict",
    "annotate_pr_diff",
    "build_agent_eval_context",
    "build_eval_context",
    "build_malformed_review_envelope",
    "check_bug_report_non_empty",
    "check_commits_ahead",
    "check_loop_iteration",
    "check_ref_state",
    "check_loop_with_progress",
    "check_review_loop",
    "check_review_posted",
    "clear_review_annotation_context",
    "close_issue_already_done",
    "consolidate_health_reports",
    "compile_eval_scorecard",
    "compute_domain_partitions",
    "detect_zero_changes",
    "deletion_regression_is_eligible",
    "determine_experimental_review_verdict",
    "diagnose_merge_gate",
    "enrich_diff_context",
    "extract_investigation",
    "fetch_merge_queue_data",
    "gate_backend_write",
    "init_counter",
    "parse_agent_eval_manifests",
    "parse_eval_manifests",
    "patch_pr_token_summary",
    "pre_iteration_cleanup",
    "prepare_experimental_review_publication",
    "publish_experimental_review_artifacts",
    "render_review_finding_body",
    "select_review_dimensions",
    "try_load_json",
    "validate_experimental_auditor_outputs",
]
