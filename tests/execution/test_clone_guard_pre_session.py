"""Tests for pre-session index validation and selective-revert staged-entry fix."""

from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from autoskillit.core.types import SubprocessResult, TerminationReason
from autoskillit.execution.clone_guard import (
    CloneSnapshot,
    ContaminationReport,
    revert_contamination,
    validate_pre_session_index,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@test.local",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@test.local",
}


def _init_git_repo(repo_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True, env=_GIT_ENV)
    subprocess.run(
        ["git", "config", "user.email", "test@test.local"],
        cwd=repo_path,
        check=True,
        capture_output=True,
        env=_GIT_ENV,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo_path,
        check=True,
        capture_output=True,
        env=_GIT_ENV,
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=repo_path,
        check=True,
        capture_output=True,
        env=_GIT_ENV,
    )


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(args, cwd=cwd, capture_output=True, env=_GIT_ENV)


def _git_status(cwd: Path) -> str:
    result = _git(["git", "status", "--porcelain"], cwd)
    return result.stdout.decode().strip()


def _head_sha(cwd: Path) -> str:
    result = _git(["git", "rev-parse", "HEAD"], cwd)
    return result.stdout.decode().strip()


class _RealAsyncRunner:
    """Async SubprocessRunner backed by asyncio.create_subprocess_exec."""

    async def __call__(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        timeout: float,
        env: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> SubprocessResult:
        run_env = dict(env) if env is not None else _GIT_ENV
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=run_env,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except TimeoutError:
            proc.kill()
            await proc.communicate()
            return SubprocessResult(
                returncode=-1,
                stdout="",
                stderr="timeout",
                termination=TerminationReason.TIMED_OUT,
                pid=proc.pid,
            )
        return SubprocessResult(
            returncode=proc.returncode if proc.returncode is not None else -1,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
            termination=TerminationReason.NATURAL_EXIT,
            pid=proc.pid,
        )


@pytest.mark.anyio
async def test_validate_pre_session_index_detects_dirty_index(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "somefile.py").write_text("original")
    _git(["git", "add", "somefile.py"], repo)
    _git(["git", "commit", "-m", "add file"], repo)
    (repo / "somefile.py").write_text("modified")
    _git(["git", "add", "somefile.py"], repo)

    runner = _RealAsyncRunner()
    result = await validate_pre_session_index(str(repo), runner)

    assert result is True


@pytest.mark.anyio
async def test_validate_pre_session_index_clean_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    runner = _RealAsyncRunner()
    result = await validate_pre_session_index(str(repo), runner)

    assert result is False


@pytest.mark.anyio
async def test_validate_pre_session_index_clears_staged_only_entries(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "evil.py").write_text("contamination")
    _git(["git", "add", "evil.py"], repo)

    runner = _RealAsyncRunner()
    await validate_pre_session_index(str(repo), runner)

    assert _git_status(repo) == ""


@pytest.mark.anyio
async def test_validate_pre_session_index_clears_staged_and_modified(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "tracked.py").write_text("original")
    _git(["git", "add", "tracked.py"], repo)
    _git(["git", "commit", "-m", "add tracked"], repo)
    (repo / "tracked.py").write_text("modified staged")
    _git(["git", "add", "tracked.py"], repo)
    (repo / "tracked.py").write_text("also unstaged change")
    (repo / "new_untracked.py").write_text("new file")

    runner = _RealAsyncRunner()
    await validate_pre_session_index(str(repo), runner)

    assert _git_status(repo) == ""


@pytest.mark.anyio
async def test_validate_pre_session_index_preserves_autoskillit_temp(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    autoskillit_dir = repo / ".autoskillit" / "temp"
    autoskillit_dir.mkdir(parents=True)
    (autoskillit_dir / "output.json").write_text("{}")
    _git(["git", "add", ".autoskillit/"], repo)

    runner = _RealAsyncRunner()
    result = await validate_pre_session_index(str(repo), runner)

    assert result is False
    # The .autoskillit/ file remains staged
    status = _git(["git", "status", "--porcelain"], repo).stdout.decode()
    assert ".autoskillit" in status


@pytest.mark.anyio
async def test_validate_pre_session_index_handles_no_commits_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, env=_GIT_ENV)
    subprocess.run(
        ["git", "config", "user.email", "test@test.local"],
        cwd=repo,
        check=True,
        capture_output=True,
        env=_GIT_ENV,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo,
        check=True,
        capture_output=True,
        env=_GIT_ENV,
    )
    (repo / "staged.py").write_text("staged content")
    _git(["git", "add", "staged.py"], repo)

    runner = _RealAsyncRunner()
    result = await validate_pre_session_index(str(repo), runner)

    assert result is True
    assert _git_status(repo) == ""


@pytest.mark.anyio
async def test_selective_revert_clears_staged_only_entries(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "somefile.py").write_text("original")
    _git(["git", "add", "somefile.py"], repo)
    _git(["git", "commit", "-m", "add file"], repo)
    sha = _head_sha(repo)
    (repo / "somefile.py").write_text("modified")
    _git(["git", "add", "somefile.py"], repo)

    snapshot = CloneSnapshot(head_sha=sha)
    report = ContaminationReport(
        pre_sha=sha,
        post_sha=sha,
        uncommitted_files=["M  somefile.py"],
        direct_commits=False,
        reverted=False,
    )
    runner = _RealAsyncRunner()
    result = await revert_contamination(snapshot, report, str(repo), runner, selective=True)

    assert result.reverted
    assert _git_status(repo) == ""


@pytest.mark.anyio
async def test_selective_revert_clears_staged_and_uncommitted(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "a.py").write_text("original a")
    (repo / "b.py").write_text("original b")
    _git(["git", "add", "."], repo)
    _git(["git", "commit", "-m", "add files"], repo)
    sha = _head_sha(repo)
    (repo / "a.py").write_text("modified a staged")
    _git(["git", "add", "a.py"], repo)
    (repo / "b.py").write_text("modified b unstaged")

    snapshot = CloneSnapshot(head_sha=sha)
    report = ContaminationReport(
        pre_sha=sha,
        post_sha=sha,
        uncommitted_files=["M  a.py", " M b.py"],
        direct_commits=False,
        reverted=False,
    )
    runner = _RealAsyncRunner()
    result = await revert_contamination(snapshot, report, str(repo), runner, selective=True)

    assert result.reverted
    assert _git_status(repo) == ""


@pytest.mark.anyio
async def test_cross_session_contamination_blocked(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    (repo / "evil.py").write_text("contamination")
    _git(["git", "add", "evil.py"], repo)

    runner = _RealAsyncRunner()
    await validate_pre_session_index(str(repo), runner)

    assert _git_status(repo) == ""
    assert not (repo / "evil.py").exists()
    assert _git(["git", "ls-files", "--error-unmatch", "evil.py"], repo).returncode != 0
