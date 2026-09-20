"""Prevent generated AutoSkillit session artifacts from becoming tracked files."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests._git_inventory import git_ls_files

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEMP_PATH_ALLOWLIST: dict[str, str] = {}
_STAGED_TEMP_GUARD = _REPO_ROOT / "scripts" / "check_staged_autoskillit_temp.sh"


def test_no_autoskillit_temp_artifacts_are_tracked() -> None:
    tracked_paths = git_ls_files(_REPO_ROOT, ".autoskillit/temp", allow_empty=True)
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


@pytest.fixture
def temporary_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init", "--quiet")

    (repo / "source.txt").write_text("source\n", encoding="utf-8")
    legacy_temp_artifact = repo / ".autoskillit" / "temp" / "legacy.txt"
    legacy_temp_artifact.parent.mkdir(parents=True)
    legacy_temp_artifact.write_text("legacy\n", encoding="utf-8")
    _run_git(repo, "add", "source.txt", ".autoskillit/temp/legacy.txt")
    _run_git(
        repo,
        "-c",
        "user.name=AutoSkillit Test",
        "-c",
        "user.email=autoskillit-test@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "initial",
    )
    return repo


def _run_staged_temp_guard(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(_STAGED_TEMP_GUARD)],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )


def test_staged_temp_guard_rejects_rename_into_temp(temporary_git_repo: Path) -> None:
    source = temporary_git_repo / "source.txt"
    destination = temporary_git_repo / ".autoskillit" / "temp" / "source.txt"
    source.replace(destination)
    _run_git(temporary_git_repo, "add", "--all")

    result = _run_staged_temp_guard(temporary_git_repo)

    assert result.returncode == 1
    assert ".autoskillit/temp/source.txt" in result.stderr


def test_staged_temp_guard_permits_deletion(temporary_git_repo: Path) -> None:
    (temporary_git_repo / ".autoskillit" / "temp" / "legacy.txt").unlink()
    _run_git(temporary_git_repo, "add", "--all")

    result = _run_staged_temp_guard(temporary_git_repo)

    assert result.returncode == 0, result.stderr
