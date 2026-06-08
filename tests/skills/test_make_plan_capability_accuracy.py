"""Semantic guard: make-plan must not declare git_metadata_write capability."""

from __future__ import annotations

import pytest

from autoskillit.core.paths import pkg_root
from autoskillit.workspace.skills import _read_skill_frontmatter

pytestmark = [pytest.mark.small]


def test_make_plan_does_not_declare_git_metadata_write():
    fm = _read_skill_frontmatter(pkg_root() / "skills_extended" / "make-plan" / "SKILL.md")
    caps = set(fm.get("uses_capabilities", []))
    assert "git_metadata_write" not in caps
