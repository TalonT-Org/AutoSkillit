"""Contract tests: SKILL.md Output section structural properties.

Enforces that every SKILL.md with an IMPORTANT callout for structured
output tokens includes code-fence prohibition language, and that
high-failure-rate skills have the IMPORTANT callout present.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.core.io import load_yaml
from autoskillit.workspace.skills import DefaultSkillResolver

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]

_CONTRACTS_YAML = Path(__file__).parents[2] / "src/autoskillit/recipe/skill_contracts.yaml"

_IMPORTANT_RE = re.compile(r"(?:^>.*\*\*IMPORTANT[:\*]|^IMPORTANT:)", re.MULTILINE)

_HIGH_RISK_SKILLS = [
    "compose-pr",
    "compose-research-pr",
    "download-data",
    "make-campaign",
    "prepare-pr",
    "prepare-research-pr",
    "setup-environment",
    "stage-data",
]


def _skills_with_output_patterns() -> list[str]:
    raw = load_yaml(_CONTRACTS_YAML)
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
            skill_md = info.path
            if skill_md.exists() and skill_md.is_file():
                return skill_md.read_text()
            skill_md = info.path / "SKILL.md"
            if skill_md.exists():
                return skill_md.read_text()
    return None


def _find_important_callouts(content: str) -> list[str]:
    regions: list[str] = []
    lines = content.splitlines(keepends=True)
    for m in _IMPORTANT_RE.finditer(content):
        line_start = content.rfind("\n", 0, m.start()) + 1
        line_idx = content[:line_start].count("\n")
        region_lines = []
        for i in range(line_idx, len(lines)):
            line = lines[i]
            if region_lines and not line.startswith(">") and line.strip():
                break
            region_lines.append(line)
        regions.append("".join(region_lines))
    return regions


@pytest.mark.parametrize("skill_name", _HIGH_RISK_SKILLS)
def test_high_risk_skills_have_important_callout(skill_name: str) -> None:
    """High-failure-rate skills must have an IMPORTANT callout for structured output."""
    content = _get_skill_md_content(skill_name)
    if content is None:
        pytest.skip(f"SKILL.md not found for {skill_name}")

    regions = _find_important_callouts(content)
    assert regions, (
        f"Skill '{skill_name}' is a high-failure-rate skill but has no IMPORTANT "
        f"callout in its SKILL.md. Add the standard IMPORTANT blockquote before "
        f"the code-fenced output example."
    )

    combined = " ".join(regions).lower()
    has_quality_signal = (
        "regex" in combined
        or "plain text" in combined
        or "literal" in combined
        or "no markdown" in combined
    )
    assert has_quality_signal, (
        f"Skill '{skill_name}' has an IMPORTANT callout but it doesn't mention "
        f"regex matching, literal/plain text, or markdown prohibition."
    )


@pytest.mark.parametrize("skill_name", _SKILLS_WITH_PATTERNS)
def test_existing_important_callouts_have_code_fence_prohibition(
    skill_name: str,
) -> None:
    """Every skill that already has an IMPORTANT callout must include
    code-fence prohibition language."""
    content = _get_skill_md_content(skill_name)
    if content is None:
        pytest.skip(f"SKILL.md not found for {skill_name}")

    regions = _find_important_callouts(content)
    if not regions:
        pytest.skip(f"No IMPORTANT callout in {skill_name} (separate enforcement)")

    combined = " ".join(regions).lower()
    has_code_fence_prohibition = "code fence" in combined
    assert has_code_fence_prohibition, (
        f"Skill '{skill_name}' IMPORTANT callout does not mention code fence "
        f"prohibition. Add 'Do not wrap the output block in a code fence.' "
        f"to the callout."
    )
