"""Shared helpers for skill-related semantic rules."""

from __future__ import annotations

from functools import lru_cache

import regex as re

from autoskillit.core import SkillLister

_SKILL_TOKEN_RE = re.compile(r"/(?:autoskillit:)?(\S+)")


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
