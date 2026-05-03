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
    from autoskillit.workspace.skills import DefaultSkillResolver

    resolver = DefaultSkillResolver()
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


def test_review_pr_verdict_allowed_values_includes_approved_with_comments(skills):
    """review-pr allowed_values must include approved_with_comments.

    The skill emits 4 distinct verdicts; the contract previously only listed 3.
    A missing allowed_value causes unrouted-verdict-value semantic rule failures.
    """
    assert "review-pr" in skills
    verdict_output = next(
        (o for o in skills["review-pr"].get("outputs", []) if o["name"] == "verdict"),
        None,
    )
    assert verdict_output is not None, "review-pr must declare a verdict output"
    allowed = verdict_output.get("allowed_values", [])
    assert "approved_with_comments" in allowed, (
        f"review-pr allowed_values must include 'approved_with_comments'; got {allowed!r}"
    )


def test_review_pr_pattern_examples_cover_all_verdicts(skills):
    """Every allowed verdict value for review-pr must appear in at least one pattern_example.

    Ensures the contract's example set is complete: one example per outcome.
    """
    assert "review-pr" in skills
    verdict_output = next(
        (o for o in skills["review-pr"].get("outputs", []) if o["name"] == "verdict"),
        None,
    )
    assert verdict_output is not None
    allowed = verdict_output.get("allowed_values", [])
    examples = skills["review-pr"].get("pattern_examples", [])
    missing = [v for v in allowed if not any(f"verdict = {v}" in ex for ex in examples)]
    assert not missing, (
        f"review-pr pattern_examples missing examples for verdicts: {missing!r}. "
        "Each allowed_value must be represented by at least one pattern_example."
    )


def test_every_pattern_example_satisfies_all_patterns(skills):
    """For every skill with expected_output_patterns and pattern_examples,
    every example must re.search-match ALL patterns (bi-directional check).

    The one-directional check (each pattern matches >=1 example) misses conditional
    tokens: a pattern may match *some* examples while failing for valid output that
    legitimately omits a conditional token.
    """
    failures = []
    for skill_name, contract in skills.items():
        patterns = contract.get("expected_output_patterns", [])
        examples = contract.get("pattern_examples", [])
        if not patterns or not examples:
            continue
        for i, example in enumerate(examples):
            for pattern in patterns:
                if not re.search(pattern, example):
                    failures.append(
                        f"Skill '{skill_name}': example[{i}] does not match pattern "
                        f"{pattern!r}.\n  Example: {example!r}"
                    )
    assert not failures, (
        "Bi-directional pattern/example check failed — "
        "conditional tokens cause AND-semantics failures at runtime:\n" + "\n".join(failures)
    )


def test_skill_contracts_allowed_values_covers_recipe_routes() -> None:
    """Every verdict value routed in recipe on_result blocks must appear in allowed_values.

    Scans implementation.yaml, remediation.yaml, implementation-groups.yaml, and
    merge-prs.yaml for result.verdict routing conditions. Any value routed in a recipe
    but absent from skill_contracts.yaml allowed_values will trigger the
    unrouted-verdict-value semantic rule at recipe-load time.
    """
    recipes_dir = Path(__file__).parents[2] / "src/autoskillit/recipes"
    target_files = [
        "implementation.yaml",
        "remediation.yaml",
        "implementation-groups.yaml",
        "merge-prs.yaml",
    ]
    contracts = yaml.safe_load(_CONTRACTS_YAML.read_text())
    verdict_output = next(
        (o for o in contracts["skills"]["review-pr"].get("outputs", []) if o["name"] == "verdict"),
        None,
    )
    assert verdict_output is not None
    allowed = set(verdict_output.get("allowed_values", []))

    # Match only lowercase verdict values (review-pr convention: approved, changes_requested…).
    # Excludes all-uppercase review-design verdicts (GO, REVISE, STOP) which appear in the
    # same recipe files under different steps.
    verdict_route_re = re.compile(r"result\.verdict\s*}}\s*==\s*([a-z][a-z_]*)")
    routed_values: set[str] = set()
    for filename in target_files:
        fpath = recipes_dir / filename
        if not fpath.exists():
            continue
        for m in verdict_route_re.finditer(fpath.read_text()):
            routed_values.add(m.group(1))

    missing = routed_values - allowed
    assert not missing, (
        f"Verdict values routed in recipes but absent from skill_contracts.yaml allowed_values: "
        f"{sorted(missing)}. Add them to the review-pr outputs[verdict].allowed_values list."
    )


# T3-1
def test_review_gate_loop_required_pattern_in_review_pr_contracts(skills):
    """review-pr gate pattern must use OR-conditional form compatible with approved_with_comments.

    The unconditional %%REVIEW_GATE::(LOOP_REQUIRED|CLEAR)%% pattern causes
    CONTRACT_VIOLATION for sessions that legitimately emit no gate tag
    (approved_with_comments verdict). The corrected form must be an OR that accepts
    either a gate tag or an approved_with_comments verdict.
    """
    assert "review-pr" in skills
    patterns = skills["review-pr"].get("expected_output_patterns", [])
    conditional_pattern = (
        "(?:%%REVIEW_GATE::(LOOP_REQUIRED|CLEAR)%%|verdict\\s*=\\s*approved_with_comments)"
    )
    assert conditional_pattern in patterns, (
        f"review-pr gate pattern must use OR-conditional form so that approved_with_comments "
        f"sessions succeed without a %%REVIEW_GATE:: tag. "
        f"Expected pattern: {conditional_pattern!r}. Got: {patterns!r}"
    )


# T3-2
def test_review_gate_clear_pattern_in_review_pr_contracts(skills):
    """review-pr REVIEW_GATE pattern must cover CLEAR and LOOP_REQUIRED; approved_with_comments
    example must exist WITHOUT a gate tag."""
    assert "review-pr" in skills
    patterns = skills["review-pr"].get("expected_output_patterns", [])
    examples = skills["review-pr"].get("pattern_examples", [])

    gate_patterns = [p for p in patterns if "REVIEW_GATE" in p]
    assert gate_patterns, "No REVIEW_GATE pattern found for review-pr"
    combined = " ".join(gate_patterns)
    assert "LOOP_REQUIRED" in combined and "CLEAR" in combined, (
        f"REVIEW_GATE pattern must reference both tags; found: {gate_patterns}"
    )

    awc_examples = [ex for ex in examples if "approved_with_comments" in ex]
    assert awc_examples, (
        "No approved_with_comments example found in pattern_examples — "
        "add one to document the no-gate-tag path"
    )
    for ex in awc_examples:
        assert "%%REVIEW_GATE::" not in ex, (
            f"approved_with_comments example must NOT include %%REVIEW_GATE:: tag; found: {ex!r}"
        )
