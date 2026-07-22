"""Semantic guard for make-plan's exact worker-routable capabilities."""

from __future__ import annotations

import pytest

from autoskillit.core.paths import pkg_root
from autoskillit.workspace.skills import _read_skill_frontmatter

pytestmark = [pytest.mark.small]


def test_make_plan_declares_exact_worker_capabilities() -> None:
    fm = _read_skill_frontmatter(pkg_root() / "skills_extended" / "make-plan" / "SKILL.md")
    caps = set(fm.get("uses_capabilities", []))
    assert caps == {"agent_model", "agent_subagent"}
