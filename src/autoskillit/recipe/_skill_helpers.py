"""Shared helpers for skill-related semantic rules."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import regex as re

from autoskillit.core import SkillLister

if TYPE_CHECKING:
    from autoskillit.core import SkillResolver

_SKILL_TOKEN_RE = re.compile(r"/(?:autoskillit:)?(\S+)")

SKILL_SEARCH_DIRS: list[Path] | None = None


def _resolve_skill_md(skill_name: str, *, resolver: SkillResolver | None = None) -> Path | None:
    """Resolve a skill name to its SKILL.md path.

    When SKILL_SEARCH_DIRS is set (e.g., in tests), searches those directories.
    Otherwise uses SkillResolver to find the bundled skill.
    """
    if SKILL_SEARCH_DIRS is not None:
        for search_dir in SKILL_SEARCH_DIRS:
            skill_md = search_dir / skill_name / "SKILL.md"
            if skill_md.is_file():
                return skill_md
        return None
    if resolver is None:
        from autoskillit.workspace import DefaultSkillResolver  # noqa: PLC0415

        resolver = DefaultSkillResolver()
    skill_info = resolver.resolve(skill_name)
    if skill_info is None:
        return None
    return skill_info.path


def _has_dynamic_skill_name(skill_cmd: str) -> bool:
    """Return True if the skill name portion contains template expressions.

    Handles both ``${{ }}`` Jinja-style expressions and bare ``{placeholder}``
    orchestrator-level template tokens (e.g. ``exp-lens-{slug}``).
    """
    m = _SKILL_TOKEN_RE.search(skill_cmd)
    if not m:
        return False
    token = m.group(1)
    first_space = token.find(" ")
    name_part = token[:first_space] if first_space >= 0 else token
    return "${{" in name_part or "{" in name_part


MULTIPART_SKILL_NAMES: frozenset[str] = frozenset({"make-plan", "rectify"})


@lru_cache(maxsize=1)
def _get_skill_category_map(lister: SkillLister | None = None) -> dict[str, frozenset[str]]:
    """Return {skill_name: categories} for all bundled skills."""
    if lister is None:
        from autoskillit.workspace import DefaultSkillResolver  # noqa: PLC0415

        lister = DefaultSkillResolver()
    return {s.name: s.categories for s in lister.list_all()}


@lru_cache(maxsize=1)
def _get_bundled_skill_names(lister: SkillLister | None = None) -> frozenset[str]:
    """Return the set of all bundled skill names."""
    if lister is None:
        from autoskillit.workspace import DefaultSkillResolver  # noqa: PLC0415

        lister = DefaultSkillResolver()
    return frozenset(s.name for s in lister.list_all())
