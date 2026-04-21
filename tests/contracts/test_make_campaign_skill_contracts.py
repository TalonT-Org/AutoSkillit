"""Contract tests: structural invariants for the make-campaign SKILL.md."""

from __future__ import annotations

import re
from typing import Any

import yaml

from autoskillit.core import pkg_root
from autoskillit.workspace.skills import DefaultSkillResolver

_SKILL_DIR = pkg_root() / "skills_extended" / "make-campaign"
_SKILL_MD = _SKILL_DIR / "SKILL.md"


def _read_skill_md() -> str:
    return _SKILL_MD.read_text()


def _parse_frontmatter(content: str) -> dict[str, Any]:
    """Extract YAML frontmatter between --- delimiters."""
    m = re.match(r"^---\n(.+?)\n---\n", content, re.DOTALL)
    assert m, "SKILL.md must have YAML frontmatter delimited by ---"
    parsed = yaml.safe_load(m.group(1))
    assert isinstance(parsed, dict), "SKILL.md frontmatter must be a YAML mapping"
    return parsed


def test_make_campaign_skill_discovered():
    """make-campaign appears in DefaultSkillResolver.list_all()."""
    resolver = DefaultSkillResolver()
    names = [s.name for s in resolver.list_all()]
    assert "make-campaign" in names, (
        "make-campaign not discovered by DefaultSkillResolver.list_all(). "
        "Ensure the directory is under skills_extended/ and SKILL.md name field matches."
    )


def test_make_campaign_frontmatter_valid():
    """Frontmatter has name='make-campaign', categories=['orchestration-family']."""
    content = _read_skill_md()
    fm = _parse_frontmatter(content)
    assert fm.get("name") == "make-campaign", (
        f"Expected name='make-campaign', got {fm.get('name')!r}"
    )
    assert "orchestration-family" in fm.get("categories", []), (
        f"Expected categories to include 'orchestration-family', got {fm.get('categories')!r}"
    )


def test_make_campaign_has_six_phases():
    """SKILL.md body contains Phase 1 through Phase 6 headings or numbered sections."""
    content = _read_skill_md()
    for phase_num in range(1, 7):
        assert re.search(rf"Phase\s+{phase_num}\b", content, re.IGNORECASE), (
            f"SKILL.md missing Phase {phase_num} section"
        )


def test_make_campaign_output_path_convention():
    """Output instructions reference .autoskillit/recipes/campaigns/<name>.yaml."""
    content = _read_skill_md()
    assert ".autoskillit/recipes/campaigns/" in content, (
        "SKILL.md must reference .autoskillit/recipes/campaigns/ as the output path"
    )


def test_make_campaign_declares_structured_output_token():
    """SKILL.md declares a campaign_path structured output token."""
    content = _read_skill_md()
    assert "campaign_path" in content, (
        "SKILL.md must declare a campaign_path structured output token"
    )
    assert re.search(r"campaign_path\s*=", content), (
        "SKILL.md must show campaign_path = <value> token emission"
    )


def test_make_campaign_no_code_modification_instructions():
    """SKILL.md never instructs to modify source code files."""
    content = _read_skill_md()
    # Strip frontmatter before checking
    body = re.sub(r"^---\n.+?\n---\n", "", content, flags=re.DOTALL)
    code_mod_patterns = [
        r"\bmodify\s+source\s+code\b",
        r"\bedit\s+\.py\s+files\b",
        r"\bwrite\s+to\s+src/\b",
    ]
    for pattern in code_mod_patterns:
        assert not re.search(pattern, body, re.IGNORECASE), (
            f"SKILL.md must not instruct modifying source code. Found pattern: {pattern!r}"
        )
    # Critical Constraints section must include NEVER modify source code
    constraints_m = re.search(
        r"##\s+Critical Constraints\b(.+?)(?=\n##\s|\Z)", body, re.DOTALL | re.IGNORECASE
    )
    assert constraints_m, "SKILL.md must have a '## Critical Constraints' section"
    constraints_section = constraints_m.group(1)
    assert re.search(
        r"NEVER\b[^\n]*modify[^\n]*source\s+code", constraints_section, re.IGNORECASE
    ), "SKILL.md Critical Constraints section must state NEVER modify source code files"


def test_make_campaign_invokes_validation():
    """SKILL.md body references recipe validation (validate_recipe or validate_from_path)."""
    content = _read_skill_md()
    assert re.search(r"validate_recipe|validate_from_path", content), (
        "SKILL.md must reference validate_recipe or validate_from_path for campaign validation"
    )


def test_make_campaign_references_recipe_discovery():
    """SKILL.md references list_recipes or find_recipe_by_name for dispatch decomposition."""
    content = _read_skill_md()
    assert re.search(r"list_recipes|find_recipe_by_name", content), (
        "SKILL.md must reference list_recipes or find_recipe_by_name for recipe discovery"
    )
