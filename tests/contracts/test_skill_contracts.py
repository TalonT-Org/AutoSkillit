"""Contract tests: every delimiter-emitting skill must be registered in skill_contracts.yaml."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_CONTRACTS_YAML = Path(__file__).parents[2] / "src/autoskillit/recipe/skill_contracts.yaml"


@pytest.fixture(scope="module")
def skills() -> dict:
    raw = yaml.safe_load(_CONTRACTS_YAML.read_text())
    return raw.get("skills", {})


def _assert_skill_has_patterns(skills: dict, skill_name: str, expected_delimiter: str) -> None:
    assert skill_name in skills, (
        f"{skill_name!r} not found in skill_contracts.yaml — "
        f"delimiter-emitting skills must be registered"
    )
    patterns = skills[skill_name].get("expected_output_patterns", [])
    assert patterns, f"{skill_name!r} has no expected_output_patterns"
    assert expected_delimiter in patterns, (
        f"No pattern matching {expected_delimiter!r} found for {skill_name!r}; got {patterns!r}"
    )


def test_skill_contracts_yaml_includes_prepare_issue(skills):
    """prepare-issue must be registered with its ---prepare-issue-result--- delimiter."""
    _assert_skill_has_patterns(skills, "prepare-issue", "---prepare-issue-result---")


def test_skill_contracts_yaml_includes_enrich_issues(skills):
    """enrich-issues must be registered with its ---enrich-issues-result--- delimiter."""
    _assert_skill_has_patterns(skills, "enrich-issues", "---enrich-issues-result---")


def test_skill_contracts_yaml_includes_report_bug(skills):
    """report-bug must be registered with its ---bug-fingerprint--- delimiter."""
    _assert_skill_has_patterns(skills, "report-bug", "---bug-fingerprint---")


def test_skill_contracts_yaml_includes_collapse_issues(skills):
    """collapse-issues must be registered with its ---collapse-issues-result--- delimiter."""
    _assert_skill_has_patterns(skills, "collapse-issues", "---collapse-issues-result---")


def test_skill_contracts_yaml_includes_issue_splitter(skills):
    """issue-splitter must be registered with its ---issue-splitter-result--- delimiter."""
    _assert_skill_has_patterns(skills, "issue-splitter", "---issue-splitter-result---")


def test_skill_contracts_yaml_includes_process_issues(skills):
    """process-issues must be registered with its ---process-issues-result--- delimiter."""
    _assert_skill_has_patterns(skills, "process-issues", "---process-issues-result---")


def test_audit_impl_no_go_pattern_matches_literal_output(skills):
    """The audit-impl pattern must match 'verdict = NO GO' (space-separated).

    Regression guard for issue #418: pattern had NO_GO (underscore) while SKILL.md
    mandates NO GO (space). Must stay RED until skill_contracts.yaml is fixed.
    """
    import re

    patterns = skills.get("audit-impl", {}).get("expected_output_patterns", [])
    assert patterns, "audit-impl must have expected_output_patterns"
    no_go_output = "verdict = NO GO\n%%ORDER_UP%%"
    for pattern in patterns:
        assert re.search(pattern, no_go_output), (
            f"Pattern {pattern!r} does not match 'verdict = NO GO' — "
            f"SKILL.md line 340 mandates space-separated format."
        )
