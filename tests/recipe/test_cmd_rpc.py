"""Tests for recipe._cmd_rpc — externalized run_python callables."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from autoskillit.recipe._cmd_rpc import (
    batch_create_issues,
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


# ─── batch_create_issues tests ─────────────────────────────────────────────


def _make_side_effect(repo_id="R_123", label_ids=None, issue_data=None):
    if label_ids is None:
        label_ids = ["L_1", "L_2"]
    if issue_data is None:
        issue_data = [
            {"number": 1, "url": "https://github.com/org/repo/issues/1"},
            {"number": 2, "url": "https://github.com/org/repo/issues/2"},
            {"number": 3, "url": "https://github.com/org/repo/issues/3"},
        ]
    alias_data = {}
    for idx, issue in enumerate(issue_data):
        alias_data[f"issue{idx}"] = {"issue": issue}
    return [
        # gh repo view
        subprocess.CompletedProcess(args=[], returncode=0, stdout="org repo\n", stderr=""),
        # gh api graphql (repo ID)
        subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({"data": {"repository": {"id": repo_id}}}),
            stderr="",
        ),
        # gh label create recipe:implementation
        subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        # gh label create enhancement
        subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        # gh api graphql (label IDs)
        subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(
                {
                    "data": {
                        "repository": {
                            "impl": {"id": label_ids[0]},
                            "enh": {"id": label_ids[1]},
                        }
                    }
                }
            ),
            stderr="",
        ),
        # gh api graphql (createIssue mutation)
        subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({"data": alias_data}),
            stderr="",
        ),
    ]


def test_batch_create_issues_discovers_ticket_bodies(tmp_path):
    va_dir = tmp_path / ".autoskillit" / "temp" / "validate-audit"
    va_dir.mkdir(parents=True)
    for n in range(1, 4):
        (va_dir / f"ticket_body_tests_{n}_2026-01-01_120000.md").write_text(
            f"validated: true\n\n# Title {n}\n\n| col1 | col2 |\n"
        )
    with (
        patch("autoskillit.recipe._cmd_rpc.subprocess.run") as mock_run,
        patch("autoskillit.recipe._cmd_rpc.time.sleep"),
    ):
        mock_run.side_effect = _make_side_effect()
        result = batch_create_issues(workspace=str(tmp_path))
    assert result["issue_count"] == "3"


def test_batch_create_issues_strips_body_content(tmp_path):
    va_dir = tmp_path / ".autoskillit" / "temp" / "validate-audit"
    va_dir.mkdir(parents=True)
    (va_dir / "ticket_body_tests_1_2026-01-01_120000.md").write_text(
        "validated: true\n\n# Audit: Missing test coverage\n\n"
        "<!-- .autoskillit/some/path -->\n\n"
        "| CONTESTED | finding |\n\n"
        "| VALID BUT EXCEPTION WARRANTED | also contested |\n\n"
        "| Item | **Contested:** 2 | **Exception warranted:** 1 |\n\n"
        "## Findings with Exceptions\n\n"
        "Some finding.\n\n---\n\n"
        "**Exception note:** this is an exception.\n"
    )
    with (
        patch("autoskillit.recipe._cmd_rpc.subprocess.run") as mock_run,
        patch("autoskillit.recipe._cmd_rpc.time.sleep"),
    ):
        mock_run.side_effect = _make_side_effect()
        batch_create_issues(workspace=str(tmp_path))
    # Find the createIssue mutation call
    mutation_call: dict[str, object] = {}
    for call in mock_run.call_args_list:
        args = call[0][0]
        if "--input" in args:
            mutation_call = json.loads(
                call[0][1]["input"] if "input" in call[0][1] else call[1].get("input", "{}")
            )
            break
    # The mutation call uses stdin via --input
    for call in mock_run.call_args_list:
        kwargs = call[1]
        if kwargs.get("input"):
            mutation_call = json.loads(kwargs["input"])
            break
    body = mutation_call["variables"]["i0"]["body"]
    assert ".autoskillit/" not in body
    assert "| CONTESTED |" not in body
    assert "| VALID BUT EXCEPTION WARRANTED |" not in body
    assert "**Exception note:**" not in body
    assert "Findings with Exceptions" not in body
    assert "**Contested:**" not in body


def test_batch_create_issues_extracts_h1_title(tmp_path):
    va_dir = tmp_path / ".autoskillit" / "temp" / "validate-audit"
    va_dir.mkdir(parents=True)
    (va_dir / "ticket_body_tests_1_2026-01-01_120000.md").write_text(
        "validated: true\n\n# Audit: Missing test coverage\n\nBody content."
    )
    with (
        patch("autoskillit.recipe._cmd_rpc.subprocess.run") as mock_run,
        patch("autoskillit.recipe._cmd_rpc.time.sleep"),
    ):
        mock_run.side_effect = _make_side_effect()
        batch_create_issues(workspace=str(tmp_path))
    for call in mock_run.call_args_list:
        kwargs = call[1]
        if kwargs.get("input"):
            mutation_call = json.loads(kwargs["input"])
            assert mutation_call["variables"]["i0"]["title"] == "Audit: Missing test coverage"
            break


def test_batch_create_issues_constructs_graphql_mutation(tmp_path):
    va_dir = tmp_path / ".autoskillit" / "temp" / "validate-audit"
    va_dir.mkdir(parents=True)
    (va_dir / "ticket_body_tests_1_2026-01-01_120000.md").write_text("# Issue One\n\nBody one.")
    (va_dir / "ticket_body_tests_2_2026-01-01_120000.md").write_text("# Issue Two\n\nBody two.")
    with (
        patch("autoskillit.recipe._cmd_rpc.subprocess.run") as mock_run,
        patch("autoskillit.recipe._cmd_rpc.time.sleep"),
    ):
        mock_run.side_effect = _make_side_effect()
        batch_create_issues(workspace=str(tmp_path))
    for call in mock_run.call_args_list:
        kwargs = call[1]
        if kwargs.get("input"):
            mutation_call = json.loads(kwargs["input"])
            query = mutation_call["query"]
            variables = mutation_call["variables"]
            assert "issue0: createIssue" in query
            assert "issue1: createIssue" in query
            assert variables["i0"]["repositoryId"] == "R_123"
            assert variables["i1"]["repositoryId"] == "R_123"
            assert variables["i0"]["labelIds"] == ["L_1", "L_2"]
            assert variables["i1"]["labelIds"] == ["L_1", "L_2"]
            break


def test_batch_create_issues_chunks_large_batches(tmp_path):
    va_dir = tmp_path / ".autoskillit" / "temp" / "validate-audit"
    va_dir.mkdir(parents=True)
    for n in range(25):
        (va_dir / f"ticket_body_tests_{n + 1}_2026-01-01_120000.md").write_text(
            f"# Issue {n + 1}\n\nBody {n + 1}."
        )
    with (
        patch("autoskillit.recipe._cmd_rpc.subprocess.run") as mock_run,
        patch("autoskillit.recipe._cmd_rpc.time.sleep"),
    ):

        def side_effect_factory():
            call_count = [0]

            def side_effect(_args, **_kwargs):
                c = call_count[0]
                call_count[0] += 1
                # repo view
                if c == 0:
                    return subprocess.CompletedProcess(
                        args=[], returncode=0, stdout="org repo\n", stderr=""
                    )
                # repo ID query
                if c == 1:
                    return subprocess.CompletedProcess(
                        args=[],
                        returncode=0,
                        stdout=json.dumps({"data": {"repository": {"id": "R_123"}}}),
                        stderr="",
                    )
                # label create
                if c in (2, 3):
                    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
                # label IDs query
                if c == 4:
                    return subprocess.CompletedProcess(
                        args=[],
                        returncode=0,
                        stdout=json.dumps(
                            {"data": {"repository": {"impl": {"id": "L_1"}, "enh": {"id": "L_2"}}}}
                        ),
                        stderr="",
                    )
                # createIssue chunks
                if c == 5:
                    data5 = {
                        f"issue{i}": {
                            "issue": {
                                "number": i + 1,
                                "url": f"https://github.com/org/repo/issues/{i + 1}",
                            }
                        }
                        for i in range(10)
                    }
                    return subprocess.CompletedProcess(
                        args=[], returncode=0, stdout=json.dumps({"data": data5}), stderr=""
                    )
                if c == 6:
                    data6 = {
                        f"issue{i}": {
                            "issue": {
                                "number": i + 11,
                                "url": f"https://github.com/org/repo/issues/{i + 11}",
                            }
                        }
                        for i in range(10)
                    }
                    return subprocess.CompletedProcess(
                        args=[], returncode=0, stdout=json.dumps({"data": data6}), stderr=""
                    )
                if c == 7:
                    data7 = {
                        f"issue{i}": {
                            "issue": {
                                "number": i + 21,
                                "url": f"https://github.com/org/repo/issues/{i + 21}",
                            }
                        }
                        for i in range(5)
                    }
                    return subprocess.CompletedProcess(
                        args=[], returncode=0, stdout=json.dumps({"data": data7}), stderr=""
                    )
                return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

            return side_effect

        mock_run.side_effect = side_effect_factory()
        batch_create_issues(workspace=str(tmp_path), chunk_size="10")
    mutation_calls = sum(
        1
        for call in mock_run.call_args_list
        if call[1].get("input") and "createIssue" in call[1]["input"]
    )
    assert mutation_calls == 3


def test_batch_create_issues_appends_validation_summary(tmp_path):
    va_dir = tmp_path / ".autoskillit" / "temp" / "validate-audit"
    va_dir.mkdir(parents=True)
    (va_dir / "ticket_body_tests_1_2026-01-01_120000.md").write_text(
        "# Audit Finding\n\nSome finding."
    )
    (va_dir / "validation_summary_tests_2026-01-01_120000.md").write_text(
        "## Validation Summary\nAll clear."
    )
    with (
        patch("autoskillit.recipe._cmd_rpc.subprocess.run") as mock_run,
        patch("autoskillit.recipe._cmd_rpc.time.sleep"),
    ):
        mock_run.side_effect = _make_side_effect()
        batch_create_issues(workspace=str(tmp_path))
    for call in mock_run.call_args_list:
        kwargs = call[1]
        if kwargs.get("input"):
            mutation_call = json.loads(kwargs["input"])
            body = mutation_call["variables"]["i0"]["body"]
            assert "## Validation Summary" in body
            assert "All clear." in body
            break


def test_batch_create_issues_handles_no_tickets(tmp_path):
    va_dir = tmp_path / ".autoskillit" / "temp" / "validate-audit"
    va_dir.mkdir(parents=True)
    # Leave directory empty
    result = batch_create_issues(workspace=str(tmp_path))
    assert result == {"issue_urls": "", "issue_count": "0"}


def test_batch_create_issues_handles_graphql_error(tmp_path):
    va_dir = tmp_path / ".autoskillit" / "temp" / "validate-audit"
    va_dir.mkdir(parents=True)
    (va_dir / "ticket_body_tests_1_2026-01-01_120000.md").write_text("# One Issue\n\nBody.")
    error_side_effect = [
        subprocess.CompletedProcess(args=[], returncode=0, stdout="org repo\n", stderr=""),
        subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({"data": {"repository": {"id": "R_123"}}}),
            stderr="",
        ),
        subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(
                {"data": {"repository": {"impl": {"id": "L_1"}, "enh": {"id": "L_2"}}}}
            ),
            stderr="",
        ),
        subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="rate limited"),
    ]
    with (
        patch("autoskillit.recipe._cmd_rpc.subprocess.run") as mock_run,
        patch("autoskillit.recipe._cmd_rpc.time.sleep"),
    ):
        mock_run.side_effect = error_side_effect
        with pytest.raises(RuntimeError, match="rate limited"):
            batch_create_issues(workspace=str(tmp_path))
