"""Contract tests: every delimiter-emitting skill must be registered in skill_contracts.yaml."""

from __future__ import annotations

from pathlib import Path

import yaml

_CONTRACTS_YAML = (
    Path(__file__).parents[2] / "src/autoskillit/recipe/skill_contracts.yaml"
)


def _load_skills() -> dict:
    raw = yaml.safe_load(_CONTRACTS_YAML.read_text())
    return raw.get("skills", {})


def _assert_skill_has_patterns(skill_name: str, expected_delimiter: str) -> None:
    skills = _load_skills()
    assert skill_name in skills, (
        f"{skill_name!r} not found in skill_contracts.yaml — "
        f"delimiter-emitting skills must be registered"
    )
    patterns = skills[skill_name].get("expected_output_patterns", [])
    assert patterns, f"{skill_name!r} has no expected_output_patterns"
    # The start delimiter must be present (matching is done as substring or exact)
    assert any(expected_delimiter in p for p in patterns), (
        f"No pattern matching {expected_delimiter!r} found for {skill_name!r}; got {patterns!r}"
    )


def test_skill_contracts_yaml_includes_prepare_issue():
    """prepare-issue must be registered with its ---prepare-issue-result--- delimiter."""
    _assert_skill_has_patterns("prepare-issue", "---prepare-issue-result---")


def test_skill_contracts_yaml_includes_enrich_issues():
    """enrich-issues must be registered with its ---enrich-issues-result--- delimiter."""
    _assert_skill_has_patterns("enrich-issues", "---enrich-issues-result---")


def test_skill_contracts_yaml_includes_report_bug():
    """report-bug must be registered with its ---bug-fingerprint--- delimiter."""
    _assert_skill_has_patterns("report-bug", "---bug-fingerprint---")


def test_skill_contracts_yaml_includes_collapse_issues():
    """collapse-issues must be registered with its ---collapse-issues-result--- delimiter."""
    _assert_skill_has_patterns("collapse-issues", "---collapse-issues-result---")


def test_skill_contracts_yaml_includes_issue_splitter():
    """issue-splitter must be registered with its ---issue-splitter-result--- delimiter."""
    _assert_skill_has_patterns("issue-splitter", "---issue-splitter-result---")


def test_skill_contracts_yaml_includes_process_issues():
    """process-issues must be registered with its ---process-issues-result--- delimiter."""
    _assert_skill_has_patterns("process-issues", "---process-issues-result---")
