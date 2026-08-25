from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.smoke_utils import (
    check_bug_report_non_empty,
    consolidate_health_reports,
    enrich_diff_context,
)

pytestmark = [pytest.mark.medium]


def test_subprocess_calls_have_timeout() -> None:
    """All subprocess.run() calls in smoke_utils.py must have a timeout= argument."""
    import ast

    pkg = Path("src/autoskillit/smoke_utils")
    for py_file in sorted(pkg.glob("*.py")):
        src = py_file.read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "run"
            ):
                kw_names = {kw.arg for kw in node.keywords}
                assert "timeout" in kw_names, (
                    f"subprocess.run() at line {node.lineno} in {py_file.name} missing timeout="
                )


def test_smoke_utils_all_exports_complete() -> None:
    """smoke_utils.__all__ must list every public name."""
    import autoskillit.smoke_utils as su

    expected = {
        "EXPERIMENTAL_REVIEW_AUDITOR_REGISTRY",
        "EXPERIMENTAL_REVIEW_AUDITORS",
        "aggregate_combined_review_candidates",
        "aggregate_review_verdict",
        "annotate_pr_diff",
        "build_agent_eval_context",
        "build_eval_context",
        "build_malformed_review_envelope",
        "check_bug_report_non_empty",
        "check_commits_ahead",
        "check_loop_iteration",
        "check_loop_with_progress",
        "check_ref_state",
        "check_review_loop",
        "check_review_posted",
        "clear_review_annotation_context",
        "close_issue_already_done",
        "compile_eval_scorecard",
        "consolidate_health_reports",
        "compute_domain_partitions",
        "detect_zero_changes",
        "deletion_regression_is_eligible",
        "determine_experimental_review_verdict",
        "diagnose_merge_gate",
        "enrich_diff_context",
        "extract_investigation",
        "fetch_merge_queue_data",
        "init_counter",
        "LOCAL_ROUND_EXEMPT_VERDICTS",
        "normalize_local_review_finding",
        "parse_agent_eval_manifests",
        "parse_eval_manifests",
        "patch_pr_token_summary",
        "pre_iteration_cleanup",
        "prepare_experimental_review_publication",
        "publish_experimental_review_artifacts",
        "render_review_finding_body",
        "REVIEW_HANDOFF_IDENTITY_FIELDS",
        "REQUIRED_CRITERION_KEYS",
        "review_handoff_pair_error",
        "run_cross_interpreter_upgrade_smoke",
        "select_experimental_review_dispatch",
        "select_review_dimensions",
        "try_load_json",
        "validate_experimental_auditor_outputs",
        "VALID_CRITERION_TYPES",
    }
    assert set(su.__all__) == expected


@pytest.mark.parametrize(
    "name",
    [
        "aggregate_combined_review_candidates",
        "aggregate_review_verdict",
        "annotate_pr_diff",
        "build_agent_eval_context",
        "build_eval_context",
        "build_malformed_review_envelope",
        "check_bug_report_non_empty",
        "check_commits_ahead",
        "check_loop_iteration",
        "check_loop_with_progress",
        "check_review_loop",
        "clear_review_annotation_context",
        "close_issue_already_done",
        "compile_eval_scorecard",
        "consolidate_health_reports",
        "compute_domain_partitions",
        "detect_zero_changes",
        "deletion_regression_is_eligible",
        "determine_experimental_review_verdict",
        "diagnose_merge_gate",
        "enrich_diff_context",
        "extract_investigation",
        "fetch_merge_queue_data",
        "init_counter",
        "normalize_local_review_finding",
        "parse_agent_eval_manifests",
        "parse_eval_manifests",
        "patch_pr_token_summary",
        "pre_iteration_cleanup",
        "prepare_experimental_review_publication",
        "publish_experimental_review_artifacts",
        "render_review_finding_body",
        "review_handoff_pair_error",
        "select_experimental_review_dispatch",
        "select_review_dimensions",
        "try_load_json",
        "validate_experimental_auditor_outputs",
    ],
)
def test_smoke_utils_callable_resolvable_via_importlib(name: str) -> None:
    """Every public callable is resolvable via the same importlib path recipes use."""
    import importlib

    mod = importlib.import_module("autoskillit.smoke_utils")
    attr = getattr(mod, name)
    assert callable(attr)


@pytest.mark.parametrize(
    "callable_name,minimal_args",
    [
        ("annotate_pr_diff", {"pr_number": "1", "cwd": "/tmp/repo"}),
        ("parse_eval_manifests", {"canary_manifest": "{}", "variant_manifest": "{}"}),
        ("parse_agent_eval_manifests", {"canary_manifest": "{}", "variant_manifest": "{}"}),
        ("compute_domain_partitions", {"batch_branch": "b", "base_branch": "main", "cwd": "/tmp"}),
        ("fetch_merge_queue_data", {"base_branch": "main", "cwd": "/tmp"}),
        ("diagnose_merge_gate", {"test_stdout": "FAILED x", "test_stderr": ""}),
    ],
)
def test_callable_rejects_relative_output_dir(callable_name: str, minimal_args: dict) -> None:
    from autoskillit import smoke_utils

    func = getattr(smoke_utils, callable_name)
    with pytest.raises(ValueError, match="absolute"):
        func(**minimal_args, output_dir=".autoskillit/temp/test")


def test_enrich_diff_context_rejects_relative_project_dir() -> None:
    with pytest.raises(ValueError, match="absolute"):
        enrich_diff_context(pr_number="1", project_dir="relative/path", output_dir="/tmp")


def test_check_bug_report_non_empty_rejects_relative_workspace() -> None:
    with pytest.raises(ValueError, match="absolute"):
        check_bug_report_non_empty(workspace="relative/path")


def test_consolidate_health_reports_rejects_relative_log_dir() -> None:
    with pytest.raises(ValueError, match="absolute"):
        consolidate_health_reports(diagnostics_log_dir="relative/path", kitchen_id="test")
