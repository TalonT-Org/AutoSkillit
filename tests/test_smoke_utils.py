"""Tests for smoke_utils — check_bug_report_non_empty and check_review_loop callables."""

from __future__ import annotations

import json
from pathlib import Path

from autoskillit.smoke_utils import check_bug_report_non_empty, check_review_loop


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
    r1 = check_review_loop("1", "/tmp", current_iteration="")
    assert r1["next_iteration"] == "1"

    r2 = check_review_loop("1", "/tmp", current_iteration="1")
    assert r2["next_iteration"] == "2"

    r3 = check_review_loop("1", "/tmp", current_iteration="2")
    assert r3["next_iteration"] == "3"


# T_CRL7
def test_crl_max_exceeded_when_next_iteration_ge_max() -> None:
    """max_exceeded=true when next_iteration >= max_iterations."""
    result = check_review_loop("1", "/tmp", current_iteration="2", max_iterations="3")
    assert result["max_exceeded"] == "true"
    assert result["next_iteration"] == "3"


# T_CRL8
def test_crl_max_not_exceeded_when_below_max() -> None:
    """max_exceeded=false when next_iteration < max_iterations."""
    result = check_review_loop("1", "/tmp", current_iteration="1", max_iterations="3")
    assert result["max_exceeded"] == "false"


def test_check_review_loop_always_continues_when_iterations_remain() -> None:
    """After a resolve cycle, check_review_loop must indicate continuation
    when max_iterations is not exceeded — regardless of GitHub thread state.

    The function is a pure iteration guard: if next_iteration < max_iterations,
    it must return max_exceeded=false so the recipe routes back to review_pr.
    """
    result = check_review_loop(
        pr_number="42",
        cwd="/tmp",
        current_iteration="0",
        max_iterations="3",
    )
    assert result["max_exceeded"] == "false"
    assert result["next_iteration"] == "1"


def test_check_review_loop_stops_at_max_iterations() -> None:
    """When current_iteration reaches max_iterations, max_exceeded must be true."""
    result = check_review_loop(
        pr_number="42",
        cwd="/tmp",
        current_iteration="2",
        max_iterations="3",
    )
    assert result["max_exceeded"] == "true"
    assert result["next_iteration"] == "3"


def test_check_review_loop_returns_expected_fields() -> None:
    """check_review_loop must return next_iteration, max_exceeded, and had_blocking."""
    result = check_review_loop(pr_number="42", cwd="/tmp")
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
    result = check_review_loop("42", "/tmp", previous_verdict="changes_requested")
    assert result["had_blocking"] == "true"


# T_CRL13
def test_crl_had_blocking_false_when_approved_with_comments() -> None:
    """had_blocking=false when previous_verdict is approved_with_comments."""
    result = check_review_loop("42", "/tmp", previous_verdict="approved_with_comments")
    assert result["had_blocking"] == "false"


# T_CRL14
def test_crl_had_blocking_false_when_empty_verdict() -> None:
    """had_blocking=false when previous_verdict is absent (first-pass guard)."""
    result = check_review_loop("42", "/tmp")
    assert result["had_blocking"] == "false"


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
