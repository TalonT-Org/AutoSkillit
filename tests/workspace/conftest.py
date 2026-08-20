"""Shared fixtures for tests/workspace/."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import cast

import pytest

from autoskillit.workspace.session_skills import (
    DefaultSessionSkillManager,
    SkillsDirectoryProvider,
)

_UNSET = object()


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

    def _factory(
        *,
        ephemeral_root: Path | None = None,
        codex_root: Path | None | object = _UNSET,
    ) -> DefaultSessionSkillManager:
        provider = SkillsDirectoryProvider()
        if codex_root is None:
            persistent_roots: dict[str, Path] = {}
        elif codex_root is _UNSET:
            persistent_roots = {"codex": tmp_path / "codex-root"}
        else:
            persistent_roots = {"codex": cast(Path, codex_root)}
        return DefaultSessionSkillManager(
            provider,
            ephemeral_root=ephemeral_root or tmp_path,
            persistent_roots=persistent_roots,
        )

    return _factory


@pytest.fixture
def codex_env():
    """Codex backend mock for delegation-contract tests."""
    from tests.workspace._helpers import _make_codex_backend

    backend = _make_codex_backend()

    return type(
        "CodexEnv",
        (),
        {
            "backend": backend,
        },
    )()


@pytest.fixture
def evidence_cache(monkeypatch):
    import autoskillit.workspace.skill_capabilities as capabilities

    cache = capabilities._SkillCapabilityEvidenceCache(
        max_entries=32,
        max_bytes=1024 * 1024,
        max_input_bytes=64 * 1024,
    )
    monkeypatch.setattr(capabilities, "_SKILL_CAPABILITY_EVIDENCE_CACHE", cache)
    return cache


@pytest.fixture
def make_evidence_cache(monkeypatch):
    """Factory fixture for tests that need a non-default cache size."""

    def _factory(*, max_entries: int) -> object:
        import autoskillit.workspace.skill_capabilities as capabilities

        cache = capabilities._SkillCapabilityEvidenceCache(
            max_entries=max_entries,
            max_bytes=1024 * 1024,
            max_input_bytes=64 * 1024,
        )
        monkeypatch.setattr(capabilities, "_SKILL_CAPABILITY_EVIDENCE_CACHE", cache)
        return cache

    return _factory


@pytest.fixture
def scan_calls(monkeypatch):
    import autoskillit.workspace.skill_capabilities as capabilities

    calls: list[tuple[str, str]] = []
    original = capabilities._scan_skill_capability_evidence_uncached

    def recording_scanner(content: str, effective_name: str):
        calls.append((content, effective_name))
        return original(content, effective_name)

    monkeypatch.setattr(
        capabilities,
        "_scan_skill_capability_evidence_uncached",
        recording_scanner,
    )
    return calls
