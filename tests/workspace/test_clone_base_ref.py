"""Tests for establishing bootstrap clones' local base-branch invariant."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import autoskillit.workspace.clone._refs as clone_refs
from autoskillit.core import ResolvedRef
from autoskillit.workspace.clone._refs import BaseBranchResolution, ensure_base_branch_local

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.medium]


def _git(repo_path: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo_path),
        check=check,
        capture_output=True,
        text=True,
    )


def _commit_sha(repo_path: Path, ref: str) -> str:
    return _git(repo_path, "rev-parse", f"{ref}^{{commit}}").stdout.strip()


def _ref_exists(repo_path: Path, ref: str) -> bool:
    return _git(repo_path, "show-ref", "--verify", "--quiet", ref, check=False).returncode == 0


def test_materializes_local_head_from_tracking_ref(
    remote_only_base_clone: tuple[Path, str],
) -> None:
    clone_path, _ = remote_only_base_clone

    resolution = ensure_base_branch_local(clone_path, "develop", "origin")

    assert resolution.created_local_branch is True
    assert resolution.resolved is not None
    assert resolution.resolved.qualified_ref == "refs/heads/develop"
    assert resolution.resolved.sha == _commit_sha(clone_path, "refs/remotes/origin/develop")
    assert _ref_exists(clone_path, "refs/heads/develop")


def test_local_head_present_is_left_untouched(
    remote_only_base_clone: tuple[Path, str],
) -> None:
    clone_path, _ = remote_only_base_clone
    _git(clone_path, "branch", "--no-track", "develop", "refs/remotes/origin/develop")
    original_sha = _commit_sha(clone_path, "refs/heads/develop")

    resolution = ensure_base_branch_local(clone_path, "develop", "origin")

    assert resolution.created_local_branch is False
    assert resolution.resolved is not None
    assert resolution.resolved.sha == original_sha
    assert _commit_sha(clone_path, "refs/heads/develop") == original_sha


def test_ambiguity_is_checked_even_when_local_head_pre_existed(
    remote_only_base_clone_with_shadow_tag: tuple[Path, str],
) -> None:
    clone_path, _ = remote_only_base_clone_with_shadow_tag
    _git(clone_path, "branch", "--no-track", "develop", "refs/remotes/origin/develop")

    resolution = ensure_base_branch_local(clone_path, "develop", "origin")

    assert resolution.resolved is None
    assert resolution.ambiguous_refs == ("refs/tags/develop",)


def test_neither_ref_present_fails_closed(
    remote_only_base_clone: tuple[Path, str],
) -> None:
    clone_path, _ = remote_only_base_clone

    resolution = ensure_base_branch_local(clone_path, "missing", "origin")

    assert resolution.failure_reason
    assert resolution.resolved is None
    assert resolution.created_local_branch is False
    assert not _ref_exists(clone_path, "refs/heads/missing")


def test_shadowing_tag_is_detected_and_fails_closed(
    remote_only_base_clone_with_shadow_tag: tuple[Path, str],
) -> None:
    clone_path, _ = remote_only_base_clone_with_shadow_tag

    resolution = ensure_base_branch_local(clone_path, "develop", "origin")

    assert resolution.ambiguous_refs == ("refs/tags/develop",)
    assert resolution.resolved is None


def test_branch_creation_collision_fails_closed(
    remote_only_base_clone: tuple[Path, str],
) -> None:
    clone_path, _ = remote_only_base_clone
    tracking_sha = _commit_sha(clone_path, "refs/remotes/origin/develop")
    _git(clone_path, "branch", "docs/legacy", tracking_sha)
    _git(clone_path, "update-ref", "refs/remotes/origin/docs", tracking_sha)

    resolution = ensure_base_branch_local(clone_path, "docs", "origin")

    assert resolution.resolved is None
    assert resolution.created_local_branch is False
    assert "cannot lock ref" in resolution.failure_reason
    assert not _ref_exists(clone_path, "refs/heads/docs")


def test_materialization_performs_no_network_access(
    remote_only_base_clone: tuple[Path, str],
) -> None:
    clone_path, upstream_url = remote_only_base_clone
    shutil.rmtree(upstream_url)

    resolution = ensure_base_branch_local(clone_path, "develop", "origin")

    assert resolution.resolved is not None
    assert resolution.resolved.sha == _commit_sha(clone_path, "refs/heads/develop")


def test_clone_local_probes_remotes_when_namespace_unknown(
    remote_only_base_clone: tuple[Path, str], tmp_path: Path
) -> None:
    clone_path, _ = remote_only_base_clone
    copy_path = tmp_path / "copytree-clone"
    shutil.copytree(clone_path, copy_path)
    tracking_sha = _commit_sha(copy_path, "refs/remotes/origin/develop")
    _git(copy_path, "remote", "remove", "origin")
    _git(copy_path, "remote", "remove", "upstream")
    _git(copy_path, "remote", "add", "src", copy_path.resolve().as_uri())
    _git(copy_path, "update-ref", "refs/remotes/src/develop", tracking_sha)

    resolution = ensure_base_branch_local(copy_path, "develop", "")

    assert _git(copy_path, "remote").stdout.splitlines() == ["src"]
    assert resolution.resolved is not None
    assert resolution.resolved.sha == tracking_sha
    assert _ref_exists(copy_path, "refs/heads/develop")


def test_resolution_never_raises_on_subprocess_failure(
    remote_only_base_clone: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    clone_path, _ = remote_only_base_clone

    def raise_timeout(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired(["git"], 10.0)

    monkeypatch.setattr(clone_refs.subprocess, "run", raise_timeout)

    resolution = ensure_base_branch_local(clone_path, "develop", "origin")

    assert isinstance(resolution, BaseBranchResolution)
    assert resolution.failure_reason
    assert resolution.resolved is None


def test_option_like_base_fails_closed_without_option_interpretation(
    remote_only_base_clone: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    clone_path, _ = remote_only_base_clone
    base_branch = "--show-origin"
    tracking_ref = f"refs/remotes/origin/{base_branch}"
    calls: list[list[str]] = []

    def fake_verify(_repo_path: Path, qualified_ref: str, *, timeout: float) -> ResolvedRef | None:
        if qualified_ref == tracking_ref:
            return ResolvedRef(
                category="remote_tracking",
                qualified_ref=tracking_ref,
                short_name=f"origin/{base_branch}",
                sha="a" * 40,
            )
        return None

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(command)
        return SimpleNamespace(returncode=1, stdout="", stderr="invalid branch name")

    monkeypatch.setattr(clone_refs, "verify_qualified_ref_sync", fake_verify)
    monkeypatch.setattr(clone_refs.subprocess, "run", fake_run)

    resolution = ensure_base_branch_local(clone_path, base_branch, "origin")

    assert resolution.failure_reason
    assert resolution.resolved is None
    assert ["git", "branch", "--no-track", "--", base_branch, tracking_ref] in calls
