"""Integration tests — planner session write isolation via clone guard.

Validates the end-to-end flow:
  output_dir → write_watch_dirs → _has_write_scope → clone snapshot
  → contamination detection → selective revert
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from autoskillit.core.types import RetryReason, SkillResult, SubprocessResult, TerminationReason
from autoskillit.execution.clone_guard import (
    check_and_revert_clone_contamination,
    snapshot_clone_state,
)
from autoskillit.pipeline.audit import DefaultAuditLog

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _make_skill_result(success: bool, exit_code: int = 0) -> SkillResult:
    return SkillResult(
        success=success,
        result="test",
        session_id="test-session",
        subtype="" if success else "error",
        is_error=not success,
        exit_code=exit_code,
        needs_retry=False,
        retry_reason=RetryReason.NONE,
        stderr="",
        worktree_path=None,
    )


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with one committed source file."""
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@test.com")
    _git(tmp_path, "config", "user.name", "Test")
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.rs").write_text("fn main() {}")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "init")
    return tmp_path


@pytest.mark.anyio
async def test_planner_session_source_write_detected_and_reverted(git_repo: Path) -> None:
    """Contamination from a planner session (write-scoped, success) is detected and reverted.

    Simulates a planner session that:
    1. Has write_watch_dirs set (triggering _has_write_scope)
    2. Succeeds (so readonly_skill=False alone would skip detection)
    3. Accidentally modifies a git-tracked source file
    """

    class RealGitRunner:
        """Thin runner that delegates git commands to the real git binary."""

        async def __call__(
            self,
            cmd: list[str],
            *,
            cwd: Path,
            timeout: float,
            **_kwargs: object,
        ) -> SubprocessResult:
            result = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return SubprocessResult(
                returncode=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                termination=TerminationReason.NATURAL_EXIT,
                pid=0,
            )

    runner = RealGitRunner()
    cwd = str(git_repo)

    snapshot = await snapshot_clone_state(cwd, runner)  # type: ignore[arg-type]
    assert snapshot is not None

    (git_repo / "src" / "main.rs").write_text("fn main() { /* L0 was here */ }")

    audit = DefaultAuditLog()
    skill_result = _make_skill_result(success=True, exit_code=0)

    # _effective_readonly=True mirrors what headless/__init__.py computes when
    # _has_write_scope=True (write_watch_dirs is non-empty for planner sessions).
    _, reverted = await check_and_revert_clone_contamination(
        snapshot,
        skill_result,
        cwd,
        runner,  # type: ignore[arg-type]
        audit,
        skill_command="/autoskillit:planner-elaborate-wps",
        readonly_skill=True,
        exclude_prefix=".autoskillit/",
    )

    assert reverted, "Clone contamination should have been detected and reverted"

    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=git_repo,
        capture_output=True,
        text=True,
    )
    assert status.stdout.strip() == "", f"Working tree not clean after revert: {status.stdout!r}"

    records = audit.get_report_as_dicts()
    assert len(records) == 1
    assert records[0].get("subtype") == "clone_contamination"
