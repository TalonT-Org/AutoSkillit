"""Shared fixtures for tests/workspace/."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from autoskillit.core import BackendCapabilities
from autoskillit.workspace.session_skills import (
    DefaultSessionSkillManager,
    SkillsDirectoryProvider,
)

_CODEX_CAPABILITIES = BackendCapabilities(
    channel_b_capable=False,
    pty_required=False,
    session_resume_capable=True,
    skill_injection_capable=True,
    supports_thinking_blocks=False,
    supports_claude_format_stdout=False,
    exit_code_is_terminal=True,
    mcp_config_capable=True,
    food_truck_capable=True,
    completion_record_types=frozenset({"turn.completed", "turn.failed", "error"}),
    session_record_types=frozenset({"item.completed"}),
    required_session_files=frozenset({"config.toml"}),
    session_dir_symlinks=frozenset({"auth.json", "sessions"}),
    skills_subdir="skills",
)


@pytest.fixture
def bare_remote(tmp_path: Path) -> Path:
    """Create a bare git remote (simulates GitHub/origin)."""
    remote = tmp_path / "remote.git"
    remote.mkdir()
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    return remote


@pytest.fixture
def local_with_remote(tmp_path: Path, bare_remote: Path) -> Path:
    """Local repo with origin configured, main pushed, feature/local-only unpublished."""
    local = tmp_path / "local"
    local.mkdir()
    subprocess.run(["git", "init", str(local)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(local), "config", "user.email", "t@t.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(local), "config", "user.name", "T"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(local), "remote", "add", "origin", str(bare_remote)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(local), "commit", "--allow-empty", "-m", "init"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(local), "branch", "-M", "main"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(local), "push", "-u", "origin", "main"],
        check=True,
        capture_output=True,
    )
    # Create local-only branch (never pushed to origin)
    subprocess.run(
        ["git", "-C", str(local), "checkout", "-b", "feature/local-only"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(local), "commit", "--allow-empty", "-m", "local"],
        check=True,
        capture_output=True,
    )
    return local


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with one empty commit.

    Returns tmp_path / 'repo' (a subdirectory) so that clone_repo output lands at
    tmp_path / 'autoskillit-runs' — inside the test's isolated tmp_path boundary.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@test.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "init"],
        check=True,
        capture_output=True,
    )
    return repo


@pytest.fixture
def make_session_skill_manager(tmp_path: Path):
    """Factory fixture returning a DefaultSessionSkillManager."""

    def _factory(*, ephemeral_root: Path | None = None) -> DefaultSessionSkillManager:
        provider = SkillsDirectoryProvider()
        return DefaultSessionSkillManager(provider, ephemeral_root=ephemeral_root or tmp_path)

    return _factory
