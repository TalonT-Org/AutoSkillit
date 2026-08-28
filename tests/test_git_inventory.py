"""Focused tests for test-side Git-index inventory helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests import _git_inventory

pytestmark = pytest.mark.medium


def test_git_ls_files_uses_git_c_and_preserves_nul_delimited_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    observed: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed["command"] = command
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, stdout="first\0second\0")

    monkeypatch.setattr(_git_inventory.subprocess, "run", fake_run)

    assert _git_inventory.git_ls_files(tmp_path, "one", "two") == ("first", "second")
    assert observed["command"] == [
        "git",
        "-C",
        str(tmp_path),
        "ls-files",
        "-z",
        "--",
        "one",
        "two",
    ]
    assert observed["kwargs"] == {
        "capture_output": True,
        "text": True,
        "check": True,
        "timeout": 10,
    }


def test_git_ls_files_requires_nonempty_inventory_unless_allowed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        _git_inventory.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, stdout=""),
    )

    with pytest.raises(RuntimeError, match=r"no tracked paths.*'skills'"):
        _git_inventory.git_ls_files(tmp_path, "skills")

    assert _git_inventory.git_ls_files(tmp_path, "skills", allow_empty=True) == ()


@pytest.mark.parametrize(
    "failure",
    (
        OSError("git unavailable"),
        subprocess.CalledProcessError(1, ["git", "ls-files"], stderr="bad repository"),
        subprocess.TimeoutExpired(["git", "ls-files"], 10),
    ),
)
def test_git_ls_files_wraps_subprocess_failures_with_pathspec_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: BaseException,
) -> None:
    def raise_failure(*args: object, **kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(_git_inventory.subprocess, "run", raise_failure)

    with pytest.raises(RuntimeError, match=r"pathspec\(s\).*'recipes'") as error:
        _git_inventory.git_ls_files(tmp_path, "recipes")

    assert error.value.__cause__ is failure
    if isinstance(failure, subprocess.CalledProcessError):
        assert failure.stderr in str(error.value)
