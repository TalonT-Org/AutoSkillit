"""Tests for the apply-review-dimensions skill SKILL.md specification."""

from __future__ import annotations

import pytest

from autoskillit.core import pkg_root
from autoskillit.core.io import load_yaml

pytestmark = [pytest.mark.medium]

_SKILL_DIR = pkg_root() / "skills_extended" / "apply-review-dimensions"
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
    assert fm["name"] == "apply-review-dimensions"
    assert fm["categories"] == ["research"]
    assert "backend_requirements" not in fm


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

    apply-review-dimensions is the apply step — verdict is computed by the
    downstream synthesis step (aggregate_review_verdict), not this skill.
    """
    content = _SKILL_MD.read_text(encoding="utf-8")
    assert "## Output" in content, "SKILL.md missing ## Output section"
    after_output = content.split("## Output", 1)[1]
    related = after_output.split("## Related Skills", 1)
    output_section = related[0] if len(related) > 1 else after_output
    assert "findings_manifest_path" in output_section
    assert "evaluation_dashboard_path" in output_section
    assert "verdict =" not in output_section, (
        "Output section must not contain a 'verdict =' token; verdict belongs downstream"
    )
    assert "verdict=" not in output_section, (
        "Output section must not contain a 'verdict=' token; verdict belongs downstream"
    )


def test_findings_manifest_schema_documented() -> None:
    """Findings manifest JSON schema is documented with all required fields."""
    content = _SKILL_MD.read_text(encoding="utf-8")
    required_fields = [
        "dimension",
        "level",
        "severity",
        "finding",
        "addressable",
        "requires_decision",
        "priority",
        "fixability",
        "message",
    ]
    for field in required_fields:
        assert field in content, f"Missing schema field: {field}"
    assert "red_team_findings" in content


def test_silencing_rules_documented() -> None:
    """Three-layer silencing rules are documented with variance_protocol exception."""
    content = _SKILL_MD.read_text(encoding="utf-8")
    assert "Static SILENT" in content or "static SILENT" in content
    assert "Foothold validation" in content or "foothold validation" in content
    assert "Finding-count suppression" in content or "finding-count suppression" in content
    assert "variance_protocol" in content  # exception to foothold validation
