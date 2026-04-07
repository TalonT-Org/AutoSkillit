"""Contract tests: every delimiter-emitting skill must be registered in skill_contracts.yaml."""

from __future__ import annotations

import re
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


def test_every_pattern_example_matches_its_patterns(skills):
    """For every skill with expected_output_patterns and pattern_examples,
    every pattern must re.search-match at least one example.

    Permanent architectural guard: pattern/SKILL.md divergence fails CI before production.
    """
    import re

    failures = []
    for skill_name, contract in skills.items():
        patterns = contract.get("expected_output_patterns", [])
        examples = contract.get("pattern_examples", [])
        if not patterns or not examples:
            continue
        for pattern in patterns:
            if not any(re.search(pattern, ex) for ex in examples):
                failures.append(
                    f"Skill '{skill_name}': pattern {pattern!r} "
                    f"matches none of the examples {examples!r}"
                )
    assert not failures, "Contract patterns do not match their declared examples:\n" + "\n".join(
        failures
    )


def test_every_skill_with_patterns_has_examples(skills):
    """Every skill with expected_output_patterns must also declare pattern_examples.

    Prevents adding patterns without verifiable examples.
    """
    missing = [
        skill_name
        for skill_name, contract in skills.items()
        if contract.get("expected_output_patterns") and not contract.get("pattern_examples")
    ]
    assert not missing, (
        "These skills have expected_output_patterns but no pattern_examples:\n"
        + "\n".join(f"  - {s}" for s in sorted(missing))
        + "\nAdd pattern_examples to skill_contracts.yaml."
    )


VALID_EXPERIMENT_TYPES = frozenset(
    {
        "benchmark",
        "configuration_study",
        "causal_inference",
        "robustness_audit",
        "exploratory",
    }
)


def test_skill_contracts_pattern_examples_use_valid_experiment_types() -> None:
    """pattern_examples in skill_contracts.yaml must use only valid experiment_type values.

    'controlled' is not a valid experiment type. Invalid examples mislead
    developers writing new skills and create false documentation contracts.
    """
    contracts_text = _CONTRACTS_YAML.read_text()
    # Use [^\s\\]+ to stop at backslash, since YAML string literals use \n (two chars)
    for m in re.finditer(r"experiment_type\s*=\s*([^\s\\]+)", contracts_text):
        value = m.group(1).strip("\"'")
        assert value in VALID_EXPERIMENT_TYPES, (
            f"skill_contracts.yaml contains invalid experiment_type in pattern_examples: "
            f"{value!r}. Valid values: {sorted(VALID_EXPERIMENT_TYPES)}"
        )


def test_review_design_experiment_type_output_has_allowed_values() -> None:
    """review-design contract must declare allowed_values for experiment_type output.

    Without this constraint, invalid values (e.g. 'controlled') can propagate
    silently through capture and downstream recipe steps.
    """
    contracts = yaml.safe_load(_CONTRACTS_YAML.read_text())
    review_design = contracts["skills"]["review-design"]
    outputs = {o["name"]: o for o in review_design["outputs"]}
    et_output = outputs.get("experiment_type")
    assert et_output is not None, "review-design must declare experiment_type output"
    assert "allowed_values" in et_output, (
        "experiment_type output must have allowed_values constraint. "
        "Without it, invalid values propagate silently."
    )
    assert set(et_output["allowed_values"]) == VALID_EXPERIMENT_TYPES


def test_all_exp_lens_skills_have_contracts(skills):
    """Every exp-lens skill must have an entry in skill_contracts.yaml."""
    from autoskillit.workspace.skills import SkillResolver

    resolver = SkillResolver()
    exp_lens = [
        s.name
        for s in resolver.list_all()
        if "exp-lens" in s.categories and s.name.startswith("exp-lens-")
    ]
    missing = [name for name in exp_lens if name not in skills]
    assert not missing, f"exp-lens skills missing contracts: {sorted(missing)}"


def test_skill_contracts_yaml_includes_prepare_research_pr(skills):
    """prepare-research-pr must be registered with prep_path output pattern."""
    _assert_skill_has_patterns(skills, "prepare-research-pr", r"prep_path\s*=\s*/.+")


def test_skill_contracts_yaml_includes_compose_research_pr(skills):
    """compose-research-pr must be registered with pr_url output pattern."""
    _assert_skill_has_patterns(
        skills, "compose-research-pr", r"pr_url\s*=\s*(https://github\.com/.*/pull/\d+)?"
    )


def test_skill_contracts_yaml_open_research_pr_removed(skills):
    """open-research-pr must no longer be registered — it has been retired."""
    assert "open-research-pr" not in skills
