"""Verify report.md YAML frontmatter matches the audit-trail schema."""

import pytest

from autoskillit.core.io import load_yaml

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

REQUIRED_FIELDS = {
    "experiment_type": str,
    "methodology_traditions": list,
    "disambiguation_rule_applied": (str, type(None)),
    "tier_c_lens": (str, type(None)),
    "design_review_verdict": str,
    "classification_timestamp": (str, type(None)),
    "audit_trail_path": dict,
}

VALID_VERDICTS = {"GO", "REVISE", "STOP"}

REQUIRED_TRAIL_KEYS = {"design_review", "visualization_trace"}


def test_frontmatter_has_all_required_fields(sample_report_frontmatter: dict):
    """All schema fields present and correctly typed."""
    for field, expected_type in REQUIRED_FIELDS.items():
        assert field in sample_report_frontmatter, f"Missing field: {field}"
        value = sample_report_frontmatter[field]
        if value is not None:
            assert isinstance(value, expected_type), (
                f"{field}: expected {expected_type}, got {type(value)}"
            )


def test_frontmatter_verdict_is_valid(sample_report_frontmatter: dict):
    """design_review_verdict is one of GO/REVISE/STOP."""
    verdict = sample_report_frontmatter.get("design_review_verdict")
    if verdict is not None:
        assert verdict in VALID_VERDICTS


def test_frontmatter_audit_trail_paths(sample_report_frontmatter: dict):
    """audit_trail_path contains both required sub-keys."""
    trail = sample_report_frontmatter.get("audit_trail_path", {})
    assert REQUIRED_TRAIL_KEYS.issubset(trail.keys())
    for key in REQUIRED_TRAIL_KEYS:
        assert isinstance(trail[key], str)
        assert "audit/" in trail[key]


def test_frontmatter_methodology_traditions_is_list(sample_report_frontmatter: dict):
    """methodology_traditions is always a list, even with one entry."""
    traditions = sample_report_frontmatter["methodology_traditions"]
    assert isinstance(traditions, list)


def test_frontmatter_round_trips_through_yaml(sample_report_text: str):
    """Frontmatter can be extracted and parsed by a standard YAML loader."""
    lines = sample_report_text.strip().splitlines()
    assert lines[0] == "---", "Report must start with YAML frontmatter delimiter"
    end_idx = lines.index("---", 1)
    yaml_block = "\n".join(lines[1:end_idx])
    parsed = load_yaml(yaml_block)
    assert isinstance(parsed, dict)
    assert "experiment_type" in parsed
