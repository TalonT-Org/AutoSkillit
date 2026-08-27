"""Git-tracked recipe enumeration for test parametrization.

``all_validated_recipe_paths``/``all_validated_recipe_names`` (see
``autoskillit.recipe.io``) scan the live working-tree directory with no regard
for git tracking status, so an untracked or gitignored stray ``.yaml`` file
under a scanned recipe directory silently inflates every test that
parametrizes over them. Production recipe discovery (``list_recipes()``) must
keep scanning the real filesystem, since a user's project-local recipes are
real files that were never committed to this repository. The test suite,
however, wants its parametrization source to match exactly what git (and
therefore CI) sees.

This module scans git's index directly for the same two roots production scans
— project-local ``.autoskillit/recipes`` and the built-in
``pkg_root()/recipes``. It delegates ordering, parsing, and duplicate handling
to the production collection engine, while retaining Git's index as the
parametrization authority.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path, PurePosixPath
from typing import NamedTuple

from autoskillit.core.paths import pkg_root
from autoskillit.core.types import LoadResult
from autoskillit.recipe.io import (
    RECIPE_SCAN_DIRS,
    collect_recipes_from_candidates,
    is_recipe_scan_path,
    load_recipe,
)
from autoskillit.recipe.schema import RecipeInfo

_PROJECT_RECIPES_RELPATH = ".autoskillit/recipes"
_GIT_TIMEOUT_SECONDS = 10


def _repo_toplevel(project_root: Path) -> Path:
    try:
        proc = subprocess.run(
            ["git", "-C", str(project_root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(
            "tracked_recipe_paths: could not resolve the git repository toplevel "
            f"for project_root={project_root!s}: {exc}"
        ) from exc
    return Path(proc.stdout.strip()).resolve()


def _builtin_recipes_pathspec(project_root: Path) -> str:
    """Return pkg_root()/'recipes' expressed relative to project_root.

    Raises if pkg_root()/'recipes' does not resolve inside project_root's own
    git repository — the suite runs against an editable install where the
    built-in recipes directory is tracked in the same repository as the
    caller's project_root, so silently returning only the project-local half
    of the recipe set would hide the divergence rather than surface it.
    """
    toplevel = _repo_toplevel(project_root)
    builtin_recipes = (pkg_root() / "recipes").resolve()
    try:
        builtin_recipes.relative_to(toplevel)
    except ValueError as exc:
        raise RuntimeError(
            "tracked_recipe_paths: pkg_root()/'recipes' "
            f"({builtin_recipes}) does not resolve inside the git repository "
            f"containing project_root={project_root!s} (toplevel {toplevel}). "
            "tracked_recipe_paths assumes an editable install where the built-in "
            "recipes directory is tracked in the same repository as project_root; "
            "refusing to silently return only the project-local half of the "
            "recipe set."
        ) from exc
    return os.path.relpath(builtin_recipes, project_root)


def _git_ls_files(project_root: Path, pathspec: str) -> list[str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(project_root), "ls-files", "-z", "--", pathspec],
            capture_output=True,
            text=True,
            check=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(
            f"tracked_recipe_paths: 'git ls-files -- {pathspec}' failed for "
            f"project_root={project_root!s}: {exc}"
        ) from exc
    return [entry for entry in proc.stdout.split("\0") if entry]


def _candidate_paths(project_root: Path, root_relpath: str) -> set[Path]:
    root = PurePosixPath(root_relpath)
    candidates: set[Path] = set()
    for line in _git_ls_files(project_root, root_relpath):
        line_path = PurePosixPath(line)
        try:
            rel_to_root = line_path.relative_to(root)
        except ValueError:
            continue
        if PurePosixPath(line).suffix not in (".yaml", ".yml"):
            continue
        if not is_recipe_scan_path(rel_to_root):
            continue
        candidates.add((project_root / line).resolve())
    return candidates


def tracked_recipe_load_result(project_root: Path) -> LoadResult[RecipeInfo]:
    """Return the Git-indexed recipe collection, including load errors."""
    resolved_root = project_root.resolve()
    builtin_relpath = _builtin_recipes_pathspec(resolved_root)
    result = collect_recipes_from_candidates(
        resolved_root / _PROJECT_RECIPES_RELPATH,
        _candidate_paths(resolved_root, _PROJECT_RECIPES_RELPATH),
        (pkg_root() / "recipes").resolve(),
        _candidate_paths(resolved_root, builtin_relpath),
    )
    result.items.sort(key=lambda info: info.path)
    return result


def _tracked_recipe_infos(project_root: Path) -> tuple[RecipeInfo, ...]:
    return tuple(tracked_recipe_load_result(project_root).items)


def tracked_recipe_paths(project_root: Path) -> tuple[Path, ...]:
    """Recipe files that exist in git's index — the set CI will also see."""
    return tuple(info.path for info in _tracked_recipe_infos(project_root))


def tracked_recipe_names(project_root: Path) -> tuple[str, ...]:
    """Recipe ``name:`` values for recipe files that exist in git's index."""
    return tuple(sorted(info.name for info in _tracked_recipe_infos(project_root)))


class RecipeStrayAnalysis(NamedTuple):
    errors: tuple[str, ...]
    report_paths: tuple[Path, ...]


def _on_disk_recipe_paths(base: Path) -> set[Path]:
    paths: set[Path] = set()
    for subdir in RECIPE_SCAN_DIRS:
        directory = base / subdir
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            rel_to_root = PurePosixPath(path.relative_to(base).as_posix())
            if (
                path.is_file()
                and path.suffix in (".yaml", ".yml")
                and is_recipe_scan_path(rel_to_root)
            ):
                paths.add(path.resolve())
    return paths


def analyze_untracked_recipes(project_root: Path) -> RecipeStrayAnalysis:
    """Classify untracked recipe-shaped files without changing test outcomes."""
    resolved_root = project_root.resolve()
    builtin_relpath = _builtin_recipes_pathspec(resolved_root)
    project_base = resolved_root / _PROJECT_RECIPES_RELPATH
    builtin_base = (pkg_root() / "recipes").resolve()
    tracked_paths = _candidate_paths(resolved_root, _PROJECT_RECIPES_RELPATH)
    tracked_paths |= _candidate_paths(resolved_root, builtin_relpath)
    stray_paths = sorted(
        (_on_disk_recipe_paths(project_base) | _on_disk_recipe_paths(builtin_base)) - tracked_paths
    )
    tracked_by_name = {
        info.name: info.path for info in tracked_recipe_load_result(resolved_root).items
    }

    errors: list[str] = []
    report_paths: list[Path] = []
    for path in stray_paths:
        try:
            recipe = load_recipe(path)
        except Exception as exc:
            errors.append(f"Untracked recipe cannot be loaded: {path}: {exc}")
            continue
        tracked_path = tracked_by_name.get(recipe.name)
        if recipe.name and tracked_path is not None:
            errors.append(
                f"Untracked recipe {path} shares name {recipe.name!r} "
                f"with tracked recipe {tracked_path}. Use `git check-ignore -v {path}` "
                "to identify why the file is untracked."
            )
            continue
        report_paths.append(path)
    return RecipeStrayAnalysis(tuple(errors), tuple(report_paths))


def format_untracked_recipe_report(analysis: RecipeStrayAnalysis) -> list[str]:
    """Format report-only local recipes for pytest's session header."""
    if not analysis.report_paths:
        return []
    return [
        "untracked non-colliding recipes (report only):",
        *(
            f"  {path} (use `git check-ignore -v {path}` to identify the responsible rule; "
            f"use `git add -f {path}` to deliberately track it)"
            for path in analysis.report_paths
        ),
    ]
