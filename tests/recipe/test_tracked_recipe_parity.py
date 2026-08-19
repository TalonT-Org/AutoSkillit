"""Parity tests for the git-tracked recipe enumeration used by test parametrization.

Guards the invariant that recipe-parametrized tests source their matrix from
``tests._tracked_recipes`` (git's index) rather than the live working tree, so
an untracked or gitignored stray ``.yaml`` file can never silently inflate a
parametrized test matrix locally while remaining invisible to CI.
"""

from __future__ import annotations

import ast
import os
import subprocess
from pathlib import Path

import pytest

from autoskillit.core import pkg_root
from autoskillit.recipe import all_validated_recipe_names
from autoskillit.recipe.io import RECIPE_SCAN_DIRS
from tests._tracked_recipes import tracked_recipe_names, tracked_recipe_paths

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_THIS_FILE = Path(__file__).resolve()

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@test.local",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@test.local",
}

_MINIMAL_RECIPE_YAML = (
    "name: t19-tracked-recipe\n"
    "description: minimal recipe for tracked-recipe parity test\n"
    "steps:\n"
    "  done:\n"
    "    action: stop\n"
    "    message: Done.\n"
)

_INVALID_RECIPE_YAML = "steps: not-a-mapping\n"


def _on_disk_recipe_files(base: Path) -> set[Path]:
    found: set[Path] = set()
    for subdir in RECIPE_SCAN_DIRS:
        scan_dir = base / subdir
        if not scan_dir.is_dir():
            continue
        for f in scan_dir.iterdir():
            if f.suffix in (".yaml", ".yml") and f.is_file():
                found.add(f.resolve())
    return found


def _git_tracked_files_under(project_root: Path, base: Path) -> set[Path]:
    relpath = os.path.relpath(base, project_root)
    proc = subprocess.run(
        ["git", "-C", str(project_root), "ls-files", "-z", "--", relpath],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    lines = [entry for entry in proc.stdout.split("\0") if entry]
    return {(project_root / line).resolve() for line in lines}


def test_no_untracked_recipe_is_present_in_working_tree() -> None:
    """Every .yaml/.yml file directly under a RECIPE_SCAN_DIRS subdirectory of
    either recipe root must be tracked by git."""
    project_base = _PROJECT_ROOT / ".autoskillit" / "recipes"
    builtin_base = pkg_root() / "recipes"

    stray: list[Path] = []
    for base in (project_base, builtin_base):
        on_disk = _on_disk_recipe_files(base)
        tracked = _git_tracked_files_under(_PROJECT_ROOT, base)
        stray.extend(sorted(on_disk - tracked))

    assert not stray, (
        "Untracked recipe-shaped .yaml/.yml file(s) found under a scanned recipe "
        "directory. These inflate the recipe-parametrized test matrix locally "
        "while being invisible to CI, since CI only ever sees git's tracked "
        "state:\n"
        + "\n".join(f"  {p}" for p in stray)
        + "\nNote a file can be present-but-untracked either because it was "
        "never `git add`ed, or because it is excluded via .git/info/exclude "
        "(which `git status --porcelain` alone will not surface — use "
        "`git status --porcelain --ignored` to see it). Either `git add` the "
        "file or remove it."
    )


def _calls_to_all_validated_recipe_functions(py_file: Path) -> list[int]:
    target_names = {"all_validated_recipe_names", "all_validated_recipe_paths"}
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    except SyntaxError:
        return []
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name in target_names:
            lines.append(node.lineno)
    return lines


def test_parametrized_modules_source_from_tracked_recipes() -> None:
    """No test file outside the two intentional exceptions may call
    all_validated_recipe_names/all_validated_recipe_paths — recipe
    parametrization must source from tests._tracked_recipes."""
    tests_root = _PROJECT_ROOT / "tests"
    exempt = {
        (tests_root / "recipe" / "test_io_discovery.py").resolve(),
        _THIS_FILE,
    }

    violations: dict[Path, list[int]] = {}
    for py_file in sorted(tests_root.rglob("*.py")):
        resolved = py_file.resolve()
        if resolved in exempt:
            continue
        lines = _calls_to_all_validated_recipe_functions(py_file)
        if lines:
            violations[resolved] = lines

    assert not violations, (
        "Found calls to all_validated_recipe_names/all_validated_recipe_paths "
        "outside tests/recipe/test_io_discovery.py (which legitimately tests "
        "list_recipes() directly). Recipe-parametrized tests must enumerate via "
        "tests._tracked_recipes.tracked_recipe_names/tracked_recipe_paths so the "
        "matrix reflects git's tracked state, not the live working tree:\n"
        + "\n".join(f"  {p}: lines {lines}" for p, lines in sorted(violations.items()))
    )


def _init_git_repo(repo_dir: Path) -> None:
    subprocess.run(
        ["git", "init", "-q"], cwd=repo_dir, check=True, capture_output=True, env=_GIT_ENV
    )


def _git_commit(repo_dir: Path, *paths: Path, message: str) -> None:
    subprocess.run(
        ["git", "add", *(str(p) for p in paths)],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        env=_GIT_ENV,
    )
    subprocess.run(
        ["git", "commit", "-q", "-m", message],
        cwd=repo_dir,
        check=True,
        capture_output=True,
        env=_GIT_ENV,
    )


def test_untracked_recipe_does_not_change_parametrization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adding a second, untracked/invalid recipe file after the first
    tracked_recipe_paths() call must not change what a subsequent call
    returns."""
    from tests import _tracked_recipes

    # pkg_root() must resolve inside project_root's own repo (tmp_path here);
    # point it at an unrelated empty subtree of the same repo so the
    # invariant holds without contributing any builtin-side candidates.
    monkeypatch.setattr(_tracked_recipes, "pkg_root", lambda: tmp_path / "fake_pkg")

    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True)
    tracked_recipe = recipes_dir / "t19-tracked.yaml"
    tracked_recipe.write_text(_MINIMAL_RECIPE_YAML, encoding="utf-8")

    _init_git_repo(tmp_path)
    _git_commit(tmp_path, tracked_recipe, message="add tracked recipe")

    first = tracked_recipe_paths(tmp_path)
    assert first == (tracked_recipe.resolve(),)

    stray_recipe = recipes_dir / "t19-untracked-stray.yaml"
    stray_recipe.write_text(_INVALID_RECIPE_YAML, encoding="utf-8")

    second = tracked_recipe_paths(tmp_path)
    assert second == first


def test_tracked_set_matches_loader_on_clean_checkout() -> None:
    """On a clean checkout (no untracked recipe strays, per T17), the
    tracked-recipe set must exactly match what the production loader
    discovers."""
    tracked = set(tracked_recipe_names(_PROJECT_ROOT))
    loader = set(all_validated_recipe_names(_PROJECT_ROOT))
    assert tracked == loader, (
        f"tracked_recipe_names diverges from all_validated_recipe_names:\n"
        f"  only in tracked: {sorted(tracked - loader)}\n"
        f"  only in loader:  {sorted(loader - tracked)}"
    )
