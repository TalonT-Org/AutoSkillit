"""Shared helpers for skill-related semantic rules."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import regex as re

from autoskillit.core import SkillLister, get_logger, pkg_root

logger = get_logger(__name__)

if TYPE_CHECKING:
    from autoskillit.core import SkillResolver


def get_allowed_values_for_skill(skill_name: str) -> dict[str, list[str]]:
    """Return {output_name: [allowed_value, ...]} for a skill's outputs with allowed_values."""
    try:
        from autoskillit.recipe.contracts import load_bundled_manifest  # noqa: PLC0415

        manifest = load_bundled_manifest()
    except Exception:
        logger.warning(
            "get_allowed_values_for_skill: failed to load bundled manifest; skipping",
            exc_info=True,
        )
        return {}
    skill_contract = manifest.get("skills", {}).get(skill_name, {})
    result: dict[str, list[str]] = {}
    for output in skill_contract.get("outputs", []):
        if "allowed_values" in output:
            result[output["name"]] = output["allowed_values"]
    return result


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

_SKILL_CATEGORY_CACHE: dict[tuple[int, int, int], dict[str, frozenset[str]]] = {}
_SKILL_NAMES_CACHE: dict[tuple[int, int, int], frozenset[str]] = {}


def _get_skill_category_map(lister: SkillLister | None = None) -> dict[str, frozenset[str]]:
    """Return {skill_name: categories} for all bundled skills."""
    from autoskillit.recipe._api_cache import _path_mtime_ns  # noqa: PLC0415

    key = (
        id(lister),
        _path_mtime_ns(pkg_root() / "skills"),
        _path_mtime_ns(pkg_root() / "skills_extended"),
    )
    if key in _SKILL_CATEGORY_CACHE:
        return _SKILL_CATEGORY_CACHE[key]
    if lister is None:
        from autoskillit.workspace import DefaultSkillResolver  # noqa: PLC0415

        lister = DefaultSkillResolver()
    result = {s.name: s.categories for s in lister.list_all()}
    _SKILL_CATEGORY_CACHE.clear()
    _SKILL_CATEGORY_CACHE[key] = result
    return result


def _get_bundled_skill_names(lister: SkillLister | None = None) -> frozenset[str]:
    """Return the set of all bundled skill names."""
    from autoskillit.recipe._api_cache import _path_mtime_ns  # noqa: PLC0415

    key = (
        id(lister),
        _path_mtime_ns(pkg_root() / "skills"),
        _path_mtime_ns(pkg_root() / "skills_extended"),
    )
    if key in _SKILL_NAMES_CACHE:
        return _SKILL_NAMES_CACHE[key]
    if lister is None:
        from autoskillit.workspace import DefaultSkillResolver  # noqa: PLC0415

        lister = DefaultSkillResolver()
    result = frozenset(s.name for s in lister.list_all())
    _SKILL_NAMES_CACHE.clear()
    _SKILL_NAMES_CACHE[key] = result
    return result
