"""Tests for qualified Git reference resolution."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import autoskillit.core.git.git_refs as git_refs
from autoskillit.core.git.git_refs import (
    ResolvedRef,
    local_branch_ref,
    remote_tracking_ref,
    verify_qualified_ref_sync,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.medium]


def _commit_sha(repo_path: Path, ref: str) -> str:
    return subprocess.run(
        ["git", "rev-parse", f"{ref}^{{commit}}"],
        cwd=str(repo_path),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_verify_qualified_ref_resolves_local_branch(
    remote_only_base_clone: tuple[Path, str],
) -> None:
    clone_path, _ = remote_only_base_clone

    assert verify_qualified_ref_sync(clone_path, "refs/heads/feat") == ResolvedRef(
        category="local_branch",
        qualified_ref="refs/heads/feat",
        short_name="feat",
        sha=_commit_sha(clone_path, "refs/heads/feat"),
    )


def test_verify_qualified_ref_resolves_remote_tracking(
    remote_only_base_clone: tuple[Path, str],
) -> None:
    clone_path, _ = remote_only_base_clone
    qualified_ref = "refs/remotes/origin/develop"

    assert verify_qualified_ref_sync(clone_path, qualified_ref) == ResolvedRef(
        category="remote_tracking",
        qualified_ref=qualified_ref,
        short_name="origin/develop",
        sha=_commit_sha(clone_path, qualified_ref),
    )


def test_verify_qualified_ref_returns_none_for_absent_ref(
    remote_only_base_clone: tuple[Path, str],
) -> None:
    clone_path, _ = remote_only_base_clone

    assert verify_qualified_ref_sync(clone_path, "refs/heads/develop") is None


def test_verify_qualified_ref_rejects_bare_name(
    remote_only_base_clone: tuple[Path, str],
) -> None:
    clone_path, _ = remote_only_base_clone

    with pytest.raises(ValueError, match="qualified_ref must start with 'refs/'"):
        verify_qualified_ref_sync(clone_path, "develop")


def test_verify_qualified_ref_rejects_option_injection(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fake_run(*args: object, **kwargs: object) -> SimpleNamespace:
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=1, stdout="")

    monkeypatch.setattr(git_refs.subprocess, "run", fake_run)

    assert (
        git_refs.verify_qualified_ref_sync(Path("/repo"), "refs/heads/--upload-pack=evil") is None
    )
    assert calls[0][0] == (
        [
            "git",
            "rev-parse",
            "--verify",
            "--end-of-options",
            "refs/heads/--upload-pack=evil^{commit}",
        ],
    )


def test_verify_qualified_ref_peels_annotated_tag_to_commit(
    remote_only_base_clone_with_shadow_tag: tuple[Path, str],
) -> None:
    clone_path, _ = remote_only_base_clone_with_shadow_tag

    assert verify_qualified_ref_sync(clone_path, "refs/tags/develop") == ResolvedRef(
        category="tag",
        qualified_ref="refs/tags/develop",
        short_name="develop",
        sha=_commit_sha(clone_path, "refs/heads/feat"),
    )


def test_ref_builders_are_exact() -> None:
    assert local_branch_ref("x") == "refs/heads/x"
    assert remote_tracking_ref("o", "x") == "refs/remotes/o/x"


def test_verify_qualified_ref_argv_is_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fake_run(*args: object, **kwargs: object) -> SimpleNamespace:
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=1, stdout="")

    monkeypatch.setattr(git_refs.subprocess, "run", fake_run)

    assert git_refs.verify_qualified_ref_sync(Path("/repo"), "refs/heads/x") is None
    assert calls[0][0] == (
        ["git", "rev-parse", "--verify", "--end-of-options", "refs/heads/x^{commit}"],
    )


def test_verify_qualified_ref_never_raises_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_timeout(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired(["git"], 10.0)

    monkeypatch.setattr(git_refs.subprocess, "run", raise_timeout)

    assert git_refs.verify_qualified_ref_sync(Path("/repo"), "refs/heads/x") is None


def test_verify_qualified_ref_never_raises_on_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_oserror(*_args: object, **_kwargs: object) -> None:
        raise OSError("git is unavailable")

    monkeypatch.setattr(git_refs.subprocess, "run", raise_oserror)

    assert git_refs.verify_qualified_ref_sync(Path("/repo"), "refs/heads/x") is None
