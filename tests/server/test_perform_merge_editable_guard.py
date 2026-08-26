"""Integration tests verifying perform_merge() aborts before cleanup on poisoned installs."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from autoskillit.config import AutomationConfig
from autoskillit.core.types import (
    MergeFailedStep,
    MergeState,
    SubprocessResult,
    TerminationReason,
    TestResult,
)
from autoskillit.server._editable_guard import EditableScanResult
from autoskillit.server.tools.tools_git import merge_worktree
from tests.fakes import InMemoryTestRunner, MockSubprocessRunner

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _make_result(returncode: int = 0, stdout: str = "", stderr: str = "") -> SubprocessResult:
    return SubprocessResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        termination=TerminationReason.NATURAL_EXIT,
        pid=12345,
    )


def _queue_direct_merge_through_guard(runner: MockSubprocessRunner, fake_wt: str) -> None:
    runner.push(_make_result(0, f"{fake_wt}/.git/worktrees/wt"))
    runner.push(_make_result(0, "feature-branch\n"))
    runner.push(_make_result(0, ""))
    runner.push(_make_result(0, ""))
    runner.push(_make_result(0, ""))
    runner.push(_make_result(0, ""))
    runner.push(_make_result(0, ""))
    runner.push(_make_result(0, ""))
    runner.push(_make_result(0, "dev\n"))
    runner.push(_make_result(0, ""))
    runner.push(_make_result(0, ""))


def _make_tester() -> InMemoryTestRunner:
    return InMemoryTestRunner(
        results=[TestResult(True, "= 10 passed =", ""), TestResult(True, "= 10 passed =", "")]
    )


def _queue_tool_merge_through_guard(runner: MockSubprocessRunner, fake_wt: str) -> None:
    runner.push(_make_result(0, f"{fake_wt}/.git/worktrees/wt"))
    runner.push(_make_result(0, "feature-branch\n"))
    runner.push(_make_result(0, ""))
    runner.push(_make_result(0, ""))
    runner.push(_make_result(0, "PASS\n= 10 passed ="))
    runner.push(_make_result(0, ""))
    runner.push(_make_result(0, "abc123\n"))
    runner.push(_make_result(0, ""))
    runner.push(_make_result(0, ""))
    runner.push(_make_result(0, "PASS\n= 10 passed ="))
    runner.push(_make_result(0, "dev\n"))
    runner.push(_make_result(0, "abc123def456\n"))
    runner.push(_make_result(0, "abc123def456\n"))
    runner.push(_make_result(0, ""))
    runner.push(_make_result(0, ""))


@pytest.mark.anyio
async def test_perform_merge_aborts_before_cleanup_on_poisoned_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    When scan_editable_installs_for_worktree returns non-empty results after the merge
    step, perform_merge must return an error result WITHOUT calling git worktree remove.
    """
    import autoskillit.server.git as git_module
    from autoskillit.server.git import perform_merge

    fake_wt = str(tmp_path)
    poisoned_report = ["autoskillit editable at file:///fake/worktree/src (autoskillit-0.6.12)"]

    monkeypatch.setattr(
        git_module,
        "scan_editable_installs_for_worktree",
        lambda worktree_path, site_packages_dirs=None: EditableScanResult(
            findings=tuple(poisoned_report)
        ),
    )

    runner = MockSubprocessRunner()
    _queue_direct_merge_through_guard(runner, fake_wt)

    with patch(
        "autoskillit.server.git.resolve_main_worktree", return_value=Path("/nonexistent-main-repo")
    ):
        result = await perform_merge(
            fake_wt, "dev", config=AutomationConfig(), runner=runner, tester=_make_tester()
        )

    assert result["merge_succeeded"] is True
    assert result["state"] == MergeState.MERGE_SUCCEEDED_CLEANUP_BLOCKED
    assert result["failed_step"] == MergeFailedStep.EDITABLE_INSTALL_GUARD
    assert "editable" in result["error"].lower()
    assert result["poisoned_installs"] == poisoned_report
    assert result["worktree_removed"] is False
    assert result["branch_deleted"] is False

    # Verify git worktree remove was NOT called (cleanup was blocked)
    wt_remove_calls = [
        args[0]
        for args in runner.call_args_list
        if len(args[0]) >= 3 and args[0][1] == "worktree" and args[0][2] == "remove"
    ]
    assert wt_remove_calls == [], "git worktree remove must not be called when guard blocks"


@pytest.mark.anyio
async def test_perform_merge_proceeds_normally_when_guard_returns_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    When scan_editable_installs_for_worktree returns [] (no poisoned installs),
    perform_merge must NOT abort — it must proceed to cleanup normally.
    """
    import autoskillit.server.git as git_module
    from autoskillit.server.git import perform_merge

    fake_wt = str(tmp_path)

    monkeypatch.setattr(
        git_module,
        "scan_editable_installs_for_worktree",
        lambda worktree_path, site_packages_dirs=None: EditableScanResult(),
    )

    runner = MockSubprocessRunner()
    _queue_direct_merge_through_guard(runner, fake_wt)

    with patch(
        "autoskillit.server.git.resolve_main_worktree", return_value=Path("/nonexistent-main-repo")
    ):
        result = await perform_merge(
            fake_wt, "dev", config=AutomationConfig(), runner=runner, tester=_make_tester()
        )

    assert result.get("merge_succeeded") is True
    assert "error" not in result
    assert result["merged_branch"] == "feature-branch"


@pytest.mark.anyio
async def test_merge_surfaces_unverified_scan_reasons(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import autoskillit.server.git as git_module
    from autoskillit.server.git import perform_merge

    reason = "python3: interpreter probe failed"
    monkeypatch.setattr(
        git_module,
        "scan_editable_installs_for_worktree",
        lambda _worktree: EditableScanResult(unverified=(reason,)),
    )
    runner = MockSubprocessRunner()
    _queue_direct_merge_through_guard(runner, str(tmp_path))

    with patch(
        "autoskillit.server.git.resolve_main_worktree", return_value=Path("/nonexistent-main-repo")
    ):
        result = await perform_merge(
            str(tmp_path),
            "dev",
            config=AutomationConfig(),
            runner=runner,
            tester=_make_tester(),
        )

    assert result["merge_succeeded"] is True
    assert result["worktree_removed"] is True
    assert result["unverified_scan_reasons"] == [reason]


@pytest.mark.anyio
async def test_merge_blocks_on_findings_regardless_of_unverified(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import autoskillit.server.git as git_module
    from autoskillit.server.git import perform_merge

    finding = "autoskillit editable at file:///worktree/src"
    reason = "python3: interpreter probe failed"
    monkeypatch.setattr(
        git_module,
        "scan_editable_installs_for_worktree",
        lambda _worktree: EditableScanResult(findings=(finding,), unverified=(reason,)),
    )
    runner = MockSubprocessRunner()
    _queue_direct_merge_through_guard(runner, str(tmp_path))

    with patch(
        "autoskillit.server.git.resolve_main_worktree", return_value=Path("/nonexistent-main-repo")
    ):
        result = await perform_merge(
            str(tmp_path),
            "dev",
            config=AutomationConfig(),
            runner=runner,
            tester=_make_tester(),
        )

    assert result["failed_step"] == MergeFailedStep.EDITABLE_INSTALL_GUARD
    assert result["state"] == MergeState.MERGE_SUCCEEDED_CLEANUP_BLOCKED
    assert result["poisoned_installs"] == [finding]
    assert result["unverified_scan_reasons"] == [reason]
    assert result["worktree_removed"] is False


@pytest.mark.anyio
async def test_merge_tool_reports_error_when_guard_raises(
    monkeypatch: pytest.MonkeyPatch, tool_ctx_kitchen_open, tmp_path: Path
) -> None:
    import autoskillit.server._misc as misc_module
    import autoskillit.server.git as git_module

    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: /repo/.git/worktrees/worktree")
    _queue_tool_merge_through_guard(tool_ctx_kitchen_open.runner, str(worktree))

    def unexpected(_worktree: Path) -> EditableScanResult:
        raise AttributeError("boom")

    monkeypatch.setattr(git_module, "scan_editable_installs_for_worktree", unexpected)
    monkeypatch.setattr(git_module, "resolve_main_worktree", lambda _path: Path("/repo"))
    monkeypatch.setattr(misc_module, "resolve_remote_name", AsyncMock(return_value="origin"))
    remove_worktree = AsyncMock()
    monkeypatch.setattr(git_module, "remove_git_worktree", remove_worktree)

    result = json.loads(await merge_worktree(str(worktree), "dev"))

    assert result["success"] is False
    assert result["error"] == "AttributeError: boom"
    remove_worktree.assert_not_awaited()
