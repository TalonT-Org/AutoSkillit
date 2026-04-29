"""Shared helpers for skill-related semantic rules."""

from __future__ import annotations

from autoskillit.core import SkillLister


def _get_skill_category_map(lister: SkillLister | None = None) -> dict[str, frozenset[str]]:
    """Return {skill_name: categories} for all bundled skills."""
    if lister is None:
        from autoskillit.workspace import DefaultSkillResolver  # noqa: PLC0415

        lister = DefaultSkillResolver()
    return {s.name: s.categories for s in lister.list_all()}
