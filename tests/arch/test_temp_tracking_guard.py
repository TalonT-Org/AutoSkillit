"""Prevent generated AutoSkillit session artifacts from becoming tracked files."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Literal

import pytest

from tests._git_inventory import git_ls_files

pytestmark = [pytest.mark.layer("arch"), pytest.mark.medium]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEMP_PATH_ALLOWLIST: dict[str, str] = {}
_TEMP_PATHSPEC = ".autoskillit/temp"
_STAGED_TEMP_GUARD = _REPO_ROOT / "scripts" / "check_staged_autoskillit_temp.sh"


def test_no_autoskillit_temp_artifacts_are_tracked() -> None:
    tracked_paths = git_ls_files(_REPO_ROOT, _TEMP_PATHSPEC, allow_empty=True)
    unexpected = sorted(set(tracked_paths).difference(_TEMP_PATH_ALLOWLIST))

    assert not unexpected, (
        "Tracked .autoskillit/temp artifacts are not permitted: "
        f"{unexpected}. Add only deliberate fixtures to _TEMP_PATH_ALLOWLIST with a "
        "justification."
    )


def test_autoskillit_temp_allowlist_has_no_stale_entries() -> None:
    tracked_paths = set(git_ls_files(_REPO_ROOT))
    stale = sorted(set(_TEMP_PATH_ALLOWLIST).difference(tracked_paths))

    assert not stale, (
        "_TEMP_PATH_ALLOWLIST entries are no longer tracked: "
        f"{stale}. Remove their obsolete allowlist entries."
    )


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _commit(repo: Path, message: str) -> None:
    _run_git(
        repo,
        "-c",
        "user.name=AutoSkillit Test",
        "-c",
        "user.email=autoskillit-test@example.invalid",
        "commit",
        "--quiet",
        "-m",
        message,
    )


@pytest.fixture
def clean_git_repo(tmp_path: Path, isolated_git_config: None) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init", "--quiet")
    (repo / "source.txt").write_text("source\n", encoding="utf-8")
    _run_git(repo, "add", "source.txt")
    _commit(repo, "initial")
    return repo


@pytest.fixture
def temporary_git_repo(clean_git_repo: Path) -> Path:
    repo = clean_git_repo
    legacy_temp_artifact = repo / ".autoskillit" / "temp" / "legacy.txt"
    legacy_temp_artifact.parent.mkdir(parents=True)
    legacy_temp_artifact.write_text("legacy\n", encoding="utf-8")
    _run_git(repo, "add", ".autoskillit/temp/legacy.txt")
    _commit(repo, "add legacy temp artifact")
    return repo


def _stage_entry(repo: Path, relative: str, kind: Literal["file", "symlink"]) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "file":
        path.write_text(f"{relative}\n", encoding="utf-8")
    else:
        path.symlink_to("link-target")
    _run_git(repo, "update-index", "--add", "--", relative)


def _run_staged_temp_guard(
    repo: Path, *, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(_STAGED_TEMP_GUARD)],
        cwd=cwd or repo,
        check=False,
        capture_output=True,
        text=True,
    )


def _assert_rejected(result: subprocess.CompletedProcess[str], path: str) -> None:
    assert result.returncode == 1
    assert result.stderr == f"Refusing to stage AutoSkillit temp artifact: {path}\n"


@pytest.mark.parametrize(
    ("path", "kind", "expect_reject"),
    [
        pytest.param(".autoskillit/temp", "file", True, id="root-file"),
        pytest.param(".autoskillit/temp", "symlink", True, id="root-symlink"),
        pytest.param(".autoskillit/temp/child.txt", "file", True, id="child-file"),
        pytest.param(".autoskillit/temporary/x", "file", False, id="sibling-dir"),
        pytest.param(".autoskillit/temp-x", "file", False, id="sibling-name"),
        pytest.param("sub/.autoskillit/temp/y", "file", False, id="nested-non-top"),
    ],
)
def test_staged_temp_guard_matches_tracked_temp_policy(
    clean_git_repo: Path, path: str, kind: Literal["file", "symlink"], expect_reject: bool
) -> None:
    _stage_entry(clean_git_repo, path, kind)
    policy = git_ls_files(clean_git_repo, _TEMP_PATHSPEC, allow_empty=True)
    assert policy == ((path,) if expect_reject else ())

    result = _run_staged_temp_guard(clean_git_repo)
    if expect_reject:
        _assert_rejected(result, path)
    else:
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""


@pytest.mark.parametrize(
    ("change", "expect_reject"),
    [pytest.param("modify", True, id="modify"), pytest.param("delete", False, id="delete")],
)
def test_staged_temp_guard_on_committed_temp_root(
    clean_git_repo: Path, change: str, expect_reject: bool
) -> None:
    _stage_entry(clean_git_repo, _TEMP_PATHSPEC, "symlink")
    _commit(clean_git_repo, "commit temp root")
    root = clean_git_repo / _TEMP_PATHSPEC
    root.unlink()
    if change == "modify":
        root.symlink_to("other-target")
    _run_git(clean_git_repo, "add", "--all")

    result = _run_staged_temp_guard(clean_git_repo)
    if expect_reject:
        _assert_rejected(result, _TEMP_PATHSPEC)
    else:
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""


def test_staged_temp_guard_rejects_temp_child_replaced_by_root(temporary_git_repo: Path) -> None:
    root = temporary_git_repo / _TEMP_PATHSPEC
    (root / "legacy.txt").unlink()
    root.rmdir()
    root.write_text("legacy\n", encoding="utf-8")
    _run_git(temporary_git_repo, "add", "--all")

    _assert_rejected(_run_staged_temp_guard(temporary_git_repo), _TEMP_PATHSPEC)


def test_staged_temp_guard_rejects_rename_into_temp(temporary_git_repo: Path) -> None:
    source = temporary_git_repo / "source.txt"
    destination = temporary_git_repo / ".autoskillit" / "temp" / "source.txt"
    source.replace(destination)
    _run_git(temporary_git_repo, "add", "--all")

    result = _run_staged_temp_guard(temporary_git_repo)

    _assert_rejected(result, ".autoskillit/temp/source.txt")


def test_staged_temp_guard_permits_deletion(temporary_git_repo: Path) -> None:
    (temporary_git_repo / ".autoskillit" / "temp" / "legacy.txt").unlink()
    _run_git(temporary_git_repo, "add", "--all")

    result = _run_staged_temp_guard(temporary_git_repo)

    assert result.returncode == 0, result.stderr


def test_staged_temp_guard_permits_non_temp_change_beside_tracked_temp(
    temporary_git_repo: Path,
) -> None:
    (temporary_git_repo / "source.txt").write_text("changed\n", encoding="utf-8")
    _run_git(temporary_git_repo, "add", "--all")

    result = _run_staged_temp_guard(temporary_git_repo)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


def test_staged_temp_guard_sees_gitlink_hidden_by_submodule_ignore(clean_git_repo: Path) -> None:
    _run_git(clean_git_repo, "config", "diff.ignoreSubmodules", "all")
    _run_git(
        clean_git_repo,
        "update-index",
        "--add",
        "--cacheinfo",
        f"160000,{'1' * 40},.autoskillit/temp/module",
    )

    _assert_rejected(_run_staged_temp_guard(clean_git_repo), ".autoskillit/temp/module")


def test_staged_temp_guard_is_independent_of_invocation_cwd(clean_git_repo: Path) -> None:
    sub = clean_git_repo / "sub"
    sub.mkdir()
    _stage_entry(clean_git_repo, ".autoskillit/temp/child.txt", "file")

    _assert_rejected(
        _run_staged_temp_guard(clean_git_repo, cwd=sub), ".autoskillit/temp/child.txt"
    )


def test_staged_temp_guard_honors_relative_alternate_index(
    clean_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sub = clean_git_repo / "sub"
    sub.mkdir()
    _stage_entry(clean_git_repo, ".autoskillit/temp/child.txt", "file")
    (clean_git_repo / "alternate-index").write_bytes(
        (clean_git_repo / ".git" / "index").read_bytes()
    )
    _run_git(clean_git_repo, "reset", "--quiet", "HEAD")
    monkeypatch.setenv("GIT_INDEX_FILE", "alternate-index")

    _assert_rejected(
        _run_staged_temp_guard(clean_git_repo, cwd=sub), ".autoskillit/temp/child.txt"
    )


def test_staged_temp_guard_rejects_root_symlink_in_real_commit(clean_git_repo: Path) -> None:
    sub = clean_git_repo / "sub"
    sub.mkdir()
    _stage_entry(clean_git_repo, _TEMP_PATHSPEC, "symlink")
    (clean_git_repo / ".git" / "hooks" / "pre-commit").symlink_to(_STAGED_TEMP_GUARD)
    head_before = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=clean_git_repo, timeout=30
    )

    result = subprocess.run(
        [
            "git",
            "--literal-pathspecs",
            "-c",
            "user.name=AutoSkillit Test",
            "-c",
            "user.email=autoskillit-test@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "reject temp root",
        ],
        cwd=sub,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode != 0
    assert f"Refusing to stage AutoSkillit temp artifact: {_TEMP_PATHSPEC}\n" in result.stderr
    assert (
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=clean_git_repo, timeout=30)
        == head_before
    )
