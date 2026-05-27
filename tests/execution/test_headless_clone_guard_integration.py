"""Integration tests — policy-based clone guard replaces _effective_readonly."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from autoskillit.core.types import RetryReason, SkillResult, SubprocessResult, TerminationReason
from autoskillit.execution.clone_guard import (
    build_clone_guard_policy,
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


class RealGitRunner:
    async def __call__(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        timeout: float,
        **_kwargs: object,
    ) -> SubprocessResult:
        result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return SubprocessResult(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            termination=TerminationReason.NATURAL_EXIT,
            pid=0,
        )


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
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
async def test_resolve_failures_commits_survive_on_success(git_repo: Path) -> None:
    """resolve-failures commits survive when success=True (clone-commit skill)."""
    runner = RealGitRunner()
    cwd = str(git_repo)
    snapshot = await snapshot_clone_state(cwd, runner)  # type: ignore[arg-type]
    assert snapshot is not None

    (git_repo / "src" / "main.rs").write_text("fn main() { /* fixed */ }")
    _git(git_repo, "add", ".")
    _git(git_repo, "commit", "-m", "fix: resolve failure")

    policy = build_clone_guard_policy(
        readonly_skill=False,
        has_write_scope=True,
        is_clone_commit=True,
        is_worktree=False,
    )
    _, reverted = await check_and_revert_clone_contamination(
        snapshot,
        _make_skill_result(success=True),
        cwd,
        runner,  # type: ignore[arg-type]
        DefaultAuditLog(),
        skill_command="/autoskillit:resolve-failures",
        policy=policy,
    )
    assert not reverted, "resolve-failures commits should survive on success"

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=git_repo,
        capture_output=True,
        text=True,
    )
    assert head.stdout.strip() != snapshot.head_sha


@pytest.mark.anyio
async def test_planner_commits_reverted_on_success(git_repo: Path) -> None:
    """planner-elaborate-wps commits are reverted on success (write-scoped, non-clone-commit)."""
    runner = RealGitRunner()
    cwd = str(git_repo)
    snapshot = await snapshot_clone_state(cwd, runner)  # type: ignore[arg-type]
    assert snapshot is not None

    (git_repo / "src" / "main.rs").write_text("fn main() { /* planner was here */ }")

    policy = build_clone_guard_policy(
        readonly_skill=False,
        has_write_scope=True,
        is_clone_commit=False,
        is_worktree=False,
    )
    _, reverted = await check_and_revert_clone_contamination(
        snapshot,
        _make_skill_result(success=True),
        cwd,
        runner,  # type: ignore[arg-type]
        DefaultAuditLog(),
        skill_command="/autoskillit:planner-elaborate-wps",
        policy=policy,
    )
    assert reverted, "Planner contamination should be detected and reverted"

    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=git_repo,
        capture_output=True,
        text=True,
    )
    assert status.stdout.strip() == ""


@pytest.mark.anyio
async def test_planner_excluded_write_scope_not_reverted_on_external_head_advance(
    git_repo: Path,
) -> None:
    """Planner session: writes under .autoskillit/ + external HEAD advance = no revert."""
    runner = RealGitRunner()
    cwd = str(git_repo)

    policy = build_clone_guard_policy(
        readonly_skill=True,
        has_write_scope=True,
        is_clone_commit=False,
        is_worktree=False,
        writes_under_exclude=True,
    )
    assert policy.should_fire(success=True) is False

    snapshot = await snapshot_clone_state(cwd, runner)  # type: ignore[arg-type]
    assert snapshot is not None

    # External HEAD advance (simulates Cursor editor sync)
    (git_repo / "src" / "main.rs").write_text("fn main() { /* external edit */ }")
    _git(git_repo, "add", ".")
    _git(git_repo, "commit", "-m", "external: cursor sync")

    # Session writes under .autoskillit/ only
    temp_dir = git_repo / ".autoskillit" / "temp" / "planner"
    temp_dir.mkdir(parents=True)
    (temp_dir / "output.json").write_text("{}")

    result, reverted = await check_and_revert_clone_contamination(
        snapshot,
        _make_skill_result(success=True),
        cwd,
        runner,  # type: ignore[arg-type]
        DefaultAuditLog(),
        skill_command="/autoskillit:planner-elaborate-wps",
        policy=policy,
        exclude_prefix=".autoskillit/",
    )
    assert result.success is True
    assert not reverted, "Guard should not fire when writes_under_exclude=True"
