"""Tests for smoke_utils callables."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from autoskillit.smoke_utils import (
    annotate_pr_diff,
    build_agent_eval_context,
    build_eval_context,
    check_bug_report_non_empty,
    check_loop_iteration,
    check_loop_with_progress,
    check_review_loop,
    compile_eval_scorecard,
    consolidate_health_reports,
    enrich_diff_context,
    parse_agent_eval_manifests,
    parse_eval_manifests,
    patch_pr_token_summary,
)
from tests.infra._token_summary_helpers import _resolve_session_label

pytestmark = [pytest.mark.medium]


# T_SU1
def test_returns_false_when_bug_report_missing(tmp_path: Path) -> None:
    """Returns {"non_empty": "false"} when bug_report.json does not exist."""
    result = check_bug_report_non_empty(str(tmp_path))
    assert result == {"non_empty": "false"}


# T_SU2
def test_returns_false_when_bug_report_empty_array(tmp_path: Path) -> None:
    """Returns {"non_empty": "false"} when bug_report.json contains []."""
    (tmp_path / "bug_report.json").write_text("[]")
    result = check_bug_report_non_empty(str(tmp_path))
    assert result == {"non_empty": "false"}


# T_SU3
def test_returns_true_when_bug_report_has_items(tmp_path: Path) -> None:
    """Returns {"non_empty": "true"} when bug_report.json has at least one item."""
    (tmp_path / "bug_report.json").write_text(json.dumps([{"bug": "x"}]))
    result = check_bug_report_non_empty(str(tmp_path))
    assert result == {"non_empty": "true"}


# T_SU4
def test_returns_false_when_bug_report_malformed(tmp_path: Path) -> None:
    """Returns {"non_empty": "false"} when bug_report.json contains malformed JSON."""
    (tmp_path / "bug_report.json").write_text("{not valid json")
    result = check_bug_report_non_empty(str(tmp_path))
    assert result == {"non_empty": "false"}


# ---------------------------------------------------------------------------
# T_CRL6–T_CRL8: check_review_loop tests (pure iteration guard)
# ---------------------------------------------------------------------------


# T_CRL6
def test_crl_next_iteration_increments() -> None:
    """next_iteration increments from current_iteration: "" → "1", "1" → "2", "2" → "3"."""
    r1 = check_review_loop("1", current_iteration="")
    assert r1["next_iteration"] == "1"

    r2 = check_review_loop("1", current_iteration="1")
    assert r2["next_iteration"] == "2"

    r3 = check_review_loop("1", current_iteration="2")
    assert r3["next_iteration"] == "3"


# T_CRL7
def test_crl_max_exceeded_when_next_iteration_ge_max() -> None:
    """max_exceeded=true when next_iteration >= max_iterations."""
    result = check_review_loop("1", current_iteration="2", max_iterations="3")
    assert result["max_exceeded"] == "true"
    assert result["next_iteration"] == "3"


# T_CRL8
def test_crl_max_not_exceeded_when_below_max() -> None:
    """max_exceeded=false when next_iteration < max_iterations."""
    result = check_review_loop("1", current_iteration="1", max_iterations="3")
    assert result["max_exceeded"] == "false"


def test_check_review_loop_always_continues_when_iterations_remain() -> None:
    """After a resolve cycle, check_review_loop must indicate continuation
    when max_iterations is not exceeded — regardless of GitHub thread state.

    The function is a pure iteration guard: if next_iteration < max_iterations,
    it must return max_exceeded=false so the recipe routes back to review_pr.
    """
    result = check_review_loop(
        pr_number="42",
        current_iteration="0",
        max_iterations="3",
    )
    assert result["max_exceeded"] == "false"
    assert result["next_iteration"] == "1"


def test_check_review_loop_stops_at_max_iterations() -> None:
    """When current_iteration reaches max_iterations, max_exceeded must be true."""
    result = check_review_loop(
        pr_number="42",
        current_iteration="2",
        max_iterations="3",
    )
    assert result["max_exceeded"] == "true"
    assert result["next_iteration"] == "3"


def test_check_review_loop_returns_expected_fields() -> None:
    """check_review_loop returns next/prev iteration, max_exceeded, had_blocking."""
    result = check_review_loop(pr_number="42")
    assert set(result.keys()) == {
        "next_iteration",
        "prev_iteration",
        "max_exceeded",
        "had_blocking",
    }


# T_CRL11 — verify check_review_loop has no subprocess calls
def test_check_review_loop_has_no_subprocess_calls() -> None:
    """The simplified check_review_loop must not use subprocess at all."""
    import ast

    src = Path("src/autoskillit/smoke_utils/_review.py").read_text()
    tree = ast.parse(src)

    # Find the check_review_loop function node
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "check_review_loop":
            for child in ast.walk(node):
                if isinstance(child, ast.Attribute) and child.attr == "run":
                    if isinstance(child.value, ast.Name) and child.value.id == "subprocess":
                        raise AssertionError(
                            "check_review_loop should not use subprocess.run() — "
                            "it is a pure iteration guard"
                        )
            break


# T_CRL12
def test_crl_had_blocking_true_when_changes_requested() -> None:
    """had_blocking=true when previous_verdict is changes_requested."""
    result = check_review_loop("42", previous_verdict="changes_requested")
    assert result["had_blocking"] == "true"


# T_CRL13
def test_crl_had_blocking_false_when_approved_with_comments() -> None:
    """had_blocking=false when previous_verdict is approved_with_comments."""
    result = check_review_loop("42", previous_verdict="approved_with_comments")
    assert result["had_blocking"] == "false"


# T_CRL14
def test_crl_had_blocking_false_when_empty_verdict() -> None:
    """had_blocking=false when previous_verdict is absent (first-pass guard)."""
    result = check_review_loop("42")
    assert result["had_blocking"] == "false"


# ---------------------------------------------------------------------------
# T_CRL15–T_CRL18: check_review_loop with local_review_rounds parameter
# ---------------------------------------------------------------------------


def test_crl_local_rounds_not_exhausted_approved_is_blocking() -> None:
    """When local_review_rounds > 0 and iteration < local_rounds, approved is blocking.

    The approved verdict must trigger re-review until local_review_rounds are
    exhausted, so review_loop_count advances on every local round.
    """
    result = check_review_loop(
        pr_number="42",
        current_iteration="0",
        max_iterations="6",
        previous_verdict="approved",
        local_review_rounds="3",
    )
    assert result["had_blocking"] == "true"
    assert result["next_iteration"] == "1"
    assert result["max_exceeded"] == "false"


def test_crl_local_rounds_exhausted_approved_is_non_blocking() -> None:
    """When iteration >= local_review_rounds, approved is non-blocking.

    Once local rounds are exhausted, approved exits to CI immediately.
    """
    result = check_review_loop(
        pr_number="42",
        current_iteration="3",
        max_iterations="6",
        previous_verdict="approved",
        local_review_rounds="3",
    )
    assert result["had_blocking"] == "false"
    assert result["next_iteration"] == "4"


def test_crl_changes_requested_always_blocking_regardless_of_local_rounds() -> None:
    """changes_requested is always blocking, even after local rounds exhausted."""
    result = check_review_loop(
        pr_number="42",
        current_iteration="5",
        max_iterations="6",
        previous_verdict="changes_requested",
        local_review_rounds="3",
    )
    assert result["had_blocking"] == "true"


def test_crl_no_local_rounds_approved_is_non_blocking() -> None:
    """When local_review_rounds is absent or zero, approved is non-blocking as before.

    This preserves backward compatibility: without local_review_rounds configured,
    the only blocking verdict is changes_requested.
    """
    result = check_review_loop(
        pr_number="42",
        current_iteration="0",
        max_iterations="3",
        previous_verdict="approved",
        local_review_rounds="",
    )
    assert result["had_blocking"] == "false"


def test_crl_local_rounds_zero_approved_is_non_blocking() -> None:
    """local_review_rounds="0" means no local rounds, approved is non-blocking."""
    result = check_review_loop(
        pr_number="42",
        current_iteration="0",
        max_iterations="3",
        previous_verdict="approved",
        local_review_rounds="0",
    )
    assert result["had_blocking"] == "false"


def test_crl_needs_human_non_blocking_when_local_rounds_exhausted() -> None:
    """needs_human is non-blocking when local rounds are exhausted."""
    result = check_review_loop(
        pr_number="42",
        current_iteration="3",
        max_iterations="6",
        previous_verdict="needs_human",
        local_review_rounds="3",
    )
    assert result["had_blocking"] == "false"


# ---------------------------------------------------------------------------
# T_CRL19–T_CRL21: approved_with_comments exemption from local_review_rounds
# ---------------------------------------------------------------------------


def test_crl_approved_with_comments_non_blocking_when_local_rounds_active() -> None:
    """approved_with_comments must NOT trigger re-review even when local_rounds are not exhausted.

    The resolve_review pass for approved_with_comments is one-shot. Re-reviewing
    after resolved warnings adds no value and wastes time budget.
    """
    result = check_review_loop(
        pr_number="42",
        current_iteration="1",
        max_iterations="6",
        previous_verdict="approved_with_comments",
        local_review_rounds="2",
    )
    assert result["had_blocking"] == "false"
    assert result["next_iteration"] == "2"


def test_crl_approved_with_comments_non_blocking_at_first_local_round() -> None:
    """approved_with_comments at iteration 0 with local_review_rounds > 0 is still non-blocking."""
    result = check_review_loop(
        pr_number="42",
        current_iteration="0",
        max_iterations="6",
        previous_verdict="approved_with_comments",
        local_review_rounds="3",
    )
    assert result["had_blocking"] == "false"


def test_local_round_exempt_verdicts_constant_exists() -> None:
    """LOCAL_ROUND_EXEMPT_VERDICTS must exist and contain approved_with_comments."""
    from autoskillit.smoke_utils import LOCAL_ROUND_EXEMPT_VERDICTS

    assert "approved_with_comments" in LOCAL_ROUND_EXEMPT_VERDICTS
    assert "changes_requested" not in LOCAL_ROUND_EXEMPT_VERDICTS
    assert "approved" not in LOCAL_ROUND_EXEMPT_VERDICTS


# ---------------------------------------------------------------------------
# T_SU_LI1–T_SU_LI5: check_loop_iteration tests (generic loop iteration guard)
# ---------------------------------------------------------------------------


def test_check_loop_iteration_first_call() -> None:
    """First iteration (empty string) → next=1, max_exceeded=false for max=2."""
    result = check_loop_iteration(current_iteration="", max_iterations="2")
    assert result == {"next_iteration": "1", "max_exceeded": "false"}


def test_check_loop_iteration_at_budget() -> None:
    """iteration=1, max=2 → next=2, max_exceeded=true."""
    result = check_loop_iteration(current_iteration="1", max_iterations="2")
    assert result == {"next_iteration": "2", "max_exceeded": "true"}


def test_check_loop_iteration_over_budget() -> None:
    """iteration=5, max=2 → max_exceeded=true."""
    result = check_loop_iteration(current_iteration="5", max_iterations="2")
    assert result == {"next_iteration": "6", "max_exceeded": "true"}


def test_check_loop_iteration_custom_max() -> None:
    """iteration=3, max=5 → next=4, max_exceeded=false."""
    result = check_loop_iteration(current_iteration="3", max_iterations="5")
    assert result == {"next_iteration": "4", "max_exceeded": "false"}


def test_check_loop_iteration_defaults() -> None:
    """No arguments → iteration=0, max=2 → next=1, max_exceeded=false."""
    result = check_loop_iteration()
    assert result == {"next_iteration": "1", "max_exceeded": "false"}


# ---------------------------------------------------------------------------
# T_SU_CL1–T_SU_CL4: check_loop_with_progress tests (progress-aware loop guard)
# ---------------------------------------------------------------------------


def test_check_loop_with_progress_zero_progress_first_iteration() -> None:
    """First zero-progress iteration returns zero_progress=false (needs 2 consecutive)."""
    result = check_loop_with_progress(
        current_iteration="1",
        max_iterations="5",
        issues_fixed_count="0",
        prev_issues_fixed_count="",
    )
    assert result["zero_progress"] == "false"
    assert result["next_iteration"] == "2"
    assert result["max_exceeded"] == "false"


def test_check_loop_with_progress_two_consecutive_zero() -> None:
    """Two consecutive zero-progress iterations returns zero_progress=true."""
    result = check_loop_with_progress(
        current_iteration="2",
        max_iterations="5",
        issues_fixed_count="0",
        prev_issues_fixed_count="0",
    )
    assert result["zero_progress"] == "true"
    assert result["next_iteration"] == "3"
    assert result["max_exceeded"] == "false"


def test_check_loop_with_progress_progress_after_zero() -> None:
    """Progress after a zero-progress iteration resets the detection."""
    result = check_loop_with_progress(
        current_iteration="2",
        max_iterations="5",
        issues_fixed_count="3",
        prev_issues_fixed_count="0",
    )
    assert result["zero_progress"] == "false"
    assert result["next_iteration"] == "3"
    assert result["max_exceeded"] == "false"


def test_check_loop_with_progress_propagates_prev_count() -> None:
    """zero_progress=false on first call propagates current as prev."""
    result = check_loop_with_progress(
        current_iteration="1",
        max_iterations="5",
        issues_fixed_count="2",
        prev_issues_fixed_count="",
    )
    assert result["prev_issues_fixed_count"] == "2"
    assert result["zero_progress"] == "false"
    assert result["next_iteration"] == "2"
    assert result["max_exceeded"] == "false"


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


# ---------------------------------------------------------------------------
# T_PTS1–T_PTS7: patch_pr_token_summary tests
# ---------------------------------------------------------------------------

PR_URL = "https://github.com/TestOwner/TestRepo/pull/42"


def _write_test_sessions(log_root: Path, entries: list[dict]) -> None:
    lines = []
    for entry in entries:
        index_entry = {
            "dir_name": entry["dir_name"],
            "cwd": entry.get("cwd", ""),
            "kitchen_id": entry.get("kitchen_id", ""),
            "order_id": entry.get("order_id", ""),
            "timestamp": entry.get("timestamp", "2026-01-01T00:00:00+00:00"),
        }
        lines.append(json.dumps(index_entry))
        session_dir = log_root / "sessions" / entry["dir_name"]
        session_dir.mkdir(parents=True, exist_ok=True)
        token_data = {
            "session_label": _resolve_session_label(entry),
            "input_tokens": entry.get("input_tokens", 1000),
            "output_tokens": entry.get("output_tokens", 500),
            "cache_write_tokens": entry.get("cache_write_tokens", 100),
            "cache_read_tokens": entry.get("cache_read_tokens", 200),
            "timing_seconds": entry.get("timing_seconds", 10.0),
            "order_id": entry.get("order_id", ""),
            "loc_insertions": entry.get("loc_insertions", 0),
            "loc_deletions": entry.get("loc_deletions", 0),
            "schema_version": 2,
        }
        if "model_identifier" in entry:
            token_data["model_identifier"] = entry["model_identifier"]
        (session_dir / "token_usage.json").write_text(json.dumps(token_data))
    (log_root / "sessions.jsonl").write_text("\n".join(lines) + "\n")


def _make_gh_mock(get_body: str = "", get_rc: int = 0, patch_rc: int = 0):
    def _mock_run(cmd, **_kwargs):
        if "--method" in cmd and "PATCH" in cmd:
            return subprocess.CompletedProcess(
                cmd, patch_rc, "", "" if patch_rc == 0 else "patch error"
            )
        return subprocess.CompletedProcess(
            cmd, get_rc, get_body if get_rc == 0 else "", "" if get_rc == 0 else "read error"
        )

    return _mock_run


# T_PTS1
@patch("time.sleep")
@patch("subprocess.run")
def test_pts_happy_path_appends_table(mock_run, _mock_sleep, tmp_path: Path) -> None:
    cwd = "/clone/test"
    _write_test_sessions(
        tmp_path,
        [
            {
                "dir_name": "s1",
                "cwd": cwd,
                "step_name": "plan",
                "input_tokens": 1000,
                "output_tokens": 500,
            },
            {
                "dir_name": "s2",
                "cwd": cwd,
                "step_name": "implement",
                "input_tokens": 2000,
                "output_tokens": 1000,
            },
            {
                "dir_name": "s3",
                "cwd": cwd,
                "step_name": "compose_pr",
                "input_tokens": 500,
                "output_tokens": 250,
            },
        ],
    )
    mock_run.side_effect = _make_gh_mock(get_body="## Summary\nSome PR body")
    result = patch_pr_token_summary(PR_URL, cwd, log_dir=str(tmp_path))
    assert result["success"] == "true"
    assert result["sessions_loaded"] == "3"
    patch_call = mock_run.call_args_list[-1]
    body_arg = [a for a in patch_call[0][0] if a.startswith("body=")][0]
    assert "## Token Usage Summary" in body_arg
    assert "plan" in body_arg
    assert "implement" in body_arg
    assert "compose_pr" in body_arg


# T_PTS2
@patch("time.sleep")
@patch("subprocess.run")
def test_pts_replaces_existing_partial_table(mock_run, _mock_sleep, tmp_path: Path) -> None:
    cwd = "/clone/test"
    _write_test_sessions(
        tmp_path,
        [
            {
                "dir_name": "s1",
                "cwd": cwd,
                "step_name": "plan",
                "input_tokens": 1000,
                "output_tokens": 500,
            },
            {
                "dir_name": "s2",
                "cwd": cwd,
                "step_name": "implement",
                "input_tokens": 2000,
                "output_tokens": 1000,
            },
            {
                "dir_name": "s3",
                "cwd": cwd,
                "step_name": "compose_pr",
                "input_tokens": 500,
                "output_tokens": 250,
            },
        ],
    )
    existing_body = (
        "## Summary\nSome text\n\n## Token Usage Summary\n\n"
        "| Step | old partial table |\n| compose_pr | 500 |"
    )
    mock_run.side_effect = _make_gh_mock(get_body=existing_body)
    result = patch_pr_token_summary(PR_URL, cwd, log_dir=str(tmp_path))
    assert result["success"] == "true"
    patch_call = mock_run.call_args_list[-1]
    body_arg = [a for a in patch_call[0][0] if a.startswith("body=")][0]
    assert body_arg.count("## Token Usage Summary") == 1
    assert "plan" in body_arg


# T_PTS3
def test_pts_invalid_pr_url() -> None:
    result = patch_pr_token_summary("not-a-url", "/clone/test")
    assert result["success"] == "false"
    assert "Invalid PR URL" in result["error"]


# T_PTS4
def test_pts_zero_sessions(tmp_path: Path) -> None:
    (tmp_path / "sessions.jsonl").write_text("")
    result = patch_pr_token_summary(PR_URL, "/clone/test", log_dir=str(tmp_path))
    assert result["success"] == "false"
    assert result["sessions_loaded"] == "0"


# T_PTS5
@patch("time.sleep")
@patch("subprocess.run")
def test_pts_cross_kitchen_sessions(mock_run, _mock_sleep, tmp_path: Path) -> None:
    cwd = "/clone/test"
    entries = [
        {
            "dir_name": f"s{i}",
            "cwd": cwd,
            "kitchen_id": "aaa",
            "step_name": f"step_a{i}",
            "input_tokens": 100,
            "output_tokens": 50,
        }
        for i in range(3)
    ] + [
        {
            "dir_name": f"s{i + 3}",
            "cwd": cwd,
            "kitchen_id": "bbb",
            "step_name": f"step_b{i}",
            "input_tokens": 100,
            "output_tokens": 50,
        }
        for i in range(3)
    ]
    _write_test_sessions(tmp_path, entries)
    mock_run.side_effect = _make_gh_mock(get_body="## Summary\nBody")
    result = patch_pr_token_summary(PR_URL, cwd, log_dir=str(tmp_path))
    assert result["success"] == "true"
    assert result["sessions_loaded"] == "6"


# T_PTS6
@patch("subprocess.run")
def test_pts_gh_api_read_failure(mock_run, tmp_path: Path) -> None:
    cwd = "/clone/test"
    _write_test_sessions(
        tmp_path,
        [
            {"dir_name": "s1", "cwd": cwd, "step_name": "plan"},
        ],
    )
    mock_run.side_effect = _make_gh_mock(get_rc=1)
    result = patch_pr_token_summary(PR_URL, cwd, log_dir=str(tmp_path))
    assert result["success"] == "false"
    assert "Failed to read PR" in result["error"]


# T_PTS7
@patch("time.sleep")
@patch("subprocess.run")
def test_pts_gh_api_patch_failure(mock_run, _mock_sleep, tmp_path: Path) -> None:
    cwd = "/clone/test"
    _write_test_sessions(
        tmp_path,
        [
            {"dir_name": "s1", "cwd": cwd, "step_name": "plan"},
        ],
    )
    mock_run.side_effect = _make_gh_mock(get_body="## Summary\nBody", patch_rc=1)
    result = patch_pr_token_summary(PR_URL, cwd, log_dir=str(tmp_path))
    assert result["success"] == "false"
    assert "Failed to patch PR" in result["error"]


# ---------------------------------------------------------------------------
# Null-safety tests (run_python None-input coercion)
# ---------------------------------------------------------------------------


def test_check_loop_iteration_none_current() -> None:
    result = check_loop_iteration(current_iteration=None)  # type: ignore[arg-type]
    assert result["next_iteration"] == "1"
    assert result["max_exceeded"] == "false"


def test_check_loop_iteration_none_max() -> None:
    result = check_loop_iteration(current_iteration="0", max_iterations=None)  # type: ignore[arg-type]
    assert result["next_iteration"] == "1"
    assert result["max_exceeded"] == "false"


def test_check_review_loop_none_current() -> None:
    result = check_review_loop(pr_number="1", current_iteration=None)  # type: ignore[arg-type]
    assert result["next_iteration"] == "1"


def test_check_review_loop_none_verdict() -> None:
    result = check_review_loop(pr_number="1", previous_verdict=None)  # type: ignore[arg-type]
    assert result["had_blocking"] == "false"


# ---------------------------------------------------------------------------
# T_PTS8–T_PTS11: order_id, efficiency table, and env-based scoping tests
# ---------------------------------------------------------------------------


# T_PTS8
@patch("time.sleep")
@patch("subprocess.run")
def test_pts_order_id_captures_cross_clone_sessions(mock_run, _mock_sleep, tmp_path: Path) -> None:
    """patch_pr_token_summary with order_id loads sessions from multiple cwd paths."""
    _write_test_sessions(
        tmp_path,
        [
            {
                "dir_name": "s-clone-a",
                "cwd": "/clone-A",
                "order_id": "issue-42",
                "step_name": "rectify",
                "input_tokens": 1000,
            },
            {
                "dir_name": "s-clone-b",
                "cwd": "/clone-B",
                "order_id": "issue-42",
                "step_name": "implement",
                "input_tokens": 2000,
            },
        ],
    )
    mock_run.side_effect = _make_gh_mock(get_body="## Summary\nBody")
    result = patch_pr_token_summary(PR_URL, order_id="issue-42", log_dir=str(tmp_path))
    assert result["success"] == "true"
    assert result["sessions_loaded"] == "2"
    patch_call = mock_run.call_args_list[-1]
    body_arg = [a for a in patch_call[0][0] if a.startswith("body=")][0]
    assert "rectify" in body_arg
    assert "implement" in body_arg


# T_PTS9
@patch("time.sleep")
@patch("subprocess.run")
def test_pts_generates_efficiency_table_when_loc_data_present(
    mock_run, _mock_sleep, tmp_path: Path
) -> None:
    """patch_pr_token_summary emits Token Efficiency table when LoC data exists."""
    _write_test_sessions(
        tmp_path,
        [
            {
                "dir_name": "s1",
                "cwd": "/clone/test",
                "step_name": "implement",
                "input_tokens": 1000,
                "output_tokens": 500,
                "loc_insertions": 120,
            },
        ],
    )
    mock_run.side_effect = _make_gh_mock(get_body="## Summary\nBody")
    result = patch_pr_token_summary(PR_URL, cwd="/clone/test", log_dir=str(tmp_path))
    assert result["success"] == "true"
    patch_call = mock_run.call_args_list[-1]
    body_arg = [a for a in patch_call[0][0] if a.startswith("body=")][0]
    assert "## Token Efficiency" in body_arg


# T_PTS10
@patch("time.sleep")
@patch("subprocess.run")
def test_pts_preserves_or_regenerates_efficiency_table(
    mock_run, _mock_sleep, tmp_path: Path
) -> None:
    """When overwriting existing summary, efficiency table is regenerated, not lost."""
    _write_test_sessions(
        tmp_path,
        [
            {
                "dir_name": "s1",
                "cwd": "/clone/test",
                "step_name": "implement",
                "input_tokens": 1000,
                "output_tokens": 500,
                "loc_insertions": 100,
            },
        ],
    )
    existing_body = (
        "## Summary\nSome text\n\n## Token Usage Summary\n\n"
        "| Step | old |\n\n"
        "## Token Efficiency\n\n| Step | old eff |\n"
    )
    mock_run.side_effect = _make_gh_mock(get_body=existing_body)
    result = patch_pr_token_summary(PR_URL, cwd="/clone/test", log_dir=str(tmp_path))
    assert result["success"] == "true"
    patch_call = mock_run.call_args_list[-1]
    body_arg = [a for a in patch_call[0][0] if a.startswith("body=")][0]
    assert body_arg.count("## Token Usage Summary") == 1
    assert "## Token Efficiency" in body_arg


# T_PTS11
@patch("time.sleep")
@patch("subprocess.run")
def test_pts_reads_order_id_from_dispatch_env(
    mock_run, _mock_sleep, tmp_path: Path, monkeypatch
) -> None:
    """patch_pr_token_summary auto-reads AUTOSKILLIT_DISPATCH_ID when order_id not passed."""
    monkeypatch.setenv("AUTOSKILLIT_DISPATCH_ID", "issue-42")
    _write_test_sessions(
        tmp_path,
        [
            {
                "dir_name": "s-clone-a",
                "cwd": "/clone-A",
                "order_id": "issue-42",
                "step_name": "rectify",
                "input_tokens": 1000,
            },
            {
                "dir_name": "s-clone-b",
                "cwd": "/clone-B",
                "order_id": "issue-42",
                "step_name": "implement",
                "input_tokens": 2000,
            },
        ],
    )
    mock_run.side_effect = _make_gh_mock(get_body="## Summary\nBody")
    result = patch_pr_token_summary(PR_URL, cwd="", log_dir=str(tmp_path))
    assert result["success"] == "true"
    assert result["sessions_loaded"] == "2"


# ---------------------------------------------------------------------------
# T_PTS12–T_PTS14: model usage breakdown + section_re coverage
# ---------------------------------------------------------------------------


# T_PTS12
@patch("time.sleep")
@patch("subprocess.run")
def test_pts_includes_model_usage_breakdown(mock_run, _mock_sleep, tmp_path: Path) -> None:
    """patch_pr_token_summary produces all 3 telemetry sections when model data is present."""
    existing_body = (
        "## Summary\nSome text\n\n"
        "## Token Usage Summary\n\n| Step | old |\n\n"
        "## Token Efficiency\n\n| Step | old eff |\n\n"
        "## Model Usage Breakdown\n\n| Model | old model |\n\n"
        "## Next Section\nFollowing content"
    )
    _write_test_sessions(
        tmp_path,
        [
            {
                "dir_name": "s1",
                "cwd": "/clone/test",
                "step_name": "implement",
                "input_tokens": 1000,
                "output_tokens": 500,
                "loc_insertions": 120,
                "model_identifier": "claude-sonnet-4-6",
            },
        ],
    )
    mock_run.side_effect = _make_gh_mock(get_body=existing_body)
    result = patch_pr_token_summary(PR_URL, cwd="/clone/test", log_dir=str(tmp_path))
    assert result["success"] == "true"
    patch_call = mock_run.call_args_list[-1]
    body_arg = next((a for a in patch_call[0][0] if a.startswith("body=")), None)
    assert body_arg is not None, "body= argument not found in gh patch call"
    assert "## Token Usage Summary" in body_arg
    assert "## Token Efficiency" in body_arg
    assert "## Model Usage Breakdown" in body_arg
    assert body_arg.count("## Token Usage Summary") == 1
    assert body_arg.count("## Token Efficiency") == 1
    assert body_arg.count("## Model Usage Breakdown") == 1
    assert "claude-sonnet-4-6" in body_arg
    assert "## Next Section" in body_arg


# T_PTS13
def test_section_re_consumes_all_three_sections() -> None:
    """section_re matches across all three telemetry sections."""
    from autoskillit.core import PR_TELEMETRY_SECTIONS
    from autoskillit.smoke_utils._telemetry import _PR_SECTION_RE

    body = (
        "## Summary\nIntro\n\n"
        "## Token Usage Summary\n\n| Step | data |\n\n"
        "## Token Efficiency\n\n| Step | eff |\n\n"
        "## Model Usage Breakdown\n\n| Model | model data |\n\n"
        "## Next Section\nMore"
    )
    m = _PR_SECTION_RE.search(body)
    assert m is not None
    matched = m.group(0)
    for section in PR_TELEMETRY_SECTIONS:
        assert section in matched


# T_PTS14
def test_section_re_covers_all_pr_telemetry_sections() -> None:
    """Every section in PR_TELEMETRY_SECTIONS is consumed by section_re when all are present."""
    from autoskillit.core import PR_TELEMETRY_SECTIONS
    from autoskillit.smoke_utils._telemetry import _PR_SECTION_RE

    parts = []
    for section in PR_TELEMETRY_SECTIONS:
        parts.append(f"{section}\n\n| data | here |")
    body = "## Summary\nIntro\n\n" + "\n\n".join(parts) + "\n\n## Other\nEnd"
    m = _PR_SECTION_RE.search(body)
    assert m is not None
    matched = m.group(0)
    for section in PR_TELEMETRY_SECTIONS:
        assert section in matched, f"{section} not consumed by section_re"


# ---------------------------------------------------------------------------
# T_EDC1–T_EDC3: enrich_diff_context tests
# ---------------------------------------------------------------------------

_ANNOTATED_DIFF_CONTENT = (
    "+++ b/src/app.py\n"
    "@@ -38,10 +38,12 @@ def main():\n"
    "[L38] existing_line_38\n"
    "[L39] existing_line_39\n"
    "[L40]+new_import\n"
    "[L41]+another_import\n"
    "[L42] existing_42\n"
    "[L43] existing_43\n"
    "[L44]+added_44\n"
    "[L45] existing_45\n"
)


def _setup_handoff(tmp_path: Path, entries: list[dict]) -> None:
    review_dir = tmp_path / ".autoskillit" / "temp" / "review-pr"
    review_dir.mkdir(parents=True)
    handoff = {"schema_version": 1, "context_entries": entries}
    (review_dir / "diff_context_123.json").write_text(json.dumps(handoff))
    (review_dir / "annotated_diff_123.txt").write_text(_ANNOTATED_DIFF_CONTENT)


# T_EDC1
def test_enrich_diff_context_fills_empty_code_regions(tmp_path: Path) -> None:
    """enrich_diff_context populates empty code_region from annotated diff."""
    _setup_handoff(
        tmp_path,
        [
            {"path": "src/app.py", "line": 42, "severity": "critical", "code_region": ""},
        ],
    )
    review_dir = tmp_path / ".autoskillit" / "temp" / "review-pr"
    result = enrich_diff_context(
        pr_number="123", project_dir=str(tmp_path), output_dir=str(review_dir)
    )
    assert result["enriched"] == "true"
    assert result["enriched_count"] == "1"

    handoff_path = review_dir / "diff_context_123.json"
    handoff = json.loads(handoff_path.read_text())
    assert "[L42]" in handoff["context_entries"][0]["code_region"]


# T_EDC2
def test_enrich_diff_context_preserves_existing_code_regions(tmp_path: Path) -> None:
    """enrich_diff_context does not overwrite non-empty code_region values."""
    _setup_handoff(
        tmp_path,
        [
            {
                "path": "src/app.py",
                "line": 42,
                "severity": "critical",
                "code_region": "pre-existing",
            },
            {"path": "src/app.py", "line": 40, "severity": "warning", "code_region": ""},
        ],
    )
    review_dir = tmp_path / ".autoskillit" / "temp" / "review-pr"
    result = enrich_diff_context(
        pr_number="123", project_dir=str(tmp_path), output_dir=str(review_dir)
    )
    assert result["enriched"] == "true"
    assert result["enriched_count"] == "1"

    handoff_path = review_dir / "diff_context_123.json"
    handoff = json.loads(handoff_path.read_text())
    assert handoff["context_entries"][0]["code_region"] == "pre-existing"
    assert "[L40]" in handoff["context_entries"][1]["code_region"]


# T_EDC3
def test_enrich_diff_context_missing_handoff_file(tmp_path: Path) -> None:
    """enrich_diff_context returns gracefully when handoff file does not exist."""
    result = enrich_diff_context(
        pr_number="999", project_dir=str(tmp_path), output_dir=str(tmp_path)
    )
    assert result["enriched"] == "false"
    assert result["reason"] == "handoff_not_found"


# ---------------------------------------------------------------------------
# T3.1–T3.7: annotate_pr_diff review_mode tests
# ---------------------------------------------------------------------------

_DIFF_OUTPUT = "+++ b/src/app.py\n@@ -1,3 +1,4 @@\n line1\n+added\n"


@patch("subprocess.run")
def test_annotate_pr_diff_returns_review_mode_local(mock_run, tmp_path: Path) -> None:
    """T3.1: iteration < local_rounds → review_mode=local."""
    mock_run.return_value = subprocess.CompletedProcess([], 0, _DIFF_OUTPUT, "")
    result = annotate_pr_diff(
        pr_number="123",
        cwd=str(tmp_path),
        output_dir=str(tmp_path),
        local_review_rounds="3",
        current_iteration="0",
        base_branch="main",
    )
    assert result["review_mode"] == "local"


@patch("subprocess.run")
def test_annotate_pr_diff_returns_review_mode_github(mock_run, tmp_path: Path) -> None:
    """T3.2: iteration >= local_rounds → review_mode=github."""
    mock_run.return_value = subprocess.CompletedProcess([], 0, _DIFF_OUTPUT, "")
    result = annotate_pr_diff(
        pr_number="123",
        cwd=str(tmp_path),
        output_dir=str(tmp_path),
        local_review_rounds="3",
        current_iteration="3",
    )
    assert result["review_mode"] == "github"


@patch("subprocess.run")
def test_annotate_pr_diff_local_mode_uses_git_diff(mock_run, tmp_path: Path) -> None:
    """T3.3: local mode calls git diff with base...HEAD."""
    mock_run.return_value = subprocess.CompletedProcess([], 0, _DIFF_OUTPUT, "")
    annotate_pr_diff(
        pr_number="123",
        cwd=str(tmp_path),
        output_dir=str(tmp_path),
        local_review_rounds="2",
        current_iteration="0",
        base_branch="main",
    )
    args = mock_run.call_args_list[0][0][0]
    assert args[:3] == ["git", "diff", "main...HEAD"]
    assert mock_run.call_count == 2


@patch("subprocess.run")
def test_annotate_pr_diff_github_mode_uses_gh_pr_diff(mock_run, tmp_path: Path) -> None:
    """T3.4: github mode calls gh pr diff."""
    mock_run.return_value = subprocess.CompletedProcess([], 0, _DIFF_OUTPUT, "")
    annotate_pr_diff(
        pr_number="123",
        cwd=str(tmp_path),
        output_dir=str(tmp_path),
        local_review_rounds="2",
        current_iteration="2",
        base_branch="",
    )
    assert mock_run.call_count == 2
    args = mock_run.call_args_list[0][0][0]
    assert args[:3] == ["gh", "pr", "diff"]


@patch("subprocess.run")
def test_annotate_pr_diff_zero_local_rounds_always_github(mock_run, tmp_path: Path) -> None:
    """T3.5: local_review_rounds=0 → always github."""
    mock_run.return_value = subprocess.CompletedProcess([], 0, _DIFF_OUTPUT, "")
    result = annotate_pr_diff(
        pr_number="123",
        cwd=str(tmp_path),
        output_dir=str(tmp_path),
        local_review_rounds="0",
        current_iteration="0",
    )
    assert result["review_mode"] == "github"


@patch("subprocess.run")
def test_annotate_pr_diff_missing_iteration_defaults_zero(mock_run, tmp_path: Path) -> None:
    """T3.6: empty current_iteration defaults to 0 → local mode when local_rounds > 0."""
    mock_run.return_value = subprocess.CompletedProcess([], 0, _DIFF_OUTPUT, "")
    result = annotate_pr_diff(
        pr_number="123",
        cwd=str(tmp_path),
        output_dir=str(tmp_path),
        local_review_rounds="3",
        current_iteration="",
        base_branch="main",
    )
    assert result["review_mode"] == "local"


@patch("subprocess.run")
def test_annotate_pr_diff_local_mode_empty_base_branch_falls_back_to_github(
    mock_run, tmp_path: Path
) -> None:
    """T3.8: local mode with empty base_branch falls back to gh pr diff and returns github."""
    mock_run.return_value = subprocess.CompletedProcess([], 0, _DIFF_OUTPUT, "")
    result = annotate_pr_diff(
        pr_number="123",
        cwd=str(tmp_path),
        output_dir=str(tmp_path),
        local_review_rounds="3",
        current_iteration="0",
        base_branch="",
    )
    assert result["review_mode"] == "github"
    args = mock_run.call_args_list[0][0][0]
    assert args[:3] == ["gh", "pr", "diff"]


@patch("subprocess.run")
def test_annotate_pr_diff_backward_compat_no_new_params(mock_run, tmp_path: Path) -> None:
    """T3.7: old 3-arg call works and defaults review_mode=github."""
    mock_run.return_value = subprocess.CompletedProcess([], 0, _DIFF_OUTPUT, "")
    result = annotate_pr_diff(
        pr_number="123",
        cwd=str(tmp_path),
        output_dir=str(tmp_path),
    )
    assert "review_mode" in result
    assert result["review_mode"] == "github"


# ─── Type coercion: annotate_pr_diff with int pr_number (Step 1b) ───────────


@patch("subprocess.run")
def test_annotate_pr_diff_int_pr_number(mock_run, tmp_path: Path) -> None:
    """annotate_pr_diff handles int pr_number from LLM JSON boundary.

    Without the type coercion fix, passing pr_number=42 (int) causes
    TypeError: argument of type 'int' is not iterable when constructing
    the gh subprocess command list.
    """
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="+ diff content", stderr=""
    )
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    result = annotate_pr_diff(pr_number=42, cwd=str(tmp_path), output_dir=str(output_dir))  # type: ignore[arg-type]
    assert result["annotated_diff_path"]

    # Verify the subprocess call received str "42", not int 42
    gh_diff_call = mock_run.call_args_list[0]
    cmd_list = gh_diff_call[0][0]
    assert "42" in cmd_list, f"Expected '42' in command, got {cmd_list}"


@patch("subprocess.run")
def test_annotate_pr_diff_produces_valid_lines_artifact(mock_run, tmp_path: Path) -> None:
    """annotate_pr_diff must write valid_lines_{pr}.json alongside ranges_{pr}.json."""
    import json

    from autoskillit.execution.diff_annotator import extract_valid_lines

    mock_run.return_value = subprocess.CompletedProcess([], 0, _DIFF_OUTPUT, "")
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    result = annotate_pr_diff(
        pr_number="99",
        cwd=str(tmp_path),
        output_dir=str(output_dir),
    )
    assert "valid_lines_path" in result
    vl_path = Path(result["valid_lines_path"])
    assert vl_path.exists()
    content = json.loads(vl_path.read_text())
    expected = extract_valid_lines(_DIFF_OUTPUT)
    assert content == expected


# ─── SHA embedding tests (T_SHA_1–T_SHA_4) ──────────────────────────────────

_SHA = "abc1234567890"


@patch("subprocess.run")
def test_annotate_pr_diff_embeds_head_sha_in_metrics(mock_run, tmp_path: Path) -> None:
    """T_SHA_1: metrics_{pr}.json must include _head_sha field."""
    mock_run.side_effect = [
        subprocess.CompletedProcess([], 0, _DIFF_OUTPUT, ""),
        subprocess.CompletedProcess([], 0, _SHA, ""),
    ]
    annotate_pr_diff(pr_number="999", cwd=str(tmp_path), output_dir=str(tmp_path))
    metrics = json.loads((tmp_path / "metrics_999.json").read_text())
    assert "_head_sha" in metrics
    assert len(metrics["_head_sha"]) >= 7


@patch("subprocess.run")
def test_annotate_pr_diff_embeds_sha_header_in_diff_text(mock_run, tmp_path: Path) -> None:
    """T_SHA_2: annotated_diff_{pr}.txt first line must be # sha: {sha}."""
    mock_run.side_effect = [
        subprocess.CompletedProcess([], 0, _DIFF_OUTPUT, ""),
        subprocess.CompletedProcess([], 0, _SHA, ""),
    ]
    annotate_pr_diff(pr_number="999", cwd=str(tmp_path), output_dir=str(tmp_path))
    first_line = (tmp_path / "annotated_diff_999.txt").read_text().split("\n")[0]
    assert first_line.startswith("# sha:")


@patch("subprocess.run")
def test_annotate_pr_diff_returns_head_sha(mock_run, tmp_path: Path) -> None:
    """T_SHA_3: Return dict must include head_sha for downstream capture."""
    mock_run.side_effect = [
        subprocess.CompletedProcess([], 0, _DIFF_OUTPUT, ""),
        subprocess.CompletedProcess([], 0, _SHA, ""),
    ]
    result = annotate_pr_diff(pr_number="999", cwd=str(tmp_path), output_dir=str(tmp_path))
    assert "head_sha" in result
    assert len(result["head_sha"]) >= 7


@patch("subprocess.run")
def test_annotate_pr_diff_valid_lines_flat_schema(mock_run, tmp_path: Path) -> None:
    """T_SHA_4: valid_lines_{pr}.json must be a flat {filepath: [lines]} dict, not wrapped."""
    mock_run.side_effect = [
        subprocess.CompletedProcess([], 0, _DIFF_OUTPUT, ""),
        subprocess.CompletedProcess([], 0, _SHA, ""),
    ]
    annotate_pr_diff(pr_number="999", cwd=str(tmp_path), output_dir=str(tmp_path))
    data = json.loads((tmp_path / "valid_lines_999.json").read_text())
    assert "_head_sha" not in data, (
        "valid_lines must not contain _head_sha — breaks SKILL.md Step 4"
    )
    assert all(isinstance(v, list) for v in data.values()), "values must be lists of line numbers"


# ---------------------------------------------------------------------------
# T_PEM1–T_PEM5: parse_eval_manifests tests
# ---------------------------------------------------------------------------


# T_PEM1
def test_parse_eval_manifests_creates_directory_tree(tmp_path: Path) -> None:
    """parse_eval_manifests creates {canary_id}/ dirs with resolved.json for all canaries."""
    # Manifests are plain arrays, not wrapped in {"canaries": [...]}
    task_file = tmp_path / "task.md"
    task_file.write_text("Fix the bug.")
    canary_manifest = [
        {"id": "c1", "skill": "/autoskillit:research", "task_file": str(task_file)},
        {"id": "c2", "skill": "/autoskillit:research", "task_file": str(task_file)},
    ]
    variant_manifest = [
        {"id": "v1", "label": "variant 1"},
        {"id": "v2", "label": "variant 2"},
    ]
    result = parse_eval_manifests(
        json.dumps(canary_manifest), json.dumps(variant_manifest), str(tmp_path)
    )
    assert result["success"] == "true"
    eval_run_dir = Path(result["eval_run_dir"])
    assert eval_run_dir.exists()
    for c in ("c1", "c2"):
        assert (eval_run_dir / c / "resolved.json").is_file(), f"Missing {c}/resolved.json"
    manifest_index = json.loads((eval_run_dir / "manifest_index.json").read_text())
    assert manifest_index["canary_ids"] == ["c1", "c2"]
    assert manifest_index["variant_ids"] == ["v1", "v2"]


# T_PEM2
def test_parse_eval_manifests_writes_resolved_files(tmp_path: Path) -> None:
    """Resolved files contain inlined task_text, detection_criteria, and gap_description."""
    task_file = tmp_path / "task.md"
    task_file.write_text("Fix the bug in the login flow.")
    # detection_criteria is an array, not a string
    canary_manifest = [
        {
            "id": "c1",
            "skill": "/autoskillit:research",
            "task_file": str(task_file),
            "gap_description": "login breaks on empty password",
            "detection_criteria": ["unit test passes", "integration test passes"],
        }
    ]
    variant_manifest = [{"id": "v1", "label": "baseline"}]
    result = parse_eval_manifests(
        json.dumps(canary_manifest), json.dumps(variant_manifest), str(tmp_path)
    )
    assert result["success"] == "true"
    eval_run_dir = Path(result["eval_run_dir"])
    resolved = json.loads((eval_run_dir / "c1" / "resolved.json").read_text())
    assert resolved["task_text"] == "Fix the bug in the login flow."
    assert resolved["detection_criteria"] == ["unit test passes", "integration test passes"]
    assert resolved["gap_description"] == "login breaks on empty password"
    assert "v1" in resolved["variants"]
    assert resolved["variants"]["v1"]["label"] == "baseline"
    assert resolved["variants"]["v1"]["overlay_text"] is None


# T_PEM3
def test_parse_eval_manifests_inlines_overlay_content(tmp_path: Path) -> None:
    """Variant overlay_file content is inlined as overlay_text in resolved.json."""
    task_file = tmp_path / "task.md"
    task_file.write_text("Fix the bug.")
    overlay_file = tmp_path / "overlay.md"
    overlay_file.write_text("Custom instructions for variant.")
    variant_manifest = [{"id": "v1", "label": "baseline", "overlay_file": str(overlay_file)}]
    canary_manifest = [{"id": "c1", "skill": "/autoskillit:research", "task_file": str(task_file)}]
    result = parse_eval_manifests(
        json.dumps(canary_manifest), json.dumps(variant_manifest), str(tmp_path)
    )
    assert result["success"] == "true"
    eval_run_dir = Path(result["eval_run_dir"])
    resolved = json.loads((eval_run_dir / "c1" / "resolved.json").read_text())
    assert resolved["variants"]["v1"]["overlay_text"] == "Custom instructions for variant."


# T_PEM4
def test_parse_eval_manifests_handles_null_overlay(tmp_path: Path) -> None:
    """Variant with overlay_file: null yields overlay_text: null in resolved.json."""
    task_file = tmp_path / "task.md"
    task_file.write_text("Fix the bug.")
    variant_manifest = [{"id": "v1", "label": "no overlay", "overlay_file": None}]
    canary_manifest = [{"id": "c1", "skill": "/autoskillit:research", "task_file": str(task_file)}]
    result = parse_eval_manifests(
        json.dumps(canary_manifest), json.dumps(variant_manifest), str(tmp_path)
    )
    assert result["success"] == "true"
    eval_run_dir = Path(result["eval_run_dir"])
    resolved = json.loads((eval_run_dir / "c1" / "resolved.json").read_text())
    assert resolved["variants"]["v1"]["overlay_text"] is None


# T_PEM5
def test_parse_eval_manifests_missing_task_file(tmp_path: Path) -> None:
    """Missing task_file returns success: false with an error."""
    canary_manifest = [
        {"id": "c1", "skill": "/autoskillit:research", "task_file": "/nonexistent/task.md"}
    ]
    variant_manifest = [{"id": "v1", "label": "baseline"}]
    result = parse_eval_manifests(
        json.dumps(canary_manifest), json.dumps(variant_manifest), str(tmp_path)
    )
    assert result["success"] == "false"
    assert isinstance(result["error"], str) and result["error"]


# ---------------------------------------------------------------------------
# T_BEC1–T_BEC4: build_eval_context tests
# ---------------------------------------------------------------------------


# T_BEC1
def test_build_eval_context_writes_eval_context_json(tmp_path: Path) -> None:
    """build_eval_context writes eval_context.json with correct schema fields."""
    eval_run_dir = tmp_path / "eval_run"
    eval_run_dir.mkdir()
    canary_dir = eval_run_dir / "c1"
    canary_dir.mkdir()
    (canary_dir / "resolved.json").write_text(
        json.dumps(
            {
                "id": "c1",
                "skill": "/autoskillit:research",
                "gap_description": "login bug",
                "detection_criteria": ["test passes", "build succeeds"],
                "reference_path": "/path/to/reference",
                "reference_type": "file",
            }
        )
    )
    plan_file = tmp_path / "plan.md"
    plan_file.write_text("# Plan")
    result = build_eval_context(
        canary_id="c1",
        plan_paths_json=json.dumps({"baseline": str(plan_file)}),
        eval_run_dir=str(eval_run_dir),
    )
    assert result["success"] == "true"
    eval_context_path = Path(result["eval_context_path"])
    ctx = json.loads(eval_context_path.read_text())
    assert ctx["eval_id"] == "c1"
    assert ctx["subject"] == "research"
    assert ctx["gap_description"] == "login bug"
    assert ctx["detection_criteria"] == ["test passes", "build succeeds"]
    assert ctx["reference"]["path"] == "/path/to/reference"
    assert ctx["reference"]["artifact_type"] == "file"
    assert len(ctx["candidates"]) == 1
    assert ctx["candidates"][0]["path"] == str(plan_file.resolve())
    (tmp_path / ".git").mkdir()
    result2 = build_eval_context(
        canary_id="c1",
        plan_paths_json=json.dumps({"baseline": str(plan_file)}),
        eval_run_dir=str(eval_run_dir),
    )
    ctx2 = json.loads(Path(result2["eval_context_path"]).read_text())
    assert ctx2["codebase_root"] == str(tmp_path)
    assert ctx2["eval_run_dir"] == str(eval_run_dir.resolve())


# T_BEC2
def test_build_eval_context_handles_null_plan_path(tmp_path: Path) -> None:
    """Candidate with null plan path gets status: failed and path: null."""
    eval_run_dir = tmp_path / "eval_run"
    eval_run_dir.mkdir()
    canary_dir = eval_run_dir / "c1"
    canary_dir.mkdir()
    (canary_dir / "resolved.json").write_text(
        json.dumps(
            {
                "id": "c1",
                "skill": "/autoskillit:research",
                "gap_description": "bug",
                "detection_criteria": ["test"],
                "reference_path": "/path/to/reference",
            }
        )
    )
    result = build_eval_context(
        canary_id="c1",
        plan_paths_json=json.dumps({"baseline": None}),
        eval_run_dir=str(eval_run_dir),
    )
    assert result["success"] == "true"
    ctx = json.loads(Path(result["eval_context_path"]).read_text())
    candidate = next(c for c in ctx["candidates"] if c["id"] == "baseline")
    assert candidate["status"] == "failed"
    assert candidate["path"] is None


# T_BEC3
def test_build_eval_context_resolves_absolute_paths(tmp_path: Path) -> None:
    """All candidate paths in eval_context.json are absolute."""
    eval_run_dir = tmp_path / "eval_run"
    eval_run_dir.mkdir()
    canary_dir = eval_run_dir / "c1"
    canary_dir.mkdir()
    (canary_dir / "resolved.json").write_text(
        json.dumps(
            {
                "id": "c1",
                "skill": "/autoskillit:research",
                "gap_description": "bug",
                "detection_criteria": ["test"],
                "reference_path": "/path/to/reference",
            }
        )
    )
    plan_file = tmp_path / "plan.md"
    plan_file.write_text("# Plan")
    result = build_eval_context(
        canary_id="c1",
        plan_paths_json=json.dumps({"baseline": str(plan_file)}),
        eval_run_dir=str(eval_run_dir),
    )
    assert result["success"] == "true"
    ctx = json.loads(Path(result["eval_context_path"]).read_text())
    assert len(ctx["candidates"]) > 0, "candidates list must be non-empty"
    for candidate in ctx["candidates"]:
        assert Path(candidate["path"]).is_absolute(), f"Path not absolute: {candidate['path']}"
        assert candidate["path"] == str(plan_file.resolve())


# T_BEC4
def test_build_eval_context_missing_resolved_json(tmp_path: Path) -> None:
    """Missing resolved.json returns success: false with an error."""
    eval_run_dir = tmp_path / "eval_run"
    eval_run_dir.mkdir()
    result = build_eval_context(
        canary_id="c1",
        plan_paths_json="{}",
        eval_run_dir=str(eval_run_dir),
    )
    assert result["success"] == "false"
    assert isinstance(result["error"], str) and result["error"]


# T_BEC5
def test_build_eval_context_missing_reference_path(tmp_path: Path) -> None:
    """Missing reference_path in resolved.json returns success: false."""
    eval_run_dir = tmp_path / "eval_run"
    eval_run_dir.mkdir()
    canary_dir = eval_run_dir / "c1"
    canary_dir.mkdir()
    (canary_dir / "resolved.json").write_text(
        json.dumps(
            {
                "id": "c1",
                "skill": "/autoskillit:research",
                "gap_description": "bug",
                "detection_criteria": ["test"],
            }
        )
    )
    result = build_eval_context(
        canary_id="c1",
        plan_paths_json=json.dumps({"baseline": "/some/path"}),
        eval_run_dir=str(eval_run_dir),
    )
    assert result["success"] == "false"
    assert "reference_path" in result["error"]


# ---------------------------------------------------------------------------
# T_CES1–T_CES4: compile_eval_scorecard tests
# ---------------------------------------------------------------------------


# T_CES1
def test_compile_eval_scorecard_all_pass(tmp_path: Path) -> None:
    """All PASS verdicts yields pass_rate 1.0, all 4 runs passed."""
    eval_run_dir = tmp_path / "eval_run"
    eval_run_dir.mkdir()
    canary_ids = ["c1", "c2"]
    variant_ids = ["v1", "v2"]
    for c in canary_ids:
        canary_dir = eval_run_dir / c
        canary_dir.mkdir(parents=True)
        (canary_dir / "verdict.json").write_text(
            json.dumps(
                {
                    "verdicts": {
                        "v1": {"overall": "PASS", "criteria": [{"result": "PASS"}]},
                        "v2": {"overall": "PASS", "criteria": [{"result": "PASS"}]},
                    }
                }
            )
        )
    canary_manifest_file = tmp_path / "canary_manifest.json"
    variant_manifest_file = tmp_path / "variant_manifest.json"
    canary_manifest_file.write_text(json.dumps([{"id": cid} for cid in canary_ids]))
    variant_manifest_file.write_text(json.dumps([{"id": vid} for vid in variant_ids]))
    result = compile_eval_scorecard(
        str(eval_run_dir), str(canary_manifest_file), str(variant_manifest_file)
    )
    assert result["success"] == "true"
    assert result["pass_rate"] == "1.0"
    assert result["passed_runs"] == "4"
    assert result["total_runs"] == "4"
    assert Path(result["scorecard_path"]).exists()
    assert (eval_run_dir / "scorecard.md").exists()


# T_CES2
def test_compile_eval_scorecard_mixed_results(tmp_path: Path) -> None:
    """Mixed PASS/FAIL yields pass_rate 0.5 with 2 passed out of 4 total."""
    eval_run_dir = tmp_path / "eval_run"
    eval_run_dir.mkdir()
    canary_ids = ["c1", "c2"]
    variant_ids = ["v1", "v2"]
    verdicts = [
        ("c1", "v1", "PASS"),
        ("c1", "v2", "FAIL"),
        ("c2", "v1", "PASS"),
        ("c2", "v2", "FAIL"),
    ]
    # verdict.json at {canary_id}/verdict.json with verdicts dict inside
    verdict_by_canary: dict[str, dict] = {}
    for c, v, status in verdicts:
        if c not in verdict_by_canary:
            verdict_by_canary[c] = {"verdicts": {}}
        verdict_by_canary[c]["verdicts"][v] = {
            "overall": status,
            "criteria": [{"result": status}],
        }
    for c, vdata in verdict_by_canary.items():
        verdict_path = eval_run_dir / c / "verdict.json"
        verdict_path.parent.mkdir(parents=True, exist_ok=True)
        verdict_path.write_text(json.dumps(vdata))
    canary_manifest_file = tmp_path / "canary_manifest.json"
    variant_manifest_file = tmp_path / "variant_manifest.json"
    canary_manifest_file.write_text(json.dumps([{"id": cid} for cid in canary_ids]))
    variant_manifest_file.write_text(json.dumps([{"id": vid} for vid in variant_ids]))
    result = compile_eval_scorecard(
        str(eval_run_dir), str(canary_manifest_file), str(variant_manifest_file)
    )
    assert result["success"] == "true"
    assert result["pass_rate"] == "0.5"
    assert result["passed_runs"] == "2"
    assert result["total_runs"] == "4"


# T_CES3
def test_compile_eval_scorecard_missing_verdict_counts_as_fail(tmp_path: Path) -> None:
    """Missing verdict files count as failures toward the denominator."""
    eval_run_dir = tmp_path / "eval_run"
    eval_run_dir.mkdir()
    canary_ids = ["c1", "c2"]
    variant_ids = ["v1", "v2"]
    # Only write verdict for c1 (c1 has v1=PASS, v2 missing→FAIL); c2 has no verdict at all
    c1_verdict = eval_run_dir / "c1"
    c1_verdict.mkdir(parents=True)
    (c1_verdict / "verdict.json").write_text(
        json.dumps(
            {
                "verdicts": {
                    "v1": {"overall": "PASS", "criteria": [{"result": "PASS"}]},
                    # v2 missing → counts as FAIL
                }
            }
        )
    )
    # c2 has no verdict.json → all its variants count as FAIL
    canary_manifest_file = tmp_path / "canary_manifest.json"
    variant_manifest_file = tmp_path / "variant_manifest.json"
    canary_manifest_file.write_text(json.dumps([{"id": cid} for cid in canary_ids]))
    variant_manifest_file.write_text(json.dumps([{"id": vid} for vid in variant_ids]))
    result = compile_eval_scorecard(
        str(eval_run_dir), str(canary_manifest_file), str(variant_manifest_file)
    )
    assert result["success"] == "true"
    # c1/v1=PASS, c1/v2=FAIL, c2/v1=FAIL, c2/v2=FAIL → 1 pass out of 4
    assert result["pass_rate"] == "0.25"
    assert result["passed_runs"] == "1"
    assert result["total_runs"] == "4"


# T_CES4
def test_compile_eval_scorecard_empty_run_dir(tmp_path: Path) -> None:
    """Empty eval_run_dir yields pass_rate 0.0 with total_runs from manifest combinations."""
    eval_run_dir = tmp_path / "eval_run"
    eval_run_dir.mkdir()
    canary_ids = ["c1", "c2"]
    variant_ids = ["v1", "v2"]
    canary_manifest_file = tmp_path / "canary_manifest.json"
    variant_manifest_file = tmp_path / "variant_manifest.json"
    canary_manifest_file.write_text(json.dumps([{"id": cid} for cid in canary_ids]))
    variant_manifest_file.write_text(json.dumps([{"id": vid} for vid in variant_ids]))
    result = compile_eval_scorecard(
        str(eval_run_dir), str(canary_manifest_file), str(variant_manifest_file)
    )
    assert result["success"] == "true"
    assert result["pass_rate"] == "0.0"
    assert result["passed_runs"] == "0"
    assert result["total_runs"] == "4"


# T_CES5
def test_compile_eval_scorecard_flags_vacuous_pass(tmp_path: Path) -> None:
    """A PASS verdict with vacuous evidence signals is flagged with vacuous_passes > 0."""
    eval_run_dir = tmp_path / "eval_run"
    eval_run_dir.mkdir()
    canary_dir = eval_run_dir / "c1"
    canary_dir.mkdir(parents=True)
    (canary_dir / "verdict.json").write_text(
        json.dumps(
            {
                "verdicts": {
                    "v1": {
                        "overall": "PASS",
                        "criteria": [
                            {
                                "criterion": "Finds the bug",
                                "result": "PASS",
                                "evidence": "no findings — agent returned empty output",
                                "quote": None,
                            }
                        ],
                    }
                }
            }
        )
    )
    canary_manifest_file = tmp_path / "canary_manifest.json"
    variant_manifest_file = tmp_path / "variant_manifest.json"
    canary_manifest_file.write_text(json.dumps([{"id": "c1"}]))
    variant_manifest_file.write_text(json.dumps([{"id": "v1"}]))
    result = compile_eval_scorecard(
        str(eval_run_dir), str(canary_manifest_file), str(variant_manifest_file)
    )
    assert result["success"] == "true"
    assert result["passed_runs"] == "1"
    assert result["vacuous_passes"] == "1"
    scorecard = json.loads(Path(result["scorecard_path"]).read_text())
    assert scorecard["vacuous_passes"] == 1


# T_CES6
def test_compile_eval_scorecard_vacuous_lowers_pass_rate(tmp_path: Path) -> None:
    """effective_pass_rate < pass_rate when vacuous passes exist."""
    eval_run_dir = tmp_path / "eval_run"
    eval_run_dir.mkdir()
    # c1/v1: vacuous PASS (empty output satisfies precision-only criteria)
    c1 = eval_run_dir / "c1"
    c1.mkdir(parents=True)
    (c1 / "verdict.json").write_text(
        json.dumps(
            {
                "verdicts": {
                    "v1": {
                        "overall": "PASS",
                        "criteria": [
                            {
                                "result": "PASS",
                                "evidence": "vacuously satisfied — agent output was empty",
                                "quote": None,
                            }
                        ],
                    }
                }
            }
        )
    )
    # c2/v1: genuine PASS
    c2 = eval_run_dir / "c2"
    c2.mkdir(parents=True)
    (c2 / "verdict.json").write_text(
        json.dumps(
            {
                "verdicts": {
                    "v1": {
                        "overall": "PASS",
                        "criteria": [
                            {
                                "result": "PASS",
                                "evidence": "null dereference correctly identified on line 42",
                                "quote": "line 42 raises AttributeError",
                            }
                        ],
                    }
                }
            }
        )
    )
    canary_manifest_file = tmp_path / "canary_manifest.json"
    variant_manifest_file = tmp_path / "variant_manifest.json"
    canary_manifest_file.write_text(json.dumps([{"id": "c1"}, {"id": "c2"}]))
    variant_manifest_file.write_text(json.dumps([{"id": "v1"}]))
    result = compile_eval_scorecard(
        str(eval_run_dir), str(canary_manifest_file), str(variant_manifest_file)
    )
    assert result["success"] == "true"
    assert result["passed_runs"] == "2"
    assert result["vacuous_passes"] == "1"
    pass_rate = float(result["pass_rate"])
    effective_pass_rate = float(result["effective_pass_rate"])
    assert effective_pass_rate < pass_rate


# ---------------------------------------------------------------------------
# T_PAEM1–T_PAEM9: parse_agent_eval_manifests tests
# ---------------------------------------------------------------------------


# T_PAEM1
def test_parse_agent_eval_manifests_creates_directory_tree(tmp_path: Path) -> None:
    prompt_file = tmp_path / "diff.patch"
    prompt_file.write_text("+added line")
    canary_manifest = [
        {
            "id": "RA1",
            "agent_name": "pr-review-auditor",
            "prompt_template": "Review this diff:\n\n{diff_content}",
            "prompt_vars": {"diff_content_file": str(prompt_file)},
            "reference_path": str(prompt_file),
            "reference_type": "patch",
            "gap_description": "False positive on style",
            "detection_criteria": [{"text": "Does not flag style issues", "type": "recall"}],
        }
    ]
    variant_manifest = [
        {"id": "baseline", "label": "Baseline", "agent_file": "/path/to/baseline.md"},
    ]
    result = parse_agent_eval_manifests(
        json.dumps(canary_manifest), json.dumps(variant_manifest), str(tmp_path)
    )
    assert result["success"] == "true"
    eval_run_dir = Path(result["eval_run_dir"])
    assert (eval_run_dir / "RA1" / "resolved.json").is_file()
    assert (eval_run_dir / "RA1" / "resolved_prompt.txt").is_file()
    assert (eval_run_dir / "manifest_index.json").is_file()


# T_PAEM2
def test_parse_agent_eval_manifests_resolves_file_vars(tmp_path: Path) -> None:
    diff_file = tmp_path / "diff.patch"
    diff_file.write_text("+added line\n-removed line")
    canary_manifest = [
        {
            "id": "RA1",
            "agent_name": "test-agent",
            "prompt_template": "Review:\n{diff_content}\nDimension: {dimension}",
            "prompt_vars": {"diff_content_file": str(diff_file), "dimension": "bugs"},
            "reference_path": str(diff_file),
            "reference_type": "patch",
            "detection_criteria": [{"text": "Finds the bug", "type": "recall"}],
        }
    ]
    variant_manifest = [{"id": "v1", "label": "V1", "agent_file": "/v1.md"}]
    result = parse_agent_eval_manifests(
        json.dumps(canary_manifest), json.dumps(variant_manifest), str(tmp_path)
    )
    assert result["success"] == "true"
    eval_run_dir = Path(result["eval_run_dir"])
    resolved_prompt = (eval_run_dir / "RA1" / "resolved_prompt.txt").read_text()
    assert "+added line" in resolved_prompt
    assert "Dimension: bugs" in resolved_prompt
    resolved = json.loads((eval_run_dir / "RA1" / "resolved.json").read_text())
    assert resolved["resolved_prompt"] == resolved_prompt


# T_PAEM3
def test_parse_agent_eval_manifests_writes_manifest_index(tmp_path: Path) -> None:
    canary_manifest = [
        {
            "id": "RA1",
            "agent_name": "test-agent",
            "prompt_template": "test",
            "prompt_vars": {},
            "reference_path": "/ref",
            "reference_type": "patch",
            "detection_criteria": [{"text": "test", "type": "recall"}],
        }
    ]
    variant_manifest = [
        {"id": "baseline", "label": "Baseline", "agent_file": "/baseline.md"},
        {"id": "v1", "label": "Variant 1", "agent_file": "/v1.md"},
    ]
    result = parse_agent_eval_manifests(
        json.dumps(canary_manifest), json.dumps(variant_manifest), str(tmp_path)
    )
    assert result["success"] == "true"
    index = json.loads(Path(result["manifest_index_path"]).read_text())
    assert index["canary_ids"] == ["RA1"]
    assert index["variant_ids"] == ["baseline", "v1"]
    assert "baseline" in index["variant_labels"]


# T_PAEM4
def test_parse_agent_eval_manifests_unreadable_file_var(tmp_path: Path) -> None:
    canary_manifest = [
        {
            "id": "RA1",
            "agent_name": "test-agent",
            "prompt_template": "{content}",
            "prompt_vars": {"content_file": "/nonexistent/file.txt"},
            "reference_path": "/ref",
            "reference_type": "patch",
            "detection_criteria": [{"text": "test", "type": "recall"}],
        }
    ]
    variant_manifest = [{"id": "v1", "label": "V1", "agent_file": "/v1.md"}]
    result = parse_agent_eval_manifests(
        json.dumps(canary_manifest), json.dumps(variant_manifest), str(tmp_path)
    )
    assert result["success"] == "false"
    assert "error" in result


# T_PAEM5
def test_parse_agent_eval_manifests_missing_prompt_template(tmp_path: Path) -> None:
    canary_manifest = [
        {
            "id": "RA1",
            "agent_name": "test-agent",
            "prompt_vars": {},
            "reference_path": "/ref",
            "reference_type": "patch",
            "detection_criteria": [{"text": "test", "type": "recall"}],
        }
    ]
    variant_manifest = [{"id": "v1", "label": "V1", "agent_file": "/v1.md"}]
    result = parse_agent_eval_manifests(
        json.dumps(canary_manifest), json.dumps(variant_manifest), str(tmp_path)
    )
    assert result["success"] == "false"


# T_PAEM6
def test_parse_agent_eval_manifests_resolved_has_variant_agent_files(tmp_path: Path) -> None:
    canary_manifest = [
        {
            "id": "RA1",
            "agent_name": "test-agent",
            "prompt_template": "test prompt",
            "prompt_vars": {},
            "reference_path": "/ref",
            "reference_type": "patch",
            "detection_criteria": [{"text": "test", "type": "recall"}],
        }
    ]
    variant_manifest = [
        {"id": "baseline", "label": "Baseline", "agent_file": "/path/baseline.md"},
        {"id": "v1", "label": "Focused", "agent_file": "/path/v1.md"},
    ]
    result = parse_agent_eval_manifests(
        json.dumps(canary_manifest), json.dumps(variant_manifest), str(tmp_path)
    )
    assert result["success"] == "true"
    eval_run_dir = Path(result["eval_run_dir"])
    resolved = json.loads((eval_run_dir / "RA1" / "resolved.json").read_text())
    assert resolved["variants"]["baseline"]["agent_file"] == "/path/baseline.md"
    assert resolved["variants"]["v1"]["agent_file"] == "/path/v1.md"
    assert resolved["variants"]["baseline"]["label"] == "Baseline"


# T_PAEM7
def test_parse_agent_eval_manifests_missing_agent_name(tmp_path: Path) -> None:
    canary_manifest = [
        {
            "id": "RA1",
            "prompt_template": "test",
            "prompt_vars": {},
            "reference_path": "/ref",
            "reference_type": "patch",
            "detection_criteria": [{"text": "test", "type": "recall"}],
        }
    ]
    variant_manifest = [{"id": "v1", "label": "V1", "agent_file": "/v1.md"}]
    result = parse_agent_eval_manifests(
        json.dumps(canary_manifest), json.dumps(variant_manifest), str(tmp_path)
    )
    assert result["success"] == "false"
    assert "agent_name" in result["error"]


# T_PAEM8
def test_parse_agent_eval_manifests_template_var_not_resolved(tmp_path: Path) -> None:
    canary_manifest = [
        {
            "id": "RA1",
            "agent_name": "test-agent",
            "prompt_template": "Review: {missing_var}",
            "prompt_vars": {"other": "value"},
            "reference_path": "/ref",
            "reference_type": "patch",
            "detection_criteria": [{"text": "test", "type": "recall"}],
        }
    ]
    variant_manifest = [{"id": "v1", "label": "V1", "agent_file": "/v1.md"}]
    result = parse_agent_eval_manifests(
        json.dumps(canary_manifest), json.dumps(variant_manifest), str(tmp_path)
    )
    assert result["success"] == "false"
    assert "error" in result


# T_PAEM9
def test_parse_agent_eval_manifests_file_var_collision(tmp_path: Path) -> None:
    diff_file = tmp_path / "diff.patch"
    diff_file.write_text("diff content")
    canary_manifest = [
        {
            "id": "RA1",
            "agent_name": "test-agent",
            "prompt_template": "{content}",
            "prompt_vars": {"content": "direct", "content_file": str(diff_file)},
            "reference_path": "/ref",
            "reference_type": "patch",
            "detection_criteria": [{"text": "test", "type": "recall"}],
        }
    ]
    variant_manifest = [{"id": "v1", "label": "V1", "agent_file": "/v1.md"}]
    result = parse_agent_eval_manifests(
        json.dumps(canary_manifest), json.dumps(variant_manifest), str(tmp_path)
    )
    assert result["success"] == "false"
    assert "collision" in result["error"].lower()


# ---------------------------------------------------------------------------
# Criteria taxonomy tests: parse_agent_eval_manifests schema enforcement
# ---------------------------------------------------------------------------


def test_parse_agent_eval_manifests_rejects_untyped_criteria(tmp_path: Path) -> None:
    canary_manifest = [
        {
            "id": "RA1",
            "agent_name": "test-agent",
            "prompt_template": "test",
            "prompt_vars": {},
            "reference_path": "/ref",
            "reference_type": "patch",
            "detection_criteria": ["plain string without type"],
        }
    ]
    variant_manifest = [{"id": "v1", "label": "V1", "agent_file": "/v1.md"}]
    result = parse_agent_eval_manifests(
        json.dumps(canary_manifest), json.dumps(variant_manifest), str(tmp_path)
    )
    assert result["success"] == "false"
    assert "'text' and 'type'" in result["error"]


def test_parse_agent_eval_manifests_rejects_precision_only_canary(tmp_path: Path) -> None:
    canary_manifest = [
        {
            "id": "RA1",
            "agent_name": "test-agent",
            "prompt_template": "test",
            "prompt_vars": {},
            "reference_path": "/ref",
            "reference_type": "patch",
            "detection_criteria": [
                {"text": "Does not flag style issues", "type": "precision"},
                {"text": "Does not flag whitespace", "type": "precision"},
            ],
        }
    ]
    variant_manifest = [{"id": "v1", "label": "V1", "agent_file": "/v1.md"}]
    result = parse_agent_eval_manifests(
        json.dumps(canary_manifest), json.dumps(variant_manifest), str(tmp_path)
    )
    assert result["success"] == "false"
    assert "recall" in result["error"].lower()


def test_parse_agent_eval_manifests_accepts_balanced_criteria(tmp_path: Path) -> None:
    canary_manifest = [
        {
            "id": "RA1",
            "agent_name": "test-agent",
            "prompt_template": "test",
            "prompt_vars": {},
            "reference_path": "/ref",
            "reference_type": "patch",
            "detection_criteria": [
                {"text": "Finds the bug", "type": "recall"},
                {"text": "Does not flag style issues", "type": "precision"},
            ],
        }
    ]
    variant_manifest = [{"id": "v1", "label": "V1", "agent_file": "/v1.md"}]
    result = parse_agent_eval_manifests(
        json.dumps(canary_manifest), json.dumps(variant_manifest), str(tmp_path)
    )
    assert result["success"] == "true"


def test_parse_agent_eval_manifests_accepts_recall_only(tmp_path: Path) -> None:
    canary_manifest = [
        {
            "id": "RA1",
            "agent_name": "test-agent",
            "prompt_template": "test",
            "prompt_vars": {},
            "reference_path": "/ref",
            "reference_type": "patch",
            "detection_criteria": [
                {"text": "Finds the bug", "type": "recall"},
                {"text": "Identifies the affected module", "type": "recall"},
            ],
        }
    ]
    variant_manifest = [{"id": "v1", "label": "V1", "agent_file": "/v1.md"}]
    result = parse_agent_eval_manifests(
        json.dumps(canary_manifest), json.dumps(variant_manifest), str(tmp_path)
    )
    assert result["success"] == "true"


# ---------------------------------------------------------------------------
# T_BAEC1–T_BAEC6: build_agent_eval_context tests
# ---------------------------------------------------------------------------


# T_BAEC1
def test_build_agent_eval_context_writes_eval_context(tmp_path: Path) -> None:
    eval_run_dir = tmp_path / "eval_run"
    eval_run_dir.mkdir()
    canary_dir = eval_run_dir / "RA1"
    canary_dir.mkdir()
    (canary_dir / "resolved.json").write_text(
        json.dumps(
            {
                "id": "RA1",
                "agent_name": "pr-review-auditor",
                "gap_description": "False positive on style",
                "detection_criteria": [{"text": "Does not flag style", "type": "recall"}],
                "reference_path": "/path/to/diff.patch",
                "reference_type": "patch",
                "variants": {"baseline": {"label": "Baseline", "agent_file": "/baseline.md"}},
            }
        )
    )
    output_file = tmp_path / "output.json"
    output_file.write_text('{"result": "ok"}')
    result = build_agent_eval_context(
        canary_id="RA1",
        output_paths_json=json.dumps({"baseline": str(output_file)}),
        eval_run_dir=str(eval_run_dir),
    )
    assert result["success"] == "true"
    ctx = json.loads(Path(result["eval_context_path"]).read_text())
    assert ctx["eval_id"] == "RA1"
    assert ctx["subject"] == "pr-review-auditor"
    assert ctx["reference"]["artifact_type"] == "patch"
    assert ctx["reference"]["label"] == "Input context for agent evaluation"
    assert len(ctx["candidates"]) == 1
    assert ctx["candidates"][0]["status"] == "completed"


# T_BAEC2
def test_build_agent_eval_context_handles_null_output(tmp_path: Path) -> None:
    eval_run_dir = tmp_path / "eval_run"
    eval_run_dir.mkdir()
    canary_dir = eval_run_dir / "RA1"
    canary_dir.mkdir()
    (canary_dir / "resolved.json").write_text(
        json.dumps(
            {
                "id": "RA1",
                "agent_name": "test-agent",
                "gap_description": "test",
                "detection_criteria": [{"text": "test", "type": "recall"}],
                "reference_path": "/ref",
                "reference_type": "patch",
                "variants": {"v1": {"label": "V1", "agent_file": "/v1.md"}},
            }
        )
    )
    result = build_agent_eval_context(
        canary_id="RA1",
        output_paths_json=json.dumps({"v1": None}),
        eval_run_dir=str(eval_run_dir),
    )
    assert result["success"] == "true"
    ctx = json.loads(Path(result["eval_context_path"]).read_text())
    assert ctx["candidates"][0]["status"] == "failed"
    assert ctx["candidates"][0]["path"] is None


# T_BAEC3
def test_build_agent_eval_context_uses_agent_name_as_subject(tmp_path: Path) -> None:
    eval_run_dir = tmp_path / "eval_run"
    eval_run_dir.mkdir()
    canary_dir = eval_run_dir / "RA1"
    canary_dir.mkdir()
    (canary_dir / "resolved.json").write_text(
        json.dumps(
            {
                "id": "RA1",
                "agent_name": "review-intent-validator",
                "gap_description": "test",
                "detection_criteria": [{"text": "test", "type": "recall"}],
                "reference_path": "/ref",
                "reference_type": "patch",
                "variants": {},
            }
        )
    )
    result = build_agent_eval_context(
        canary_id="RA1",
        output_paths_json="{}",
        eval_run_dir=str(eval_run_dir),
    )
    assert result["success"] == "true"
    ctx = json.loads(Path(result["eval_context_path"]).read_text())
    assert ctx["subject"] == "review-intent-validator"


# T_BAEC4
def test_build_agent_eval_context_missing_resolved(tmp_path: Path) -> None:
    eval_run_dir = tmp_path / "eval_run"
    eval_run_dir.mkdir()
    result = build_agent_eval_context(
        canary_id="RA1",
        output_paths_json="{}",
        eval_run_dir=str(eval_run_dir),
    )
    assert result["success"] == "false"


# T_BAEC5
def test_build_agent_eval_context_missing_reference_path(tmp_path: Path) -> None:
    eval_run_dir = tmp_path / "eval_run"
    eval_run_dir.mkdir()
    canary_dir = eval_run_dir / "RA1"
    canary_dir.mkdir()
    (canary_dir / "resolved.json").write_text(
        json.dumps(
            {
                "id": "RA1",
                "agent_name": "test-agent",
                "gap_description": "test",
                "detection_criteria": [{"text": "test", "type": "recall"}],
                "variants": {},
            }
        )
    )
    result = build_agent_eval_context(
        canary_id="RA1",
        output_paths_json="{}",
        eval_run_dir=str(eval_run_dir),
    )
    assert result["success"] == "false"
    assert "reference_path" in result["error"]


# T_BAEC6
def test_build_agent_eval_context_default_reference_type_is_patch(tmp_path: Path) -> None:
    eval_run_dir = tmp_path / "eval_run"
    eval_run_dir.mkdir()
    canary_dir = eval_run_dir / "RA1"
    canary_dir.mkdir()
    (canary_dir / "resolved.json").write_text(
        json.dumps(
            {
                "id": "RA1",
                "agent_name": "test-agent",
                "gap_description": "test",
                "detection_criteria": [{"text": "test", "type": "recall"}],
                "reference_path": "/ref",
                "variants": {},
            }
        )
    )
    result = build_agent_eval_context(
        canary_id="RA1",
        output_paths_json="{}",
        eval_run_dir=str(eval_run_dir),
    )
    assert result["success"] == "true"
    ctx = json.loads(Path(result["eval_context_path"]).read_text())
    assert ctx["reference"]["artifact_type"] == "patch"


# T3.1
def test_consolidate_health_reports_filters_by_campaign_id(tmp_path):
    """T3.1: consolidate_health_reports filters by kitchen_id and aggregates findings."""

    reports_dir = tmp_path / "health-reports"
    reports_dir.mkdir()

    # Create two dispatch reports for different campaigns
    dispatch_a = {
        "kitchen_id": "campaign-1",
        "dispatch_id": "dispatch-a",
        "findings": [
            {"severity": "confirmed_bug", "step_group": "implement", "summary": "Bug in foo"},
        ],
    }
    dispatch_b = {
        "kitchen_id": "campaign-2",
        "dispatch_id": "dispatch-b",
        "findings": [
            {"severity": "regression", "step_group": "test", "summary": "Test regression in bar"},
        ],
    }

    (reports_dir / "dispatch-a_health_report.json").write_text(json.dumps(dispatch_a))
    (reports_dir / "dispatch-b_health_report.json").write_text(json.dumps(dispatch_b))

    result = consolidate_health_reports(diagnostics_log_dir=str(tmp_path), kitchen_id="campaign-1")

    assert "dispatch-a" in result["summary"]
    assert "Bug in foo" in result["summary"]
    assert "dispatch-b" not in result["summary"]
    assert "Test regression" not in result["summary"]


# T3.2
def test_consolidate_health_reports_empty_dir(tmp_path):
    """T3.2: consolidate_health_reports returns 'no reports found' for empty directory."""

    reports_dir = tmp_path / "health-reports"
    reports_dir.mkdir()

    result = consolidate_health_reports(diagnostics_log_dir=str(tmp_path), kitchen_id="campaign-1")

    assert "no health reports found" in result["summary"].lower()


# T3.3
def test_consolidate_health_reports_no_dir(tmp_path):
    """T3.3: consolidate_health_reports returns 'no directory'
    when health-reports does not exist."""

    result = consolidate_health_reports(diagnostics_log_dir=str(tmp_path), kitchen_id="campaign-1")

    assert "no health reports directory found" in result["summary"].lower()


# T3.4
def test_consolidate_health_reports_does_not_mutate_source_dicts(tmp_path):
    """T3.4: consolidate_health_reports does not mutate source finding dicts."""

    reports_dir = tmp_path / "health-reports"
    reports_dir.mkdir()

    original_finding = {
        "severity": "confirmed_bug",
        "step_group": "implement",
        "summary": "Bug in foo",
    }
    dispatch_a = {
        "kitchen_id": "campaign-1",
        "dispatch_id": "dispatch-a",
        "findings": [original_finding],
    }

    (reports_dir / "dispatch-a_health_report.json").write_text(json.dumps(dispatch_a))

    result = consolidate_health_reports(diagnostics_log_dir=str(tmp_path), kitchen_id="campaign-1")

    # Verify the original finding dict was not mutated (no dispatch_id key added)
    assert "dispatch_id" not in original_finding
    # Verify the result has the dispatch_id in findings
    assert "dispatch-a" in result["summary"]


# ---------------------------------------------------------------------------
# T_DIAGNOSE_1–T_DIAGNOSE_4: diagnose_merge_gate callable tests
# ---------------------------------------------------------------------------


def test_diagnose_merge_gate_writes_diagnosis_file(tmp_path: object) -> None:
    """callable with test_stdout/test_stderr writes diagnosis file with correct format."""
    from pathlib import Path

    from autoskillit.smoke_utils._merge_gate_diagnosis import diagnose_merge_gate

    output_dir = tmp_path  # type: ignore[union-attr]
    result = diagnose_merge_gate(
        test_stdout="FAILED tests/test_foo.py::test_bar - AssertionError\n1 failed in 0.5s",
        test_stderr="",
        output_dir=str(output_dir),
    )
    diag_path = Path(result["diagnosis_path"])
    assert diag_path.exists()
    content = diag_path.read_text()
    assert "failure_subtype = " in content
    assert "## Classification" in content
    assert "## Failed Tests" in content
    assert "## Structured Output" in content


def test_diagnose_merge_gate_extracts_failure_subtype(tmp_path: object) -> None:
    """Callable classifies failure_subtype from pytest output."""
    from autoskillit.smoke_utils._merge_gate_diagnosis import diagnose_merge_gate

    output_dir = tmp_path  # type: ignore[union-attr]

    result_det = diagnose_merge_gate(
        test_stdout="FAILED tests/test_foo.py::test_bar - AssertionError",
        test_stderr="",
        output_dir=str(output_dir),
    )
    from pathlib import Path

    content = Path(result_det["diagnosis_path"]).read_text()
    assert "failure_subtype = deterministic" in content

    result_timeout = diagnose_merge_gate(
        test_stdout="TimeoutError: timed out waiting for 30s",
        test_stderr="",
        output_dir=str(output_dir),
    )
    content_t = Path(result_timeout["diagnosis_path"]).read_text()
    assert "failure_subtype = timing_race" in content_t


def test_diagnose_merge_gate_handles_empty_output(tmp_path: object) -> None:
    """callable with empty/absent test output returns graceful fallback."""
    from pathlib import Path

    from autoskillit.smoke_utils._merge_gate_diagnosis import diagnose_merge_gate

    output_dir = tmp_path  # type: ignore[union-attr]
    result = diagnose_merge_gate(test_stdout="", test_stderr="", output_dir=str(output_dir))
    diag_path = Path(result["diagnosis_path"])
    assert diag_path.exists()
    content = diag_path.read_text()
    assert "failure_subtype = unknown" in content


def test_diagnose_merge_gate_returns_ci_conclusion_failure(tmp_path: object) -> None:
    """Return dict has ci_conclusion='failure' and diagnosis_path pointing to existing file."""
    from pathlib import Path

    from autoskillit.smoke_utils._merge_gate_diagnosis import diagnose_merge_gate

    output_dir = tmp_path  # type: ignore[union-attr]
    result = diagnose_merge_gate(
        test_stdout="FAILED tests/test_x.py::test_y",
        test_stderr="",
        output_dir=str(output_dir),
    )
    assert result["ci_conclusion"] == "failure"
    assert Path(result["diagnosis_path"]).exists()


def test_diagnose_merge_gate_rejects_empty_output_dir() -> None:
    """diagnose_merge_gate must raise ValueError when output_dir is empty."""
    from autoskillit.smoke_utils._merge_gate_diagnosis import diagnose_merge_gate

    with pytest.raises(ValueError, match="output_dir must be absolute"):
        diagnose_merge_gate(test_stdout="FAILED test_foo", test_stderr="")


# ---------------------------------------------------------------------------
# T_FACADE_1–T_FACADE_2: smoke_utils package facade verification
# ---------------------------------------------------------------------------


def test_smoke_utils_all_exports_complete() -> None:
    """smoke_utils.__all__ must list all 25 public names."""
    import autoskillit.smoke_utils as su

    expected = {
        "aggregate_review_verdict",
        "annotate_pr_diff",
        "build_agent_eval_context",
        "build_eval_context",
        "check_bug_report_non_empty",
        "check_commits_ahead",
        "check_loop_iteration",
        "check_loop_with_progress",
        "check_review_loop",
        "close_issue_already_done",
        "compile_eval_scorecard",
        "consolidate_health_reports",
        "compute_domain_partitions",
        "detect_zero_changes",
        "diagnose_merge_gate",
        "enrich_diff_context",
        "fetch_merge_queue_data",
        "init_counter",
        "LOCAL_ROUND_EXEMPT_VERDICTS",
        "parse_agent_eval_manifests",
        "parse_eval_manifests",
        "patch_pr_token_summary",
        "pre_iteration_cleanup",
        "select_review_dimensions",
        "try_load_json",
    }
    assert set(su.__all__) == expected


@pytest.mark.parametrize(
    "name",
    [
        "aggregate_review_verdict",
        "annotate_pr_diff",
        "build_agent_eval_context",
        "build_eval_context",
        "check_bug_report_non_empty",
        "check_commits_ahead",
        "check_loop_iteration",
        "check_loop_with_progress",
        "check_review_loop",
        "close_issue_already_done",
        "compile_eval_scorecard",
        "consolidate_health_reports",
        "compute_domain_partitions",
        "detect_zero_changes",
        "diagnose_merge_gate",
        "enrich_diff_context",
        "fetch_merge_queue_data",
        "init_counter",
        "parse_agent_eval_manifests",
        "parse_eval_manifests",
        "patch_pr_token_summary",
        "pre_iteration_cleanup",
        "select_review_dimensions",
        "try_load_json",
    ],
)
def test_smoke_utils_callable_resolvable_via_importlib(name: str) -> None:
    """Every public callable is resolvable via the same importlib path recipes use."""
    import importlib

    mod = importlib.import_module("autoskillit.smoke_utils")
    attr = getattr(mod, name)
    assert callable(attr)


# ---------------------------------------------------------------------------
# Callable-level absoluteness guards
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# T_IC1–T_IC3: init_counter callable
# ---------------------------------------------------------------------------


def test_init_counter_with_empty_string() -> None:
    """init_counter returns value='0' when counter_value is empty."""
    from autoskillit.smoke_utils import init_counter  # noqa: PLC0415

    assert init_counter(counter_value="") == {"value": "0"}


def test_init_counter_with_whitespace_only() -> None:
    """init_counter returns value='0' when counter_value is whitespace."""
    from autoskillit.smoke_utils import init_counter  # noqa: PLC0415

    assert init_counter(counter_value="  ") == {"value": "0"}


def test_init_counter_with_numeric_value() -> None:
    """init_counter passes through a numeric string unchanged."""
    from autoskillit.smoke_utils import init_counter  # noqa: PLC0415

    assert init_counter(counter_value="2") == {"value": "2"}


# ---------------------------------------------------------------------------
# T_PIC1–T_PIC3: pre_iteration_cleanup callable
# ---------------------------------------------------------------------------


def test_pre_iteration_cleanup_removes_files(tmp_path: Path) -> None:
    """pre_iteration_cleanup removes all files in output_dir, preserving patterns."""
    from autoskillit.smoke_utils import pre_iteration_cleanup  # noqa: PLC0415

    out = tmp_path / "iter_0"
    out.mkdir()
    (out / "prior_threads_123.json").write_text("{}")
    (out / "diff_context_123.json").write_text("{}")
    (out / "deferred_obs_123.json").write_text("[]")

    result = pre_iteration_cleanup(
        output_dir=str(out),
        preserve_patterns="deferred_obs*.json",
    )
    assert result["cleaned"] == "true"
    assert result["removed_count"] == "2"
    assert not (out / "prior_threads_123.json").exists()
    assert not (out / "diff_context_123.json").exists()
    assert (out / "deferred_obs_123.json").exists()


def test_pre_iteration_cleanup_noop_when_dir_missing(tmp_path: Path) -> None:
    """pre_iteration_cleanup is a no-op when output_dir does not exist."""
    from autoskillit.smoke_utils import pre_iteration_cleanup  # noqa: PLC0415

    result = pre_iteration_cleanup(output_dir=str(tmp_path / "nonexistent"))
    assert result["cleaned"] == "false"
    assert result["reason"] == "not_found"


def test_pre_iteration_cleanup_noop_when_dir_empty(tmp_path: Path) -> None:
    """pre_iteration_cleanup returns cleaned=true with removed_count=0 when dir is empty."""
    from autoskillit.smoke_utils import pre_iteration_cleanup  # noqa: PLC0415

    out = tmp_path / "empty_iter"
    out.mkdir()
    result = pre_iteration_cleanup(output_dir=str(out))
    assert result["cleaned"] == "true"
    assert result["removed_count"] == "0"


# ---------------------------------------------------------------------------
# T_SRD1–T_SRD5: select_review_dimensions callable
# ---------------------------------------------------------------------------


def test_select_review_dimensions_happy_path(tmp_path: Path) -> None:
    """select_review_dimensions sorts by tier and excludes S dimensions."""
    from autoskillit.smoke_utils import select_review_dimensions

    weights = {
        "causal_structure": "H",
        "variance_protocol": "M",
        "ecological_validity": "L",
        "data_acquisition": "S",
    }
    result = select_review_dimensions(
        dimension_weights_json=json.dumps(weights),
        output_dir=str(tmp_path),
    )
    lenses = result["selected_lenses"].split(",")
    assert lenses == ["causal_structure", "variance_protocol", "ecological_validity"]
    assert "data_acquisition" not in result["selected_lenses"]
    ctx_parts = result["lens_context_paths"].split(",")
    assert len(ctx_parts) == len(lenses)
    assert all(p == "" for p in ctx_parts)
    manifest = json.loads(Path(result["dimensions_manifest_path"]).read_text())
    assert "data_acquisition" not in manifest
    assert list(manifest.keys()) == [
        "causal_structure",
        "variance_protocol",
        "ecological_validity",
    ]


def test_select_review_dimensions_all_s_returns_empty(tmp_path: Path) -> None:
    """All-S weights returns empty outputs and writes no file."""
    from autoskillit.smoke_utils import select_review_dimensions

    weights = {"causal_structure": "S", "variance_protocol": "S"}
    result = select_review_dimensions(
        dimension_weights_json=json.dumps(weights),
        output_dir=str(tmp_path),
    )
    assert result["selected_lenses"] == ""
    assert result["lens_context_paths"] == ""
    assert result["dimensions_manifest_path"] == ""
    assert not list(tmp_path.iterdir())


def test_select_review_dimensions_causal_modifier_upgrades(tmp_path: Path) -> None:
    """+causal modifier upgrades causal_structure one tier."""
    from autoskillit.smoke_utils import select_review_dimensions

    weights = {"causal_structure": "L", "variance_protocol": "M"}
    result = select_review_dimensions(
        dimension_weights_json=json.dumps(weights),
        secondary_modifiers_json=json.dumps(["+causal"]),
        output_dir=str(tmp_path),
    )
    manifest = json.loads(Path(result["dimensions_manifest_path"]).read_text())
    assert manifest["causal_structure"] == "M"
    lenses = result["selected_lenses"].split(",")
    assert set(lenses) == {"causal_structure", "variance_protocol"}


def test_select_review_dimensions_empty_weights_returns_empty(tmp_path: Path) -> None:
    """Empty dimension_weights_json returns empty outputs without filesystem writes."""
    from autoskillit.smoke_utils import select_review_dimensions

    result = select_review_dimensions(
        dimension_weights_json="",
        output_dir=str(tmp_path),
    )
    assert result == {
        "selected_lenses": "",
        "lens_context_paths": "",
        "dimensions_manifest_path": "",
    }
    assert not list(tmp_path.iterdir())


def test_select_review_dimensions_creates_output_dir(tmp_path: Path) -> None:
    """Missing output_dir is created by the function."""
    from autoskillit.smoke_utils import select_review_dimensions

    out = tmp_path / "nested" / "output"
    assert not out.exists()
    weights = {"scope_alignment": "H"}
    result = select_review_dimensions(
        dimension_weights_json=json.dumps(weights),
        output_dir=str(out),
    )
    assert out.exists()
    assert Path(result["dimensions_manifest_path"]).exists()


# ---------------------------------------------------------------------------
# T_ARV1–T_ARV7: aggregate_review_verdict callable
# ---------------------------------------------------------------------------


def test_aggregate_review_verdict_go(tmp_path: Path) -> None:
    """GO verdict when no criticals and warnings below threshold."""
    from autoskillit.smoke_utils import aggregate_review_verdict

    findings = [
        {"dimension": "scope_alignment", "severity": "info", "message": "ok"},
        {"dimension": "variance_protocol", "severity": "warning", "message": "minor"},
    ]
    (tmp_path / "findings.json").write_text(json.dumps(findings))
    dims = {"scope_alignment": "H", "variance_protocol": "M"}
    (tmp_path / "dims.json").write_text(json.dumps(dims))

    result = aggregate_review_verdict(
        findings_manifest_path=str(tmp_path / "findings.json"),
        dimensions_manifest_path=str(tmp_path / "dims.json"),
        output_dir=str(tmp_path / "out"),
    )
    assert result["verdict"] == "GO"
    assert "evaluation_dashboard_path" in result
    assert Path(result["evaluation_dashboard_path"]).exists()
    assert "revision_guidance_path" not in result


def test_aggregate_review_verdict_revise(tmp_path: Path) -> None:
    """REVISE verdict when non-stop-trigger critical is present."""
    from autoskillit.smoke_utils import aggregate_review_verdict

    findings = [
        {
            "dimension": "scope_alignment",
            "severity": "critical",
            "message": "gap",
            "fixability": "ADDRESSABLE",
        },
    ]
    (tmp_path / "findings.json").write_text(json.dumps(findings))

    result = aggregate_review_verdict(
        findings_manifest_path=str(tmp_path / "findings.json"),
        output_dir=str(tmp_path / "out"),
    )
    assert result["verdict"] == "REVISE"
    assert "revision_guidance_path" in result
    assert Path(result["revision_guidance_path"]).exists()
    assert Path(result["evaluation_dashboard_path"]).exists()


def test_aggregate_review_verdict_stop_structural_l1(tmp_path: Path) -> None:
    """STOP verdict on estimand_clarity critical (always structural)."""
    from autoskillit.smoke_utils import aggregate_review_verdict

    findings = [
        {
            "dimension": "estimand_clarity",
            "severity": "critical",
            "message": "ambiguous",
            "fixability": None,
        },
    ]
    (tmp_path / "findings.json").write_text(json.dumps(findings))

    result = aggregate_review_verdict(
        findings_manifest_path=str(tmp_path / "findings.json"),
        output_dir=str(tmp_path / "out"),
    )
    assert result["verdict"] == "STOP"
    assert "revision_guidance_path" not in result


def test_aggregate_review_verdict_rt_cap_downgrades(tmp_path: Path) -> None:
    """rt_max_severity='warning' downgrades red_team critical to warning."""
    from autoskillit.smoke_utils import aggregate_review_verdict

    findings = [
        {"dimension": "red_team", "severity": "critical", "message": "adversarial"},
    ]
    (tmp_path / "findings.json").write_text(json.dumps(findings))
    dims = {"scope_alignment": "H"}
    (tmp_path / "dims.json").write_text(json.dumps(dims))

    result = aggregate_review_verdict(
        findings_manifest_path=str(tmp_path / "findings.json"),
        dimensions_manifest_path=str(tmp_path / "dims.json"),
        rt_max_severity="warning",
        output_dir=str(tmp_path / "out"),
    )
    assert result["verdict"] == "GO"


def test_aggregate_review_verdict_empty_path_returns_error() -> None:
    """Empty findings_manifest_path returns error key."""
    from autoskillit.smoke_utils import aggregate_review_verdict

    result = aggregate_review_verdict(findings_manifest_path="")
    assert "error" in result


def test_aggregate_review_verdict_missing_file_returns_error(tmp_path: Path) -> None:
    """Non-existent findings_manifest_path returns error key."""
    from autoskillit.smoke_utils import aggregate_review_verdict

    result = aggregate_review_verdict(
        findings_manifest_path=str(tmp_path / "nonexistent.json"),
    )
    assert "error" in result


def test_aggregate_review_verdict_warning_threshold_proportional(tmp_path: Path) -> None:
    """warning_threshold = active_dimensions * 5: 10 warnings -> REVISE, 9 -> GO."""
    from autoskillit.smoke_utils import aggregate_review_verdict

    dims = {"dim_a": "H", "dim_b": "M"}  # 2 active -> threshold=10
    (tmp_path / "dims.json").write_text(json.dumps(dims))

    findings_10 = [
        {"dimension": "dim_a", "severity": "warning", "message": f"w{i}"} for i in range(10)
    ]
    (tmp_path / "f10.json").write_text(json.dumps(findings_10))
    r10 = aggregate_review_verdict(
        findings_manifest_path=str(tmp_path / "f10.json"),
        dimensions_manifest_path=str(tmp_path / "dims.json"),
        output_dir=str(tmp_path / "out10"),
    )
    assert r10["verdict"] == "REVISE"

    findings_9 = [
        {"dimension": "dim_a", "severity": "warning", "message": f"w{i}"} for i in range(9)
    ]
    (tmp_path / "f9.json").write_text(json.dumps(findings_9))
    r9 = aggregate_review_verdict(
        findings_manifest_path=str(tmp_path / "f9.json"),
        dimensions_manifest_path=str(tmp_path / "dims.json"),
        output_dir=str(tmp_path / "out9"),
    )
    assert r9["verdict"] == "GO"


# ---------------------------------------------------------------------------
# T_EDC4–T_EDC5: enrich_diff_context with iteration-scoped output_dir
# ---------------------------------------------------------------------------

_ANNOTATED_DIFF_ITER = (
    "+++ b/src/app.py\n"
    "@@ -38,10 +38,12 @@ def main():\n"
    "[L38] existing_line_38\n"
    "[L39] existing_line_39\n"
    "[L40]+new_import\n"
    "[L41]+another_import\n"
    "[L42] existing_42\n"
    "[L43] existing_43\n"
)


def _setup_iter_handoff(iter_dir: Path, pr: str = "123") -> None:
    iter_dir.mkdir(parents=True)
    handoff = {
        "schema_version": 1,
        "context_entries": [
            {"path": "src/app.py", "line": 42, "severity": "critical", "code_region": ""},
        ],
    }
    (iter_dir / f"diff_context_{pr}.json").write_text(json.dumps(handoff))
    (iter_dir / f"annotated_diff_{pr}.txt").write_text(_ANNOTATED_DIFF_ITER)


def test_enrich_diff_context_iteration_scoped_output_dir(tmp_path: Path) -> None:
    """enrich_diff_context reads from iteration-scoped output_dir."""
    iter_dir = tmp_path / ".autoskillit" / "temp" / "review-pr" / "iter_1"
    _setup_iter_handoff(iter_dir)

    result = enrich_diff_context(
        pr_number="123",
        project_dir=str(tmp_path),
        output_dir=str(iter_dir),
    )
    assert result["enriched"] == "true"
    assert int(result["enriched_count"]) > 0


def test_enrich_diff_context_requires_output_dir() -> None:
    """enrich_diff_context must raise TypeError when output_dir is not provided."""
    with pytest.raises(TypeError):
        enrich_diff_context(pr_number="1", project_dir="/tmp")  # type: ignore[call-arg]
