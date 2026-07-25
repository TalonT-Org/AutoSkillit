"""Semantic guard for make-plan's exact worker-routable capabilities."""

from __future__ import annotations

import pytest

from autoskillit.core.paths import pkg_root
from autoskillit.workspace.skill_format import read_skill_frontmatter

pytestmark = [pytest.mark.small]


def test_make_plan_declares_exact_worker_capabilities() -> None:
    parsed = read_skill_frontmatter(pkg_root() / "skills_extended" / "make-plan" / "SKILL.md")
    assert parsed.data is not None
    caps = set(parsed.data.get("uses_capabilities", []))
    assert caps == {"agent_model", "agent_subagent"}
