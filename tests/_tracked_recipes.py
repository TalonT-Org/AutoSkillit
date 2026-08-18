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

This module scans git's index directly for the same two roots
``list_recipes()`` scans — project-local ``.autoskillit/recipes`` and the
built-in ``pkg_root()/recipes`` — restricted to ``RECIPE_SCAN_DIRS``
subdirectories, and intersects the result with what ``list_recipes()``
actually returns so a git-tracked but otherwise-rejected file (malformed
YAML, duplicate ``name:``) is never yielded.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path, PurePosixPath

from autoskillit.core.paths import pkg_root
from autoskillit.recipe.io import NON_RECIPE_DIRS, RECIPE_SCAN_DIRS, list_recipes
from autoskillit.recipe.schema import RecipeInfo

_PROJECT_RECIPES_RELPATH = ".autoskillit/recipes"
_GIT_TIMEOUT_SECONDS = 10

_infos_cache: dict[Path, tuple[RecipeInfo, ...]] = {}


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


def _matches_scan_dirs(rel_to_root: PurePosixPath) -> bool:
    """True if a path relative to a recipes root is a direct child of a
    RECIPE_SCAN_DIRS subdirectory (mirroring _collect_recipes' non-recursive
    directory.iterdir() scan)."""
    parts = rel_to_root.parts
    if len(parts) == 1:
        return "." in RECIPE_SCAN_DIRS
    if len(parts) == 2:
        subdir = parts[0]
        return subdir in RECIPE_SCAN_DIRS and subdir not in NON_RECIPE_DIRS
    return False


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
        if not _matches_scan_dirs(rel_to_root):
            continue
        candidates.add((project_root / line).resolve())
    return candidates


def _tracked_recipe_infos(project_root: Path) -> tuple[RecipeInfo, ...]:
    resolved_root = project_root.resolve()
    cached = _infos_cache.get(resolved_root)
    if cached is not None:
        return cached

    builtin_relpath = _builtin_recipes_pathspec(resolved_root)
    candidates = _candidate_paths(resolved_root, _PROJECT_RECIPES_RELPATH)
    candidates |= _candidate_paths(resolved_root, builtin_relpath)

    infos = tuple(
        sorted(
            (
                info
                for info in list_recipes(resolved_root).items
                if info.path.resolve() in candidates
            ),
            key=lambda info: info.path,
        )
    )
    _infos_cache[resolved_root] = infos
    return infos


def tracked_recipe_paths(project_root: Path) -> tuple[Path, ...]:
    """Recipe files that exist in git's index — the set CI will also see."""
    return tuple(info.path for info in _tracked_recipe_infos(project_root))


def tracked_recipe_names(project_root: Path) -> tuple[str, ...]:
    """Recipe ``name:`` values for recipe files that exist in git's index."""
    return tuple(sorted(info.name for info in _tracked_recipe_infos(project_root)))
