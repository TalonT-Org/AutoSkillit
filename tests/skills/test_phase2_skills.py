"""Phase 2 tests: open-kitchen and close-kitchen SKILL.md files."""

from __future__ import annotations

import re

import pytest

from autoskillit.core.io import load_yaml
from autoskillit.core.paths import pkg_root
from autoskillit.workspace.skills import DefaultSkillResolver

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]


def test_open_kitchen_skill_has_disable_model_invocation() -> None:
    skill_md = pkg_root() / "skills" / "open-kitchen" / "SKILL.md"
    assert skill_md.exists()
    content = skill_md.read_text()
    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    assert fm_match, "SKILL.md must have YAML frontmatter"
    fm = load_yaml(fm_match.group(1))
    assert fm.get("disable-model-invocation") is True
    assert fm.get("name") == "open-kitchen"


def test_open_kitchen_skill_respects_host_declared_visibility() -> None:
    content = (pkg_root() / "skills" / "open-kitchen" / "SKILL.md").read_text()

    assert "Each new coding-agent session starts with kitchen tools hidden" not in content
    assert "Skip calling `open_kitchen` and assume the kitchen is already open" not in content
    assert "**ALWAYS:**\n- Call `open_kitchen` with no arguments" not in content
    assert "host" in content and "pre-revealed" in content
    assert "no-argument" in content and "solely to gain access" in content
    assert "explicitly requests activation or promotion" in content
    assert "promotion remains valid even when the tools are pre-revealed" in content
    assert "name=" in content
    assert "close_kitchen" in content and "reopen" in content


def test_close_kitchen_skill_has_disable_model_invocation() -> None:
    skill_md = pkg_root() / "skills" / "close-kitchen" / "SKILL.md"
    assert skill_md.exists()
    content = skill_md.read_text()
    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    assert fm_match
    fm = load_yaml(fm_match.group(1))
    assert fm.get("disable-model-invocation") is True
    assert fm.get("name") == "close-kitchen"


def test_open_close_kitchen_skills_listed_by_resolver() -> None:
    resolver = DefaultSkillResolver()
    names = {s.name for s in resolver.list_all()}
    assert "open-kitchen" in names
    assert "close-kitchen" in names
