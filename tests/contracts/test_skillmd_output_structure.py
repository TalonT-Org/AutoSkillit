"""Contract tests: SKILL.md Output section structural properties.

Enforces that every SKILL.md with expected_output_patterns in
skill_contracts.yaml includes the IMPORTANT callout before code-fenced
output examples, with code-fence prohibition language.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from autoskillit.workspace.skills import DefaultSkillResolver

_CONTRACTS_YAML = Path(__file__).parents[2] / "src/autoskillit/recipe/skill_contracts.yaml"


def _skills_with_output_patterns() -> list[str]:
    raw = yaml.safe_load(_CONTRACTS_YAML.read_text())
    return sorted(
        name
        for name, contract in raw.get("skills", {}).items()
        if contract.get("expected_output_patterns")
    )


_SKILLS_WITH_PATTERNS = _skills_with_output_patterns()


def _get_skill_md_content(skill_name: str) -> str | None:
    resolver = DefaultSkillResolver()
    for info in resolver.list_all():
        if info.name == skill_name:
            skill_md = info.path / "SKILL.md"
            if skill_md.exists():
                return skill_md.read_text()
    return None


def _extract_output_section(content: str) -> str | None:
    match = re.search(r"^## Output\b.*", content, re.MULTILINE)
    if not match:
        return None
    start = match.start()
    next_h2 = re.search(r"^## ", content[start + 1 :], re.MULTILINE)
    if next_h2:
        return content[start : start + 1 + next_h2.start()]
    return content[start:]


def _find_important_callout_near_output(content: str) -> str | None:
    output_section = _extract_output_section(content)
    if output_section and re.search(
        r"(?:^>.*\*\*IMPORTANT[:\*]|^IMPORTANT:)", output_section, re.MULTILINE
    ):
        return output_section

    match = re.search(r"^## Output\b", content, re.MULTILINE)
    if not match:
        return None
    before_output = content[max(0, match.start() - 600) : match.start()]
    if re.search(r"(?:^>.*\*\*IMPORTANT[:\*]|^IMPORTANT:)", before_output, re.MULTILINE):
        return before_output + (output_section or "")
    return None


@pytest.mark.parametrize("skill_name", _SKILLS_WITH_PATTERNS)
def test_skills_with_output_patterns_have_important_callout(skill_name: str) -> None:
    """Every skill with expected_output_patterns must have an IMPORTANT callout
    near its Output section instructing literal plain text emission."""
    content = _get_skill_md_content(skill_name)
    if content is None:
        pytest.skip(f"SKILL.md not found for {skill_name}")

    region = _find_important_callout_near_output(content)
    assert region is not None, (
        f"Skill '{skill_name}' has expected_output_patterns in skill_contracts.yaml "
        f"but no IMPORTANT callout near its ## Output section. "
        f"Add the standard IMPORTANT blockquote before the code-fenced output example."
    )

    has_regex_match_warning = "regex match" in region.lower() or "regex" in region.lower()
    has_plain_text = "plain text" in region.lower() or "literal" in region.lower()
    assert has_regex_match_warning or has_plain_text, (
        f"Skill '{skill_name}' has an IMPORTANT callout but it doesn't mention "
        f"regex matching or literal plain text emission requirements."
    )


@pytest.mark.parametrize("skill_name", _SKILLS_WITH_PATTERNS)
def test_output_section_code_fence_has_no_wrap_prohibition(skill_name: str) -> None:
    """Every skill with expected_output_patterns must have code-fence prohibition
    language in its IMPORTANT callout."""
    content = _get_skill_md_content(skill_name)
    if content is None:
        pytest.skip(f"SKILL.md not found for {skill_name}")

    region = _find_important_callout_near_output(content)
    if region is None:
        pytest.skip(f"No IMPORTANT callout found for {skill_name} (covered by sibling test)")

    has_code_fence_prohibition = bool(re.search(r"code fence", region, re.IGNORECASE))
    assert has_code_fence_prohibition, (
        f"Skill '{skill_name}' IMPORTANT callout does not mention code fence prohibition. "
        f"Add 'Do not wrap the output block in a code fence.' to the callout."
    )
