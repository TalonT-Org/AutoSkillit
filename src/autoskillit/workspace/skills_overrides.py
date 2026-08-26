"""Skill override discovery surface.

Owns the project-local override search configuration (``_OVERRIDE_SEARCH_DIRS``),
the ``ProjectLocalOverride`` NamedTuple, and the discovery function
``detect_project_local_overrides``. The facade re-exports these names so
internal callers reach them through the facade module globals — preserving
the monkeypatch contract on ``_OVERRIDE_SEARCH_DIRS`` used by
``tests/arch/test_skill_search_dir_symmetry.py``.
"""

from __future__ import annotations

import stat
from pathlib import Path
from typing import NamedTuple

from autoskillit.core import ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS, SkillContractError

_OVERRIDE_SEARCH_DIRS = ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS


def _project_skill_path(root: Path, search: Path, name: str) -> Path | None:
    """Return a non-symlinked SKILL.md contained by its project search root."""
    entry = search / name
    skill_path = entry / "SKILL.md"
    try:
        search_root_stat = search.lstat()
        entry_stat = entry.lstat()
        skill_stat = skill_path.lstat()
        resolved_project_root = root.resolve(strict=True)
        resolved_root = search.resolve(strict=True)
        resolved_skill = skill_path.resolve(strict=True)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SkillContractError(
            f"cannot validate project-local skill {name!r} under {search}: {exc}"
        ) from exc
    if any(stat.S_ISLNK(item.st_mode) for item in (search_root_stat, entry_stat, skill_stat)):
        return None
    if not stat.S_ISDIR(entry_stat.st_mode) or not stat.S_ISREG(skill_stat.st_mode):
        return None
    if not resolved_root.is_relative_to(
        resolved_project_root
    ) or not resolved_skill.is_relative_to(resolved_root):
        return None
    return skill_path


class ProjectLocalOverride(NamedTuple):
    name: str
    search_dir: str
    skill_path: Path


def override_names(overrides: frozenset[ProjectLocalOverride]) -> frozenset[str]:
    return frozenset(o.name for o in overrides)


def detect_project_local_overrides(
    project_dir: Path,
    search_dirs: tuple[str, ...] | None = None,
) -> frozenset[ProjectLocalOverride]:
    """Return project-local skill overrides with path provenance.

    Scans all directories in `search_dirs` (or `_OVERRIDE_SEARCH_DIRS` when
    `search_dirs is None`) under `project_dir`. First-match-wins: if a skill
    name appears under multiple search dirs, only the first (by tuple order)
    is returned.
    """
    overrides: set[ProjectLocalOverride] = set()
    seen: set[str] = set()
    active = search_dirs if search_dirs is not None else _OVERRIDE_SEARCH_DIRS
    for subdir in active:
        search_root = project_dir / subdir
        if not search_root.is_dir():
            continue
        try:
            entries = list(search_root.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir() and entry.name not in seen and (entry / "SKILL.md").is_file():
                seen.add(entry.name)
                overrides.add(
                    ProjectLocalOverride(
                        name=entry.name,
                        search_dir=subdir,
                        skill_path=entry / "SKILL.md",
                    )
                )
    return frozenset(overrides)


__all__ = [
    "ProjectLocalOverride",
    "_OVERRIDE_SEARCH_DIRS",
    "_project_skill_path",
    "detect_project_local_overrides",
    "override_names",
]
