"""Tests for smoke_utils callables."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from autoskillit.smoke_utils import (
    annotate_pr_diff,
    check_bug_report_non_empty,
    check_loop_iteration,
    check_review_loop,
    enrich_diff_context,
    patch_pr_token_summary,
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
    """check_review_loop must return next_iteration, max_exceeded, and had_blocking."""
    result = check_review_loop(pr_number="42")
    assert set(result.keys()) == {"next_iteration", "max_exceeded", "had_blocking"}


# T_CRL11 — verify check_review_loop has no subprocess calls
def test_check_review_loop_has_no_subprocess_calls() -> None:
    """The simplified check_review_loop must not use subprocess at all."""
    import ast

    src = Path("src/autoskillit/smoke_utils.py").read_text()
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


def test_subprocess_calls_have_timeout() -> None:
    """All subprocess.run() calls in smoke_utils.py must have a timeout= argument."""
    import ast

    src = Path("src/autoskillit/smoke_utils.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"
        ):
            kw_names = {kw.arg for kw in node.keywords}
            assert "timeout" in kw_names, (
                f"subprocess.run() at line {node.lineno} in smoke_utils.py missing timeout="
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
            "step_name": entry.get("step_name", "unknown"),
            "input_tokens": entry.get("input_tokens", 1000),
            "output_tokens": entry.get("output_tokens", 500),
            "cache_creation_input_tokens": entry.get("cache_creation_input_tokens", 100),
            "cache_read_input_tokens": entry.get("cache_read_input_tokens", 200),
            "timing_seconds": entry.get("timing_seconds", 10.0),
            "order_id": entry.get("order_id", ""),
            "loc_insertions": entry.get("loc_insertions", 0),
            "loc_deletions": entry.get("loc_deletions", 0),
        }
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
    result = enrich_diff_context(pr_number="123", work_dir=str(tmp_path))
    assert result["enriched"] == "true"
    assert result["enriched_count"] == "1"

    handoff_path = tmp_path / ".autoskillit" / "temp" / "review-pr" / "diff_context_123.json"
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
    result = enrich_diff_context(pr_number="123", work_dir=str(tmp_path))
    assert result["enriched"] == "true"
    assert result["enriched_count"] == "1"

    handoff_path = tmp_path / ".autoskillit" / "temp" / "review-pr" / "diff_context_123.json"
    handoff = json.loads(handoff_path.read_text())
    assert handoff["context_entries"][0]["code_region"] == "pre-existing"
    assert "[L40]" in handoff["context_entries"][1]["code_region"]


# T_EDC3
def test_enrich_diff_context_missing_handoff_file(tmp_path: Path) -> None:
    """enrich_diff_context returns gracefully when handoff file does not exist."""
    result = enrich_diff_context(pr_number="999", work_dir=str(tmp_path))
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
    args = mock_run.call_args[0][0]
    assert args[:3] == ["git", "diff", "main...HEAD"]
    mock_run.assert_called_once()


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
    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
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
    args = mock_run.call_args[0][0]
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
