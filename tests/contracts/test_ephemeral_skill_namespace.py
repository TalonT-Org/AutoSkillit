"""Contract: ephemeral SKILL.md bodies use the correct namespace for the session context.

After init_session(), every written SKILL.md must reference cross-skills using the
namespace that matches how those skills are delivered in the session:
- BUNDLED_EXTENDED skills are delivered via --add-dir as bare /name
- BUNDLED skills are delivered via --plugin-dir as /autoskillit:name

A /autoskillit:<ref> reference in an ephemeral SKILL.md for a BUNDLED_EXTENDED target
is wrong — the agent will not find it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.core import SkillSource
from autoskillit.workspace.session_skills import (
    _SKILLS_SUBDIR,
    DefaultSessionSkillManager,
    SkillsDirectoryProvider,
)
from autoskillit.workspace.skills import DefaultSkillResolver

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]

_PREFIXED_REF_RE = re.compile(r"/autoskillit:([a-z][a-z0-9-]*)")


def test_ephemeral_skill_md_namespace_matches_session_delivery(tmp_path: Path) -> None:
    provider = SkillsDirectoryProvider()
    mgr = DefaultSessionSkillManager(provider, ephemeral_root=tmp_path)
    session_path = mgr.init_session("ns-check-session", cook_session=True)

    skills_base = session_path / _SKILLS_SUBDIR
    resolver = DefaultSkillResolver()
    violations: list[str] = []

    for skill_md in sorted(skills_base.glob("*/SKILL.md")):
        skill_name = skill_md.parent.name
        body = skill_md.read_text()
        for m in _PREFIXED_REF_RE.finditer(body):
            ref_name = m.group(1)
            info = resolver.resolve(ref_name)
            if info is not None and info.source == SkillSource.BUNDLED_EXTENDED:
                line_no = body[: m.start()].count("\n") + 1
                violations.append(
                    f"{skill_name}/SKILL.md:{line_no}: /autoskillit:{ref_name} "
                    f"is BUNDLED_EXTENDED — must be /{ref_name} in ephemeral content"
                )

    assert not violations, (
        "Ephemeral SKILL.md bodies contain /autoskillit: references for BUNDLED_EXTENDED skills "
        "(delivered as bare /name via --add-dir):\n" + "\n".join(f"  - {v}" for v in violations)
    )
