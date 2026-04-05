"""Tests for clone contamination guard — detect and revert direct changes."""

from __future__ import annotations

import json

import pytest

from autoskillit.core.types import (
    RetryReason,
    SkillResult,
    SubprocessResult,
    TerminationReason,
)
from autoskillit.execution.clone_guard import (
    CloneSnapshot,
    check_and_revert_clone_contamination,
    detect_contamination,
    is_worktree_skill,
    revert_contamination,
    snapshot_clone_state,
)
from autoskillit.execution.headless import _build_skill_result
from autoskillit.pipeline.audit import DefaultAuditLog
from tests.conftest import MockSubprocessRunner


def _git_result(stdout: str = "", returncode: int = 0) -> SubprocessResult:
    """Build a minimal SubprocessResult simulating a git command."""
    return SubprocessResult(
        returncode=returncode,
        stdout=stdout,
        stderr="",
        termination=TerminationReason.NATURAL_EXIT,
        pid=99999,
    )


def _make_skill_result(
    success: bool = False,
    needs_retry: bool = True,
    worktree_path: str | None = None,
    exit_code: int = 1,
) -> SkillResult:
    """Build a minimal SkillResult for guard tests."""
    return SkillResult(
        success=success,
        result="test result",
        session_id="test-session",
        subtype="error",
        is_error=not success,
        exit_code=exit_code,
        needs_retry=needs_retry,
        retry_reason=RetryReason.RESUME if needs_retry else RetryReason.NONE,
        stderr="",
        worktree_path=worktree_path,
    )


# ---------------------------------------------------------------------------
# T1: is_worktree_skill positive cases
# ---------------------------------------------------------------------------
class TestIsWorktreeSkillPositive:
    def test_slash_command_implement(self):
        assert is_worktree_skill("/autoskillit:implement-worktree-no-merge /path/to/plan.md")

    def test_slash_command_retry(self):
        assert is_worktree_skill("/autoskillit:retry-worktree /path/to/worktree")

    def test_bare_name(self):
        assert is_worktree_skill("implement-worktree-no-merge")

    def test_full_path(self):
        assert is_worktree_skill(
            "/path/to/skills_extended/implement-worktree-no-merge/SKILL.md some args"
        )


# ---------------------------------------------------------------------------
# T2: is_worktree_skill negative cases
# ---------------------------------------------------------------------------
class TestIsWorktreeSkillNegative:
    def test_investigate(self):
        assert not is_worktree_skill("/autoskillit:investigate")

    def test_make_plan(self):
        assert not is_worktree_skill("/autoskillit:make-plan")

    def test_open_pr(self):
        assert not is_worktree_skill("/autoskillit:open-pr")

    def test_implement_worktree_without_no_merge(self):
        assert not is_worktree_skill("implement-worktree")


# ---------------------------------------------------------------------------
# T3: snapshot_clone_state captures SHA
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_snapshot_clone_state_captures_sha():
    runner = MockSubprocessRunner()
    runner.push(_git_result(stdout="abc123\n"))
    snapshot = await snapshot_clone_state("/tmp/clone", runner)
    assert snapshot is not None
    assert snapshot.head_sha == "abc123"


# ---------------------------------------------------------------------------
# T4: snapshot_clone_state returns None on failure
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_snapshot_clone_state_returns_none_on_failure():
    runner = MockSubprocessRunner()
    runner.push(_git_result(returncode=128))
    snapshot = await snapshot_clone_state("/tmp/clone", runner)
    assert snapshot is None


# ---------------------------------------------------------------------------
# T5: detect_contamination — uncommitted changes
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_detect_contamination_uncommitted_changes():
    runner = MockSubprocessRunner()
    runner.push(_git_result(stdout="abc123\n"))  # git rev-parse HEAD (same)
    runner.push(_git_result(stdout=" M src/main.py\n?? new_file.txt\n"))  # git status
    snapshot = CloneSnapshot(head_sha="abc123")
    report = await detect_contamination(snapshot, "/tmp/clone", runner)
    assert report is not None
    assert len(report.uncommitted_files) == 2
    assert not report.direct_commits


# ---------------------------------------------------------------------------
# T6: detect_contamination — direct commits
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_detect_contamination_direct_commits():
    runner = MockSubprocessRunner()
    runner.push(_git_result(stdout="def456\n"))  # HEAD moved
    runner.push(_git_result(stdout=""))  # clean status
    snapshot = CloneSnapshot(head_sha="abc123")
    report = await detect_contamination(snapshot, "/tmp/clone", runner)
    assert report is not None
    assert report.direct_commits
    assert len(report.uncommitted_files) == 0


# ---------------------------------------------------------------------------
# T7: detect_contamination — both
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_detect_contamination_both():
    runner = MockSubprocessRunner()
    runner.push(_git_result(stdout="def456\n"))  # HEAD moved
    runner.push(_git_result(stdout=" M dirty.py\n"))  # dirty
    snapshot = CloneSnapshot(head_sha="abc123")
    report = await detect_contamination(snapshot, "/tmp/clone", runner)
    assert report is not None
    assert report.direct_commits
    assert len(report.uncommitted_files) == 1


# ---------------------------------------------------------------------------
# T8: detect_contamination — clean
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_detect_contamination_clean():
    runner = MockSubprocessRunner()
    runner.push(_git_result(stdout="abc123\n"))  # same HEAD
    runner.push(_git_result(stdout=""))  # clean status
    snapshot = CloneSnapshot(head_sha="abc123")
    report = await detect_contamination(snapshot, "/tmp/clone", runner)
    assert report is None


# ---------------------------------------------------------------------------
# T9: revert_contamination — uncommitted only
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_revert_uncommitted_only():
    runner = MockSubprocessRunner()
    snapshot = CloneSnapshot(head_sha="abc123")
    from autoskillit.execution.clone_guard import ContaminationReport

    report = ContaminationReport(
        pre_sha="abc123",
        post_sha="abc123",
        uncommitted_files=[" M src/main.py"],
        direct_commits=False,
        reverted=False,
    )
    result = await revert_contamination(snapshot, report, "/tmp/clone", runner)
    assert result.reverted
    cmds = [call[0] for call in runner.call_args_list]
    assert ["git", "reset", "--hard", "abc123"] in cmds
    assert ["git", "clean", "-fd"] in cmds


# ---------------------------------------------------------------------------
# T10: revert_contamination — direct commits
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_revert_direct_commits():
    runner = MockSubprocessRunner()
    snapshot = CloneSnapshot(head_sha="abc123")
    from autoskillit.execution.clone_guard import ContaminationReport

    report = ContaminationReport(
        pre_sha="abc123",
        post_sha="def456",
        uncommitted_files=[],
        direct_commits=True,
        reverted=False,
    )
    result = await revert_contamination(snapshot, report, "/tmp/clone", runner)
    assert result.reverted
    cmds = [call[0] for call in runner.call_args_list]
    assert ["git", "reset", "--hard", "abc123"] in cmds
    assert ["git", "clean", "-fd"] in cmds


# ---------------------------------------------------------------------------
# T11: guard full flow — contamination detected
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_guard_full_flow_contamination_detected():
    runner = MockSubprocessRunner()
    # detect_contamination: rev-parse HEAD (moved), status (dirty)
    runner.push(_git_result(stdout="def456\n"))
    runner.push(_git_result(stdout=" M file.py\n"))
    # revert_contamination: reset, clean (defaults are rc=0)

    snapshot = CloneSnapshot(head_sha="abc123")
    skill_result = _make_skill_result(success=False, worktree_path=None)
    audit = DefaultAuditLog()

    result, reverted = await check_and_revert_clone_contamination(
        snapshot,
        skill_result,
        "/tmp/clone",
        runner,
        audit,
        skill_command="/autoskillit:implement-worktree-no-merge plan.md",
    )
    assert reverted
    assert len(audit.get_report()) == 1
    record = audit.get_report()[0]
    assert record.subtype == "clone_contamination"
    assert "pre_sha=abc123" in record.stderr
    assert "post_sha=def456" in record.stderr


# ---------------------------------------------------------------------------
# T12: guard skipped when success
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_guard_skipped_when_success():
    runner = MockSubprocessRunner()
    snapshot = CloneSnapshot(head_sha="abc123")
    skill_result = _make_skill_result(success=True, worktree_path=None)

    result, reverted = await check_and_revert_clone_contamination(
        snapshot, skill_result, "/tmp/clone", runner, None
    )
    assert not reverted
    assert len(runner.call_args_list) == 0


# ---------------------------------------------------------------------------
# T13: guard skipped when worktree created
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_guard_skipped_when_worktree_created():
    runner = MockSubprocessRunner()
    snapshot = CloneSnapshot(head_sha="abc123")
    skill_result = _make_skill_result(success=False, worktree_path="/some/worktree")

    result, reverted = await check_and_revert_clone_contamination(
        snapshot, skill_result, "/tmp/clone", runner, None
    )
    assert not reverted
    assert len(runner.call_args_list) == 0


# ---------------------------------------------------------------------------
# T14: guard skipped when no snapshot
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_guard_skipped_when_no_snapshot():
    runner = MockSubprocessRunner()
    skill_result = _make_skill_result(success=False, worktree_path=None)

    result, reverted = await check_and_revert_clone_contamination(
        None, skill_result, "/tmp/clone", runner, None
    )
    assert not reverted
    assert len(runner.call_args_list) == 0


# ---------------------------------------------------------------------------
# T15: worktree_path always extracted (even when needs_retry=False)
# ---------------------------------------------------------------------------
def test_worktree_path_always_extracted():
    """worktree_path should be extracted regardless of needs_retry status."""
    assistant = json.dumps(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": "worktree_path = /tmp/wt\nbranch_name = impl-test",
            },
        }
    )
    result_json = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "result": "Task completed.",
            "session_id": "test-session",
            "is_error": False,
        }
    )
    stdout = f"{assistant}\n{result_json}\n"
    sr = SubprocessResult(
        returncode=0,
        stdout=stdout,
        stderr="",
        termination=TerminationReason.NATURAL_EXIT,
        pid=12345,
    )
    skill = _build_skill_result(sr)
    assert skill.worktree_path == "/tmp/wt"


# ---------------------------------------------------------------------------
# T16: audit log records contamination
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_audit_log_records_contamination():
    runner = MockSubprocessRunner()
    runner.push(_git_result(stdout="def456\n"))
    runner.push(_git_result(stdout=" M a.py\n M b.py\n"))

    snapshot = CloneSnapshot(head_sha="abc123")
    skill_result = _make_skill_result(success=False, worktree_path=None)
    audit = DefaultAuditLog()

    await check_and_revert_clone_contamination(
        snapshot,
        skill_result,
        "/tmp/clone",
        runner,
        audit,
        skill_command="/autoskillit:implement-worktree-no-merge plan.md",
    )

    records = audit.get_report()
    assert len(records) == 1
    record = records[0]
    assert record.subtype == "clone_contamination"
    assert record.retry_reason == RetryReason.CLONE_CONTAMINATION.value
    assert "pre_sha=abc123" in record.stderr
    assert "post_sha=def456" in record.stderr
    assert "files=2" in record.stderr
    assert "direct_commits=True" in record.stderr
