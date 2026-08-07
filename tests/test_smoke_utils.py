"""Tests for smoke_utils callables."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoskillit.smoke_utils import (
    EXPERIMENTAL_REVIEW_AUDITOR_REGISTRY,
    EXPERIMENTAL_REVIEW_AUDITORS,
    REVIEW_HANDOFF_IDENTITY_FIELDS,
    aggregate_combined_review_candidates,
    annotate_pr_diff,
    build_agent_eval_context,
    build_eval_context,
    build_malformed_review_envelope,
    check_bug_report_non_empty,
    check_diff_size,
    check_loop_iteration,
    check_loop_with_progress,
    check_review_loop,
    check_review_posted,
    compile_eval_scorecard,
    consolidate_health_reports,
    determine_experimental_review_verdict,
    enrich_diff_context,
    extract_investigation,
    init_counter,
    normalize_local_review_finding,
    parse_agent_eval_manifests,
    parse_eval_manifests,
    patch_pr_token_summary,
    prepare_experimental_review_publication,
    publish_experimental_review_artifacts,
    remove_worktree_for_replan,
    render_review_finding_body,
    review_handoff_pair_error,
    select_experimental_review_dispatch,
    validate_experimental_auditor_outputs,
)
from tests.infra._token_summary_helpers import _resolve_session_label

pytestmark = [pytest.mark.medium]

_EXPERIMENTAL_BOUNDARIES = (
    "reflection_decorators",
    "dependency_injection",
    "plugin_registry",
    "cli_entrypoint",
    "serialization",
    "generated_code",
    "public_api",
)


@pytest.fixture(autouse=True)
def _isolate_kitchen_marker(monkeypatch):
    """Prevent read_kitchen_id_from_marker from reading real hook config files.

    Tests that need kitchen_id pass it explicitly — the marker is never consulted.
    """
    monkeypatch.setattr(
        "autoskillit.core.read_kitchen_id_from_marker",
        lambda base=None: "",
    )


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


def test_remove_worktree_for_replan_passes_unsafe_path_as_single_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    main_repo = tmp_path / "main"
    main_repo.mkdir()
    worktree = tmp_path / 'worktree "$(echo injected)"'
    worktree.mkdir()
    observed: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed.append(args)
        assert kwargs["cwd"] == main_repo
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("autoskillit.smoke_utils._git.subprocess.run", fake_run)

    result = remove_worktree_for_replan(str(worktree), str(main_repo))

    assert result == {"cleanup_state": "removed"}
    assert observed == [
        [
            "git",
            "-C",
            str(main_repo),
            "worktree",
            "remove",
            "--force",
            str(worktree),
        ]
    ]


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


def test_needs_human_exempt_from_local_rounds() -> None:
    """needs_human must be exempt from local_review_rounds re-review."""
    result = check_review_loop(
        pr_number="42",
        current_iteration="0",
        max_iterations="6",
        previous_verdict="needs_human",
        local_review_rounds="2",
    )
    assert result["had_blocking"] == "false", (
        "needs_human must yield had_blocking=false regardless of local_review_rounds "
        "because it indicates review was skipped (graceful degradation) and "
        "re-review would be pointless."
    )


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


def test_check_loop_iteration_budget_semantics_documented() -> None:
    """Document: max_iterations=N allows N-1 loop body executions (>= comparison).

    With max_iterations="3" (the corrected default for audit remediation):
    - Round 0→1: allowed (first remediation attempt)
    - Round 1→2: allowed (second remediation attempt)
    - Round 2→3: blocked (budget exhausted)
    Result: 2 remediation rounds with max_iterations="3".
    """
    r1 = check_loop_iteration(current_iteration="", max_iterations="3")
    assert r1["max_exceeded"] == "false"

    r2 = check_loop_iteration(current_iteration=r1["next_iteration"], max_iterations="3")
    assert r2["max_exceeded"] == "false"

    r3 = check_loop_iteration(current_iteration=r2["next_iteration"], max_iterations="3")
    assert r3["max_exceeded"] == "true"


def test_check_loop_iteration_ref_push_budget_two_attempts() -> None:
    """Ref-push budget: max_iterations='3' yields exactly 2 usable push attempts.

    Locks the ref-push recovery budget under the existing ``>=`` semantics:
    with ``max_iterations='3'`` (the production value for ``check_ref_push_loop``
    and ``check_ref_push_loop_pre_remediation`` in ``remediation.yaml``),
    the counter permits two push attempts before exhausting:

    - Round 0→1: allowed (first push attempt)
    - Round 1→2: allowed (second push attempt)
    - Round 2→3: blocked (budget exhausted)

    This matches the intent of the ref-push recovery chain — two retries are
    enough to absorb a transient ref-coherence divergence without false
    positives. Do NOT change ``check_loop_iteration``'s ``>=`` operator — the
    ``max_iterations='3'`` value is the canonical budget adjustment for
    ref-push sites (issue #4274, Part B Step 1).
    """
    r1 = check_loop_iteration(current_iteration="", max_iterations="3")
    assert r1["next_iteration"] == "1"
    assert r1["max_exceeded"] == "false"

    r2 = check_loop_iteration(current_iteration="1", max_iterations="3")
    assert r2["next_iteration"] == "2"
    assert r2["max_exceeded"] == "false"

    r3 = check_loop_iteration(current_iteration="2", max_iterations="3")
    assert r3["next_iteration"] == "3"
    assert r3["max_exceeded"] == "true"


def test_check_loop_iteration_cross_cycle_budget_starvation() -> None:
    """Cross-cycle budget starvation: counter persists → new cycle has zero budget.

    Simulates: cycle 1 uses 2 fix attempts (counter reaches "2"), then cycle 2
    tries to use the counter without resetting — max_exceeded fires immediately.
    After resetting via init_counter, the budget is fresh.
    """
    r1 = check_loop_iteration(current_iteration="", max_iterations="3")
    assert r1["max_exceeded"] == "false"

    r2 = check_loop_iteration(current_iteration=r1["next_iteration"], max_iterations="3")
    assert r2["max_exceeded"] == "false"

    r3 = check_loop_iteration(current_iteration=r2["next_iteration"], max_iterations="3")
    assert r3["max_exceeded"] == "true"

    reset = init_counter(counter_value="")
    r4 = check_loop_iteration(current_iteration=reset["value"], max_iterations="3")
    assert r4["max_exceeded"] == "false"
    assert r4["next_iteration"] == "1"


def test_check_loop_iteration_max_iterations_two_single_push_boundary() -> None:
    """max_iterations="2" allows exactly ONE push attempt (issue #4274 boundary).

    With max_iterations="2" (the production value for ``ref_push_count``), the
    counter permits exactly one increment before exhausting the budget:

    - Round 1: 0→1 (allowed), the single permitted push attempt.
    - Reset: counter back to "" via ``init_counter``.
    - Round 2: 0→1 (allowed), the second push attempt.
    - Final increment 1→2: blocked — ``max_exceeded == "true"``.

    Any cycle that needs ≥2 pushes between resets therefore exhausts the
    budget at the second push, regardless of how many audit-rem cycles
    wrap around it. The existing ``max_iterations="3"`` test has comfortable
    margin; this ``max=2`` variant exposes the tight single-push boundary
    that the ref-push retry chain actually operates under.
    """
    # First push attempt
    r1 = check_loop_iteration(current_iteration="", max_iterations="2")
    assert r1["max_exceeded"] == "false"
    assert r1["next_iteration"] == "1"

    # Reset between cycles — init_counter returns "0" for blank input
    reset = init_counter(counter_value="")
    assert reset["value"] == "0"

    # Second push attempt — counter fresh, allowed
    r2 = check_loop_iteration(current_iteration=reset["value"], max_iterations="2")
    assert r2["max_exceeded"] == "false"
    assert r2["next_iteration"] == "1"

    # The next increment 1→2 exhausts the budget
    r3 = check_loop_iteration(current_iteration=r2["next_iteration"], max_iterations="2")
    assert r3["max_exceeded"] == "true"


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


# T_PTS_K1
@patch("time.sleep")
@patch("subprocess.run")
def test_pts_kitchen_id_fallback_includes_worktree_sessions(
    mock_run, _mock_sleep, tmp_path: Path
) -> None:
    """patch_pr_token_summary falls back to kitchen_id filter when no order_id.

    When the caller passes only a cwd that matches the clone root and the
    sessions were logged with mixed cwds (clone root + worktree) under a
    single kitchen_id, the kitchen_id fallback includes the worktree session
    that cwd_filter would silently drop.
    """
    clone_root = "/clone/test"
    worktree_path = "/clone/test/.worktrees/fix-42"
    _write_test_sessions(
        tmp_path,
        [
            {
                "dir_name": "s-plan",
                "cwd": clone_root,
                "kitchen_id": "kitchen-xyz",
                "step_name": "plan",
                "input_tokens": 1000,
            },
            {
                "dir_name": "s-review",
                "cwd": clone_root,
                "kitchen_id": "kitchen-xyz",
                "step_name": "review",
                "input_tokens": 800,
            },
            {
                "dir_name": "s-implement",
                "cwd": worktree_path,
                "kitchen_id": "kitchen-xyz",
                "step_name": "implement",
                "input_tokens": 2000,
            },
        ],
    )
    mock_run.side_effect = _make_gh_mock(get_body="## Summary\nBody")
    result = patch_pr_token_summary(
        PR_URL, cwd=clone_root, kitchen_id="kitchen-xyz", log_dir=str(tmp_path)
    )
    assert result["success"] == "true"
    assert result["sessions_loaded"] == "3"
    patch_call = mock_run.call_args_list[-1]
    body_arg = [a for a in patch_call[0][0] if a.startswith("body=")][0]
    assert "plan" in body_arg
    assert "review" in body_arg
    assert "implement" in body_arg


# T_PTS_K2
@patch("time.sleep")
@patch("subprocess.run")
def test_pts_cwd_filter_silently_excludes_worktree_sessions(
    mock_run, _mock_sleep, tmp_path: Path
) -> None:
    """When caller uses cwd_filter directly, worktree sessions are silently dropped.

    Documents the bug that the kitchen_id fallback fixes: with only cwd_filter
    available, sessions from the worktree subdir are excluded and the implement
    step is missing from the PR body.
    """
    clone_root = "/clone/test"
    worktree_path = "/clone/test/.worktrees/fix-42"
    _write_test_sessions(
        tmp_path,
        [
            {
                "dir_name": "s-plan",
                "cwd": clone_root,
                "step_name": "plan",
                "input_tokens": 1000,
            },
            {
                "dir_name": "s-implement",
                "cwd": worktree_path,
                "step_name": "implement",
                "input_tokens": 2000,
            },
        ],
    )
    mock_run.side_effect = _make_gh_mock(get_body="## Summary\nBody")
    result = patch_pr_token_summary(PR_URL, cwd=clone_root, log_dir=str(tmp_path))
    assert result["success"] == "true"
    assert result["sessions_loaded"] == "1"
    patch_call = mock_run.call_args_list[-1]
    body_arg = [a for a in patch_call[0][0] if a.startswith("body=")][0]
    assert "plan" in body_arg
    assert "implement" not in body_arg, "implement should be silently dropped by cwd_filter"


# T_PTS_K3
@patch("time.sleep")
@patch("subprocess.run")
def test_pts_expected_steps_surfaces_missing_in_pr_body(
    mock_run, _mock_sleep, tmp_path: Path
) -> None:
    """When expected_steps is provided, missing steps appear in the PR body."""
    cwd = "/clone/test"
    _write_test_sessions(
        tmp_path,
        [
            {"dir_name": "s-plan", "cwd": cwd, "step_name": "plan", "input_tokens": 1000},
            {"dir_name": "s-review", "cwd": cwd, "step_name": "review", "input_tokens": 800},
        ],
    )
    mock_run.side_effect = _make_gh_mock(get_body="## Summary\nBody")
    result = patch_pr_token_summary(
        PR_URL,
        cwd=cwd,
        log_dir=str(tmp_path),
        expected_steps=["plan", "review", "implement"],
    )
    assert result["success"] == "true"
    patch_call = mock_run.call_args_list[-1]
    body_arg = [a for a in patch_call[0][0] if a.startswith("body=")][0]
    assert "completeness warning" in body_arg
    assert "implement" in body_arg


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


# T_PTS15
@patch("time.sleep")
@patch("subprocess.run")
def test_pts_uses_injected_token_log(
    mock_run: MagicMock, _mock_sleep: MagicMock, tmp_path: Path
) -> None:
    """patch_pr_token_summary uses injected token_log instead of constructing a new one.

    Regression guard for the DI gap: callers with a pre-loaded DefaultTokenLog
    should be able to inject it to avoid redundant disk I/O, and the function
    must not replace the injected instance with a fresh DefaultTokenLog.
    """
    from autoskillit.pipeline import DefaultTokenLog
    from autoskillit.smoke_utils._telemetry import patch_pr_token_summary

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
        ],
    )

    token_log = DefaultTokenLog()
    token_log.load_from_log_dir(tmp_path, cwd_filter=cwd)
    sentinel_id = id(token_log)

    mock_run.side_effect = _make_gh_mock(get_body="## Summary\nTest PR")
    result = patch_pr_token_summary(PR_URL, cwd, log_dir=str(tmp_path), token_log=token_log)
    assert result["success"] == "true"
    assert id(token_log) == sentinel_id, "injected token_log must not be replaced"


# ---------------------------------------------------------------------------
# Experimental review validation and aggregation
# ---------------------------------------------------------------------------


def _experimental_candidate(dimension: str, *, file: str = "src/app.py", line: int = 42) -> dict:
    return {
        "file": file,
        "line": line,
        "dimension": dimension,
        "severity": "warning",
        "message": "The abstraction has no reachable consumer",
        "requires_decision": False,
        "evidence": [
            {"path": file, "line": line, "role": "anchor", "claim": "Declaration"},
            {"path": file, "line": line + 1, "role": "consumer", "claim": "Only consumer"},
        ],
        "trace": [{"path": file, "line": line + 1, "relation": "calls"}],
        "boundary_checks": [
            {
                "boundary": boundary,
                "status": "checked_no_reachable_path",
                "claim": f"{boundary} has no reachable path",
            }
            for boundary in _EXPERIMENTAL_BOUNDARIES
        ],
        "confidence": 0.9,
        "simpler_behavior": (
            "Equivalent return values, exceptions, ordering, persistence, "
            "concurrency, and compatibility"
        ),
    }


def test_malformed_review_envelope_bounds_untrusted_output() -> None:
    raw = "π" * 5000

    envelope = build_malformed_review_envelope(
        producer=EXPERIMENTAL_REVIEW_AUDITORS[0],
        terminal_status="success",
        raw_output=raw,
        errors=[f"{index}-{'x' * 2000}" for index in range(1000)],
        rejection_reason="schema_invalid",
    )

    raw_bytes = raw.encode()
    assert envelope["received_byte_length"] == len(raw_bytes)
    assert envelope["received_sha256"] == hashlib.sha256(raw_bytes).hexdigest()
    assert envelope["excerpt_byte_length"] <= 4096
    assert len(str(envelope["excerpt"]).encode()) <= 4096
    assert envelope["excerpt"] != raw
    assert "raw_output" not in envelope
    assert len(envelope["errors"]) == 32
    assert all(len(str(error).encode()) <= 1024 for error in envelope["errors"])


def test_review_handoff_pair_requires_one_ordered_identity_contract() -> None:
    identity = {
        "_head_sha": "head",
        "annotation_generation_id": "annotation",
        "review_generation_id": "review",
    }

    assert REVIEW_HANDOFF_IDENTITY_FIELDS == tuple(identity)
    assert review_handoff_pair_error(identity, dict(identity)) is None


@pytest.mark.parametrize(
    ("field", "second_value", "expected_error"),
    [
        ("_head_sha", "", "require non-empty _head_sha"),
        (
            "annotation_generation_id",
            "different",
            "mismatched annotation_generation_id",
        ),
        ("review_generation_id", None, "require non-empty review_generation_id"),
    ],
)
def test_review_handoff_pair_rejects_missing_or_mixed_generations(
    field: str,
    second_value: object,
    expected_error: str,
) -> None:
    first = {
        "_head_sha": "head",
        "annotation_generation_id": "annotation",
        "review_generation_id": "review",
    }
    second = dict(first)
    second[field] = second_value

    assert expected_error in str(review_handoff_pair_error(first, second))


def test_experimental_dispatch_is_derived_from_ordered_registry() -> None:
    result = select_experimental_review_dispatch(
        gate_state="valid_true",
        annotated_diff="[L1] changed",
        valid_diff_lines={"src/app.py": [1]},
        standard_agent_names=["arch", "tests"],
    )

    expected = [
        {"agent_name": agent_name, "dimension": dimension}
        for agent_name, dimension in EXPERIMENTAL_REVIEW_AUDITOR_REGISTRY
    ]
    assert EXPERIMENTAL_REVIEW_AUDITORS == tuple(item["agent_name"] for item in expected)
    assert result == {
        "audit_state": "pending",
        "dispatch_agents": expected,
        "auditors": expected,
        "reason": "",
    }


@pytest.mark.parametrize(
    ("gate_state", "annotated_diff", "valid_diff_lines", "standard_agents", "state"),
    [
        ("valid_false", "[L1] changed", {"src/app.py": [1]}, ["arch"], "not_required"),
        ("degraded", "[L1] changed", {"src/app.py": [1]}, ["arch"], "degraded"),
        ("valid_true", "", {"src/app.py": [1]}, ["arch"], "degraded"),
        ("valid_true", "[L1] changed", [], ["arch"], "degraded"),
        (
            "valid_true",
            "[L1] changed",
            {"src/app.py": [1]},
            [EXPERIMENTAL_REVIEW_AUDITORS[0]],
            "degraded",
        ),
    ],
)
def test_experimental_dispatch_closes_on_ineligible_authority(
    gate_state: str,
    annotated_diff: object,
    valid_diff_lines: object,
    standard_agents: list[str],
    state: str,
) -> None:
    result = select_experimental_review_dispatch(
        gate_state=gate_state,
        annotated_diff=annotated_diff,
        valid_diff_lines=valid_diff_lines,
        standard_agent_names=standard_agents,
    )

    assert result["audit_state"] == state
    assert result["dispatch_agents"] == []


def test_experimental_output_validation_is_atomic_and_fixed_order(tmp_path: Path) -> None:
    reachability, abstraction = EXPERIMENTAL_REVIEW_AUDITORS
    input_candidates = {
        abstraction: _experimental_candidate("overengineering_abstraction_surface"),
        reachability: _experimental_candidate("overengineering_reachability"),
    }
    valid_outputs = {
        abstraction: {
            "terminal_status": "success",
            "output": [input_candidates[abstraction]],
        },
        reachability: {
            "terminal_status": "success",
            "output": [input_candidates[reachability]],
        },
    }
    kwargs = {
        "valid_diff_lines": {"src/app.py": [42]},
        "snapshot": {"head_sha": "head", "diff_sha256": "diff"},
        "review_root": str(tmp_path),
    }

    complete = validate_experimental_auditor_outputs(outputs=valid_outputs, **kwargs)
    assert complete["state"] == "complete"
    assert [candidate["auditor_name"] for candidate in complete["candidates"]] == list(
        EXPERIMENTAL_REVIEW_AUDITORS
    )
    assert [candidate["original_index"] for candidate in complete["candidates"]] == [0, 0]
    expected_identities = []
    for auditor_name in EXPERIMENTAL_REVIEW_AUDITORS:
        canonical = json.dumps(
            input_candidates[auditor_name],
            sort_keys=True,
            separators=(",", ":"),
        )
        record_digest = hashlib.sha256(canonical.encode()).hexdigest()
        identity = json.dumps(
            {
                "snapshot": kwargs["snapshot"],
                "auditor_name": auditor_name,
                "original_index": 0,
                "record_digest": record_digest,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        expected_identities.append((record_digest, hashlib.sha256(identity.encode()).hexdigest()))
    assert [
        (candidate["record_digest"], candidate["candidate_id"])
        for candidate in complete["candidates"]
    ] == expected_identities

    malformed_outputs = json.loads(json.dumps(valid_outputs))
    malformed_outputs[abstraction]["output"][0]["message"] = ""
    degraded = validate_experimental_auditor_outputs(outputs=malformed_outputs, **kwargs)
    assert degraded["state"] == "degraded"
    assert degraded["candidates"] == []
    assert degraded["status_by_name"][reachability]["status"] == "success"
    assert degraded["status_by_name"][abstraction]["reason_code"] == "schema_invalid"
    assert len(degraded["malformed_envelopes"]) == 1

    wrong_enum_type = json.loads(json.dumps(valid_outputs))
    wrong_enum_type[abstraction]["output"][0]["severity"] = []
    degraded = validate_experimental_auditor_outputs(outputs=wrong_enum_type, **kwargs)
    assert degraded["state"] == "degraded"
    assert degraded["candidates"] == []
    assert degraded["status_by_name"][abstraction]["reason_code"] == "schema_invalid"

    empty = validate_experimental_auditor_outputs(
        outputs={
            auditor: {"terminal_status": "success", "output": []}
            for auditor in EXPERIMENTAL_REVIEW_AUDITORS
        },
        **kwargs,
    )
    assert empty["state"] == "complete"
    assert empty["candidates"] == []


@pytest.mark.parametrize(
    "confidence",
    [
        10**1000,
        -(10**1000),
        math.inf,
        -math.inf,
        math.nan,
        "0.9",
        None,
    ],
)
def test_experimental_validation_degrades_extreme_confidence_without_raising(
    tmp_path: Path, confidence: object
) -> None:
    reachability, abstraction = EXPERIMENTAL_REVIEW_AUDITORS
    invalid_candidate = _experimental_candidate("overengineering_abstraction_surface")
    invalid_candidate["confidence"] = confidence
    outputs = {
        reachability: {
            "terminal_status": "success",
            "output": [_experimental_candidate("overengineering_reachability")],
        },
        abstraction: {
            "terminal_status": "success",
            "output": [invalid_candidate],
        },
    }

    result = validate_experimental_auditor_outputs(
        outputs=outputs,
        valid_diff_lines={"src/app.py": [42]},
        snapshot={"head_sha": "head", "diff_sha256": "diff"},
        review_root=str(tmp_path),
    )

    assert result["state"] == "degraded"
    assert result["candidates"] == []
    assert result["status_by_name"][reachability]["status"] == "success"
    assert result["status_by_name"][abstraction]["reason_code"] == "schema_invalid"
    assert len(result["malformed_envelopes"]) == 1
    assert len(json.dumps(result["malformed_envelopes"]).encode()) < 40_000


def test_experimental_validation_bounds_oversized_payload_and_rejects_mixed_batch(
    tmp_path: Path,
) -> None:
    reachability, abstraction = EXPERIMENTAL_REVIEW_AUDITORS
    oversized = _experimental_candidate("overengineering_abstraction_surface")
    oversized["message"] = "x" * (1024 * 1024 + 1)
    result = validate_experimental_auditor_outputs(
        outputs={
            reachability: {
                "terminal_status": "success",
                "output": [_experimental_candidate("overengineering_reachability")],
            },
            abstraction: {"terminal_status": "success", "output": [oversized]},
        },
        valid_diff_lines={"src/app.py": [42]},
        snapshot={"head_sha": "head", "diff_sha256": "diff"},
        review_root=str(tmp_path),
    )

    assert result["state"] == "degraded"
    assert result["candidates"] == []
    envelope = result["malformed_envelopes"][0]
    assert envelope["received_byte_length"] > 1024 * 1024
    assert envelope["excerpt_byte_length"] <= 4096
    assert len(envelope["errors"]) == 1


@pytest.mark.parametrize(
    ("field", "value", "expected_reason"),
    [
        ("file", "../outside.py", "path_escape"),
        ("line", 99, "not_changed_line"),
    ],
)
def test_experimental_validation_preserves_specific_reason_codes(
    tmp_path: Path,
    field: str,
    value: object,
    expected_reason: str,
) -> None:
    outputs = {}
    for auditor, dimension in zip(
        EXPERIMENTAL_REVIEW_AUDITORS,
        ("overengineering_reachability", "overengineering_abstraction_surface"),
        strict=True,
    ):
        candidate = _experimental_candidate(dimension)
        candidate[field] = value
        outputs[auditor] = {"terminal_status": "success", "output": [candidate]}

    result = validate_experimental_auditor_outputs(
        outputs=outputs,
        valid_diff_lines={"src/app.py": [42]},
        snapshot={"head_sha": "head", "diff_sha256": "diff"},
        review_root=str(tmp_path),
    )

    assert result["state"] == "degraded"
    assert result["candidates"] == []
    assert all(
        status["reason_code"] == expected_reason for status in result["status_by_name"].values()
    )
    assert all(
        envelope["rejection_reason"] == expected_reason
        for envelope in result["malformed_envelopes"]
    )


@pytest.mark.parametrize(
    "missing_facet",
    [
        "return_values",
        "exceptions_errors",
        "ordering",
        "persistence",
        "concurrency",
        "compatibility",
    ],
)
def test_experimental_validation_rejects_each_missing_behavior_facet(
    tmp_path: Path, missing_facet: str
) -> None:
    phrases = {
        "return_values": "return values",
        "exceptions_errors": "exceptions and errors",
        "ordering": "ordering",
        "persistence": "persistence",
        "concurrency": "concurrency",
        "compatibility": "compatibility",
    }
    outputs = {}
    for auditor, dimension in zip(
        EXPERIMENTAL_REVIEW_AUDITORS,
        ("overengineering_reachability", "overengineering_abstraction_surface"),
        strict=True,
    ):
        candidate = _experimental_candidate(dimension)
        candidate["simpler_behavior"] = ", ".join(
            phrase for facet, phrase in phrases.items() if facet != missing_facet
        )
        outputs[auditor] = {"terminal_status": "success", "output": [candidate]}

    result = validate_experimental_auditor_outputs(
        outputs=outputs,
        valid_diff_lines={"src/app.py": [42]},
        snapshot={"head_sha": "head", "diff_sha256": "diff"},
        review_root=str(tmp_path),
    )

    assert result["state"] == "degraded"
    assert result["candidates"] == []
    assert all(
        status["reason_code"] == "schema_invalid" for status in result["status_by_name"].values()
    )


@pytest.mark.parametrize(
    ("facet", "superstring"),
    [
        ("return_values", "returning"),
        ("exceptions_errors", "exceptional"),
        ("ordering", "reordering"),
        ("persistence", "persistent"),
        ("concurrency", "concurrency-safe"),
        ("compatibility", "non-compatibility"),
    ],
)
def test_experimental_validation_rejects_behavior_facet_superstrings(
    tmp_path: Path,
    facet: str,
    superstring: str,
) -> None:
    phrases = {
        "return_values": "return values",
        "exceptions_errors": "exceptions and errors",
        "ordering": "ordering",
        "persistence": "persistence",
        "concurrency": "concurrency",
        "compatibility": "compatibility",
    }
    phrases[facet] = superstring
    outputs = {}
    for auditor, dimension in zip(
        EXPERIMENTAL_REVIEW_AUDITORS,
        ("overengineering_reachability", "overengineering_abstraction_surface"),
        strict=True,
    ):
        candidate = _experimental_candidate(dimension)
        candidate["simpler_behavior"] = ", ".join(phrases.values())
        outputs[auditor] = {"terminal_status": "success", "output": [candidate]}

    result = validate_experimental_auditor_outputs(
        outputs=outputs,
        valid_diff_lines={"src/app.py": [42]},
        snapshot={"head_sha": "head", "diff_sha256": "diff"},
        review_root=str(tmp_path),
    )

    assert result["state"] == "degraded"
    assert all(facet in str(envelope["errors"]) for envelope in result["malformed_envelopes"])


def test_experimental_validation_accepts_boundaries_and_negated_facets(tmp_path: Path) -> None:
    outputs = {}
    for auditor, dimension in zip(
        EXPERIMENTAL_REVIEW_AUDITORS,
        ("overengineering_reachability", "overengineering_abstraction_surface"),
        strict=True,
    ):
        candidate = _experimental_candidate(dimension)
        candidate["simpler_behavior"] = (
            "Return values stay equivalent; no exceptions or errors; ordering is unchanged; "
            "persistence is unchanged; no concurrency change; no compatibility break."
        )
        outputs[auditor] = {"terminal_status": "success", "output": [candidate]}

    result = validate_experimental_auditor_outputs(
        outputs=outputs,
        valid_diff_lines={"src/app.py": [42]},
        snapshot={"head_sha": "head", "diff_sha256": "diff"},
        review_root=str(tmp_path),
    )

    assert result["state"] == "complete"


def test_experimental_candidate_closed_key_error_lists_missing_and_extra(tmp_path: Path) -> None:
    outputs = {}
    for auditor, dimension in zip(
        EXPERIMENTAL_REVIEW_AUDITORS,
        ("overengineering_reachability", "overengineering_abstraction_surface"),
        strict=True,
    ):
        candidate = _experimental_candidate(dimension)
        del candidate["message"]
        candidate["unexpected"] = True
        outputs[auditor] = {"terminal_status": "success", "output": [candidate]}

    result = validate_experimental_auditor_outputs(
        outputs=outputs,
        valid_diff_lines={"src/app.py": [42]},
        snapshot={"head_sha": "head", "diff_sha256": "diff"},
        review_root=str(tmp_path),
    )

    assert all(
        envelope["errors"]
        == ["candidate has invalid closed keys: missing=['message']; extra=['unexpected']"]
        for envelope in result["malformed_envelopes"]
    )


@pytest.mark.parametrize(
    ("failure_kind", "expected_reason"),
    [
        ("tool_failure", "tool_failure"),
        ("refusal", "refusal"),
        ("interruption", "interruption"),
        ("truncation", "truncation"),
        ("missing_result", "missing_result"),
        ("malformed_json", "malformed_json"),
        ("non_array", "non_array"),
        ("schema_invalid", "schema_invalid"),
    ],
)
def test_experimental_failure_matrix_degrades_without_partial_candidates(
    tmp_path: Path, failure_kind: str, expected_reason: str
) -> None:
    reachability, abstraction = EXPERIMENTAL_REVIEW_AUDITORS
    outputs: dict[str, dict[str, object]] = {
        reachability: {
            "terminal_status": "success",
            "output": [_experimental_candidate("overengineering_reachability")],
        }
    }
    if failure_kind in {"tool_failure", "refusal", "interruption", "truncation"}:
        outputs[abstraction] = {"terminal_status": failure_kind, "output": "[]"}
    elif failure_kind == "malformed_json":
        outputs[abstraction] = {"terminal_status": "success", "output": "["}
    elif failure_kind == "non_array":
        outputs[abstraction] = {"terminal_status": "success", "output": "{}"}
    elif failure_kind == "schema_invalid":
        candidate = _experimental_candidate("overengineering_abstraction_surface")
        candidate["message"] = ""
        outputs[abstraction] = {"terminal_status": "success", "output": [candidate]}

    result = validate_experimental_auditor_outputs(
        outputs=outputs,
        valid_diff_lines={"src/app.py": [42]},
        snapshot={"head_sha": "head", "diff_sha256": "diff"},
        review_root=str(tmp_path),
    )

    assert result["state"] == "degraded"
    assert result["candidates"] == []
    assert result["status_by_name"][reachability]["status"] == "success"
    assert result["status_by_name"][abstraction]["reason_code"] == expected_reason
    envelope = result["malformed_envelopes"][0]
    assert envelope["producer"] == abstraction
    assert envelope["rejection_reason"] == expected_reason


def test_experimental_aggregation_is_deterministic_and_retains_losers() -> None:
    reachability, abstraction = EXPERIMENTAL_REVIEW_AUDITORS
    candidates = [
        {
            "candidate_id": "suppressed",
            "auditor_name": reachability,
            "original_index": 0,
            "file": "src/old.py",
            "line": 10,
            "severity": "critical",
            "requires_decision": False,
        },
        {
            "candidate_id": "fixed-rank-winner",
            "auditor_name": reachability,
            "original_index": 2,
            "file": "src/app.py",
            "line": 42,
            "severity": "warning",
            "requires_decision": False,
        },
        {
            "candidate_id": "dedup-loser",
            "auditor_name": abstraction,
            "original_index": 0,
            "file": "src/app.py",
            "line": 42,
            "severity": "warning",
            "requires_decision": False,
        },
        {
            "candidate_id": "rejected",
            "auditor_name": reachability,
            "original_index": 1,
            "file": "src/other.py",
            "line": 7,
            "severity": "critical",
            "requires_decision": False,
        },
    ]
    dispositions = [
        {
            "candidate_id": candidate_id,
            "disposition_id": f"disposition-{candidate_id}",
            "reason_code": "accepted",
        }
        for candidate_id in ("suppressed", "fixed-rank-winner", "dedup-loser")
    ] + [
        {
            "candidate_id": "rejected",
            "disposition_id": "disposition-rejected",
            "reason_code": "insufficient_evidence",
        }
    ]
    kwargs = {
        "dispositions": dispositions,
        "prior_resolved_findings": [{"file": "src/old.py", "line": 12}],
    }

    forward = aggregate_combined_review_candidates(candidates=candidates, **kwargs)
    reverse = aggregate_combined_review_candidates(candidates=list(reversed(candidates)), **kwargs)

    assert forward == reverse
    assert [candidate["candidate_id"] for candidate in forward["survivors"]] == [
        "fixed-rank-winner"
    ]
    records = forward["aggregation_records"]
    assert any(
        record
        == {
            "candidate_id": "suppressed",
            "reason_code": "suppressed_prior_thread",
        }
        for record in records
    )
    loser = next(record for record in records if record["candidate_id"] == "dedup-loser")
    assert loser["reason_code"] == "duplicate_candidate"
    assert loser["winner_candidate_id"] == "fixed-rank-winner"
    assert loser["member_ids"] == ["fixed-rank-winner", "dedup-loser"]
    assert "rejected" not in {str(record["candidate_id"]) for record in records}


def test_experimental_aggregation_rejects_accepted_disposition_without_identity() -> None:
    candidate = {
        "candidate_id": "candidate-1",
        "auditor_name": EXPERIMENTAL_REVIEW_AUDITORS[0],
        "original_index": 0,
        "file": "src/app.py",
        "line": 42,
        "severity": "critical",
        "requires_decision": False,
    }

    result = aggregate_combined_review_candidates(
        candidates=[candidate],
        dispositions=[{"candidate_id": "candidate-1", "reason_code": "accepted"}],
        prior_resolved_findings=[],
    )

    assert result == {
        "state": "complete",
        "survivors": [],
        "aggregation_records": [],
        "validation_errors": [],
    }


def test_combined_review_aggregation_is_cross_source_and_completion_order_independent() -> None:
    reachability, abstraction = EXPERIMENTAL_REVIEW_AUDITORS
    standard = [
        {
            "file": "src/app.py",
            "line": 42,
            "dimension": "arch",
            "severity": "warning",
            "message": "Standard finding wins the collision",
            "requires_decision": False,
        }
    ]
    deletion = [
        {
            "file": "src/deleted.py",
            "line": 8,
            "dimension": "deletion_regression",
            "severity": "critical",
            "message": "Deleted behavior was restored",
            "requires_decision": False,
        }
    ]
    experimental = [
        {
            "candidate_id": "experimental-collision",
            "auditor_name": reachability,
            "original_index": 0,
            "file": "src/app.py",
            "line": 42,
            "dimension": "overengineering_reachability",
            "severity": "warning",
            "message": "No consumer is reachable",
            "requires_decision": False,
        },
        {
            "candidate_id": "experimental-abstraction",
            "auditor_name": abstraction,
            "original_index": 0,
            "file": "src/other.py",
            "line": 19,
            "dimension": "overengineering_abstraction_surface",
            "severity": "warning",
            "message": "The abstraction surface is unused",
            "requires_decision": False,
        },
    ]
    dispositions = [
        {
            "candidate_id": item["candidate_id"],
            "disposition_id": f"disposition-{item['candidate_id']}",
            "reason_code": "accepted",
        }
        for item in experimental
    ]
    kwargs = {
        "dispositions": dispositions,
        "prior_resolved_findings": [],
        "standard_findings": standard,
        "deletion_findings": deletion,
        "valid_diff_lines": {
            "src/app.py": [42],
            "src/deleted.py": [8],
            "src/other.py": [19],
        },
        "snapshot": {"head_sha": "head", "base_sha": "base"},
        "review_root": str(Path.cwd()),
    }

    forward = aggregate_combined_review_candidates(
        candidates=experimental,
        **kwargs,
    )
    reverse = aggregate_combined_review_candidates(
        candidates=list(reversed(experimental)),
        **kwargs,
    )

    assert forward == reverse
    assert [item["dimension"] for item in forward["survivors"]] == [
        "arch",
        "deletion_regression",
        "overengineering_abstraction_surface",
    ]
    standard_winner_id = str(forward["survivors"][0]["candidate_id"])
    loser = next(
        record
        for record in forward["aggregation_records"]
        if record["candidate_id"] == "experimental-collision"
    )
    assert loser["winner_candidate_id"] == standard_winner_id
    assert loser["member_ids"] == [standard_winner_id, "experimental-collision"]
    assert loser["dedup_group_id"]
    assert "fixed source rank" in loser["rationale"]


def test_review_finding_renderer_is_shared_and_preserves_proof_provenance() -> None:
    experimental = {
        "severity": "warning",
        "dimension": "overengineering_reachability",
        "message": "No consumer is reachable",
        "evidence": [
            {
                "path": "src/app.py",
                "line": 42,
                "role": "anchor",
                "claim": "Declaration",
            }
        ],
        "candidate_id": "candidate-1",
        "disposition_id": "disposition-1",
    }

    rendered = render_review_finding_body(experimental)

    assert rendered.startswith("[warning] overengineering_reachability: No consumer is reachable")
    assert "src/app.py:42 [anchor] Declaration" in rendered
    assert "candidate_id=candidate-1 disposition_id=disposition-1" in rendered
    assert (
        render_review_finding_body(
            {
                "severity": "critical",
                "dimension": "bugs",
                "message": "Standard finding",
            }
        )
        == "[critical] bugs: Standard finding"
    )


def test_review_finding_renderer_bounds_utf8_body_and_reserves_provenance() -> None:
    rendered = render_review_finding_body(
        {
            "severity": "warning",
            "dimension": "overengineering_reachability",
            "message": "π" * 100_000,
            "evidence": [
                {
                    "path": "src/" + ("nested/" * 1000) + "app.py",
                    "line": 42,
                    "role": "anchor" * 1000,
                    "claim": "claim" * 10_000,
                }
                for _index in range(20)
            ],
            "candidate_id": "candidate-1",
            "disposition_id": "disposition-1",
        }
    )

    assert len(rendered.encode("utf-8")) <= 60 * 1024
    assert rendered.endswith("Provenance: candidate_id=candidate-1 disposition_id=disposition-1")
    assert "π" * 100_000 not in rendered


def test_local_review_normalization_preserves_proof_fields_and_adds_aliases() -> None:
    finding = {
        "file": "src/app.py",
        "line": 42,
        "severity": "warning",
        "dimension": "overengineering_reachability",
        "message": "No consumer is reachable",
        "evidence": [{"path": "src/app.py", "line": 42, "role": "anchor", "claim": "declaration"}],
        "trace": [{"path": "src/app.py", "line": 42}],
        "boundary_checks": [{"boundary": "public_api", "status": "checked_absent"}],
        "confidence": 0.9,
        "simpler_behavior": "Equivalent return values and errors",
        "candidate_id": "candidate-1",
        "disposition_id": "disposition-1",
        "snapshot": {"head_sha": "head"},
    }

    normalized = normalize_local_review_finding(finding)

    assert {key: normalized[key] for key in finding} == finding
    assert normalized["path"] == finding["file"]
    assert normalized["body"] == render_review_finding_body(finding)


@pytest.mark.parametrize(
    (
        "retained_snapshot_was_valid",
        "final_snapshot_is_fresh",
        "gate_state",
        "experimental_audit_state",
        "findings",
        "expected",
    ),
    [
        (True, False, "valid_true", "complete", [], "stale_snapshot"),
        (
            True,
            True,
            "degraded",
            "degraded",
            [{"severity": "critical", "requires_decision": False}],
            "changes_requested",
        ),
        (False, False, "degraded", "not_eligible", [], "needs_human"),
        (True, True, "valid_true", "degraded", [], "needs_human"),
        (
            True,
            True,
            "valid_true",
            "complete",
            [
                {"severity": "warning", "requires_decision": False},
                {"severity": "info", "requires_decision": True},
            ],
            "approved_with_comments",
        ),
        (
            True,
            True,
            "valid_true",
            "complete",
            [{"severity": "info", "requires_decision": True}],
            "needs_human",
        ),
        (True, True, "valid_false", "not_required", [], "approved"),
    ],
)
def test_experimental_review_verdict_branches_and_precedence(
    retained_snapshot_was_valid: bool,
    final_snapshot_is_fresh: bool,
    gate_state: str,
    experimental_audit_state: str,
    findings: list[dict[str, object]],
    expected: str,
) -> None:
    assert (
        determine_experimental_review_verdict(
            retained_snapshot_was_valid=retained_snapshot_was_valid,
            final_snapshot_is_fresh=final_snapshot_is_fresh,
            gate_state=gate_state,
            experimental_audit_state=experimental_audit_state,
            findings=findings,
        )
        == expected
    )


def test_valid_retained_snapshot_movement_is_stale() -> None:
    assert (
        determine_experimental_review_verdict(
            retained_snapshot_was_valid=True,
            final_snapshot_is_fresh=False,
            gate_state="valid_true",
            experimental_audit_state="complete",
            findings=[],
        )
        == "stale_snapshot"
    )


def test_experimental_publication_preserves_provenance_and_suppresses_stale_effects(
    tmp_path: Path,
) -> None:
    reachability, abstraction = EXPERIMENTAL_REVIEW_AUDITORS
    snapshot = {
        "head_sha": "head",
        "base_sha": "base",
        "merge_base_sha": "merge-base",
        "diff_sha256": "diff",
    }
    validation = validate_experimental_auditor_outputs(
        outputs={
            reachability: {
                "terminal_status": "success",
                "output": [_experimental_candidate("overengineering_reachability")],
            },
            abstraction: {
                "terminal_status": "success",
                "output": [
                    _experimental_candidate(
                        "overengineering_abstraction_surface",
                        line=43,
                    )
                ],
            },
        },
        valid_diff_lines={"src/app.py": [42, 43]},
        snapshot=snapshot,
        review_root=str(tmp_path),
    )
    accepted, rejected = validation["candidates"]
    dispositions = [
        {
            "candidate_id": accepted["candidate_id"],
            "disposition_id": "accepted-disposition",
            "reason_code": "accepted",
        },
        {
            "candidate_id": rejected["candidate_id"],
            "disposition_id": "rejected-disposition",
            "reason_code": "simpler_behavior_not_equivalent",
        },
    ]
    aggregation = aggregate_combined_review_candidates(
        candidates=validation["candidates"],
        dispositions=dispositions,
        prior_resolved_findings=[],
    )
    assert [item["candidate_id"] for item in aggregation["survivors"]] == [
        accepted["candidate_id"]
    ]
    survivor = aggregation["survivors"][0]
    assert survivor["disposition_id"] == "accepted-disposition"

    ledger = {
        "candidates": validation["candidates"],
        "disposition_records": dispositions,
        "aggregation_records": aggregation["aggregation_records"],
    }
    publication = prepare_experimental_review_publication(
        raw_ledger=ledger,
        survivors=aggregation["survivors"],
        snapshot=snapshot,
        annotation_generation_id="annotation-generation",
        mode="local",
        snapshot_is_fresh=True,
    )

    assert publication["artifact_order"] == [
        "raw_findings",
        "diff_context",
        "local_findings",
    ]
    assert "effect_artifacts" not in publication
    artifacts = publication["artifacts"]
    identity_keys = {
        "_head_sha",
        "_base_sha",
        "_merge_base_sha",
        "annotation_generation_id",
        "review_generation_id",
    }
    identities = [{key: artifact[key] for key in identity_keys} for artifact in artifacts.values()]
    assert identities[1:] == identities[:-1]
    for artifact_name, field in (
        ("diff_context", "context_entries"),
        ("local_findings", "findings"),
    ):
        finding = artifacts[artifact_name][field][0]
        assert {key: finding[key] for key in survivor} == survivor
        assert finding["path"] == survivor["file"]
        assert finding["side"] == "RIGHT"
        assert finding["code_region"] == ""
        assert finding["body"] == render_review_finding_body(survivor)
        assert finding["candidate_id"] == accepted["candidate_id"]
        assert finding["disposition_id"] == "accepted-disposition"
        assert finding["evidence"] == accepted["evidence"]
        assert finding["trace"] == accepted["trace"]
        assert finding["boundary_checks"] == accepted["boundary_checks"]
        assert rejected["candidate_id"] not in {
            item["candidate_id"] for item in artifacts[artifact_name][field]
        }

    stale = prepare_experimental_review_publication(
        raw_ledger=ledger,
        survivors=aggregation["survivors"],
        snapshot=snapshot,
        annotation_generation_id="annotation-generation",
        mode="local",
        snapshot_is_fresh=False,
    )
    assert stale["state"] == "stale_snapshot"
    assert stale["artifact_order"] == ["raw_findings"]
    assert set(stale["artifacts"]) == {"raw_findings"}
    assert "effect_artifacts" not in stale
    assert stale["artifacts"]["raw_findings"]["survivors"] == []
    assert (
        stale["artifacts"]["raw_findings"]["review_generation_id"]
        != publication["artifacts"]["raw_findings"]["review_generation_id"]
    )

    no_survivors = prepare_experimental_review_publication(
        raw_ledger=ledger,
        survivors=[],
        snapshot=snapshot,
        annotation_generation_id="annotation-generation",
        mode="local",
        snapshot_is_fresh=True,
    )
    assert (
        no_survivors["artifacts"]["raw_findings"]["review_generation_id"]
        != publication["artifacts"]["raw_findings"]["review_generation_id"]
    )


def _prepared_local_experimental_publication() -> dict[str, object]:
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


def test_experimental_publication_retires_obsolete_artifacts(tmp_path: Path) -> None:
    output_dir = tmp_path / "review-output"
    base_arguments = {
        "raw_ledger": {"candidate_records": [{"candidate_id": "candidate-1"}]},
        "survivors": [{"file": "src/app.py", "line": 42}],
        "snapshot": {"head_sha": "head", "base_sha": "base"},
        "annotation_generation_id": "annotation-generation",
    }

    local = prepare_experimental_review_publication(
        **base_arguments,
        mode="local",
        snapshot_is_fresh=True,
    )
    publish_experimental_review_artifacts(
        publication=local,
        output_dir=str(output_dir),
        pr_number="18",
    )
    assert {path.name for path in output_dir.iterdir()} == {
        "raw_findings_18.json",
        "diff_context_18.json",
        "local_findings_18.json",
    }

    github_with_receipt = prepare_experimental_review_publication(
        **base_arguments,
        mode="github",
        snapshot_is_fresh=True,
        receipt={"posted": True, "http_status": 200},
    )
    publish_experimental_review_artifacts(
        publication=github_with_receipt,
        output_dir=str(output_dir),
        pr_number="18",
    )
    assert {path.name for path in output_dir.iterdir()} == {
        "raw_findings_18.json",
        "diff_context_18.json",
        "batch_review_response_18.json",
    }

    github_without_receipt = prepare_experimental_review_publication(
        **base_arguments,
        mode="github",
        snapshot_is_fresh=True,
    )
    publish_experimental_review_artifacts(
        publication=github_without_receipt,
        output_dir=str(output_dir),
        pr_number="18",
    )
    assert {path.name for path in output_dir.iterdir()} == {
        "raw_findings_18.json",
        "diff_context_18.json",
    }

    stale = prepare_experimental_review_publication(
        **base_arguments,
        mode="github",
        snapshot_is_fresh=False,
    )
    publish_experimental_review_artifacts(
        publication=stale,
        output_dir=str(output_dir),
        pr_number="18",
    )
    assert {path.name for path in output_dir.iterdir()} == {"raw_findings_18.json"}


def _combined_review_survivors(gate_state: str) -> list[dict[str, object]]:
    reachability, abstraction = EXPERIMENTAL_REVIEW_AUDITORS
    standard = [
        {
            "file": "src/app.py",
            "line": 40,
            "dimension": "bugs",
            "severity": "critical",
            "message": "Standard behavior regressed",
            "requires_decision": False,
        }
    ]
    deletion = [
        {
            "file": "src/deleted.py",
            "line": 7,
            "dimension": "deletion_regression",
            "severity": "critical",
            "message": "Deleted symbol was restored",
            "requires_decision": False,
        }
    ]
    experimental = [
        {
            "candidate_id": "reachability",
            "auditor_name": reachability,
            "original_index": 0,
            **_experimental_candidate("overengineering_reachability", line=42),
        },
        {
            "candidate_id": "abstraction",
            "auditor_name": abstraction,
            "original_index": 0,
            **_experimental_candidate(
                "overengineering_abstraction_surface",
                line=43,
            ),
        },
    ]
    eligible_experimental = experimental if gate_state == "valid_true" else []
    dispositions = [
        {
            "candidate_id": finding["candidate_id"],
            "disposition_id": f"disposition-{finding['candidate_id']}",
            "reason_code": "accepted",
        }
        for finding in eligible_experimental
    ]
    result = aggregate_combined_review_candidates(
        candidates=eligible_experimental,
        dispositions=dispositions,
        prior_resolved_findings=[],
        standard_findings=standard,
        deletion_findings=deletion,
        valid_diff_lines={"src/app.py": [40], "src/deleted.py": [7]},
        snapshot={"head_sha": "head", "base_sha": "base"},
        review_root=str(Path.cwd()),
    )
    assert result["state"] == "complete"
    return [dict(finding) for finding in result["survivors"]]


@pytest.mark.parametrize(
    "invalid_finding",
    [
        {
            "file": "src/app.py",
            "line": "40",
            "dimension": "bugs",
            "severity": "critical",
            "message": "Malformed line",
            "requires_decision": False,
        },
        {
            "file": "../escape.py",
            "line": 40,
            "dimension": "bugs",
            "severity": "critical",
            "message": "Escaping path",
            "requires_decision": False,
        },
        {
            "file": "src/app.py",
            "line": 41,
            "dimension": "bugs",
            "severity": "critical",
            "message": "Unchanged line",
            "requires_decision": False,
        },
        {
            "file": "src/app.py",
            "line": 40,
            "dimension": "bugs",
            "severity": "critical",
            "message": "Unexpected field",
            "requires_decision": False,
            "opaque": True,
        },
    ],
)
def test_standard_review_findings_degrade_atomically(
    invalid_finding: dict[str, object],
) -> None:
    valid_finding = {
        "file": "src/valid.py",
        "line": 7,
        "dimension": "tests",
        "severity": "warning",
        "message": "Valid sibling",
        "requires_decision": False,
    }
    result = aggregate_combined_review_candidates(
        candidates=[],
        dispositions=[],
        prior_resolved_findings=[],
        standard_findings=[valid_finding, invalid_finding],
        valid_diff_lines={"src/valid.py": [7], "src/app.py": [40]},
        snapshot={"head_sha": "head", "base_sha": "base"},
        review_root=str(Path.cwd()),
    )
    assert result["state"] == "degraded"
    assert result["survivors"] == []
    assert result["validation_errors"]


def test_standard_finding_closed_key_error_lists_missing_and_extra() -> None:
    finding = {
        "file": "src/app.py",
        "line": 40,
        "dimension": "bugs",
        "severity": "warning",
        "requires_decision": False,
        "unexpected": True,
    }

    result = aggregate_combined_review_candidates(
        candidates=[],
        dispositions=[],
        prior_resolved_findings=[],
        standard_findings=[finding],
        valid_diff_lines={"src/app.py": [40]},
        snapshot={"head_sha": "head", "base_sha": "base"},
        review_root=str(Path.cwd()),
    )

    assert result["validation_errors"] == [
        "standard[0]: finding has invalid closed keys: missing=['message']; extra=['unexpected']"
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [("dimension", []), ("severity", {})],
)
def test_standard_review_findings_reject_unhashable_enum_values(
    field: str,
    value: object,
) -> None:
    finding = {
        "file": "src/app.py",
        "line": 40,
        "dimension": "bugs",
        "severity": "critical",
        "message": "Malformed enum value",
        "requires_decision": False,
    }
    finding[field] = value

    result = aggregate_combined_review_candidates(
        candidates=[],
        dispositions=[],
        prior_resolved_findings=[],
        standard_findings=[finding],
        valid_diff_lines={"src/app.py": [40]},
        snapshot={"head_sha": "head", "base_sha": "base"},
        review_root=str(Path.cwd()),
    )

    assert result["state"] == "degraded"
    assert result["survivors"] == []
    assert result["validation_errors"] == [f"standard[0]: {field} must be a non-empty string"]


def test_standard_review_findings_accept_hunk_range_fallback() -> None:
    finding = {
        "file": "src/app.py",
        "line": 40,
        "dimension": "bugs",
        "severity": "warning",
        "message": "Valid hunk-range finding",
        "requires_decision": False,
    }

    result = aggregate_combined_review_candidates(
        candidates=[],
        dispositions=[],
        prior_resolved_findings=[],
        standard_findings=[finding],
        valid_diff_lines={},
        valid_line_ranges={"src/app.py": [[38, 42]]},
        snapshot={"head_sha": "head", "base_sha": "base"},
        review_root=str(Path.cwd()),
    )

    assert result["state"] == "complete"
    assert [item["line"] for item in result["survivors"]] == [40]
    assert result["validation_errors"] == []


def test_standard_review_findings_require_snapshot_authority() -> None:
    result = aggregate_combined_review_candidates(
        candidates=[],
        dispositions=[],
        prior_resolved_findings=[],
        standard_findings=[
            {
                "file": "src/app.py",
                "line": 40,
                "dimension": "bugs",
                "severity": "critical",
                "message": "Missing snapshot",
                "requires_decision": False,
            }
        ],
        valid_diff_lines={"src/app.py": [40]},
        review_root=str(Path.cwd()),
    )
    assert result["state"] == "degraded"
    assert result["survivors"] == []
    assert result["validation_errors"] == ["snapshot head/base authority must be non-empty"]


@pytest.mark.parametrize(
    ("gate_state", "expected_dimensions"),
    [
        (
            "valid_true",
            {
                "bugs",
                "deletion_regression",
                "overengineering_reachability",
                "overengineering_abstraction_surface",
            },
        ),
        ("valid_false", {"bugs", "deletion_regression"}),
        ("degraded", {"bugs", "deletion_regression"}),
    ],
)
def test_combined_findings_survive_local_publication_for_every_gate_state(
    tmp_path: Path,
    gate_state: str,
    expected_dimensions: set[str],
) -> None:
    survivors = _combined_review_survivors(gate_state)
    publication = prepare_experimental_review_publication(
        raw_ledger={
            "candidate_records": survivors,
            "verdict_use_records": [
                {"candidate_id": item["candidate_id"], "used": True} for item in survivors
            ],
        },
        survivors=survivors,
        snapshot={"head_sha": "head", "base_sha": "base", "merge_base_sha": "merge"},
        annotation_generation_id="annotation",
        mode="local",
        snapshot_is_fresh=True,
        handoff_metadata={
            "summary": "AutoSkillit review",
            "verdict": "changes_requested",
            "pr_number": 17,
            "iteration": 2,
            "schema_version": 1,
        },
    )
    result = publish_experimental_review_artifacts(
        publication=publication,
        output_dir=str(tmp_path / gate_state),
        pr_number="17",
    )

    local_document = json.loads(Path(result["published_paths"]["local_findings"]).read_text())
    findings = local_document["findings"]
    assert {finding["dimension"] for finding in findings} == expected_dimensions
    assert all(finding["path"] == finding["file"] for finding in findings)
    assert all(finding["body"] == render_review_finding_body(finding) for finding in findings)
    assert local_document["iteration"] == 2
    assert local_document["verdict"] == "changes_requested"
    standard_finding = next(finding for finding in findings if finding["dimension"] == "bugs")
    assert standard_finding["record_digest"]
    assert standard_finding["snapshot"] == {"head_sha": "head", "base_sha": "base"}


def test_github_receipt_shares_prederived_generation_and_is_published_last(
    tmp_path: Path,
) -> None:
    survivors = _combined_review_survivors("valid_true")
    ledger = {
        "candidate_records": survivors,
        "verdict_use_records": [
            {"candidate_id": item["candidate_id"], "verdict": "changes_requested"}
            for item in survivors
        ],
    }
    metadata = {"pr_number": 23, "schema_version": 1}
    snapshot = {"head_sha": "head", "base_sha": "base", "merge_base_sha": "merge"}
    seed = prepare_experimental_review_publication(
        raw_ledger=ledger,
        survivors=survivors,
        snapshot=snapshot,
        annotation_generation_id="annotation",
        mode="github",
        snapshot_is_fresh=True,
        handoff_metadata=metadata,
    )
    generation_id = seed["artifacts"]["raw_findings"]["review_generation_id"]
    publication = prepare_experimental_review_publication(
        raw_ledger=ledger,
        survivors=survivors,
        snapshot=snapshot,
        annotation_generation_id="annotation",
        mode="github",
        snapshot_is_fresh=True,
        handoff_metadata=metadata,
        receipt={
            "posted": True,
            "http_status": 200,
            "commit_id": "head",
            "review_generation_id": "must-be-overridden",
        },
    )

    assert publication["artifact_order"] == [
        "raw_findings",
        "diff_context",
        "review_receipt",
    ]
    assert publication["artifacts"]["review_receipt"]["review_generation_id"] == generation_id
    result = publish_experimental_review_artifacts(
        publication=publication,
        output_dir=str(tmp_path / "github"),
        pr_number="23",
    )

    assert [Path(record["path"]).name for record in result["publication_records"]] == [
        "raw_findings_23.json",
        "diff_context_23.json",
        "batch_review_response_23.json",
    ]
    documents = {
        name: json.loads(Path(path).read_text())
        for name, path in result["published_paths"].items()
    }
    assert {document["review_generation_id"] for document in documents.values()} == {generation_id}
    consumer_index = {
        (entry["path"], entry["line"]): entry
        for entry in documents["diff_context"]["context_entries"]
    }
    assert ("src/app.py", 42) in consumer_index
    assert ("src/app.py", 43) in consumer_index
    assert {consumer_index[("src/app.py", line)]["dimension"] for line in (42, 43)} == {
        "overengineering_reachability",
        "overengineering_abstraction_surface",
    }
    assert consumer_index[("src/app.py", 42)]["disposition_id"] == ("disposition-reachability")
    assert documents["review_receipt"]["commit_id"] == "head"


def test_write_temp_bytes_closes_descriptor_when_fdopen_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import autoskillit.smoke_utils._experimental_review as experimental_review

    real_close = os.close
    opened_fd = -1
    closed_fds: list[int] = []

    def failing_fdopen(fd: int, mode: str) -> None:
        nonlocal opened_fd
        opened_fd = fd
        raise OSError("injected fdopen failure")

    def recording_close(fd: int) -> None:
        closed_fds.append(fd)
        real_close(fd)

    monkeypatch.setattr(experimental_review.os, "fdopen", failing_fdopen)
    monkeypatch.setattr(experimental_review.os, "close", recording_close)

    with pytest.raises(OSError, match="injected fdopen failure"):
        experimental_review._write_temp_bytes(tmp_path, "artifact.json", b"payload")

    assert closed_fds == [opened_fd]
    assert not list(tmp_path.iterdir())


def test_experimental_publication_executes_same_directory_marker_last_renames(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import autoskillit.smoke_utils._experimental_review as experimental_review

    publication = _prepared_local_experimental_publication()
    output_dir = tmp_path / "review-output"
    real_replace = os.replace
    rename_calls: list[tuple[Path, Path]] = []

    def recording_replace(source: str | os.PathLike[str], destination: str | os.PathLike[str]):
        rename_calls.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(experimental_review.os, "replace", recording_replace)
    result = publish_experimental_review_artifacts(
        publication=publication,
        output_dir=str(output_dir),
        pr_number="17",
    )

    expected_names = [
        "raw_findings_17.json",
        "diff_context_17.json",
        "local_findings_17.json",
    ]
    assert [destination.name for _, destination in rename_calls] == expected_names
    assert all(
        source.parent == destination.parent == output_dir for source, destination in rename_calls
    )
    assert all(source.name.endswith(".tmp") for source, _ in rename_calls)
    assert list(result["published_paths"]) == [
        "raw_findings",
        "diff_context",
        "local_findings",
    ]
    for artifact_name, path in result["published_paths"].items():
        assert json.loads(Path(path).read_text()) == publication["artifacts"][artifact_name]
    assert result["publication_records"][-1]["artifact"] == "local_findings"
    assert not list(output_dir.glob(".*.tmp"))


@pytest.mark.parametrize("failure_index", [0, 1, 2])
def test_experimental_publication_rolls_back_each_write_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_index: int,
) -> None:
    import autoskillit.smoke_utils._experimental_review as experimental_review

    publication = _prepared_local_experimental_publication()
    output_dir = tmp_path / "review-output"
    output_dir.mkdir()
    final_paths = [
        output_dir / "raw_findings_18.json",
        output_dir / "diff_context_18.json",
        output_dir / "local_findings_18.json",
    ]
    for index, path in enumerate(final_paths):
        path.write_text(f"old-{index}")
    original_write = experimental_review._write_temp_bytes
    write_index = 0

    def failing_write(directory: Path, final_name: str, content: bytes) -> Path:
        nonlocal write_index
        current_index = write_index
        write_index += 1
        if current_index == failure_index:
            raise OSError("injected write failure")
        return original_write(directory, final_name, content)

    monkeypatch.setattr(experimental_review, "_write_temp_bytes", failing_write)
    with pytest.raises(RuntimeError, match="publication failed"):
        publish_experimental_review_artifacts(
            publication=publication,
            output_dir=str(output_dir),
            pr_number="18",
        )

    assert [path.read_text() for path in final_paths] == ["old-0", "old-1", "old-2"]
    assert not list(output_dir.glob(".*.tmp"))


@pytest.mark.parametrize("failure_index", [0, 1, 2])
def test_experimental_publication_rolls_back_each_rename_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_index: int,
) -> None:
    import autoskillit.smoke_utils._experimental_review as experimental_review

    publication = _prepared_local_experimental_publication()
    output_dir = tmp_path / "review-output"
    output_dir.mkdir()
    final_paths = [
        output_dir / "raw_findings_19.json",
        output_dir / "diff_context_19.json",
        output_dir / "local_findings_19.json",
    ]
    for index, path in enumerate(final_paths):
        path.write_text(f"old-{index}")
    real_replace = os.replace
    rename_index = 0
    failure_injected = False

    def failing_replace(source: str | os.PathLike[str], destination: str | os.PathLike[str]):
        nonlocal failure_injected, rename_index
        if not failure_injected:
            current_index = rename_index
            rename_index += 1
            if current_index == failure_index:
                failure_injected = True
                raise OSError("injected rename failure")
        real_replace(source, destination)

    monkeypatch.setattr(experimental_review.os, "replace", failing_replace)
    with pytest.raises(RuntimeError, match="publication failed"):
        publish_experimental_review_artifacts(
            publication=publication,
            output_dir=str(output_dir),
            pr_number="19",
        )

    assert [path.read_text() for path in final_paths] == ["old-0", "old-1", "old-2"]
    assert not list(output_dir.glob(".*.tmp"))


def test_experimental_publication_preserves_publication_and_rollback_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import autoskillit.smoke_utils._experimental_review as experimental_review

    publication = _prepared_local_experimental_publication()
    output_dir = tmp_path / "review-output"
    output_dir.mkdir()
    final_paths = [
        output_dir / "raw_findings_20.json",
        output_dir / "diff_context_20.json",
        output_dir / "local_findings_20.json",
    ]
    for index, path in enumerate(final_paths):
        path.write_text(f"old-{index}")
    real_replace = os.replace
    replace_count = 0

    def failing_replace(source: str | os.PathLike[str], destination: str | os.PathLike[str]):
        nonlocal replace_count
        replace_count += 1
        if replace_count == 2:
            raise OSError("injected publication failure")
        if replace_count == 3:
            raise OSError("injected rollback failure")
        real_replace(source, destination)

    monkeypatch.setattr(experimental_review.os, "replace", failing_replace)
    with pytest.raises(ExceptionGroup) as exc_info:
        publish_experimental_review_artifacts(
            publication=publication,
            output_dir=str(output_dir),
            pr_number="20",
        )

    errors = exc_info.value.exceptions
    assert [str(error) for error in errors] == [
        "injected publication failure",
        "injected rollback failure",
    ]
    assert [path.read_text() for path in final_paths[1:]] == ["old-1", "old-2"]


# ---------------------------------------------------------------------------
# T_EDC1–T_EDC4: enrich_diff_context tests
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
def test_enrich_diff_context_preserves_experimental_provenance(tmp_path: Path) -> None:
    """Enrichment changes only code_region on an experimental context entry."""
    review_dir = tmp_path / ".autoskillit" / "temp" / "review-pr"
    review_dir.mkdir(parents=True)
    entry = {
        "path": "src/app.py",
        "line": 42,
        "severity": "warning",
        "message": "Unreachable abstraction",
        "code_region": "",
        "evidence": [
            {"path": "src/app.py", "line": 42, "role": "anchor", "claim": "Declaration"},
            {"path": "src/app.py", "line": 44, "role": "consumer", "claim": "Only consumer"},
        ],
        "trace": [{"path": "src/app.py", "line": 44, "relation": "calls"}],
        "boundary_checks": [
            {
                "boundary": "public_api",
                "status": "checked_no_reachable_path",
                "claim": "No public entry point",
            }
        ],
        "confidence": 0.9,
        "simpler_behavior": "Equivalent across all semantic categories",
        "candidate_id": "candidate-1",
        "disposition_id": "disposition-1",
        "snapshot": {"head_sha": "head", "diff_sha256": "diff"},
        "opaque_future_field": {"preserve": True},
    }
    handoff = {
        "schema_version": 2,
        "_head_sha": "head",
        "_base_sha": "base",
        "_merge_base_sha": "merge-base",
        "annotation_generation_id": "generation-1",
        "review_generation_id": "review-1",
        "context_entries": [entry],
    }
    (review_dir / "diff_context_123.json").write_text(json.dumps(handoff))
    (review_dir / "annotated_diff_123.txt").write_text(_ANNOTATED_DIFF_CONTENT)

    result = enrich_diff_context(
        pr_number="123", project_dir=str(tmp_path), output_dir=str(review_dir)
    )

    assert result["enriched"] == "true"
    enriched = json.loads((review_dir / "diff_context_123.json").read_text())
    expected = json.loads(json.dumps(handoff))
    expected["context_entries"][0]["code_region"] = enriched["context_entries"][0]["code_region"]
    assert "[L42]" in enriched["context_entries"][0]["code_region"]
    assert enriched == expected


# T_EDC4
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
_SHA = "a" * 40
_BASE_SHA = "b" * 40
_MERGE_BASE_SHA = "c" * 40


def _annotation_run_side_effect(
    diff_output: str = _DIFF_OUTPUT,
    *,
    head_sha: str = _SHA,
    base_sha: str = _BASE_SHA,
    live_base_sha: str | None = None,
    merge_base_sha: str = _MERGE_BASE_SHA,
):
    def _run(args, **_kwargs):
        if args[:2] == ["gh", "api"]:
            assert len(args) == 5
            assert re.fullmatch(r"repos/\{owner\}/\{repo\}/pulls/\d+", args[2])
            assert args[3:] == [
                "--jq",
                "{headRefOid: .head.sha, baseRefOid: .base.sha}",
            ]
            payload = json.dumps(
                {
                    "headRefOid": head_sha,
                    "baseRefOid": live_base_sha or base_sha,
                }
            )
            return subprocess.CompletedProcess(args, 0, payload.encode(), b"")
        if args[:3] == ["gh", "pr", "diff"]:
            return subprocess.CompletedProcess(args, 0, diff_output.encode(), b"")
        if args[:3] == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(args, 0, head_sha.encode(), b"")
        if args[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(args, 0, base_sha.encode(), b"")
        if args[:2] == ["git", "merge-base"]:
            return subprocess.CompletedProcess(args, 0, merge_base_sha.encode(), b"")
        if args[:2] == ["git", "diff"]:
            return subprocess.CompletedProcess(args, 0, diff_output.encode(), b"")
        raise AssertionError(f"unexpected annotation command: {args}")

    return _run


@patch("subprocess.run")
def test_annotate_pr_diff_returns_review_mode_local(mock_run, tmp_path: Path) -> None:
    """T3.1: iteration < local_rounds → review_mode=local."""
    mock_run.side_effect = _annotation_run_side_effect()
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
    mock_run.side_effect = _annotation_run_side_effect()
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
    """T3.3: local mode resolves refs before a pinned git diff."""
    mock_run.side_effect = _annotation_run_side_effect()
    annotate_pr_diff(
        pr_number="123",
        cwd=str(tmp_path),
        output_dir=str(tmp_path),
        local_review_rounds="2",
        current_iteration="0",
        base_branch="main",
    )
    commands = [call[0][0] for call in mock_run.call_args_list]
    diff_command = next(command for command in commands if command[:2] == ["git", "diff"])
    assert diff_command[-2:] == [_MERGE_BASE_SHA, _SHA]
    assert commands.index(diff_command) > commands.index(["git", "merge-base", _BASE_SHA, _SHA])


@patch("subprocess.run")
def test_annotate_pr_diff_github_mode_uses_gh_pr_diff(mock_run, tmp_path: Path) -> None:
    """T3.4: github mode calls gh pr diff."""
    mock_run.side_effect = _annotation_run_side_effect()
    annotate_pr_diff(
        pr_number="123",
        cwd=str(tmp_path),
        output_dir=str(tmp_path),
        local_review_rounds="2",
        current_iteration="2",
        base_branch="",
    )
    commands = [call[0][0] for call in mock_run.call_args_list]
    diff_index = next(
        index for index, command in enumerate(commands) if command[:3] == ["gh", "pr", "diff"]
    )
    assert commands[diff_index - 1][:2] == ["gh", "api"]
    assert commands[diff_index + 1][:2] == ["gh", "api"]


@patch("subprocess.run")
def test_annotate_pr_diff_zero_local_rounds_always_github(mock_run, tmp_path: Path) -> None:
    """T3.5: local_review_rounds=0 → always github."""
    mock_run.side_effect = _annotation_run_side_effect()
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
    mock_run.side_effect = _annotation_run_side_effect()
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
    mock_run.side_effect = _annotation_run_side_effect()
    result = annotate_pr_diff(
        pr_number="123",
        cwd=str(tmp_path),
        output_dir=str(tmp_path),
        local_review_rounds="3",
        current_iteration="0",
        base_branch="",
    )
    assert result["review_mode"] == "github"
    commands = [call[0][0] for call in mock_run.call_args_list]
    assert any(command[:3] == ["gh", "pr", "diff"] for command in commands)


@patch("subprocess.run")
def test_annotate_pr_diff_backward_compat_no_new_params(mock_run, tmp_path: Path) -> None:
    """T3.7: old 3-arg call works and defaults review_mode=github."""
    mock_run.side_effect = _annotation_run_side_effect()
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
    mock_run.side_effect = _annotation_run_side_effect("+diff content\n")
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    result = annotate_pr_diff(pr_number=42, cwd=str(tmp_path), output_dir=str(output_dir))  # type: ignore[arg-type]
    assert result["annotated_diff_path"]

    # Verify the subprocess call received str "42", not int 42
    cmd_list = next(
        call[0][0] for call in mock_run.call_args_list if call[0][0][:3] == ["gh", "pr", "diff"]
    )
    assert "42" in cmd_list, f"Expected '42' in command, got {cmd_list}"


@patch("subprocess.run")
def test_annotate_pr_diff_produces_valid_lines_artifact(mock_run, tmp_path: Path) -> None:
    """annotate_pr_diff writes valid lines alongside the explicitly named hunk ranges."""
    import json

    from autoskillit.execution.diff_annotator import extract_valid_lines

    mock_run.side_effect = _annotation_run_side_effect()
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


def _churn_diff(*, additions: int, removals: int) -> str:
    return (
        "diff --git a/f.py b/f.py\n"
        "--- a/f.py\n"
        "+++ b/f.py\n"
        f"@@ -1,{removals} +1,{additions} @@\n"
        + "".join(f"-old_{index}\n" for index in range(removals))
        + "".join(f"+new_{index}\n" for index in range(additions))
    )


@pytest.mark.parametrize(
    ("additions", "removals", "expected"),
    [
        (2000, 0, False),
        (2001, 0, True),
        (1000, 1001, True),
        (0, 2001, True),
    ],
)
@patch("subprocess.run")
def test_annotate_pr_diff_writes_native_overengineering_gate(
    mock_run,
    tmp_path: Path,
    additions: int,
    removals: int,
    expected: bool,
) -> None:
    mock_run.side_effect = _annotation_run_side_effect(
        _churn_diff(additions=additions, removals=removals)
    )
    annotate_pr_diff(pr_number="91", cwd=str(tmp_path), output_dir=str(tmp_path))
    gate = json.loads((tmp_path / "metrics_91.json").read_text())["run_overengineering_audits"]
    assert type(gate) is bool
    assert gate is expected


@patch("subprocess.run")
def test_annotate_pr_diff_publishes_snapshot_manifest_last(
    mock_run,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hashlib

    import autoskillit.core as core

    diff = _churn_diff(additions=2, removals=1)
    mock_run.side_effect = _annotation_run_side_effect(diff)
    original_atomic_write = core.atomic_write
    write_order: list[str] = []

    def recording_atomic_write(path: Path, content: str) -> None:
        write_order.append(path.name)
        original_atomic_write(path, content)

    monkeypatch.setattr(core, "atomic_write", recording_atomic_write)
    annotate_pr_diff(
        pr_number="92",
        cwd=str(tmp_path),
        output_dir=str(tmp_path),
        local_review_rounds="1",
        current_iteration="0",
        base_branch="main",
    )
    metrics = json.loads((tmp_path / "metrics_92.json").read_text())
    assert metrics["_head_sha"] == _SHA
    assert metrics["_base_sha"] == _BASE_SHA
    assert metrics["_merge_base_sha"] == _MERGE_BASE_SHA
    assert metrics["diff_sha256"] == hashlib.sha256(diff.encode()).hexdigest()
    assert metrics["diff_byte_length"] == len(diff.encode())
    assert set(metrics["diff_source"]) == {
        "kind",
        "comparison",
        "context_lines",
        "rename_detection",
        "external_diff",
        "text_conversion",
        "profile_id",
    }
    for artifact in metrics["artifacts"].values():
        artifact_bytes = (tmp_path / artifact["basename"]).read_bytes()
        assert artifact["byte_length"] == len(artifact_bytes)
        assert artifact["sha256"] == hashlib.sha256(artifact_bytes).hexdigest()
    assert write_order == [
        "annotated_diff_92.txt",
        "hunk_ranges_92.json",
        "valid_lines_92.json",
        "metrics_92.json",
    ]


@pytest.mark.parametrize(
    "failure_name",
    ["annotated_diff_94.txt", "hunk_ranges_94.json", "valid_lines_94.json"],
)
@patch("subprocess.run")
def test_annotate_pr_diff_never_publishes_manifest_after_sidecar_failure(
    mock_run,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_name: str,
) -> None:
    import autoskillit.core as core

    mock_run.side_effect = _annotation_run_side_effect(_churn_diff(additions=2, removals=1))
    original_atomic_write = core.atomic_write
    write_order: list[str] = []

    def failing_atomic_write(path: Path, content: str) -> None:
        write_order.append(path.name)
        if path.name == failure_name:
            raise OSError("injected sidecar write failure")
        original_atomic_write(path, content)

    monkeypatch.setattr(core, "atomic_write", failing_atomic_write)
    with pytest.raises(OSError, match="injected sidecar write failure"):
        annotate_pr_diff(
            pr_number="94",
            cwd=str(tmp_path),
            output_dir=str(tmp_path),
            base_branch="main",
        )

    assert "metrics_94.json" not in write_order
    assert not (tmp_path / "metrics_94.json").exists()


@patch("subprocess.run")
def test_annotate_pr_diff_preserves_stderr_bytes_when_diff_fails(mock_run, tmp_path: Path) -> None:
    metrics_path = tmp_path / "metrics_93.json"
    metrics_path.write_text('{"generation_id":"stale"}')

    def fail_diff(args, **_kwargs):
        if args[:2] == ["gh", "api"]:
            payload = json.dumps({"headRefOid": _SHA, "baseRefOid": _BASE_SHA})
            return subprocess.CompletedProcess(args, 0, payload.encode(), b"")
        if args[:3] == ["gh", "pr", "diff"]:
            return subprocess.CompletedProcess(args, 1, b"", b"diff failed: \xff")
        raise AssertionError(f"unexpected annotation command: {args}")

    mock_run.side_effect = fail_diff
    with pytest.raises(RuntimeError, match="annotation command failed") as exc_info:
        annotate_pr_diff(pr_number="93", cwd=str(tmp_path), output_dir=str(tmp_path))
    assert "diff failed: \\xff" in str(exc_info.value)
    assert not metrics_path.exists()


@patch("subprocess.run")
def test_annotate_pr_diff_failed_ref_lookup_publishes_no_authority(
    mock_run, tmp_path: Path
) -> None:
    metrics_path = tmp_path / "metrics_96.json"
    metrics_path.write_text('{"generation_id":"stale"}')

    def fail_ref_lookup(args, **_kwargs):
        if args[:2] == ["gh", "api"]:
            return subprocess.CompletedProcess(args, 1, b"", b"ref lookup failed")
        raise AssertionError(f"unexpected annotation command: {args}")

    mock_run.side_effect = fail_ref_lookup
    with pytest.raises(RuntimeError, match="unable to resolve live PR head/base refs"):
        annotate_pr_diff(pr_number="96", cwd=str(tmp_path), output_dir=str(tmp_path))
    assert not metrics_path.exists()


@patch("subprocess.run")
def test_annotation_marker_protocol_detects_overlapping_publication(
    mock_run, tmp_path: Path
) -> None:
    first_diff = _churn_diff(additions=2, removals=1)
    mock_run.side_effect = _annotation_run_side_effect(first_diff)
    annotate_pr_diff(pr_number="97", cwd=str(tmp_path), output_dir=str(tmp_path))

    marker_path = tmp_path / "metrics_97.json"
    marker_retained = threading.Event()
    publisher_finished = threading.Event()
    consumer_result: dict[str, bool] = {}

    def consume_generation(*, overlap: bool) -> bool:
        retained_marker_bytes = marker_path.read_bytes()
        retained_marker = json.loads(retained_marker_bytes)
        if overlap:
            marker_retained.set()
            assert publisher_finished.wait(timeout=10)
        sidecars_match = all(
            artifact["byte_length"] == len((tmp_path / artifact["basename"]).read_bytes())
            and artifact["sha256"]
            == hashlib.sha256((tmp_path / artifact["basename"]).read_bytes()).hexdigest()
            for artifact in retained_marker["artifacts"].values()
        )
        return sidecars_match and retained_marker_bytes == marker_path.read_bytes()

    def overlapping_consumer() -> None:
        consumer_result["accepted"] = consume_generation(overlap=True)

    consumer = threading.Thread(target=overlapping_consumer)
    consumer.start()
    assert marker_retained.wait(timeout=10)

    second_diff = _churn_diff(additions=3, removals=2)
    mock_run.side_effect = _annotation_run_side_effect(second_diff)
    annotate_pr_diff(pr_number="97", cwd=str(tmp_path), output_dir=str(tmp_path))
    publisher_finished.set()
    consumer.join(timeout=10)

    assert not consumer.is_alive()
    assert consumer_result == {"accepted": False}
    assert consume_generation(overlap=False)


@patch("subprocess.run")
def test_annotate_pr_diff_rejects_moving_github_refs(mock_run, tmp_path: Path) -> None:
    ref_reads = 0

    def moving_refs(args, **_kwargs):
        nonlocal ref_reads
        if args[:2] == ["gh", "api"]:
            ref_reads += 1
            head = _SHA if ref_reads == 1 else "d" * 40
            payload = json.dumps({"headRefOid": head, "baseRefOid": _BASE_SHA})
            return subprocess.CompletedProcess(args, 0, payload.encode(), b"")
        if args[:3] == ["gh", "pr", "diff"]:
            return subprocess.CompletedProcess(args, 0, _DIFF_OUTPUT.encode(), b"")
        raise AssertionError(f"unexpected annotation command: {args}")

    mock_run.side_effect = moving_refs
    with pytest.raises(RuntimeError, match="moved during diff acquisition"):
        annotate_pr_diff(pr_number="94", cwd=str(tmp_path), output_dir=str(tmp_path))
    assert not (tmp_path / "metrics_94.json").exists()


@patch("subprocess.run")
def test_annotate_pr_diff_rejects_local_base_disagreement(mock_run, tmp_path: Path) -> None:
    mock_run.side_effect = _annotation_run_side_effect(live_base_sha="d" * 40)
    with pytest.raises(RuntimeError, match="local base ref does not match"):
        annotate_pr_diff(
            pr_number="95",
            cwd=str(tmp_path),
            output_dir=str(tmp_path),
            local_review_rounds="1",
            current_iteration="0",
            base_branch="main",
        )
    assert not (tmp_path / "metrics_95.json").exists()


# ─── SHA embedding tests (T_SHA_1–T_SHA_4) ──────────────────────────────────


@patch("subprocess.run")
def test_annotate_pr_diff_embeds_head_sha_in_metrics(mock_run, tmp_path: Path) -> None:
    """T_SHA_1: metrics_{pr}.json must include _head_sha field."""
    mock_run.side_effect = _annotation_run_side_effect()
    annotate_pr_diff(pr_number="999", cwd=str(tmp_path), output_dir=str(tmp_path))
    metrics = json.loads((tmp_path / "metrics_999.json").read_text())
    assert metrics["_head_sha"] == _SHA


@patch("subprocess.run")
def test_annotate_pr_diff_embeds_sha_header_in_diff_text(mock_run, tmp_path: Path) -> None:
    """T_SHA_2: annotated_diff_{pr}.txt first line must be # sha: {sha}."""
    mock_run.side_effect = _annotation_run_side_effect()
    annotate_pr_diff(pr_number="999", cwd=str(tmp_path), output_dir=str(tmp_path))
    first_line = (tmp_path / "annotated_diff_999.txt").read_text().split("\n")[0]
    assert first_line == f"# sha: {_SHA}"


@patch("subprocess.run")
def test_annotate_pr_diff_returns_head_sha(mock_run, tmp_path: Path) -> None:
    """T_SHA_3: Return dict must include head_sha for downstream capture."""
    mock_run.side_effect = _annotation_run_side_effect()
    result = annotate_pr_diff(pr_number="999", cwd=str(tmp_path), output_dir=str(tmp_path))
    assert result["pr_head_sha"] == _SHA
    assert re.fullmatch(r"[0-9a-f]{40}", result["pr_head_sha"])


@patch("subprocess.run")
def test_annotate_pr_diff_valid_lines_flat_schema(mock_run, tmp_path: Path) -> None:
    """T_SHA_4: valid_lines_{pr}.json must be a flat {filepath: [lines]} dict, not wrapped."""
    mock_run.side_effect = _annotation_run_side_effect()
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


def test_diagnose_merge_gate_dirty_tree_step(tmp_path: object) -> None:
    """When failed_step is dirty_tree, subtype must be dirty_tree not unknown."""
    from pathlib import Path

    from autoskillit.smoke_utils._merge_gate_diagnosis import diagnose_merge_gate

    output_dir = tmp_path  # type: ignore[union-attr]
    result = diagnose_merge_gate(
        test_stdout="",
        test_stderr="",
        output_dir=str(output_dir),
        failed_step="dirty_tree",
    )
    content = Path(result["diagnosis_path"]).read_text()
    assert "failure_subtype = dirty_tree" in content
    assert "failure_type = pre_test" in content


def test_diagnose_merge_gate_test_gate_empty_output(tmp_path: object) -> None:
    """test_gate with empty output means collection failed, not unknown."""
    from pathlib import Path

    from autoskillit.smoke_utils._merge_gate_diagnosis import diagnose_merge_gate

    output_dir = tmp_path  # type: ignore[union-attr]
    result = diagnose_merge_gate(
        test_stdout="",
        test_stderr="",
        output_dir=str(output_dir),
        failed_step="test_gate",
    )
    content = Path(result["diagnosis_path"]).read_text()
    assert "failure_subtype = no_test_output" in content
    assert "failure_type = test" in content


def test_diagnose_merge_gate_test_gate_with_output(tmp_path: object) -> None:
    """test_gate with FAILED lines still classifies as deterministic."""
    from pathlib import Path

    from autoskillit.smoke_utils._merge_gate_diagnosis import diagnose_merge_gate

    output_dir = tmp_path  # type: ignore[union-attr]
    result = diagnose_merge_gate(
        test_stdout="FAILED tests/test_x.py::test_y",
        test_stderr="",
        output_dir=str(output_dir),
        failed_step="test_gate",
    )
    content = Path(result["diagnosis_path"]).read_text()
    assert "failure_subtype = deterministic" in content
    assert "failure_type = test" in content


def test_diagnose_merge_gate_handles_empty_output(tmp_path: object) -> None:
    """callable with empty/absent test output returns graceful fallback."""
    from pathlib import Path

    from autoskillit.smoke_utils._merge_gate_diagnosis import diagnose_merge_gate

    output_dir = tmp_path  # type: ignore[union-attr]
    result = diagnose_merge_gate(test_stdout="", test_stderr="", output_dir=str(output_dir))
    diag_path = Path(result["diagnosis_path"])
    assert diag_path.exists()
    content = diag_path.read_text()
    assert "failure_subtype = no_test_output" in content
    assert "failure_type = test" in content


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
        "check_diff_size",
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
        "check_diff_size",
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

    assert init_counter(counter_value="") == {"value": "0"}


def test_init_counter_with_whitespace_only() -> None:
    """init_counter returns value='0' when counter_value is whitespace."""

    assert init_counter(counter_value="  ") == {"value": "0"}


def test_init_counter_with_numeric_value() -> None:
    """init_counter passes through a numeric string unchanged."""

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
# T_SRD1–T_SRD6: select_review_dimensions callable
# ---------------------------------------------------------------------------


def test_select_review_dimensions_happy_path(tmp_path: Path) -> None:
    """Registry-grounded benchmark type returns 8 non-S lenses in H→M→L order."""
    from autoskillit.recipe import get_experiment_type_by_name
    from autoskillit.smoke_utils import select_review_dimensions

    spec = get_experiment_type_by_name("benchmark")
    assert spec is not None
    expected_dims = {d for d, w in spec.dimension_weights.items() if w != "S"}

    result = select_review_dimensions(
        experiment_type="benchmark",
        output_dir=str(tmp_path),
    )
    lenses = result["selected_lenses"].split(",")
    assert len(lenses) == len(expected_dims)
    assert set(lenses) == expected_dims
    manifest = json.loads(Path(result["dimensions_manifest_path"]).read_text())
    tiers = list(manifest.values())
    expected_order = sorted(tiers, key=lambda t: {"H": 0, "M": 1, "L": 2}.get(t, 3))
    assert tiers == expected_order


def test_select_review_dimensions_creates_output_dir(tmp_path: Path) -> None:
    """Benchmark experiment type creates missing output_dir and writes manifest."""
    from autoskillit.smoke_utils import select_review_dimensions

    out = tmp_path / "nested" / "output"
    assert not out.exists()
    result = select_review_dimensions(
        experiment_type="benchmark",
        output_dir=str(out),
    )
    assert out.exists()
    assert Path(result["dimensions_manifest_path"]).exists()


def test_select_review_dimensions_qualitative_type_returns_active_dims(
    tmp_path: Path,
) -> None:
    """Qualitative-interpretive type returns only its 2 active L-tier dimensions."""
    from autoskillit.recipe import get_experiment_type_by_name
    from autoskillit.smoke_utils import select_review_dimensions

    spec = get_experiment_type_by_name("qualitative_interpretive")
    assert spec is not None
    expected_dims = {d for d, w in spec.dimension_weights.items() if w != "S"}

    result = select_review_dimensions(
        experiment_type="qualitative_interpretive",
        output_dir=str(tmp_path),
    )
    lenses = result["selected_lenses"].split(",")
    assert len(lenses) == len(expected_dims)
    assert set(lenses) == expected_dims
    assert "data_acquisition" in lenses
    assert "agent_implementability" in lenses
    assert "causal_structure" not in lenses
    assert Path(result["dimensions_manifest_path"]).exists()


def test_select_review_dimensions_registry_happy_path(tmp_path: Path) -> None:
    """Registry lookup returns non-empty lenses for a known experiment type."""
    from autoskillit.smoke_utils import select_review_dimensions

    result = select_review_dimensions(
        experiment_type="causal_inference",
        output_dir=str(tmp_path),
    )
    assert result["selected_lenses"] != ""
    assert result["dimensions_manifest_path"] != ""
    manifest_path = Path(result["dimensions_manifest_path"])
    assert manifest_path.is_absolute()
    assert manifest_path.exists()


def test_select_review_dimensions_unknown_type_returns_empty(tmp_path: Path) -> None:
    """Unknown experiment type returns _EMPTY and writes no files."""
    from autoskillit.smoke_utils import select_review_dimensions

    result = select_review_dimensions(
        experiment_type="nonexistent_type",
        output_dir=str(tmp_path),
    )
    assert result == {
        "selected_lenses": "",
        "lens_context_paths": "",
        "dimensions_manifest_path": "",
    }
    assert not list(tmp_path.iterdir())


def test_select_review_dimensions_empty_type_returns_empty(tmp_path: Path) -> None:
    """Empty experiment_type returns _EMPTY and writes no files."""
    from autoskillit.smoke_utils import select_review_dimensions

    result = select_review_dimensions(
        experiment_type="",
        output_dir=str(tmp_path),
    )
    assert result == {
        "selected_lenses": "",
        "lens_context_paths": "",
        "dimensions_manifest_path": "",
    }
    assert not list(tmp_path.iterdir())


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
    """STOP verdict on estimand_clarity critical with fixability=None."""
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
    assert "evaluation_dashboard_path" in result
    assert Path(result["evaluation_dashboard_path"]).exists()


def test_aggregate_review_verdict_estimand_clarity_addressable_is_revise(tmp_path: Path) -> None:
    """estimand_clarity critical with fixability=ADDRESSABLE → REVISE, not STOP."""
    from autoskillit.smoke_utils import aggregate_review_verdict

    findings = [
        {
            "dimension": "estimand_clarity",
            "severity": "critical",
            "message": "ambiguous but addressable",
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


def test_structural_fixability_values_matches_skill_md_pseudocode() -> None:
    """_STRUCTURAL_FIXABILITY_VALUES must be referenced by name in SKILL.md pseudocode."""
    from autoskillit.core import pkg_root

    skill_md = (pkg_root() / "skills_extended" / "review-design" / "SKILL.md").read_text()
    step7_start = skill_md.find("### Step 7")
    assert step7_start != -1, "SKILL.md must contain '### Step 7' heading"
    step7_end = skill_md.find("### Step 8")
    assert step7_end != -1, "SKILL.md must contain '### Step 8' heading"
    step7_text = skill_md[step7_start:step7_end]

    match = re.search(
        r"structural_stop_triggers\s*=\s*\[(.+?)\n\s*\]",
        step7_text,
        re.DOTALL,
    )
    assert match, "Step 7 must contain structural_stop_triggers list comprehension"
    comprehension_body = match.group(1)

    assert "_STRUCTURAL_FIXABILITY_VALUES" in comprehension_body, (
        "structural_stop_triggers must reference _STRUCTURAL_FIXABILITY_VALUES by name — "
        "do not inline the fixability values as separate OR clauses"
    )

    assert "f.dimension ==" not in comprehension_body, (
        "structural_stop_triggers must not use dimension-only matching — "
        "this was the original bug (issue #3092)"
    )
    assert 'f.get("dimension")' not in comprehension_body, (
        "structural_stop_triggers must not use f.get('dimension') matching — "
        "use fixability-based gating only"
    )


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
    dashboard = Path(result["evaluation_dashboard_path"]).read_text()
    assert "warning_count: 1" in dashboard
    assert "critical_count: 0" in dashboard


def test_aggregate_review_verdict_empty_path_returns_go(tmp_path: Path) -> None:
    """Empty findings_manifest_path (silent type path) returns GO with no findings."""
    from autoskillit.smoke_utils import aggregate_review_verdict

    result = aggregate_review_verdict(
        findings_manifest_path="",
        output_dir=str(tmp_path / "out"),
    )
    assert result.get("verdict") == "GO"
    assert "error" not in result


def test_aggregate_review_verdict_missing_file_returns_error(tmp_path: Path) -> None:
    """Non-existent findings_manifest_path returns error key."""
    from autoskillit.smoke_utils import aggregate_review_verdict

    result = aggregate_review_verdict(
        findings_manifest_path=str(tmp_path / "nonexistent.json"),
        output_dir=str(tmp_path / "out"),
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


# ---------------------------------------------------------------------------
# detect_zero_changes: multi-signal change detection
# ---------------------------------------------------------------------------

_DZC_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@test.com",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@test.com",
}


def test_detect_zero_changes_uncommitted_files(tmp_path: Path) -> None:
    """detect_zero_changes returns has_changes=true for uncommitted files."""
    from autoskillit.smoke_utils import detect_zero_changes

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )
    (tmp_path / "new_file.txt").write_text("content")
    result = detect_zero_changes(str(tmp_path), "HEAD")
    assert result["has_changes"] == "true"
    assert result["has_uncommitted_changes"] == "true"


def test_detect_zero_changes_override_does_not_skip_git_on_clean_tree(tmp_path: Path) -> None:
    """write_evidence_override=true must not short-circuit git verification.

    On a clean repo, override=true forces has_changes=true via OR-combination,
    but the git signals (commit_count, has_uncommitted_changes) must STILL be
    populated — override is an OR-condition, not a bypass.
    """
    from autoskillit.smoke_utils import detect_zero_changes

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )
    result = detect_zero_changes(str(tmp_path), "HEAD", write_evidence_override="True")
    assert result["has_changes"] == "true"
    assert result["write_evidence_override"] == "true"
    assert result["commit_count"] == "0"
    assert result["has_uncommitted_changes"] == "false"


def test_detect_zero_changes_override_false_with_commits(tmp_path: Path) -> None:
    """write_evidence_override=false reports commits ahead via git."""
    from autoskillit.smoke_utils import detect_zero_changes

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )
    base_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "second"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "third"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )
    result = detect_zero_changes(str(tmp_path), base_commit, write_evidence_override="false")
    assert result["has_changes"] == "true"
    assert result["commit_count"] == "2"


def test_detect_zero_changes_override_true_with_commits(tmp_path: Path) -> None:
    """Override and commit signals agree without skipping git."""
    from autoskillit.smoke_utils import detect_zero_changes

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )
    base_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "second"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )
    result = detect_zero_changes(str(tmp_path), base_commit, write_evidence_override="True")
    assert result["has_changes"] == "true"
    assert result["write_evidence_override"] == "true"
    assert result["commit_count"] == "1"


def test_detect_zero_changes_git_error_fallback(tmp_path: Path) -> None:
    """detect_zero_changes returns has_changes=true on git subprocess errors."""
    from autoskillit.smoke_utils import detect_zero_changes

    result = detect_zero_changes(str(tmp_path), "HEAD", write_evidence_override="false")
    assert result["has_changes"] == "true"
    assert "error" in result
    assert result["commit_count"] == "error"
    assert result["has_uncommitted_changes"] == "error"


def test_detect_zero_changes_clean_repo(tmp_path: Path) -> None:
    """detect_zero_changes returns has_changes=false for a clean repo with no commits ahead."""
    from autoskillit.smoke_utils import detect_zero_changes

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )
    result = detect_zero_changes(str(tmp_path), "HEAD")
    assert result["has_changes"] == "false"
    assert result["commit_count"] == "0"
    assert result["has_uncommitted_changes"] == "false"


# ---------------------------------------------------------------------------
# T_SU_CRS1–T_SU_CRS3: check_ref_state tests (issue #4274, Part B Step 7)
# ---------------------------------------------------------------------------


def test_check_ref_state_local_ahead_returns_true(tmp_path: Path) -> None:
    """Local branch ahead of remote tracking ref returns remote_is_ancestor=true.

    Constructs a repo with one initial commit (simulating remote), then
    advances a local ``feature`` branch by one commit (local ahead).
    ``check_ref_state`` must detect that origin/feature is an ancestor of
    feature and return ``{"remote_is_ancestor": "true"}``.

    Issue #4274 Part B: this is the benign-exhaustion case — local work is
    audit-approved and trivially push-recoverable; the recipe must route to
    ``register_clone_unconfirmed`` instead of escalating to ``fail``.
    """
    from autoskillit.smoke_utils import check_ref_state

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )
    # Capture the base tip via HEAD (not by name) — the initial branch name
    # created by ``git init`` depends on ``init.defaultBranch``, which is not
    # guaranteed to be ``main`` in every environment (e.g. CI runners).
    base_tip = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "checkout", "-b", "feature"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "feature work"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )
    # Simulate a remote tracking ref pointing at the original commit.
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/feature", base_tip],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )

    result = check_ref_state(str(tmp_path), "feature")
    assert result == {"remote_is_ancestor": "true"}


def test_check_ref_state_genuine_divergence_returns_false(tmp_path: Path) -> None:
    """Genuine local/remote divergence returns remote_is_ancestor=false.

    Constructs a repo where ``feature`` and the simulated remote tracking
    ref have both advanced independently from the same base — true
    divergence. ``check_ref_state`` must NOT report ancestor relationship.
    """
    from autoskillit.smoke_utils import check_ref_state

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )
    base_tip = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()

    subprocess.run(
        ["git", "checkout", "-b", "feature"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "local advance"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )

    # Simulate remote having advanced from the same base as a different branch.
    subprocess.run(
        ["git", "checkout", base_tip],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )
    subprocess.run(
        ["git", "checkout", "-b", "remote_tip"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "remote advance"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )
    divergent_remote = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/feature", divergent_remote],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )

    result = check_ref_state(str(tmp_path), "feature")
    assert result == {"remote_is_ancestor": "false"}


def test_check_ref_state_missing_branch_returns_false(tmp_path: Path) -> None:
    """Missing local branch returns remote_is_ancestor=false (no ancestry to test)."""
    from autoskillit.smoke_utils import check_ref_state

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    result = check_ref_state(str(tmp_path), "nonexistent")
    assert result == {"remote_is_ancestor": "false"}


# ---------------------------------------------------------------------------
# T_CRP1–T_CRP25: check_review_posted
# ---------------------------------------------------------------------------

_REVIEW_REPOSITORY = "openai/autoskillit"
_REVIEW_PR_NUMBER = 42
_REVIEW_HEAD_SHA = "0123456789abcdef0123456789abcdef01234567"
_REVIEW_LOGICAL_ITERATION = "review-pr:2"
_REVIEW_OPERATION_KEY = "f" * 64


def test_check_review_posted_has_keyword_only_identity_signature() -> None:
    import inspect

    signature = inspect.signature(check_review_posted)
    assert list(signature.parameters) == [
        "cwd",
        "receipt_path",
        "mode",
        "repository",
        "pr_number",
        "head_sha",
        "logical_iteration",
        "operation_key",
        "post_state",
    ]
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in signature.parameters.values()
    )


def _review_receipt_payload(
    *,
    state: str = "SUCCEEDED",
    reconciliation_result: str = "NOT_NEEDED",
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "operation_key": _REVIEW_OPERATION_KEY,
        "repository": _REVIEW_REPOSITORY,
        "pr_number": _REVIEW_PR_NUMBER,
        "head_sha": _REVIEW_HEAD_SHA,
        "logical_iteration": _REVIEW_LOGICAL_ITERATION,
        "state": state,
        "review_id": 9001,
        "comment_ids": [101, 102],
        "canonical_finding_count": 3,
        "finding_dispositions": [
            {"original_index": 0, "kind": "POSTED", "remote_comment_id": 101},
            {"original_index": 1, "kind": "ALREADY_PRESENT", "remote_comment_id": 102},
            {
                "original_index": 2,
                "kind": "OMITTED_INVALID",
                "reason": "line is outside the live diff",
            },
        ],
        "reconciliation_result": reconciliation_result,
        "dry_run": False,
    }


def _review_receipt_path(cwd: Path, pr_number: int = _REVIEW_PR_NUMBER) -> Path:
    path = (
        cwd
        / ".autoskillit"
        / "temp"
        / "review-pr"
        / _REVIEW_LOGICAL_ITERATION
        / f"batch_review_response_{pr_number}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _invoke_review_receipt_check(
    *,
    cwd: Path,
    receipt_path: Path,
    mode: str = "github",
    repository: str = _REVIEW_REPOSITORY,
    pr_number: int = _REVIEW_PR_NUMBER,
    head_sha: str = _REVIEW_HEAD_SHA,
    logical_iteration: str = _REVIEW_LOGICAL_ITERATION,
    operation_key: str = _REVIEW_OPERATION_KEY,
    post_state: str = "SUCCEEDED",
) -> dict[str, str]:
    return check_review_posted(
        cwd=str(cwd.resolve()),
        receipt_path=str(receipt_path),
        mode=mode,
        repository=repository,
        pr_number=pr_number,
        head_sha=head_sha,
        logical_iteration=logical_iteration,
        operation_key=operation_key,
        post_state=post_state,
    )


@pytest.mark.parametrize("state", ["SUCCEEDED", "RECONCILED"])
@pytest.mark.parametrize("reconciliation_result", ["NOT_NEEDED", "MATCHED", "ENRICHED"])
def test_check_review_posted_accepts_only_valid_final_receipt(
    tmp_path: Path,
    state: str,
    reconciliation_result: str,
) -> None:
    """A contained, identity-matched, exhaustively accounted final receipt succeeds."""
    cwd = tmp_path / "repo"
    receipt = _review_receipt_path(cwd)
    receipt.write_text(
        json.dumps(
            _review_receipt_payload(
                state=state,
                reconciliation_result=reconciliation_result,
            )
        )
    )

    result = _invoke_review_receipt_check(
        cwd=cwd,
        receipt_path=receipt,
        post_state=state,
    )

    assert result["reviews_posted"] == "true"
    assert result.get("sentinel", "") == ""


def test_check_review_posted_missing_receipt_returns_false(tmp_path: Path) -> None:
    """GitHub mode fails closed when the captured receipt does not exist."""
    cwd = tmp_path / "repo"
    receipt = _review_receipt_path(cwd)

    result = _invoke_review_receipt_check(cwd=cwd, receipt_path=receipt)

    assert result["reviews_posted"] == "false"


def test_check_review_posted_local_mode_succeeds_without_reading_receipt(
    tmp_path: Path,
) -> None:
    """Local review mode has no publication receipt and remains an explicit success."""
    cwd = tmp_path / "repo"
    missing_receipt = _review_receipt_path(cwd)

    result = _invoke_review_receipt_check(
        cwd=cwd,
        receipt_path=missing_receipt,
        mode="local",
        repository="not/a-canonical identity",
        head_sha="",
        logical_iteration="",
        operation_key="",
        post_state="",
    )

    assert result["reviews_posted"] == "true"


@pytest.mark.parametrize("mode", ["", "dry-run", "GITHUB", "unknown"])
def test_check_review_posted_unknown_mode_fails(tmp_path: Path, mode: str) -> None:
    cwd = tmp_path / "repo"
    receipt = _review_receipt_path(cwd)
    receipt.write_text(json.dumps(_review_receipt_payload()))

    result = _invoke_review_receipt_check(cwd=cwd, receipt_path=receipt, mode=mode)

    assert result["reviews_posted"] == "false"


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("repository", "someone/else"),
        ("pr_number", 43),
        ("head_sha", "1" * 40),
        ("logical_iteration", "review-pr:3"),
        ("operation_key", "different-operation"),
        ("state", "RECONCILED"),
    ],
)
def test_check_review_posted_rejects_receipt_identity_mismatch(
    tmp_path: Path,
    field: str,
    bad_value: object,
) -> None:
    cwd = tmp_path / "repo"
    receipt = _review_receipt_path(cwd)
    payload = _review_receipt_payload()
    payload[field] = bad_value
    receipt.write_text(json.dumps(payload))

    result = _invoke_review_receipt_check(cwd=cwd, receipt_path=receipt)

    assert result["reviews_posted"] == "false"


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("repository", "OpenAI/autoskillit"),
        ("head_sha", "ABCDEF0123456789ABCDEF0123456789ABCDEF01"),
        ("head_sha", "abc123"),
        ("logical_iteration", "2"),
        ("operation_key", ""),
        ("operation_key", "review-v1:approved"),
    ],
)
def test_check_review_posted_rejects_noncanonical_requested_identity(
    tmp_path: Path,
    field: str,
    bad_value: object,
) -> None:
    cwd = tmp_path / "repo"
    receipt = _review_receipt_path(cwd)
    receipt.write_text(json.dumps(_review_receipt_payload()))
    assert isinstance(bad_value, str)
    requested_identity = {
        "repository": _REVIEW_REPOSITORY,
        "head_sha": _REVIEW_HEAD_SHA,
        "logical_iteration": _REVIEW_LOGICAL_ITERATION,
        "operation_key": _REVIEW_OPERATION_KEY,
    }
    requested_identity[field] = bad_value

    result = _invoke_review_receipt_check(
        cwd=cwd,
        receipt_path=receipt,
        repository=requested_identity["repository"],
        head_sha=requested_identity["head_sha"],
        logical_iteration=requested_identity["logical_iteration"],
        operation_key=requested_identity["operation_key"],
    )

    assert result["reviews_posted"] == "false"


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("schema_version", 2),
        ("schema_version", "1"),
        ("state", "PENDING"),
        ("state", "FAILED"),
        ("state", "DRY_RUN"),
        ("reconciliation_result", "UNKNOWN"),
        ("dry_run", True),
        ("review_id", 0),
        ("review_id", -1),
        ("review_id", "9001"),
        ("comment_ids", [101, 101]),
        ("comment_ids", [101, 0]),
        ("comment_ids", [101, "102"]),
        ("canonical_finding_count", -1),
        ("canonical_finding_count", "3"),
    ],
)
def test_check_review_posted_rejects_invalid_schema_or_nonfinal_state(
    tmp_path: Path,
    field: str,
    bad_value: object,
) -> None:
    cwd = tmp_path / "repo"
    receipt = _review_receipt_path(cwd)
    payload = _review_receipt_payload()
    payload[field] = bad_value
    receipt.write_text(json.dumps(payload))

    result = _invoke_review_receipt_check(cwd=cwd, receipt_path=receipt)

    assert result["reviews_posted"] == "false"


@pytest.mark.parametrize(
    "missing_field",
    [
        "schema_version",
        "operation_key",
        "repository",
        "pr_number",
        "head_sha",
        "logical_iteration",
        "state",
        "review_id",
        "comment_ids",
        "canonical_finding_count",
        "finding_dispositions",
        "reconciliation_result",
    ],
)
def test_check_review_posted_requires_complete_receipt_schema(
    tmp_path: Path,
    missing_field: str,
) -> None:
    cwd = tmp_path / "repo"
    receipt = _review_receipt_path(cwd)
    payload = _review_receipt_payload()
    del payload[missing_field]
    receipt.write_text(json.dumps(payload))

    result = _invoke_review_receipt_check(cwd=cwd, receipt_path=receipt)

    assert result["reviews_posted"] == "false"


@pytest.mark.parametrize(
    "dispositions",
    [
        [
            {"original_index": 0, "kind": "POSTED", "remote_comment_id": 101},
            {"original_index": 0, "kind": "ALREADY_PRESENT", "remote_comment_id": 102},
            {"original_index": 2, "kind": "OMITTED_INVALID", "reason": "invalid"},
        ],
        [
            {"original_index": 0, "kind": "POSTED", "remote_comment_id": 101},
            {"original_index": 2, "kind": "ALREADY_PRESENT", "remote_comment_id": 102},
        ],
        [
            {"original_index": 0, "kind": "POSTED", "remote_comment_id": 101},
            {"original_index": 1, "kind": "UNKNOWN", "remote_comment_id": 102},
            {"original_index": 2, "kind": "OMITTED_INVALID", "reason": "invalid"},
        ],
        [
            {"original_index": 0, "kind": "POSTED"},
            {"original_index": 1, "kind": "ALREADY_PRESENT", "remote_comment_id": 102},
            {"original_index": 2, "kind": "OMITTED_INVALID", "reason": "invalid"},
        ],
        [
            {"original_index": 0, "kind": "POSTED", "remote_comment_id": 0},
            {"original_index": 1, "kind": "ALREADY_PRESENT", "remote_comment_id": 102},
            {"original_index": 2, "kind": "OMITTED_INVALID", "reason": "invalid"},
        ],
        [
            {"original_index": 0, "kind": "POSTED", "remote_comment_id": 101},
            {"original_index": 1, "kind": "ALREADY_PRESENT", "remote_comment_id": 101},
            {"original_index": 2, "kind": "OMITTED_INVALID", "reason": "invalid"},
        ],
        [
            {"original_index": 0, "kind": "POSTED", "remote_comment_id": 101},
            {"original_index": 1, "kind": "ALREADY_PRESENT", "remote_comment_id": 102},
            {"original_index": 2, "kind": "OMITTED_INVALID", "reason": ""},
        ],
        [
            {"original_index": 0, "kind": "POSTED", "remote_comment_id": 101},
            {"original_index": 1, "kind": "ALREADY_PRESENT", "remote_comment_id": 102},
            {
                "original_index": 2,
                "kind": "OMITTED_INVALID",
                "remote_comment_id": 103,
                "reason": "invalid",
            },
        ],
    ],
)
def test_check_review_posted_requires_exhaustive_unique_disposition_partition(
    tmp_path: Path,
    dispositions: list[dict[str, object]],
) -> None:
    cwd = tmp_path / "repo"
    receipt = _review_receipt_path(cwd)
    payload = _review_receipt_payload()
    payload["finding_dispositions"] = dispositions
    receipt.write_text(json.dumps(payload))

    result = _invoke_review_receipt_check(cwd=cwd, receipt_path=receipt)

    assert result["reviews_posted"] == "false"


def test_check_review_posted_rejects_disposition_count_mismatch(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    receipt = _review_receipt_path(cwd)
    payload = _review_receipt_payload()
    payload["canonical_finding_count"] = 4
    receipt.write_text(json.dumps(payload))

    result = _invoke_review_receipt_check(cwd=cwd, receipt_path=receipt)

    assert result["reviews_posted"] == "false"


@pytest.mark.parametrize(
    "raw_content",
    [
        "{not valid JSON",
        "[]",
        "null",
        '"string"',
    ],
)
def test_check_review_posted_rejects_malformed_or_nonobject_json(
    tmp_path: Path,
    raw_content: str,
) -> None:
    cwd = tmp_path / "repo"
    receipt = _review_receipt_path(cwd)
    receipt.write_text(raw_content)

    result = _invoke_review_receipt_check(cwd=cwd, receipt_path=receipt)

    assert result["reviews_posted"] == "false"


def test_check_review_posted_rejects_oversized_receipt(tmp_path: Path) -> None:
    """A syntactically valid receipt over the bounded parser limit is rejected."""
    cwd = tmp_path / "repo"
    receipt = _review_receipt_path(cwd)
    receipt.write_text(json.dumps(_review_receipt_payload()) + (" " * 1_048_577))

    result = _invoke_review_receipt_check(cwd=cwd, receipt_path=receipt)

    assert result["reviews_posted"] == "false"


def test_check_review_posted_rejects_path_outside_managed_temp(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    receipt = cwd / "outside" / f"batch_review_response_{_REVIEW_PR_NUMBER}.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps(_review_receipt_payload()))

    result = _invoke_review_receipt_check(cwd=cwd, receipt_path=receipt)

    assert result["reviews_posted"] == "false"


def test_check_review_posted_rejects_traversal_path(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    managed_temp = cwd / ".autoskillit" / "temp"
    managed_temp.mkdir(parents=True)
    outside = cwd / "outside" / f"batch_review_response_{_REVIEW_PR_NUMBER}.json"
    outside.parent.mkdir()
    outside.write_text(json.dumps(_review_receipt_payload()))
    traversal = managed_temp / ".." / ".." / "outside" / outside.name

    result = _invoke_review_receipt_check(cwd=cwd, receipt_path=traversal)

    assert result["reviews_posted"] == "false"


def test_check_review_posted_rejects_relative_receipt_path(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    receipt = _review_receipt_path(cwd)
    receipt.write_text(json.dumps(_review_receipt_payload()))
    relative = receipt.relative_to(cwd)

    result = _invoke_review_receipt_check(cwd=cwd, receipt_path=relative)

    assert result["reviews_posted"] == "false"


@pytest.mark.parametrize(
    "basename",
    [
        "receipt.json",
        "batch_review_response_41.json",
        "batch_review_response_42.json.bak",
    ],
)
def test_check_review_posted_requires_exact_receipt_basename(
    tmp_path: Path,
    basename: str,
) -> None:
    cwd = tmp_path / "repo"
    receipt = _review_receipt_path(cwd).with_name(basename)
    receipt.write_text(json.dumps(_review_receipt_payload()))

    result = _invoke_review_receipt_check(cwd=cwd, receipt_path=receipt)

    assert result["reviews_posted"] == "false"


def test_check_review_posted_rejects_symlink_receipt(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    receipt = _review_receipt_path(cwd)
    target = receipt.with_name("target.json")
    target.write_text(json.dumps(_review_receipt_payload()))
    receipt.symlink_to(target)

    result = _invoke_review_receipt_check(cwd=cwd, receipt_path=receipt)

    assert result["reviews_posted"] == "false"


def test_check_review_posted_rejects_symlinked_parent_escape(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    managed_temp = cwd / ".autoskillit" / "temp"
    managed_temp.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    receipt = outside / f"batch_review_response_{_REVIEW_PR_NUMBER}.json"
    receipt.write_text(json.dumps(_review_receipt_payload()))
    linked_namespace = managed_temp / "review-pr"
    linked_namespace.symlink_to(outside, target_is_directory=True)

    result = _invoke_review_receipt_check(
        cwd=cwd,
        receipt_path=linked_namespace / receipt.name,
    )

    assert result["reviews_posted"] == "false"


def test_check_review_posted_rejects_hardlinked_receipt(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    receipt = _review_receipt_path(cwd)
    source = receipt.with_name("source.json")
    source.write_text(json.dumps(_review_receipt_payload()))
    os.link(source, receipt)

    result = _invoke_review_receipt_check(cwd=cwd, receipt_path=receipt)

    assert result["reviews_posted"] == "false"


def test_check_review_posted_has_no_subprocess_calls(tmp_path):
    """check_review_posted must not invoke any subprocess."""
    cwd = tmp_path / "repo"
    receipt = _review_receipt_path(cwd)
    with patch.object(
        subprocess, "run", side_effect=AssertionError("unexpected subprocess")
    ) as mock_run:
        _invoke_review_receipt_check(cwd=cwd, receipt_path=receipt)
    mock_run.assert_not_called()


def test_check_review_posted_in_smoke_utils_all():
    """check_review_posted must be exported from smoke_utils.__all__."""
    import autoskillit.smoke_utils as sm

    assert "check_review_posted" in sm.__all__
    assert callable(sm.check_review_posted)


# ---------------------------------------------------------------------------
# T_EI1–T_EI8: extract_investigation
# ---------------------------------------------------------------------------


_ISSUE_BODY_WITH_INVESTIGATION = (
    "Some preamble not in the investigation section.\n"
    "\n"
    "## Investigation\n"
    "<!-- investigation_complete: true -->\n"
    "> Prior investigation completed interactively. See below for root cause analysis.\n"
    "\n"
    "# Investigation: Topic\n"
    "## Summary\n"
    "Summary content here.\n"
    "## Root Cause\n"
    "Root cause content here.\n"
    "## Evidence\n"
    "Evidence content here.\n"
    "## Recommendations\n"
    "Recommendation content here.\n"
)


@patch("autoskillit.smoke_utils._investigation.run_gh")
def test_extract_investigation_full_content(mock_run_gh, tmp_path: Path) -> None:
    """Extraction must retain all ## subsections inside ## Investigation."""

    mock_run_gh.return_value = subprocess.CompletedProcess(
        [], 0, _ISSUE_BODY_WITH_INVESTIGATION, ""
    )
    out_dir = tmp_path / "investigate"
    result = extract_investigation(
        investigation_path="",
        issue_number="42",
        output_dir=str(out_dir),
    )
    assert result["investigation_report"] == str(out_dir / "investigation_from_issue.md")
    written = Path(result["investigation_report"]).read_text()
    assert "## Summary" in written
    assert "## Root Cause" in written
    assert "## Evidence" in written
    assert "## Recommendations" in written
    assert "Summary content here." in written
    assert "Root cause content here." in written
    assert "Evidence content here." in written
    assert "Recommendation content here." in written


def test_extract_investigation_passthrough(tmp_path: Path) -> None:
    """When investigation_path is set, file exists, and content is complete, return it."""

    report = tmp_path / "investigation_full.md"
    report.write_text(
        "# Investigation: Topic\n"
        "## Summary\n"
        "Summary content.\n"
        "## Recommendations\n"
        "Recommendations content.\n"
    )
    result = extract_investigation(
        investigation_path=str(report),
        issue_number="42",
        output_dir=str(tmp_path / "unused"),
    )
    assert result["investigation_report"] == str(report)


def test_extract_investigation_passthrough_truncated_raises(tmp_path: Path) -> None:
    """When investigation_path points to a truncated file (no ## subsections), callable raises."""

    truncated = tmp_path / "investigation_truncated.md"
    truncated.write_text(
        "<!-- investigation_complete: true -->\n"
        "> Prior investigation completed interactively. See below for root cause analysis.\n"
    )
    with pytest.raises(ValueError, match="no '## ' subsections"):
        extract_investigation(
            investigation_path=str(truncated),
            issue_number="42",
            output_dir=str(tmp_path / "unused"),
        )


@patch("autoskillit.smoke_utils._investigation.run_gh")
def test_extract_investigation_no_section_raises(mock_run_gh, tmp_path: Path) -> None:
    """When issue body has no ## Investigation section, callable raises."""

    mock_run_gh.return_value = subprocess.CompletedProcess(
        [], 0, "Body without the investigation section.\n## Other\n", ""
    )
    with pytest.raises(ValueError, match="## Investigation"):
        extract_investigation(
            investigation_path="",
            issue_number="42",
            output_dir=str(tmp_path),
        )


@patch("autoskillit.smoke_utils._investigation.run_gh")
def test_extract_investigation_empty_body_raises(mock_run_gh, tmp_path: Path) -> None:
    """When neither the section nor the body carries any ## subsection, callable raises."""

    mock_run_gh.return_value = subprocess.CompletedProcess(
        [], 0, "Preamble with no structure.\n## Investigation\n\n", ""
    )
    with pytest.raises(ValueError, match="no investigation to hand to rectify"):
        extract_investigation(
            investigation_path="",
            issue_number="42",
            output_dir=str(tmp_path),
        )


@patch("autoskillit.smoke_utils._investigation.run_gh")
def test_extract_investigation_attestation_section_falls_back_to_body(
    mock_run_gh, tmp_path: Path
) -> None:
    """An attestation-style section hands rectify the whole body, not a three-line note.

    Regression test for #4392. A sizeable minority of issues use ``## Investigation`` to
    record *that* an investigation happened, with the analysis written above the heading.
    Requiring a ``## Recommendations`` heading rejected 16 of 34 such issues and — because
    bridge_investigation now halts on failure — stopped the pipeline outright.
    """

    body = (
        "## Problem\n"
        "The real analysis lives up here, above the attestation.\n"
        "## Root cause\n"
        "Detailed root cause content.\n"
        "\n"
        "## Investigation\n"
        "<!-- investigation_complete: true -->\n"
        "> Prior investigation completed interactively; analysis included above.\n"
    )
    mock_run_gh.return_value = subprocess.CompletedProcess([], 0, body, "")
    out_dir = tmp_path / "investigate"
    result = extract_investigation(
        investigation_path="",
        issue_number="42",
        output_dir=str(out_dir),
    )
    written = Path(result["investigation_report"]).read_text()
    # The whole body is handed over, so the analysis above the heading survives.
    assert "The real analysis lives up here" in written
    assert "Detailed root cause content." in written
    assert "## Problem" in written


@patch("autoskillit.smoke_utils._investigation.run_gh")
def test_extract_investigation_gh_failure_raises(mock_run_gh, tmp_path: Path) -> None:
    """When gh issue view fails, callable raises ValueError."""

    mock_run_gh.return_value = subprocess.CompletedProcess([], 1, "", "gh: not authenticated")
    with pytest.raises(ValueError, match="gh issue view failed"):
        extract_investigation(
            investigation_path="",
            issue_number="42",
            output_dir=str(tmp_path),
        )


@patch("autoskillit.smoke_utils._investigation.run_gh")
def test_extract_investigation_ignores_h3_investigation_decoy(mock_run_gh, tmp_path: Path) -> None:
    """A decoy '### Investigation' subsection must not be mistaken for the real heading."""

    body = (
        "Preamble.\n"
        "### Investigation\n"
        "Decoy sub-subsection text, not the real heading.\n"
        "\n"
        "## Investigation\n"
        "## Summary\n"
        "Summary content.\n"
        "## Recommendations\n"
        "Recommendation content.\n"
    )
    mock_run_gh.return_value = subprocess.CompletedProcess([], 0, body, "")
    out_dir = tmp_path / "investigate"
    result = extract_investigation(
        investigation_path="",
        issue_number="42",
        output_dir=str(out_dir),
    )
    written = Path(result["investigation_report"]).read_text()
    assert "Decoy sub-subsection text" not in written
    assert "## Summary" in written
    assert "Summary content." in written


def test_extract_investigation_passthrough_rejects_h3_subsection_decoy(
    tmp_path: Path,
) -> None:
    """A '### ' heading is not a '## ' subsection and must not satisfy the check."""

    truncated = tmp_path / "investigation_truncated.md"
    truncated.write_text(
        "<!-- investigation_complete: true -->\n"
        "> Prior investigation completed interactively.\n"
        "### Recommendations for future work (not a real section)\n"
    )
    with pytest.raises(ValueError, match="no '## ' subsections"):
        extract_investigation(
            investigation_path=str(truncated),
            issue_number="42",
            output_dir=str(tmp_path / "unused"),
        )


def test_extract_investigation_accepts_report_without_recommendations(
    tmp_path: Path,
) -> None:
    """Completeness must not be proxied on one heading name (#4392).

    A structured report using a different terminal section is complete. The prior
    revision rejected this shape, which is what halted 16 of 34 real investigations.
    """

    report = tmp_path / "investigation_no_recs.md"
    report.write_text(
        "# Investigation: Topic\n"
        "## Summary\n"
        "Summary content.\n"
        "## Root Cause\n"
        "Root cause content.\n"
        "## Scope Boundary\n"
        "Scope content — no Recommendations heading anywhere.\n"
    )
    result = extract_investigation(
        investigation_path=str(report),
        issue_number="42",
        output_dir=str(tmp_path / "unused"),
    )
    assert result["investigation_report"] == str(report)


# ---------------------------------------------------------------------------
# check_diff_size: deterministic diff-size gate
# ---------------------------------------------------------------------------


def _init_diff_size_repo(tmp_path: Path, *, branch: str | None = "feature") -> None:
    """Create the common baseline repository used by diff-size gate tests."""
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )
    if branch is not None:
        subprocess.run(
            ["git", "checkout", "-b", branch],
            cwd=tmp_path,
            capture_output=True,
            check=True,
            env=_DZC_GIT_ENV,
        )


def test_check_diff_size_within_budget(tmp_path: Path) -> None:
    """A small diff under the default budget reports within_budget with no split proposal."""
    _init_diff_size_repo(tmp_path)
    small_file = tmp_path / "small.py"
    small_file.write_text("\n".join(f"line {i}" for i in range(10)) + "\n")
    subprocess.run(["git", "add", "small.py"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "add small file"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )

    result = check_diff_size(str(tmp_path), "main")

    assert result["size_verdict"] == "within_budget"
    assert result["added_lines"] == "10"
    assert result["split_proposal_path"] == ""


def test_check_diff_size_over_budget_added_lines(tmp_path: Path) -> None:
    """A diff exceeding the line budget reports over_budget and writes a breach report."""
    _init_diff_size_repo(tmp_path)
    big_file = tmp_path / "big.py"
    big_file.write_text("\n".join(f"line {i}" for i in range(10)) + "\n")
    subprocess.run(["git", "add", "big.py"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "add big file"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )
    output_dir = tmp_path / "output"

    result = check_diff_size(str(tmp_path), "main", default_budget="5", output_dir=str(output_dir))

    assert result["size_verdict"] == "over_budget"
    assert result["split_proposal_path"] != ""
    assert Path(result["split_proposal_path"]).exists()


def test_check_diff_size_uncommitted_changes(tmp_path: Path) -> None:
    """Unstaged working-tree modifications are captured in the diff-size measurement."""
    _init_diff_size_repo(tmp_path)
    tracked = tmp_path / "tracked.py"
    tracked.write_text("original\n")
    subprocess.run(["git", "add", "tracked.py"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "add tracked"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )
    # Unstaged modification — never staged, never committed.
    tracked.write_text("original\nmodified line\n")

    result = check_diff_size(str(tmp_path), "main")

    assert result["added_lines"] == "2"


def test_check_diff_size_untracked_files(tmp_path: Path) -> None:
    """Untracked files are supplemented into the added_lines and changed_files counts."""
    _init_diff_size_repo(tmp_path)
    untracked = tmp_path / "untracked.py"
    untracked.write_text("\n".join(f"line {i}" for i in range(20)) + "\n")

    result = check_diff_size(str(tmp_path), "main")

    assert result["added_lines"] == "20"
    assert result["changed_files"] == "1"


def test_check_diff_size_streams_untracked_files(tmp_path: Path, monkeypatch) -> None:
    """Untracked files are counted without the unbounded Path.read_bytes API."""
    _init_diff_size_repo(tmp_path)
    (tmp_path / "untracked.py").write_text("line\n" * 20_000)

    def fail_read_bytes(_path: Path) -> bytes:
        raise AssertionError("check_diff_size must stream untracked files")

    monkeypatch.setattr(Path, "read_bytes", fail_read_bytes)

    result = check_diff_size(str(tmp_path), "main", default_budget="1000000")

    assert result["added_lines"] == "20000"


def test_check_diff_size_untracked_read_failure_returns_error(tmp_path: Path) -> None:
    """An unreadable untracked entry cannot silently produce a partial measurement."""
    _init_diff_size_repo(tmp_path)
    (tmp_path / "broken.py").symlink_to(tmp_path / "missing-target.py")

    result = check_diff_size(str(tmp_path), "main")

    assert result["size_verdict"] == "error"
    assert result["added_lines"] == "unknown"
    assert result["changed_files"] == "unknown"


def test_check_diff_size_per_part_isolation(tmp_path: Path) -> None:
    """Only the current part's own diff since merge_target is measured, not inherited history."""
    _init_diff_size_repo(tmp_path, branch=None)
    large_file = tmp_path / "large.py"
    large_file.write_text("\n".join(f"line {i}" for i in range(500)) + "\n")
    subprocess.run(["git", "add", "large.py"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "large commit"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )
    subprocess.run(
        ["git", "checkout", "-b", "merge_target"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )
    subprocess.run(
        ["git", "checkout", "-b", "work"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )
    small_file = tmp_path / "small.py"
    small_file.write_text("\n".join(f"line {i}" for i in range(5)) + "\n")
    subprocess.run(["git", "add", "small.py"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "small change"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )

    result = check_diff_size(str(tmp_path), "merge_target")

    assert result["added_lines"] == "5"
    assert result["changed_files"] == "1"


def test_check_diff_size_excludes_test_source_map(tmp_path: Path) -> None:
    """The test-source-map.json artifact is excluded from the diff-size measurement."""
    _init_diff_size_repo(tmp_path)
    autoskillit_dir = tmp_path / ".autoskillit"
    autoskillit_dir.mkdir()
    (autoskillit_dir / "test-source-map.json").write_text('{"mapped": "data"}\n' * 20)

    result = check_diff_size(str(tmp_path), "main")

    assert result["added_lines"] == "0"
    assert result["changed_files"] == "0"


def test_check_diff_size_plan_budget_overrides_default(tmp_path: Path) -> None:
    """A plan-declared size_budget line overrides the default_budget ingredient."""
    _init_diff_size_repo(tmp_path)
    plan_path = tmp_path / "plan.md"
    plan_path.write_text("# Plan\n\nsize_budget = 800\n\nDetails...\n")
    (tmp_path / "budgeted.py").write_text("line\n" * 1_000)

    result = check_diff_size(str(tmp_path), "main", plan_path=str(plan_path))

    assert result["budget"] == "800"
    assert result["budget_source"] == "plan"
    assert result["size_verdict"] == "over_budget"


def test_check_diff_size_malformed_plan_budget_falls_back(tmp_path: Path) -> None:
    """Malformed size_budget (thousands separator) fails the digit regex."""
    _init_diff_size_repo(tmp_path)
    plan_path = tmp_path / "plan.md"
    plan_path.write_text("# Plan\n\nsize_budget = 4,800\n\nDetails...\n")
    (tmp_path / "budgeted.py").write_text("line\n" * 5_000)

    result = check_diff_size(str(tmp_path), "main", plan_path=str(plan_path))

    assert result["budget_source"] == "ingredient"
    assert result["budget"] == "6000"
    assert result["size_verdict"] == "within_budget"


def test_check_diff_size_malformed_default_budget_returns_error(tmp_path: Path) -> None:
    """A non-numeric default budget produces the structured fail-open verdict."""
    _init_diff_size_repo(tmp_path)

    result = check_diff_size(str(tmp_path), "main", default_budget="not-a-number")

    assert result["size_verdict"] == "error"
    assert result["added_lines"] == "unknown"
    assert result["changed_files"] == "unknown"
    assert result["budget"] == "not-a-number"
    assert result["budget_source"] == "ingredient"
    assert result["split_proposal_path"] == ""


def test_check_diff_size_git_diff_failure_returns_error(tmp_path: Path, monkeypatch) -> None:
    """A failed git diff cannot be interpreted as a zero-line measurement."""
    _init_diff_size_repo(tmp_path)

    def fake_run(args, **_kwargs):
        if args[1] == "merge-base":
            return subprocess.CompletedProcess(args, 0, stdout="abc123\n", stderr="")
        return subprocess.CompletedProcess(args, 1, stdout=b"", stderr=b"diff failed")

    monkeypatch.setattr("autoskillit.smoke_utils._git.subprocess.run", fake_run)

    result = check_diff_size(str(tmp_path), "main")

    assert result["size_verdict"] == "error"
    assert result["added_lines"] == "unknown"


def test_check_diff_size_merge_base_failure_is_logged(tmp_path: Path) -> None:
    """Merge-base failures retain a bounded operator-visible diagnostic."""
    _init_diff_size_repo(tmp_path)

    with patch("autoskillit.smoke_utils._git.logger.warning") as warning:
        result = check_diff_size(str(tmp_path), "missing-base-ref")

    assert result["size_verdict"] == "error"
    assert warning.call_count == 1
    assert warning.call_args.args[0].startswith("check_diff_size merge-base failed")


def test_check_diff_size_plan_read_failure_returns_error(tmp_path: Path) -> None:
    """An unreadable plan cannot silently substitute the ingredient budget."""
    _init_diff_size_repo(tmp_path)
    plan_path = tmp_path / "plan-directory"
    plan_path.mkdir()

    result = check_diff_size(str(tmp_path), "main", plan_path=str(plan_path))

    assert result["size_verdict"] == "error"
    assert result["budget_source"] == "ingredient"


def test_check_diff_size_requires_worktree_path() -> None:
    """The runtime signature reflects the required worktree-path contract."""
    with pytest.raises(TypeError):
        check_diff_size()  # type: ignore[call-arg]


def test_diff_size_gate_constants_are_not_public() -> None:
    """Implementation limits are not part of the smoke_utils facade."""
    import autoskillit.smoke_utils as smoke_utils

    assert not hasattr(smoke_utils, "DIFF_SIZE_GATE_EXCLUDED_PATHSPECS")
    assert not hasattr(smoke_utils, "DIFF_SIZE_GATE_MAX_CHANGED_FILES")


def test_check_diff_size_changed_files_bound(tmp_path: Path) -> None:
    """Exceeding the changed-files limit alone triggers over_budget under a large line budget."""
    _init_diff_size_repo(tmp_path)
    for i in range(161):
        (tmp_path / f"file_{i}.py").write_text(f"line {i}\n")

    result = check_diff_size(str(tmp_path), "main", default_budget="1000000")

    assert result["size_verdict"] == "over_budget"


def test_check_diff_size_all_values_are_strings(tmp_path: Path) -> None:
    """Every value in the returned dict is a string, per the smoke_utils string-typed contract."""
    _init_diff_size_repo(tmp_path)
    small_file = tmp_path / "small.py"
    small_file.write_text("line 1\n")
    subprocess.run(["git", "add", "small.py"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "add small file"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        env=_DZC_GIT_ENV,
    )

    result = check_diff_size(str(tmp_path), "main")

    assert all(isinstance(v, str) for v in result.values())
