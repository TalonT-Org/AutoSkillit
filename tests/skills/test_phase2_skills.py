"""Phase 2 tests: open-kitchen and close-kitchen SKILL.md files."""

from __future__ import annotations

import re

import yaml

from autoskillit.core.paths import pkg_root
from autoskillit.workspace.skills import DefaultSkillResolver


def test_open_kitchen_skill_has_disable_model_invocation() -> None:
    skill_md = pkg_root() / "skills" / "open-kitchen" / "SKILL.md"
    assert skill_md.exists()
    content = skill_md.read_text()
    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    assert fm_match, "SKILL.md must have YAML frontmatter"
    fm = yaml.safe_load(fm_match.group(1))
    assert fm.get("disable-model-invocation") is True
    assert fm.get("name") == "open-kitchen"


def test_close_kitchen_skill_has_disable_model_invocation() -> None:
    skill_md = pkg_root() / "skills" / "close-kitchen" / "SKILL.md"
    assert skill_md.exists()
    content = skill_md.read_text()
    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    assert fm_match
    fm = yaml.safe_load(fm_match.group(1))
    assert fm.get("disable-model-invocation") is True
    assert fm.get("name") == "close-kitchen"


def test_open_close_kitchen_skills_listed_by_resolver() -> None:
    resolver = DefaultSkillResolver()
    names = {s.name for s in resolver.list_all()}
    assert "open-kitchen" in names
    assert "close-kitchen" in names
