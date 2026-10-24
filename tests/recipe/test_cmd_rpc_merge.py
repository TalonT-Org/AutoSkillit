"""Tests for recipe/_cmd_rpc_merge.py — base branch fetch discipline."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

import autoskillit.recipe._cmd_rpc as _cmd_rpc
import autoskillit.recipe.cmd_rpc._cmd_rpc_merge as _patch_cmd_rpc__cmd_rpc_merge

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _mock_result(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


def _fake_run_git_factory(
    responses: list[MagicMock] | None = None,
    call_log: list[list[str]] | None = None,
) -> object:
    """Return a side_effect callable that records commands and pops responses."""
    _responses = list(responses) if responses else []
    _call_log = call_log if call_log is not None else []

    def fake(cmd, *, cwd=None, check=False):
        _call_log.append(list(cmd))
        if _responses:
            result = _responses.pop(0)
        else:
            result = _mock_result(0)
        if check and result.returncode != 0:
            import subprocess

            raise subprocess.CalledProcessError(
                result.returncode, cmd, result.stdout, result.stderr
            )
        return result

    return fake


def test_attempt_cheap_rebase_fetches_base_branch():
    """attempt_cheap_rebase must fetch base_branch before rebasing onto it."""
    from autoskillit.recipe._cmd_rpc_merge import attempt_cheap_rebase

    call_log: list[list[str]] = []
    fake = _fake_run_git_factory(call_log=call_log)

    with patch.object(_patch_cmd_rpc__cmd_rpc_merge, "run_git", side_effect=fake):
        result = attempt_cheap_rebase("/work", "pr-branch", "develop", "/tmp/report.md")

    assert result == {"status": "clean"}
    fetch_base_indices = [
        i
        for i, cmd in enumerate(call_log)
        if cmd[:1] == ["fetch"] and "develop" in cmd and "pr-branch" not in cmd
    ]
    rebase_indices = [
        i for i, cmd in enumerate(call_log) if cmd[:1] == ["rebase"] and "--abort" not in cmd
    ]
    assert fetch_base_indices, f"fetch base_branch not found in call log: {call_log}"
    assert rebase_indices, f"rebase not found in call log: {call_log}"
    assert fetch_base_indices[0] < rebase_indices[0], (
        f"fetch base_branch (idx {fetch_base_indices[0]}) must precede "
        f"rebase (idx {rebase_indices[0]}); call_log={call_log}"
    )


def test_proactive_rebase_next_pr_fetches_base_branch():
    """proactive_rebase_next_pr must fetch base_branch before rebasing onto it."""
    from autoskillit.recipe._cmd_rpc_merge import proactive_rebase_next_pr

    call_log: list[list[str]] = []
    fake = _fake_run_git_factory(call_log=call_log)

    with patch.object(_patch_cmd_rpc__cmd_rpc_merge, "run_git", side_effect=fake):
        result = proactive_rebase_next_pr("/work", "next-pr-branch", "develop", "/tmp/report.md")

    assert result == {"status": "clean"}
    fetch_base_indices = [
        i
        for i, cmd in enumerate(call_log)
        if cmd[:1] == ["fetch"] and "develop" in cmd and "next-pr-branch" not in cmd
    ]
    rebase_indices = [
        i for i, cmd in enumerate(call_log) if cmd[:1] == ["rebase"] and "--abort" not in cmd
    ]
    assert fetch_base_indices, f"fetch base_branch not found in call log: {call_log}"
    assert rebase_indices, f"rebase not found in call log: {call_log}"
    assert fetch_base_indices[0] < rebase_indices[0], (
        f"fetch base_branch (idx {fetch_base_indices[0]}) must precede "
        f"rebase (idx {rebase_indices[0]}); call_log={call_log}"
    )


def test_queue_ejected_fix_returns_fetch_error_on_network_failure():
    """queue_ejected_fix must return fetch_error (not conflicts) when fetch fails."""
    from autoskillit.recipe._cmd_rpc_merge import queue_ejected_fix

    responses = [
        _mock_result(1, "", ""),  # _detect_remote: upstream not found → origin
        _mock_result(1, "", "Could not resolve host: github.com"),  # fetch fails
    ]
    fake = _fake_run_git_factory(responses=responses)

    with patch.object(_patch_cmd_rpc__cmd_rpc_merge, "run_git", side_effect=fake):
        result = queue_ejected_fix("/work", "develop")

    assert result["status"] == "fetch_error", f"Expected fetch_error, got: {result}"
    assert "Could not resolve host" in result.get("stderr", "")


def test_queue_ejected_fix_returns_conflicts_with_stderr():
    """Rebase failure must preserve stderr for downstream diagnostics."""
    from autoskillit.recipe._cmd_rpc_merge import queue_ejected_fix

    responses = [
        _mock_result(0, "origin\n", ""),  # _detect_remote
        _mock_result(0, "", ""),  # fetch
        _mock_result(1, "", "CONFLICT (content): ..."),  # rebase (fails)
        _mock_result(0, "", ""),  # rebase --abort
    ]
    fake = _fake_run_git_factory(responses=responses, call_log=[])

    with patch.object(_patch_cmd_rpc__cmd_rpc_merge, "run_git", side_effect=fake):
        result = queue_ejected_fix(work_dir="/tmp/test", base_branch="main")

    assert result["status"] == "conflicts"
    assert "stderr" in result
    assert "CONFLICT" in result["stderr"]


def test_queue_ejected_fix_distinguishes_dirty_tree():
    """Dirty-tree rebase rejection must still surface stderr for diagnostics."""
    from autoskillit.recipe._cmd_rpc_merge import queue_ejected_fix

    responses = [
        _mock_result(0, "origin\n", ""),
        _mock_result(0, "", ""),
        _mock_result(1, "", "cannot rebase: you have unstaged changes"),
        _mock_result(0, "", ""),
    ]
    fake = _fake_run_git_factory(responses=responses, call_log=[])

    with patch.object(_patch_cmd_rpc__cmd_rpc_merge, "run_git", side_effect=fake):
        result = queue_ejected_fix(work_dir="/tmp/test", base_branch="main")

    assert result["status"] == "conflicts"
    assert "stderr" in result
    assert "unstaged changes" in result["stderr"]


def test_attempt_cheap_rebase_publishes_bounded_conflict_report(tmp_path):
    """attempt_cheap_rebase must atomically publish bounded conflict evidence."""
    from autoskillit.recipe._cmd_rpc_merge import attempt_cheap_rebase

    responses = [
        _mock_result(0, "origin\n", ""),  # _detect_remote
        _mock_result(0, "", ""),  # fetch ejected_pr_branch (check=True)
        _mock_result(0, "", ""),  # fetch base_branch
        _mock_result(0, "", ""),  # checkout ejected_pr_branch (check=True)
        _mock_result(1, "", "CONFLICT (content): " + "x" * 6000),  # rebase fails
        _mock_result(0, "src/x.py\n" + "y" * 6000, ""),  # unmerged files
        _mock_result(0, "", ""),  # rebase --abort
    ]
    call_log: list[list[str]] = []
    fake = _fake_run_git_factory(responses=responses, call_log=call_log)
    report_path = tmp_path / "ejected.md"

    with patch.object(_patch_cmd_rpc__cmd_rpc_merge, "run_git", side_effect=fake):
        result = attempt_cheap_rebase(
            work_dir="/tmp/test",
            ejected_pr_branch="pr-branch",
            base_branch="main",
            conflict_report_path=str(report_path),
        )

    assert result["status"] == "conflicts"
    assert result["conflict_report_path"] == str(report_path)
    report = report_path.read_text()
    assert "y" * 100 in report
    assert "CONFLICT" in report
    assert report.count("...[truncated ") == 2
    assert len(report) < 11_000
    assert call_log.index(["diff", "--name-only", "--diff-filter=U"]) < call_log.index(
        ["rebase", "--abort"]
    )


def test_proactive_rebase_next_pr_publishes_conflict_report(tmp_path):
    """proactive_rebase_next_pr must publish evidence before aborting."""
    from autoskillit.recipe._cmd_rpc_merge import proactive_rebase_next_pr

    responses = [
        _mock_result(0, "origin\n", ""),  # _detect_remote
        _mock_result(0, "", ""),  # fetch next_pr_branch (check=True)
        _mock_result(0, "", ""),  # fetch base_branch
        _mock_result(0, "", ""),  # checkout -B (check=True)
        _mock_result(1, "", "CONFLICT (content): Merge conflict in y.py"),  # rebase fails
        _mock_result(0, "src/y.py\n", ""),  # unmerged files
        _mock_result(0, "", ""),  # rebase --abort
    ]
    fake = _fake_run_git_factory(responses=responses, call_log=[])
    report_path = tmp_path / "proactive.md"

    with patch.object(_patch_cmd_rpc__cmd_rpc_merge, "run_git", side_effect=fake):
        result = proactive_rebase_next_pr(
            work_dir="/tmp/test",
            next_pr_branch="next-pr",
            base_branch="main",
            conflict_report_path=str(report_path),
        )

    assert result["status"] == "conflicts"
    assert result["conflict_report_path"] == str(report_path)
    assert "src/y.py" in report_path.read_text()


def _ordered_pr(number: object, complexity: object = "simple") -> dict[str, object]:
    return {
        "number": number,
        "title": f"PR {number}",
        "branch": f"feature/{number}",
        "complexity": complexity,
        "files_changed": [f"src/{number}.py"],
        "test_files_changed": [f"tests/test_{number}.py"],
        "additions": 2,
        "deletions": 1,
        "overlap_with_pr_numbers": [],
    }


def _order_document(prs: list[dict[str, object]]) -> dict[str, object]:
    return {
        "batch_branch": "pr-batch/pr-merge-20261024-120000",
        "base_branch": "main",
        "generated_at": "2026-10-24T12:00:00Z",
        "pr_count": len(prs),
        "prs": prs,
    }


def _write_order_file(tmp_path, document: object):
    path = tmp_path / "pr-order.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


@pytest.fixture
def pr_order_file(tmp_path):
    return _write_order_file(
        tmp_path,
        _order_document(
            [
                _ordered_pr(42),
                _ordered_pr(4, "needs_check"),
                _ordered_pr(123),
            ]
        ),
    )


def test_first_ordered_pr_returns_first_number_and_complexity(pr_order_file):
    assert _cmd_rpc.first_ordered_pr(pr_order_file=str(pr_order_file)) == {
        "pr_number": "42",
        "complexity": "simple",
        "done": "false",
    }


def test_first_ordered_pr_returns_done_for_empty_order(tmp_path):
    path = _write_order_file(tmp_path, _order_document([]))

    assert _cmd_rpc.first_ordered_pr(pr_order_file=str(path)) == {
        "pr_number": "",
        "complexity": "",
        "done": "true",
    }


@pytest.mark.parametrize(
    ("current", "number", "complexity"), [("42", "4", "needs_check"), ("4", "123", "simple")]
)
def test_next_ordered_pr_advances_by_file_position(pr_order_file, current, number, complexity):
    assert _cmd_rpc.next_ordered_pr(
        pr_order_file=str(pr_order_file), current_pr_number=current
    ) == {
        "pr_number": number,
        "complexity": complexity,
        "done": "false",
    }


def test_next_ordered_pr_repeats_last_entry_as_done_without_mutating_file(
    pr_order_file,
):
    before = pr_order_file.read_bytes()

    expected = {"pr_number": "123", "complexity": "simple", "done": "true"}
    first = _cmd_rpc.next_ordered_pr(pr_order_file=str(pr_order_file), current_pr_number="123")
    second = _cmd_rpc.next_ordered_pr(pr_order_file=str(pr_order_file), current_pr_number="123")

    assert first == expected
    assert second == expected
    assert pr_order_file.read_bytes() == before


def test_first_ordered_pr_rejects_relative_path_before_file_access():
    with pytest.raises(ValueError):
        _cmd_rpc.first_ordered_pr(pr_order_file="relative-pr-order.json")


@pytest.mark.parametrize("current_pr_number", ["", "999", "done", "#4"])
def test_next_ordered_pr_rejects_invalid_current_number(pr_order_file, current_pr_number):
    with pytest.raises(ValueError):
        _cmd_rpc.next_ordered_pr(
            pr_order_file=str(pr_order_file), current_pr_number=current_pr_number
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("complexity", [], id="complexity-list"),
        pytest.param("complexity", "unknown", id="complexity-unknown"),
        pytest.param("number", True, id="number-bool"),
        pytest.param("number", 0, id="number-zero"),
        pytest.param("number", -1, id="number-negative"),
        pytest.param("number", "42", id="number-string"),
    ],
)
def test_first_ordered_pr_reports_invalid_pr_field_location(tmp_path, field, value):
    pr = _ordered_pr(42)
    pr[field] = value
    path = _write_order_file(tmp_path, _order_document([pr]))

    with pytest.raises(ValueError) as exc_info:
        _cmd_rpc.first_ordered_pr(pr_order_file=str(path))

    message = str(exc_info.value)
    assert str(path) in message
    assert "prs[0]" in message


def test_first_ordered_pr_reports_duplicate_number_location(tmp_path):
    path = _write_order_file(tmp_path, _order_document([_ordered_pr(42), _ordered_pr(42)]))

    with pytest.raises(ValueError) as exc_info:
        _cmd_rpc.first_ordered_pr(pr_order_file=str(path))

    assert str(path) in str(exc_info.value)
    assert "prs[1]" in str(exc_info.value)


@pytest.mark.parametrize(
    "malformation", ["top-level-list", "missing-prs", "nonlist-prs", "nondict-entry"]
)
def test_first_ordered_pr_reports_invalid_order_document(tmp_path, malformation):
    document = _order_document([_ordered_pr(42)])
    if malformation == "top-level-list":
        document = []
    elif malformation == "missing-prs":
        document.pop("prs")
    elif malformation == "nonlist-prs":
        document["prs"] = {}
    else:
        document["prs"] = [None]
    path = _write_order_file(tmp_path, document)

    with pytest.raises(ValueError) as exc_info:
        _cmd_rpc.first_ordered_pr(pr_order_file=str(path))

    message = str(exc_info.value)
    assert str(path) in message
    if malformation == "nondict-entry":
        assert "prs[0]" in message


@pytest.mark.parametrize("file_contents", [None, "{"])
def test_first_ordered_pr_wraps_file_read_errors_as_runtime_error(tmp_path, file_contents):
    path = tmp_path / "pr-order.json"
    if file_contents is not None:
        path.write_text(file_contents, encoding="utf-8")

    with pytest.raises(RuntimeError) as exc_info:
        _cmd_rpc.first_ordered_pr(pr_order_file=str(path))

    assert str(path) in str(exc_info.value)
