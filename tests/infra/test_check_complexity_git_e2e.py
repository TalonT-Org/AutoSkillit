"""End-to-end tests for scripts/check_complexity.py against real git repositories.

tests/infra/test_check_complexity.py exercises the ratchet, counter, and CLI against mocked
git plumbing (fake `_git`, in-memory readers). This file drives the real `git` binary
against a disposable repository per test to exercise what mocks cannot: rename detection
(`-M`), delete+add inheritance, untracked-file enumeration, and ratchet persistence across
real commits -- following the same importlib-loaded-module pattern used there.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.infra._complexity_helpers import (
    _MINIMAL_LIMITS,
    _git,
    _source_with_function,
    load_check_script,
)

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[2]
_CHECK_SCRIPT = REPO_ROOT / "scripts" / "check_complexity.py"
_CHECK_MODULE_NAME = "_autoskillit_check_complexity_git_e2e"

check = load_check_script(_CHECK_MODULE_NAME, _CHECK_SCRIPT)


# --- disposable repo fixture: seeding differs from test_check_complexity.py (branch
# creation, fixed complexity-12/4 fixture), so it stays file-local rather than shared.


def _write_a_py(repo: Path, f_complexity: int, g_complexity: int) -> None:
    source = (
        _source_with_function("f", f_complexity)
        + "\n\n"
        + _source_with_function("g", g_complexity)
    )
    (repo / "src" / "a.py").write_text(source, encoding="utf-8")


def _seed_repo(tmp_path: Path) -> Path:
    """A real repo with src/a.py (complexity-12 `f` and complexity-4 `g`) committed, and a
    `base` branch pointing at that seed commit. Every test starts from a fresh call to
    this -- repo state is never shared across tests."""
    repo = tmp_path / "repo"
    (repo / "tests" / "arch").mkdir(parents=True)
    (repo / "src").mkdir()
    _git(repo.parent, "init", "-q", "repo")
    _git(repo, "config", "user.email", "gate@example.invalid")
    _git(repo, "config", "user.name", "gate")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "tests" / "arch" / "_complexity_limits.py").write_text(
        _MINIMAL_LIMITS, encoding="utf-8"
    )
    _write_a_py(repo, 12, 4)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "seed")
    _git(repo, "branch", "base")
    return repo


# --- case 1: growth past the base-revision ceiling warns, then fails once promoted --------


def test_staged_growth_past_ceiling_warns_then_fails(tmp_path, monkeypatch, capsys):
    repo = _seed_repo(tmp_path)
    _write_a_py(repo, 13, 4)
    _git(repo, "add", "-A")

    assert check.main(["--staged", "--repo-root", str(repo)]) == 0
    assert "complexity 13 > allowed 12" in capsys.readouterr().out

    monkeypatch.setattr(check, "ENFORCEMENT", "fail")
    assert check.main(["--staged", "--repo-root", str(repo)]) == 1


# --- case 2: growth that stays under MAX_COMPLEXITY never violates, no false positive -----


def test_staged_growth_under_max_complexity_is_silent(tmp_path, capsys):
    repo = _seed_repo(tmp_path)
    _write_a_py(repo, 12, 5)
    _git(repo, "add", "-A")

    assert check.main(["--staged", "--repo-root", str(repo)]) == 0
    assert capsys.readouterr().out == ""


# --- case 3: a straight rename with no complexity growth never violates -------------------


@pytest.mark.parametrize("destination", ["src/b.py", "tests/moved.py"])
def test_staged_rename_with_no_growth_is_silent(tmp_path, destination, capsys):
    repo = _seed_repo(tmp_path)
    _git(repo, "mv", "src/a.py", destination)

    assert check.main(["--staged", "--repo-root", str(repo)]) == 0
    assert capsys.readouterr().out == ""


# --- case 4: delete + add of a differently-named file inherits the vanished ceiling -------


def test_staged_delete_and_add_inherits_vanished_ceiling(tmp_path, capsys):
    repo = _seed_repo(tmp_path)
    _git(repo, "rm", "-q", "src/a.py")
    # git rm removes the now-empty "src" directory too; recreate it for the new file.
    (repo / "src").mkdir(exist_ok=True)
    # Enough unrelated padding around the unchanged `f` keeps similarity below git's default
    # 50% rename threshold, so this lands as a genuine D+A pair rather than an R rename.
    padding = "\n".join(f"UNRELATED_CONSTANT_{i} = {i}" for i in range(40))
    (repo / "src" / "c.py").write_text(
        padding + "\n\n\n" + _source_with_function("f", 12), encoding="utf-8"
    )
    _git(repo, "add", "-A")

    assert check.main(["--staged", "--repo-root", str(repo)]) == 0
    assert capsys.readouterr().out == ""


# --- case 5: an untracked file is enumerated and checked against --base -------------------


def test_untracked_file_against_base_reports_violation(tmp_path, capsys):
    repo = _seed_repo(tmp_path)
    (repo / "src" / "new.py").write_text(_source_with_function("f", 11), encoding="utf-8")

    assert check.main(["--base", "base", "--repo-root", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "src/new.py" in out
    assert "new function" in out


# --- a merged reduction becomes the next ceiling (Design Decision 2) ----------------------


def test_merged_reduction_lowers_next_ceiling(tmp_path, monkeypatch, capsys):
    repo = _seed_repo(tmp_path)
    monkeypatch.setattr(check, "ENFORCEMENT", "fail")

    _write_a_py(repo, 11, 4)
    _git(repo, "add", "-A")
    assert check.main(["--staged", "--repo-root", str(repo)]) == 0
    assert capsys.readouterr().out == ""

    _git(repo, "commit", "-qm", "reduce")

    _write_a_py(repo, 12, 4)
    _git(repo, "add", "-A")
    assert check.main(["--staged", "--repo-root", str(repo)]) == 1
    assert "was 11 at the base revision" in capsys.readouterr().out


# --- refactor and exemption removal can land together in one change ----------------------


_VALID_RATIONALE = (
    "Tried splitting the branch dispatch into a lookup table, but the domain rules do "
    "not decompose that way without losing the shared error-handling context."
)


def test_refactor_and_exemption_removal_in_one_change(tmp_path, monkeypatch, capsys):
    repo = _seed_repo(tmp_path)
    exempt_limits = (
        "MAX_COMPLEXITY = 10\nMIN_RATIONALE_CHARS = 60\n"
        "COMPLEXITY_EXEMPTIONS = {\n"
        '    "src/a.py::f": ComplexityExemption(\n'
        f"        limit=14, rationale={_VALID_RATIONALE!r},\n"
        "    ),\n"
        "}\n"
    )
    (repo / "tests" / "arch" / "_complexity_limits.py").write_text(exempt_limits, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "exempt")

    _write_a_py(repo, 10, 4)
    (repo / "tests" / "arch" / "_complexity_limits.py").write_text(
        _MINIMAL_LIMITS, encoding="utf-8"
    )
    _git(repo, "add", "-A")

    monkeypatch.setattr(check, "ENFORCEMENT", "fail")
    assert check.main(["--staged", "--repo-root", str(repo)]) == 0
    assert capsys.readouterr().out == ""
