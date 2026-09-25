"""Git-tracked project-local skill enumeration for test parametrization.

Production skill resolution (``DefaultSkillResolver.resolve_effective`` and
``list_effective``) scans every project-local search root on the real
filesystem with no regard for git tracking status, since a user's
project-local overrides are real files that were never committed to this
repository. The test suite, however, wants its parametrization source to
match exactly what git (and therefore CI) sees, so an untracked developer
override cannot fail — or silently satisfy — a repository contract.

This module scans git's index for the same search roots the resolver scans —
``ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS`` — and admits each tracked path through
the resolver's own per-candidate admission seam, so a test asserts exactly the
decision ``resolve_effective`` would make for that file.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from autoskillit.core import ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS
from autoskillit.workspace.skills import DefaultSkillResolver, SkillInfo
from tests._git_inventory import git_ls_files


def tracked_project_local_skill_paths(repo_root: Path) -> tuple[Path, ...]:
    """Return every tracked ``<search_dir>/<name>/SKILL.md`` under ``repo_root``."""
    return tuple(
        sorted(
            repo_root / rel
            for rel in git_ls_files(repo_root, *ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS)
            if PurePosixPath(rel).name == "SKILL.md"
            and PurePosixPath(rel).parent.parent.as_posix() in ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS
        )
    )


def admit_tracked_project_local_skill(repo_root: Path, skill_path: Path) -> SkillInfo | None:
    """Admit one tracked skill path through the resolver's candidate-admission seam."""
    from autoskillit.workspace.skills import _project_local_candidate

    search_dir = skill_path.parent.parent.relative_to(repo_root).as_posix()
    precedence = ALL_PROJECT_LOCAL_SKILL_SEARCH_DIRS.index(search_dir)
    name = skill_path.parent.name
    return _project_local_candidate(
        repo_root.resolve(),
        search_dir,
        precedence,
        name,
        lambda: next(
            (skill for skill in DefaultSkillResolver().list_all() if skill.name == name), None
        ),
    )
