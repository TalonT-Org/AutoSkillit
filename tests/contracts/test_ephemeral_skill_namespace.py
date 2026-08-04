"""Contract: ephemeral SKILL.md bodies use the correct namespace for the session context.

After init_session(), every written SKILL.md must reference cross-skills using the
namespace that matches how those skills are delivered in the session:
- BUNDLED_EXTENDED skills are delivered via --add-dir as bare /name
- BUNDLED skills are delivered via --plugin-dir as /autoskillit:name

A /autoskillit:<ref> reference in an ephemeral SKILL.md for an available
BUNDLED_EXTENDED target is wrong — the agent will not find it. Disabled or
otherwise unavailable targets are intentionally not projected as invocable.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.core import ClaudeDirectoryConventions, SkillExecutionRole, SkillSource
from autoskillit.workspace.session_skills import (
    DefaultSessionSkillManager,
    SkillsDirectoryProvider,
)
from autoskillit.workspace.skills import DefaultSkillResolver
from tests.contracts._projection_helpers import non_exploration_catalog

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]

_PREFIXED_REF_RE = re.compile(r"/autoskillit:([a-z][a-z0-9-]*)")


def test_ephemeral_skill_md_namespace_matches_session_delivery(tmp_path: Path) -> None:
    provider = SkillsDirectoryProvider()
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
    resolver = DefaultSkillResolver()
    catalog = non_exploration_catalog(
        resolver.list_effective(tmp_path, SkillExecutionRole.SESSION)
    )
    context = provider.catalog_projection_context(catalog, tmp_path)
    session_path = mgr.init_session("ns-check-session", catalog, context)

    skills_base = session_path / ClaudeDirectoryConventions.ADD_DIR_SKILLS_SUBDIR
    violations: list[str] = []

    for skill_md in sorted(skills_base.glob("*/SKILL.md")):
        skill_name = skill_md.parent.name
        body = skill_md.read_text()
        for m in _PREFIXED_REF_RE.finditer(body):
            ref_name = m.group(1)
            if catalog.namespace_sources.get(ref_name) == SkillSource.BUNDLED_EXTENDED:
                line_no = body[: m.start()].count("\n") + 1
                violations.append(
                    f"{skill_name}/SKILL.md:{line_no}: /autoskillit:{ref_name} "
                    f"is BUNDLED_EXTENDED — must be /{ref_name} in ephemeral content"
                )

    assert not violations, (
        "Ephemeral SKILL.md bodies contain /autoskillit: references for BUNDLED_EXTENDED skills "
        "(delivered as bare /name via --add-dir):\n" + "\n".join(f"  - {v}" for v in violations)
    )
