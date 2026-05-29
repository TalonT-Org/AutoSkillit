"""Shared helpers for skill-related semantic rules."""

from __future__ import annotations

from autoskillit.core import SkillLister

MULTIPART_SKILL_NAMES: frozenset[str] = frozenset({"make-plan", "rectify"})


def _get_skill_category_map(lister: SkillLister | None = None) -> dict[str, frozenset[str]]:
    """Return {skill_name: categories} for all bundled skills."""
    if lister is None:
        from autoskillit.workspace import DefaultSkillResolver  # noqa: PLC0415

        lister = DefaultSkillResolver()
    return {s.name: s.categories for s in lister.list_all()}


def _get_bundled_skill_names(lister: SkillLister | None = None) -> frozenset[str]:
    """Return the set of all bundled skill names."""
    if lister is None:
        from autoskillit.workspace import DefaultSkillResolver  # noqa: PLC0415

        lister = DefaultSkillResolver()
    return frozenset(s.name for s in lister.list_all())
