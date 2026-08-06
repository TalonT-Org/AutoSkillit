"""Shared setup for the plugin-projection contract tests.

Lives in contracts/ rather than workspace/ because asserting the projection is
fresh requires driving it from every entrypoint — `make_context` (IL-3), a
backend (IL-1), and the workspace projection itself. That cross-layer reach is
the point of the assertion, and `tests/workspace/` is layer-scoped.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autoskillit.workspace import EffectiveSkillCatalog

__all__ = [
    "STALE_VERSION",
    "non_exploration_catalog",
    "plant_stale_snapshot",
    "session_catalog",
]

STALE_VERSION = "0.0.1-stale"


def non_exploration_catalog(
    catalog: EffectiveSkillCatalog,
) -> EffectiveSkillCatalog:
    """Return the explicit catalog for tests unrelated to exploration.

    The runtime import stays local to avoid collection-time cross-layer loading.
    """
    from autoskillit.workspace import EffectiveSkillCatalog

    skills = tuple(skill for skill in catalog.skills if not skill.exploration_vectors)
    names = frozenset(skill.name for skill in skills)
    return EffectiveSkillCatalog(
        skills=skills,
        execution_role=catalog.execution_role,
        namespace_sources={
            name: source for name, source in catalog.namespace_sources.items() if name in names
        },
    )


def session_catalog():
    """The bundled session-role catalog `cook` and the MCP server both project."""
    from autoskillit.core import SkillExecutionRole, SkillSource
    from autoskillit.workspace import (
        DefaultSkillResolver,
        EffectiveSkillCatalog,
        SkillCatalogEntry,
    )

    skills = tuple(s for s in DefaultSkillResolver().list_all() if s.source is SkillSource.BUNDLED)
    return EffectiveSkillCatalog(
        skills=tuple(SkillCatalogEntry.from_skill_info(s) for s in skills),
        execution_role=SkillExecutionRole.SESSION,
    )


def plant_stale_snapshot(home: Path) -> Path:
    """Write a plausible-looking, deliberately stale plugin cache snapshot.

    The exact shape the old resolver picked up: a real directory under the Claude
    Code plugin cache, named by installed_plugins.json, whose assets come from an
    older release. Every test that plants one asserts nothing reads it.
    """
    snapshot = (
        home
        / ".claude"
        / "plugins"
        / "cache"
        / "autoskillit-local"
        / "autoskillit"
        / STALE_VERSION
    )
    (snapshot / ".claude-plugin").mkdir(parents=True)
    (snapshot / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "autoskillit", "version": STALE_VERSION})
    )
    for name in ("recipes", "agents", "hooks", "skills"):
        (snapshot / name).mkdir()
        (snapshot / name / "STALE.md").write_text(f"stale {name} from {STALE_VERSION}\n")

    registry = home / ".claude" / "plugins" / "installed_plugins.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(
        json.dumps(
            {
                "version": 2,
                "plugins": {"autoskillit@autoskillit-local": {"installPath": str(snapshot)}},
            }
        )
    )
    return snapshot
