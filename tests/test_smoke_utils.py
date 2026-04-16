"""Tests for smoke_utils — check_bug_report_non_empty and check_review_loop callables."""

from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from autoskillit.smoke_utils import check_bug_report_non_empty, check_review_loop


def _make_graphql_response(nodes: list[dict]) -> str:
    """Build a minimal single-page GraphQL reviewThreads response."""
    return json.dumps(
        {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": nodes,
                        }
                    }
                }
            }
        }
    )


def _mock_run_factory(nodes: list[dict]):
    """Return a subprocess.run mock that serves repo-view then graphql responses."""

    def _mock_run(cmd, **kwargs):
        result = MagicMock(spec=subprocess.CompletedProcess)
        result.returncode = 0
        if "nameWithOwner" in " ".join(cmd):
            result.stdout = "owner/repo\n"
        else:
            result.stdout = _make_graphql_response(nodes)
        result.stderr = ""
        return result

    return _mock_run


def _thread(is_resolved: bool, body: str) -> dict:
    return {
        "isResolved": is_resolved,
        "line": 10,
        "originalLine": 10,
        "comments": {"nodes": [{"body": body}]},
    }


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
# T_CRL1–T_CRL11: check_review_loop tests
# ---------------------------------------------------------------------------


# T_CRL1
def test_crl_no_unresolved_threads_returns_not_blocking(tmp_path: Path) -> None:
    """Returns has_blocking=false and next_iteration=1 when no unresolved threads exist."""
    with patch("subprocess.run", side_effect=_mock_run_factory([])):
        result = check_review_loop("42", str(tmp_path))
    assert result["has_blocking"] == "false"
    assert result["next_iteration"] == "1"


# T_CRL2
def test_crl_unresolved_critical_is_blocking(tmp_path: Path) -> None:
    """Returns has_blocking=true when at least one unresolved thread body contains [critical]."""
    nodes = [_thread(False, "[critical] arch: missing abstraction")]
    with patch("subprocess.run", side_effect=_mock_run_factory(nodes)):
        result = check_review_loop("42", str(tmp_path))
    assert result["has_blocking"] == "true"
    assert result["blocking_count"] == "1"


# T_CRL3
def test_crl_unresolved_warning_is_blocking(tmp_path: Path) -> None:
    """Returns has_blocking=true when at least one unresolved thread body contains [warning]."""
    nodes = [_thread(False, "[warning] tests: weak assertion")]
    with patch("subprocess.run", side_effect=_mock_run_factory(nodes)):
        result = check_review_loop("42", str(tmp_path))
    assert result["has_blocking"] == "true"


# T_CRL4
def test_crl_unresolved_info_not_blocking(tmp_path: Path) -> None:
    """Returns has_blocking=false for unresolved threads with neither [critical] nor [warning]."""
    nodes = [_thread(False, "[info] style: minor nit")]
    with patch("subprocess.run", side_effect=_mock_run_factory(nodes)):
        result = check_review_loop("42", str(tmp_path))
    assert result["has_blocking"] == "false"


# T_CRL5
def test_crl_resolved_critical_not_blocking(tmp_path: Path) -> None:
    """Returns has_blocking=false for resolved threads even if body contains [critical]."""
    nodes = [_thread(True, "[critical] already fixed")]
    with patch("subprocess.run", side_effect=_mock_run_factory(nodes)):
        result = check_review_loop("42", str(tmp_path))
    assert result["has_blocking"] == "false"


# T_CRL6
def test_crl_next_iteration_increments(tmp_path: Path) -> None:
    """next_iteration increments from current_iteration: "" → "1", "1" → "2", "2" → "3"."""
    nodes: list[dict] = []
    with patch("subprocess.run", side_effect=_mock_run_factory(nodes)):
        r1 = check_review_loop("1", str(tmp_path), current_iteration="")
    assert r1["next_iteration"] == "1"

    with patch("subprocess.run", side_effect=_mock_run_factory(nodes)):
        r2 = check_review_loop("1", str(tmp_path), current_iteration="1")
    assert r2["next_iteration"] == "2"

    with patch("subprocess.run", side_effect=_mock_run_factory(nodes)):
        r3 = check_review_loop("1", str(tmp_path), current_iteration="2")
    assert r3["next_iteration"] == "3"


# T_CRL7
def test_crl_max_exceeded_when_next_iteration_ge_max(tmp_path: Path) -> None:
    """max_exceeded=true when next_iteration >= max_iterations."""
    nodes = [_thread(False, "[critical] blocking")]
    with patch("subprocess.run", side_effect=_mock_run_factory(nodes)):
        result = check_review_loop("1", str(tmp_path), current_iteration="2", max_iterations="3")
    # next_iteration = 3, max_iterations = 3 → 3 >= 3 → max_exceeded=true
    assert result["max_exceeded"] == "true"
    assert result["next_iteration"] == "3"


# T_CRL8
def test_crl_max_not_exceeded_when_below_max(tmp_path: Path) -> None:
    """max_exceeded=false when next_iteration < max_iterations."""
    nodes = [_thread(False, "[critical] blocking")]
    with patch("subprocess.run", side_effect=_mock_run_factory(nodes)):
        result = check_review_loop("1", str(tmp_path), current_iteration="1", max_iterations="3")
    # next_iteration = 2, max_iterations = 3 → 2 < 3 → max_exceeded=false
    assert result["max_exceeded"] == "false"


# T_CRL9
def test_crl_graceful_degradation_on_subprocess_failure(tmp_path: Path) -> None:
    """Returns has_blocking=false and max_exceeded=false on gh CLI failure."""

    def _fail(*args, **kwargs):
        raise OSError("gh not found")

    with patch("subprocess.run", side_effect=_fail):
        result = check_review_loop("1", str(tmp_path))
    assert result["has_blocking"] == "false"
    assert result["max_exceeded"] == "false"


# T_CRL10
def test_crl_blocking_count_reflects_unresolved_severity_threads(tmp_path: Path) -> None:
    """blocking_count reflects number of unresolved critical/warning threads."""
    nodes = [
        _thread(False, "[critical] arch issue"),
        _thread(False, "[warning] test coverage"),
        _thread(False, "[info] nit"),
        _thread(True, "[critical] already resolved"),
    ]
    with patch("subprocess.run", side_effect=_mock_run_factory(nodes)):
        result = check_review_loop("42", str(tmp_path))
    assert result["blocking_count"] == "2"


# T_CRL11 — enforced by the existing AST test_subprocess_calls_have_timeout below


def test_subprocess_calls_have_timeout() -> None:
    """All subprocess.run() calls in smoke_utils.py must have a timeout= argument."""
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
