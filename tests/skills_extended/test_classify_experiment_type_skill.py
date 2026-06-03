"""Tests for the classify-experiment-type skill SKILL.md specification.

These are stateless read-only tests that verify structural validity of the
SKILL.md file: directory exists, frontmatter parses, required sections are
present, silent-type rule is documented correctly, and verdict is not in
the output tokens.
"""

from __future__ import annotations

import pytest

from autoskillit.core import pkg_root
from autoskillit.core.io import load_yaml

pytestmark = [pytest.mark.medium]

_SKILL_DIR = pkg_root() / "skills_extended" / "classify-experiment-type"
_SKILL_MD = _SKILL_DIR / "SKILL.md"


def test_skill_directory_exists() -> None:
    """Skill directory exists and SKILL.md is present."""
    assert _SKILL_DIR.is_dir(), f"Skill directory missing: {_SKILL_DIR}"
    assert _SKILL_MD.is_file(), f"SKILL.md missing: {_SKILL_MD}"


def test_skill_frontmatter() -> None:
    """SKILL.md frontmatter is valid YAML and contains required fields."""
    content = _SKILL_MD.read_text(encoding="utf-8")
    parts = content.split("---", 2)
    assert len(parts) >= 3, "SKILL.md missing YAML frontmatter delimiters"
    fm = load_yaml(parts[1])
    assert isinstance(fm, dict), "SKILL.md frontmatter is not a YAML mapping"
    assert fm["name"] == "classify-experiment-type"
    assert fm["categories"] == ["research"]
    assert fm["backend_requirements"] == ["claude-code"]


def test_skill_sections() -> None:
    """All required H2 sections are present in the SKILL.md body."""
    content = _SKILL_MD.read_text(encoding="utf-8")
    required = [
        "## When to Use",
        "## Arguments",
        "## Critical Constraints",
        "## Workflow",
        "## Output",
        "## Related Skills",
    ]
    for section in required:
        assert section in content, f"SKILL.md missing required section: {section!r}"


def test_no_verdict_token() -> None:
    """The Output section must not emit a 'verdict' token.

    classify-experiment-type is the dial step — verdict belongs to
    apply-review-dimensions / review-design, not this skill.
    """
    content = _SKILL_MD.read_text(encoding="utf-8")
    assert "## Output" in content, "SKILL.md missing ## Output section"
    after_output = content.split("## Output", 1)[1]
    # Look at content between ## Output and the next H2 (Related Skills).
    related = after_output.split("## Related Skills", 1)
    output_section = related[0] if len(related) > 1 else after_output
    assert "verdict =" not in output_section, (
        "Output section must not contain a 'verdict =' token; verdict belongs downstream"
    )
    assert "verdict=" not in output_section, (
        "Output section must not contain a 'verdict=' token; verdict belongs downstream"
    )


def test_silent_type_rule_documented() -> None:
    """Silent-type detection rule references '>=6 of 9' and the shared convention doc."""
    content = _SKILL_MD.read_text(encoding="utf-8")
    threshold_present = ">=6 of 9" in content or "≥6 of 9" in content
    assert threshold_present, "Silent-type rule '>=6 of 9' not documented in SKILL.md"
    assert "is_silent_type" in content, "Token 'is_silent_type' not referenced in SKILL.md"
    assert "docs/research/silent-type-convention.md" in content, (
        "Reference to docs/research/silent-type-convention.md missing from SKILL.md"
    )
    # Confirm the rule operates on the BASE registry entry, not modifier-adjusted weights.
    assert "BASE registry entry" in content, (
        "Silent-type check must explicitly reference the BASE registry entry's dimension_weights"
    )
    assert "modifier-adjusted" in content, (
        "SKILL.md must clarify that silent-type check does NOT use modifier-adjusted weights"
    )
