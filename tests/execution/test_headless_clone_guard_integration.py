"""Integration tests — output_dir == cwd path through clone guard logic."""

from __future__ import annotations

import pytest

from autoskillit.execution.clone_guard import (
    CloneSnapshot,
    build_clone_guard_policy,
    check_and_revert_clone_contamination,
    derive_exclude_prefix,
)
from tests.fakes import MockSubprocessRunner

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _git_result(stdout: str = "", returncode: int = 0):
    from autoskillit.core.types import SubprocessResult, TerminationReason

    return SubprocessResult(
        returncode=returncode,
        stdout=stdout,
        stderr="",
        termination=TerminationReason.NATURAL_EXIT,
        pid=99999,
    )


def _make_skill_result(success: bool = True, exit_code: int = 0):
    from autoskillit.core.types import RetryReason, SkillResult

    return SkillResult(
        success=success,
        result="test",
        session_id="test-session",
        subtype="success" if success else "error",
        is_error=not success,
        exit_code=exit_code,
        needs_retry=False,
        retry_reason=RetryReason.NONE,
        stderr="",
    )


@pytest.mark.anyio
async def test_output_dir_equals_cwd_no_crash_and_guard_suppressed(tmp_path):
    """output_dir == cwd: derive_exclude_prefix returns None, guard suppressed on success."""
    # Step 1: derive_exclude_prefix([cwd], cwd) must return None, not crash with IndexError.
    prefix = derive_exclude_prefix([tmp_path], tmp_path)
    assert prefix is None

    # Step 2: _output_dir_is_cwd = True → writes_under_exclude = True.
    _output_dir_is_cwd = tmp_path == tmp_path
    _writes_under_exclude = _output_dir_is_cwd

    policy = build_clone_guard_policy(
        readonly_skill=False,
        has_write_scope=True,
        is_clone_commit=False,
        is_worktree=False,
        writes_under_exclude=_writes_under_exclude,
    )

    # Step 3: guard must not fire on success.
    assert policy.should_fire(success=True) is False

    # Step 4-5: with snapshot present, guard must not fire on success — writes preserved.
    runner = MockSubprocessRunner()
    snapshot = CloneSnapshot(head_sha="abc123")
    skill_result = _make_skill_result(success=True)

    result, reverted = await check_and_revert_clone_contamination(
        snapshot,
        skill_result,
        str(tmp_path),
        runner,
        None,
        policy=policy,
        exclude_prefix=".autoskillit/",
    )
    assert not reverted
    assert len(runner.call_args_list) == 0
