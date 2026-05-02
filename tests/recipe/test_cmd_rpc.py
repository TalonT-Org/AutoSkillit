"""Tests for recipe._cmd_rpc — externalized run_python callables."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from autoskillit.recipe._cmd_rpc import (
    check_dropped_healthy_loop,
    check_eject_limit,
    commit_guard,
    compute_branch,
    emit_fallback_map,
    ensure_results,
    export_local_bundle,
    refetch_issues,
)

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]


def test_compute_branch_with_issue():
    result = compute_branch(issue_slug="feat-widget", run_name="run1", issue_number="42")
    assert result == {"branch_name": "feat-widget/42"}


def test_compute_branch_without_issue():
    result = compute_branch(issue_slug="feat-widget", run_name="run1", issue_number="")
    assert "feat-widget/" in result["branch_name"]


def test_compute_branch_uses_run_name_when_no_slug():
    result = compute_branch(issue_slug="", run_name="my-run", issue_number="7")
    assert result == {"branch_name": "my-run/7"}


def test_check_eject_limit_under_threshold(tmp_path):
    counter = tmp_path / "count"
    result = check_eject_limit(counter_file=str(counter), max_ejects="3")
    assert result["status"] == "EJECT_OK"
    assert result["count"] == "1"


def test_check_eject_limit_exceeded(tmp_path):
    counter = tmp_path / "count"
    counter.write_text("3")
    result = check_eject_limit(counter_file=str(counter), max_ejects="3")
    assert result["status"] == "EJECT_LIMIT_EXCEEDED"
    assert result["count"] == "4"


def test_check_eject_limit_creates_parent_dirs(tmp_path):
    counter = tmp_path / "nested" / "dir" / "count"
    result = check_eject_limit(counter_file=str(counter), max_ejects="5")
    assert result["status"] == "EJECT_OK"
    assert counter.exists()


def test_check_dropped_healthy_loop_under(tmp_path):
    counter = tmp_path / "dropped"
    result = check_dropped_healthy_loop(counter_file=str(counter), max_drops="2")
    assert result["status"] == "DROPPED_OK"
    assert result["count"] == "1"


def test_check_dropped_healthy_loop_exceeded(tmp_path):
    counter = tmp_path / "dropped"
    counter.write_text("2")
    result = check_dropped_healthy_loop(counter_file=str(counter), max_drops="2")
    assert result["status"] == "DROPPED_LIMIT_EXCEEDED"
    assert result["count"] == "3"


def _init_git_repo(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.local"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )


def test_commit_guard_clean_tree(tmp_path):
    _init_git_repo(tmp_path)
    result = commit_guard(worktree_path=str(tmp_path))
    assert result["committed"] == "false"


def test_commit_guard_dirty_tree(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "newfile.txt").write_text("content")
    result = commit_guard(worktree_path=str(tmp_path))
    assert result["committed"] == "true"


def test_ensure_results_with_existing_path():
    result = ensure_results(experiment_results="/some/path.md", worktree_path="/tmp")
    assert result == {"experiment_results": "/some/path.md"}


def test_ensure_results_creates_placeholder(tmp_path):
    result = ensure_results(
        experiment_results="",
        worktree_path=str(tmp_path),
        temp_subdir=".autoskillit/temp",
    )
    path = Path(result["experiment_results"])
    assert path.exists()
    assert "INCONCLUSIVE" in path.read_text()


def test_emit_fallback_map(tmp_path):
    result = emit_fallback_map(
        issue_urls="https://github.com/org/repo/issues/1,https://github.com/org/repo/issues/2",
        temp_dir=str(tmp_path),
    )
    assert "execution_map" in result
    data = json.loads(Path(result["execution_map"]).read_text())
    assert data["merge_order"] == [1, 2]
    assert len(data["groups"]) == 1


def test_emit_fallback_map_no_urls(tmp_path):
    with pytest.raises(RuntimeError, match="no issue numbers"):
        emit_fallback_map(issue_urls="", temp_dir=str(tmp_path))


def test_export_local_bundle(tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    research_dir = tmp_path / "2024-01-01-test"
    research_dir.mkdir()
    (research_dir / "report.md").write_text("# Report")
    result = export_local_bundle(source_dir=str(source_dir), research_dir=str(research_dir))
    assert Path(result["local_bundle_path"]).exists()
    assert (Path(result["local_bundle_path"]) / "report.md").read_text() == "# Report"


def test_refetch_issues_builds_query():
    with patch("autoskillit.recipe._cmd_rpc.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="1 2", stderr=""
        )
        result = refetch_issues(
            issue_urls="https://github.com/org/repo/issues/1,https://github.com/org/repo/issues/2"
        )
    assert result["issue_numbers"] == "1 2"
    call_args = mock_run.call_args[0][0]
    assert "gh" in call_args
    assert "graphql" in call_args
    query_arg = next(a for a in call_args if a.startswith("query="))
    assert "org" in query_arg
    assert "repo" in query_arg
    assert "issue(number: 1)" in query_arg
    assert "issue(number: 2)" in query_arg
