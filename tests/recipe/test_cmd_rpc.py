"""Tests for recipe._cmd_rpc — externalized run_python callables."""

from __future__ import annotations

import json
import os
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
    create_audit_run_dir,
    emit_fallback_map,
    ensure_results,
    export_local_bundle,
    force_push_and_wait_mergeability,
    main_repo_guard,
    refetch_issues,
    wait_for_direct_merge,
)

# Git environment for deterministic identity in tests.
_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@test.local",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@test.local",
}

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


def test_commit_guard_excludes_generated_files(tmp_path):
    """commit_guard must not commit files matching GENERATED_FILES patterns."""
    _init_git_repo(tmp_path)
    # A real dirty file — should be committed
    (tmp_path / "real_change.py").write_text("print('hello')")
    # A file matching a generated file path — should NOT be committed
    contracts_dir = tmp_path / "src" / "autoskillit" / "recipes" / "contracts"
    contracts_dir.mkdir(parents=True)
    (contracts_dir / "some-recipe.yaml").write_text("name: test")
    result = commit_guard(worktree_path=str(tmp_path))
    assert result["committed"] == "true"
    # Verify only the real file was committed
    log_result = subprocess.run(
        ["git", "log", "--oneline", "-1", "--name-only"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    committed_files = log_result.stdout.splitlines()[1:]  # skip commit hash line
    assert any("real_change.py" in f for f in committed_files), (
        f"real_change.py should be committed, but committed files were: {committed_files}"
    )
    assert not any("contracts/" in f for f in committed_files), (
        f"Generated contracts/ files should NOT be committed, but were: {committed_files}"
    )


# T-DM-3
@pytest.mark.medium
def test_main_repo_guard_cleans_dirty_state(tmp_path):
    """main_repo_guard stashes dirty state and returns cleaned=true."""
    _init_git_repo(tmp_path)
    (tmp_path / "uncommitted.txt").write_text("dirty content")
    result = main_repo_guard(clone_path=str(tmp_path))
    assert result["cleaned"] == "true"
    # git status --porcelain must be empty after stash
    status_result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert status_result.stdout.strip() == "", (
        f"main_repo_guard should leave repo clean, but status was: {status_result.stdout!r}"
    )
    stash_list = subprocess.run(
        ["git", "stash", "list"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert "autoskillit: main_repo_guard pre-merge stash" in stash_list.stdout, (
        f"stash entry not found; git stash list output: {stash_list.stdout!r}"
    )


# T-DM-4
@pytest.mark.medium
def test_main_repo_guard_clean_repo_noop(tmp_path):
    """main_repo_guard returns cleaned=false when repo is already clean."""
    _init_git_repo(tmp_path)
    result = main_repo_guard(clone_path=str(tmp_path))
    assert result["cleaned"] == "false"


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


def test_emit_fallback_map_includes_deferred_groups(tmp_path):
    result = emit_fallback_map(
        issue_urls="https://github.com/o/r/issues/1,https://github.com/o/r/issues/2",
        temp_dir=str(tmp_path),
    )
    data = json.loads(Path(result["execution_map"]).read_text())
    assert data["deferred_groups"] == []
    assert data["deferred_merge_order"] == []


@pytest.mark.medium
def test_main_repo_guard_removes_embedded_worktree(tmp_path):
    """main_repo_guard detects and removes a linked worktree embedded inside the clone."""
    _init_git_repo(tmp_path)

    # Create a linked worktree inside the repo using _GIT_ENV.
    wt_path = tmp_path / "embedded-wt"
    subprocess.run(
        ["git", "worktree", "add", "--no-checkout", str(wt_path)],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        env=_GIT_ENV,
    )

    # Write a dirty file in the main repo.
    (tmp_path / "dirty.txt").write_text("uncommitted content")

    result = main_repo_guard(clone_path=str(tmp_path))

    assert result["cleaned"] in ("true", "force"), (
        f"expected cleaned='true' or 'force', got: {result!r}"
    )
    # Embedded worktree directory must no longer exist.
    assert not wt_path.exists(), f"embedded worktree {wt_path} should have been removed"
    # Repo must be clean after guard.
    status_result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=_GIT_ENV,
    )
    assert status_result.stdout.strip() == "", (
        f"repo should be clean, but status was: {status_result.stdout!r}"
    )


@pytest.mark.medium
def test_main_repo_guard_removes_multiple_embedded_worktrees(tmp_path):
    """main_repo_guard removes all embedded linked worktrees before merging."""
    _init_git_repo(tmp_path)

    wt_a = tmp_path / "wt-a"
    wt_b = tmp_path / "wt-b"
    for wt_path in (wt_a, wt_b):
        subprocess.run(
            ["git", "worktree", "add", "--no-checkout", str(wt_path)],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
            env=_GIT_ENV,
        )

    # Dirty file in main repo.
    (tmp_path / "dirty.txt").write_text("content")

    result = main_repo_guard(clone_path=str(tmp_path))

    assert result["cleaned"] in ("true", "force"), (
        f"expected cleaned='true' or 'force', got: {result!r}"
    )
    assert not wt_a.exists(), f"embedded worktree {wt_a} should have been removed"
    assert not wt_b.exists(), f"embedded worktree {wt_b} should have been removed"

    status_result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=_GIT_ENV,
    )
    assert status_result.stdout.strip() == "", (
        f"repo should be clean, but status was: {status_result.stdout!r}"
    )


@pytest.mark.skipif(os.getuid() == 0, reason="chmod restrictions ignored when running as root")
@pytest.mark.medium
def test_main_repo_guard_post_clean_verify_catches_persistent_dirt(tmp_path):
    """When neither stash nor force-clean can succeed, main_repo_guard returns failed."""
    _init_git_repo(tmp_path)

    # Add a tracked file and an embedded worktree at a read-only path.
    (tmp_path / "tracked.txt").write_text("original")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "add tracked"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        env=_GIT_ENV,
    )
    # Modify the tracked file to make the repo dirty.
    (tmp_path / "tracked.txt").write_text("modified")

    # Create an embedded worktree.
    wt_path = tmp_path / "wt"
    subprocess.run(
        ["git", "worktree", "add", "--no-checkout", str(wt_path)],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        env=_GIT_ENV,
    )

    # Lock .git to make stash and checkout fail while clean still runs.
    git_dir = tmp_path / ".git"
    git_dir.chmod(0o555)

    try:
        result = main_repo_guard(clone_path=str(tmp_path))

        assert result.get("cleaned") == "failed", f"expected cleaned='failed', got: {result!r}"
        assert "remaining" in result, f"result should contain 'remaining' key: {result!r}"
    finally:
        # Restore permissions so tmp_path cleanup can proceed.
        git_dir.chmod(0o755)


@pytest.mark.medium
def test_main_repo_guard_post_clean_verify_on_stash_success(tmp_path):
    """When stash succeeds and post-clean verify confirms clean, cleaned=true is returned."""
    _init_git_repo(tmp_path)

    # Normal dirty file.
    (tmp_path / "dirty.txt").write_text("content")

    result = main_repo_guard(clone_path=str(tmp_path))

    assert result["cleaned"] == "true", f"expected cleaned='true', got: {result!r}"

    # Explicitly verify repo is clean.
    status_result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=_GIT_ENV,
    )
    assert status_result.stdout.strip() == "", (
        f"repo should be clean, but status was: {status_result.stdout!r}"
    )


@pytest.mark.medium
def test_main_repo_guard_fallback_clears_staged_entries(tmp_path, monkeypatch):
    """Stash-failure fallback path clears staged-only index entries via git reset HEAD."""
    from autoskillit.core import run_git as real_run_git

    _init_git_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("original")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "add tracked"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        env=_GIT_ENV,
    )
    (tmp_path / "tracked.txt").write_text("staged modification")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True, capture_output=True)

    def _stash_fails(args, **kwargs):
        if args and args[0] == "stash":
            return subprocess.CompletedProcess(
                args=args, returncode=1, stdout="", stderr="stash failed"
            )
        return real_run_git(args, **kwargs)

    monkeypatch.setattr("autoskillit.recipe._cmd_rpc_guards.run_git", _stash_fails)
    result = main_repo_guard(clone_path=str(tmp_path))

    assert result["cleaned"] == "force"
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert status.stdout.strip() == ""


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
    with patch("autoskillit.recipe._cmd_rpc_issues.run_gh") as mock_run_gh:
        mock_run_gh.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="1 2", stderr=""
        )
        result = refetch_issues(
            issue_urls="https://github.com/org/repo/issues/1,https://github.com/org/repo/issues/2"
        )
    assert result["issue_numbers"] == "1 2"
    call_args = mock_run_gh.call_args[0][0]
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
        issue_data = [{"number": 1, "url": "https://github.com/org/repo/issues/1"}]
    alias_data = {}
    for idx, issue in enumerate(issue_data):
        alias_data[f"issue{idx}"] = {"issue": issue}
    return [
        subprocess.CompletedProcess(args=[], returncode=0, stdout="org repo\n", stderr=""),
        subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({"data": {"repository": {"id": repo_id}}}),
            stderr="",
        ),
        subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
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
        patch("autoskillit.recipe._cmd_rpc_issues.run_gh") as mock_run_gh,
        patch("autoskillit.recipe._cmd_rpc_issues.time.sleep"),
    ):
        mock_run_gh.side_effect = _make_side_effect(
            issue_data=[
                {"number": 1, "url": "https://github.com/org/repo/issues/1"},
                {"number": 2, "url": "https://github.com/org/repo/issues/2"},
                {"number": 3, "url": "https://github.com/org/repo/issues/3"},
            ]
        )
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
        patch("autoskillit.recipe._cmd_rpc_issues.run_gh") as mock_run_gh,
        patch("autoskillit.recipe._cmd_rpc_issues.time.sleep"),
    ):
        mock_run_gh.side_effect = _make_side_effect()
        batch_create_issues(workspace=str(tmp_path))
    # Find the createIssue mutation call
    mutation_call: dict[str, object] = {}
    for call in mock_run_gh.call_args_list:
        args = call[0][0]
        if "--input" in args:
            mutation_call = json.loads(call[1].get("input_data", "{}"))
            break
    assert mutation_call, "no createIssue mutation call found in mock_run_gh calls"
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
        patch("autoskillit.recipe._cmd_rpc_issues.run_gh") as mock_run_gh,
        patch("autoskillit.recipe._cmd_rpc_issues.time.sleep"),
    ):
        mock_run_gh.side_effect = _make_side_effect()
        batch_create_issues(workspace=str(tmp_path))
    found = False
    for call in mock_run_gh.call_args_list:
        kwargs = call[1]
        if kwargs.get("input_data"):
            mutation_call = json.loads(kwargs["input_data"])
            assert mutation_call["variables"]["i0"]["title"] == "Audit: Missing test coverage"
            found = True
            break
    assert found, "no createIssue mutation call found in mock_run_gh calls"


def test_batch_create_issues_constructs_graphql_mutation(tmp_path):
    va_dir = tmp_path / ".autoskillit" / "temp" / "validate-audit"
    va_dir.mkdir(parents=True)
    (va_dir / "ticket_body_tests_1_2026-01-01_120000.md").write_text("# Issue One\n\nBody one.")
    (va_dir / "ticket_body_tests_2_2026-01-01_120000.md").write_text("# Issue Two\n\nBody two.")
    with (
        patch("autoskillit.recipe._cmd_rpc_issues.run_gh") as mock_run_gh,
        patch("autoskillit.recipe._cmd_rpc_issues.time.sleep"),
    ):
        mock_run_gh.side_effect = _make_side_effect(
            issue_data=[
                {"number": 1, "url": "https://github.com/org/repo/issues/1"},
                {"number": 2, "url": "https://github.com/org/repo/issues/2"},
            ]
        )
        batch_create_issues(workspace=str(tmp_path))
    found = False
    for call in mock_run_gh.call_args_list:
        kwargs = call[1]
        if kwargs.get("input_data"):
            mutation_call = json.loads(kwargs["input_data"])
            query = mutation_call["query"]
            variables = mutation_call["variables"]
            assert "issue0: createIssue" in query
            assert "issue1: createIssue" in query
            assert variables["i0"]["repositoryId"] == "R_123"
            assert variables["i1"]["repositoryId"] == "R_123"
            assert variables["i0"]["labelIds"] == ["L_1", "L_2"]
            assert variables["i1"]["labelIds"] == ["L_1", "L_2"]
            found = True
            break
    assert found, "no createIssue mutation call found in mock_run_gh calls"


def test_batch_create_issues_chunks_large_batches(tmp_path):
    va_dir = tmp_path / ".autoskillit" / "temp" / "validate-audit"
    va_dir.mkdir(parents=True)
    for n in range(25):
        (va_dir / f"ticket_body_tests_{n + 1}_2026-01-01_120000.md").write_text(
            f"# Issue {n + 1}\n\nBody {n + 1}."
        )
    with (
        patch("autoskillit.recipe._cmd_rpc_issues.run_gh") as mock_run_gh,
        patch("autoskillit.recipe._cmd_rpc_issues.time.sleep"),
    ):

        def side_effect_factory():
            call_count = [0]

            def side_effect(_args, **_kwargs):
                c = call_count[0]
                call_count[0] += 1
                if c == 0:
                    return subprocess.CompletedProcess(
                        args=[], returncode=0, stdout="org repo\n", stderr=""
                    )
                if c == 1:
                    return subprocess.CompletedProcess(
                        args=[],
                        returncode=0,
                        stdout=json.dumps({"data": {"repository": {"id": "R_123"}}}),
                        stderr="",
                    )
                if c in (2, 3):
                    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
                if c == 4:
                    return subprocess.CompletedProcess(
                        args=[],
                        returncode=0,
                        stdout=json.dumps(
                            {"data": {"repository": {"impl": {"id": "L_1"}, "enh": {"id": "L_2"}}}}
                        ),
                        stderr="",
                    )
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

        mock_run_gh.side_effect = side_effect_factory()
        result = batch_create_issues(workspace=str(tmp_path), chunk_size="10")
    mutation_calls = sum(
        1
        for call in mock_run_gh.call_args_list
        if call[1].get("input_data") and "createIssue" in call[1]["input_data"]
    )
    assert mutation_calls == 3
    assert result["issue_count"] == "25"


def test_batch_create_issues_ignores_validation_summary_file(tmp_path):
    """batch_create_issues must not append validation_summary content to issue bodies.

    The validation_summary file is a bulk audit artifact covering all findings
    for a source. SKILL.md § Issue Body Construction states it is NOT part of the issue body.
    """
    va_dir = tmp_path / ".autoskillit" / "temp" / "validate-audit"
    va_dir.mkdir(parents=True)
    (va_dir / "ticket_body_tests_1_2026-01-01_120000.md").write_text(
        "validated: true\n\n# Audit Finding\n\nSome finding."
    )
    (va_dir / "validation_summary_tests_2026-01-01_120000.md").write_text(
        "## Validation Summary\nAll clear."
    )
    with (
        patch("autoskillit.recipe._cmd_rpc_issues.run_gh") as mock_run_gh,
        patch("autoskillit.recipe._cmd_rpc_issues.time.sleep"),
    ):
        mock_run_gh.side_effect = _make_side_effect()
        batch_create_issues(workspace=str(tmp_path))
    for call in mock_run_gh.call_args_list:
        kwargs = call[1]
        if kwargs.get("input_data"):
            mutation_call = json.loads(kwargs["input_data"])
            body = mutation_call["variables"]["i0"]["body"]
            assert "## Validation Summary" not in body, (
                "validation_summary content must not be appended to issue bodies"
            )
            assert "All clear." not in body, (
                "validation_summary content must not be appended to issue bodies"
            )
            return
    pytest.fail("no createIssue mutation call found in mock_run_gh calls")


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
        patch("autoskillit.recipe._cmd_rpc_issues.run_gh") as mock_run_gh,
        patch("autoskillit.recipe._cmd_rpc_issues.time.sleep"),
    ):
        mock_run_gh.side_effect = error_side_effect
        with pytest.raises(RuntimeError, match="rate limited"):
            batch_create_issues(workspace=str(tmp_path))


# ─── Type coercion: int pr_number for _cmd_rpc callables (Step 1c) ───────────


@patch("autoskillit.recipe._cmd_rpc_merge.run_gh")
@patch("autoskillit.recipe._cmd_rpc_merge.time.sleep")
def test_wait_for_direct_merge_int_pr_number(mock_sleep, mock_run_gh):
    """wait_for_direct_merge handles int pr_number from LLM JSON boundary."""
    mock_run_gh.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="MERGED\n", stderr=""
    )
    result = wait_for_direct_merge(pr_number=42, max_polls="1", poll_interval="1")  # type: ignore[arg-type]
    assert result["state"] == "merged"
    assert mock_run_gh.call_count >= 1
    call_cmd = mock_run_gh.call_args[0][0]
    assert "42" in call_cmd


# ─── Multi-run accumulation tests for batch_create_issues ────────────────────


def test_batch_create_issues_ignores_prior_run_files(tmp_path):
    """batch_create_issues must only process files from the current run, not prior runs.

    Reproduces the bug: without audit_run_dir scoping, the callable globs ALL
    ticket_body_*.md files in the flat validate-audit/ directory, including those
    written by previous pipeline runs. The issue_count should reflect only the
    files present when the callable is invoked.
    """
    va_dir = tmp_path / ".autoskillit" / "temp" / "validate-audit"
    va_dir.mkdir(parents=True)

    # Simulate Run 1: 3 ticket bodies
    for n in range(1, 4):
        (va_dir / f"ticket_body_tests_{n}_2026-05-05_120000.md").write_text(
            f"# Issue {n}\n\nBody from Run 1."
        )

    # Call batch_create_issues — should return 3 issues
    with (
        patch("autoskillit.recipe._cmd_rpc_issues.run_gh") as mock_run_gh,
        patch("autoskillit.recipe._cmd_rpc_issues.time.sleep"),
    ):
        mock_run_gh.side_effect = _make_side_effect(
            issue_data=[
                {"number": 1, "url": "https://github.com/org/repo/issues/1"},
                {"number": 2, "url": "https://github.com/org/repo/issues/2"},
                {"number": 3, "url": "https://github.com/org/repo/issues/3"},
            ]
        )
        result = batch_create_issues(workspace=str(tmp_path))

    assert result["issue_count"] == "3"

    # Simulate Run 2: 2 MORE ticket bodies added (total 5 in directory)
    for n in range(4, 6):
        (va_dir / f"ticket_body_tests_{n}_2026-05-06_130000.md").write_text(
            f"# Issue {n}\n\nBody from Run 2."
        )

    # Call batch_create_issues WITHOUT audit_run_dir — globs all 5 files
    with (
        patch("autoskillit.recipe._cmd_rpc_issues.run_gh") as mock_run_gh,
        patch("autoskillit.recipe._cmd_rpc_issues.time.sleep"),
    ):
        mock_run_gh.side_effect = _make_side_effect(
            issue_data=[
                {"number": i, "url": f"https://github.com/org/repo/issues/{i}"}
                for i in range(1, 6)
            ]
        )
        result = batch_create_issues(workspace=str(tmp_path))

    # Without audit_run_dir, batch_create_issues counts ALL files in the directory
    assert result["issue_count"] == "5", (
        "Without audit_run_dir, batch_create_issues counts ALL files in the directory"
    )


def test_batch_create_issues_scoped_to_audit_run_dir(tmp_path):
    """batch_create_issues with audit_run_dir must only process files in that directory.

    This is the key test for the fix: when audit_run_dir is provided, the callable
    must glob within that specific run directory, not the flat validate-audit/ dir.
    """
    va_dir = tmp_path / ".autoskillit" / "temp" / "validate-audit"
    va_dir.mkdir(parents=True)

    # Create two per-run subdirectories (simulating two pipeline runs)
    run1_dir = va_dir / "run-20260505-120000-aabb1122"
    run2_dir = va_dir / "run-20260506-130000-ccdd3344"
    run1_dir.mkdir()
    run2_dir.mkdir()

    # Run 1 files
    for n in range(1, 4):
        (run1_dir / f"ticket_body_tests_{n}_2026-05-05_120000.md").write_text(
            f"# Issue {n}\n\nBody from Run 1."
        )

    # Run 2 files
    for n in range(4, 6):
        (run2_dir / f"ticket_body_tests_{n}_2026-05-06_130000.md").write_text(
            f"# Issue {n}\n\nBody from Run 2."
        )

    # Call batch_create_issues scoped to run2_dir only
    with (
        patch("autoskillit.recipe._cmd_rpc_issues.run_gh") as mock_run_gh,
        patch("autoskillit.recipe._cmd_rpc_issues.time.sleep"),
    ):
        mock_run_gh.side_effect = _make_side_effect(
            issue_data=[
                {"number": 4, "url": "https://github.com/org/repo/issues/4"},
                {"number": 5, "url": "https://github.com/org/repo/issues/5"},
            ]
        )
        result = batch_create_issues(workspace=str(tmp_path), audit_run_dir=str(run2_dir))

    assert result["issue_count"] == "2", (
        "batch_create_issues should only process files in audit_run_dir, not the parent directory"
    )


def test_batch_create_issues_audit_run_dir_only(tmp_path):
    """batch_create_issues with only audit_run_dir (no workspace-derived fallback).

    When audit_run_dir is provided, it must be used as the sole discovery path,
    completely ignoring the workspace-derived path.
    """
    va_dir = tmp_path / ".autoskillit" / "temp" / "validate-audit"
    va_dir.mkdir(parents=True)

    # Files in the workspace-derived path (should be IGNORED)
    for n in range(1, 4):
        (va_dir / f"ticket_body_tests_{n}_2026-05-05_120000.md").write_text(
            "# Stale Issue\n\nShould be ignored."
        )

    # Files in the audit_run_dir (should be processed)
    scoped_dir = va_dir / "run-20260506-130000-ccdd3344"
    scoped_dir.mkdir()
    (scoped_dir / "ticket_body_tests_1_2026-05-06_130000.md").write_text("# Active Issue\n\nBody.")

    with (
        patch("autoskillit.recipe._cmd_rpc_issues.run_gh") as mock_run_gh,
        patch("autoskillit.recipe._cmd_rpc_issues.time.sleep"),
    ):
        mock_run_gh.side_effect = _make_side_effect(
            issue_data=[{"number": 99, "url": "https://github.com/org/repo/issues/99"}]
        )
        result = batch_create_issues(workspace=str(tmp_path), audit_run_dir=str(scoped_dir))

    assert result["issue_count"] == "1", (
        "batch_create_issues should only process files in audit_run_dir"
    )


def test_batch_create_issues_audit_run_dir_not_a_directory(tmp_path):
    """batch_create_issues raises ValueError when audit_run_dir does not exist."""
    bogus = str(tmp_path / "nonexistent")
    with pytest.raises(ValueError, match="audit_run_dir must be an existing directory"):
        batch_create_issues(workspace=str(tmp_path), audit_run_dir=bogus)


def test_create_audit_run_dir_wraps_os_error(tmp_path, monkeypatch):
    """create_audit_run_dir wraps OSError with a descriptive ValueError."""
    monkeypatch.setattr(Path, "mkdir", _raise_os_error)
    with pytest.raises(ValueError, match="cannot create audit run directory"):
        create_audit_run_dir(temp_dir=str(tmp_path))


def _raise_os_error(*_args, **_kwargs):
    raise OSError("Permission denied")


@patch("autoskillit.recipe._cmd_rpc_merge.run_gh")
@patch("autoskillit.recipe._cmd_rpc_merge.run_git")
@patch("autoskillit.recipe._cmd_rpc_merge.time.sleep")
def test_force_push_int_review_pr_number(mock_sleep, mock_run_git, mock_run_gh, tmp_path):
    """force_push_and_wait_mergeability handles int review_pr_number."""
    with patch("autoskillit.recipe._cmd_rpc_merge._detect_remote", return_value="origin"):
        mock_run_git.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        mock_run_gh.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="TRUE\n", stderr=""
        )
        result = force_push_and_wait_mergeability(
            work_dir=str(tmp_path),
            batch_branch="feat-x/42",
            review_pr_number=1958,  # type: ignore[arg-type]
            max_polls="1",
            poll_interval="1",
        )
    assert result["ok"] == "true"
    assert mock_run_gh.call_count >= 1
    call_cmd = mock_run_gh.call_args[0][0]
    assert "1958" in call_cmd


# ─────────────────────────────────────────────────────────────────────────────
# review_path_rebase
# ─────────────────────────────────────────────────────────────────────────────


class TestReviewPathRebase:
    """Unit tests for the review_path_rebase callable."""

    def test_delegates_to_queue_ejected_fix(self) -> None:
        """review_path_rebase must delegate to queue_ejected_fix."""
        from unittest.mock import patch

        from autoskillit.recipe._cmd_rpc import review_path_rebase

        with patch("autoskillit.recipe._cmd_rpc_merge.queue_ejected_fix") as mock:
            mock.return_value = {"status": "clean"}
            result = review_path_rebase(work_dir="/tmp/work", base_branch="main")
            assert result == {"status": "clean"}
            mock.assert_called_once_with(work_dir="/tmp/work", base_branch="main")

    def test_returns_conflicts_on_conflict(self) -> None:
        """review_path_rebase returns conflicts when delegate does."""
        from unittest.mock import patch

        from autoskillit.recipe._cmd_rpc import review_path_rebase

        with patch("autoskillit.recipe._cmd_rpc_merge.queue_ejected_fix") as mock:
            mock.return_value = {"status": "conflicts"}
            result = review_path_rebase(work_dir="/tmp/work", base_branch="main")
            assert result == {"status": "conflicts"}


def _init_git_repo_on_main(tmp_path: Path) -> None:
    """Init git repo with branch named 'main' and an initial empty commit."""
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        env=_GIT_ENV,
    )
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
        env=_GIT_ENV,
    )


def test_commit_guard_detects_regression(tmp_path: Path) -> None:
    """commit_guard must detect and refuse when pending changes revert implementation commits."""
    _init_git_repo_on_main(tmp_path)
    subprocess.run(
        ["git", "checkout", "-b", "feature"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        env=_GIT_ENV,
    )
    (tmp_path / "module_a.py").write_text("\n".join(f"line_{i} = {i}" for i in range(20)) + "\n")
    (tmp_path / "module_b.py").write_text(
        "\n".join(f"x_{i} = 'value_{i}'" for i in range(20)) + "\n"
    )
    (tmp_path / "module_c.py").write_text("\n".join(f"Y_{i} = True" for i in range(15)) + "\n")
    subprocess.run(
        ["git", "add", "--", "module_a.py", "module_b.py", "module_c.py"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        env=_GIT_ENV,
    )
    subprocess.run(
        ["git", "commit", "-m", "feat: add implementation"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        env=_GIT_ENV,
    )
    (tmp_path / "module_a.py").write_text("line_0 = 0\n")
    (tmp_path / "module_b.py").write_text("x_0 = 'value_0'\n")
    (tmp_path / "module_c.py").write_text("Y_0 = True\n")
    result = commit_guard(worktree_path=str(tmp_path), base_branch="main")
    assert result["committed"] == "regression_detected", (
        f"Expected regression_detected but got: {result}"
    )
    assert "reverted_files" in result


def test_commit_guard_allows_normal_changes(tmp_path: Path) -> None:
    """commit_guard must allow normal incremental changes after implementation commits."""
    _init_git_repo_on_main(tmp_path)
    subprocess.run(
        ["git", "checkout", "-b", "feature"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        env=_GIT_ENV,
    )
    code_lines = "\n".join(f"line_{i} = {i}" for i in range(20))
    (tmp_path / "module_a.py").write_text(code_lines + "\n")
    subprocess.run(
        ["git", "add", "--", "module_a.py"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        env=_GIT_ENV,
    )
    subprocess.run(
        ["git", "commit", "-m", "feat: add module_a"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        env=_GIT_ENV,
    )
    (tmp_path / "module_a.py").write_text(
        code_lines + "\nline_20 = 20\nline_21 = 21\nline_22 = 22\n"
    )
    result = commit_guard(worktree_path=str(tmp_path), base_branch="main")
    assert result["committed"] == "true", (
        f"Expected committed=true for normal changes but got: {result}"
    )


def test_commit_guard_skips_regression_check_without_base_branch(tmp_path: Path) -> None:
    """commit_guard must commit normally when no base_branch is provided (backward compat)."""
    _init_git_repo_on_main(tmp_path)
    (tmp_path / "newfile.py").write_text("x = 1\n")
    result = commit_guard(worktree_path=str(tmp_path))
    assert result["committed"] == "true", (
        f"Expected committed=true without base_branch but got: {result}"
    )


def test_commit_guard_skips_regression_no_implementation_commits(tmp_path: Path) -> None:
    """commit_guard must commit normally when there are no implementation commits to protect."""
    _init_git_repo_on_main(tmp_path)
    # No feature branch divergence: merge-base == HEAD, committed_net == 0
    (tmp_path / "new_file.py").write_text("x = 1\n")
    result = commit_guard(worktree_path=str(tmp_path), base_branch="main")
    assert result["committed"] == "true", (
        f"Expected committed=true (no implementation commits) but got: {result}"
    )
