"""Contract tests: every delimiter-emitting skill must be registered in skill_contracts.yaml."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Final

import pytest

from autoskillit.core.io import load_yaml
from autoskillit.execution.session._session_content import _check_expected_patterns

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]

_CONTRACTS_YAML = Path(__file__).parents[2] / "src/autoskillit/recipe/skill_contracts.yaml"


@pytest.fixture(scope="module")
def skills() -> dict:
    raw = load_yaml(_CONTRACTS_YAML)
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
    failures = []
    for skill_name, contract in skills.items():
        patterns = contract.get("expected_output_patterns", [])
        examples = contract.get("pattern_examples", [])
        if not patterns or not examples:
            continue
        for pattern in patterns:
            if not any(_check_expected_patterns(ex, [pattern]) for ex in examples):
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


@pytest.mark.parametrize(
    "skill_name",
    ["review-design", "classify-experiment-type"],
)
def test_experiment_type_output_has_allowed_values(skill_name: str) -> None:
    """Skills that emit experiment_type must declare allowed_values matching
    VALID_EXPERIMENT_TYPES — the canonical set shared across the phoropter pipeline."""
    contracts = load_yaml(_CONTRACTS_YAML)
    skill = contracts["skills"][skill_name]
    outputs = {o["name"]: o for o in skill["outputs"]}
    et_output = outputs.get("experiment_type")
    assert et_output is not None, f"{skill_name} must declare experiment_type output"
    assert "allowed_values" in et_output, (
        f"{skill_name}: experiment_type output must have allowed_values constraint. "
        "Without it, invalid values propagate silently."
    )
    assert set(et_output["allowed_values"]) == VALID_EXPERIMENT_TYPES


def test_classify_and_apply_in_tier3_between_review_and_resolve() -> None:
    """classify-experiment-type and apply-review-dimensions must be positioned
    between review-design and resolve-design-review in defaults.yaml tier3."""
    defaults = load_yaml(_CONTRACTS_YAML.parent.parent / "config" / "defaults.yaml")
    tier3 = defaults["skills"]["tier3"]
    rd_idx = tier3.index("review-design")
    rdr_idx = tier3.index("resolve-design-review")
    cet_idx = tier3.index("classify-experiment-type")
    ard_idx = tier3.index("apply-review-dimensions")
    assert rd_idx < cet_idx < rdr_idx, (
        f"classify-experiment-type (idx={cet_idx}) must be between "
        f"review-design (idx={rd_idx}) and resolve-design-review (idx={rdr_idx})"
    )
    assert rd_idx < ard_idx < rdr_idx, (
        f"apply-review-dimensions (idx={ard_idx}) must be between "
        f"review-design (idx={rd_idx}) and resolve-design-review (idx={rdr_idx})"
    )


def test_classify_experiment_type_pattern_examples_cover_all_types(
    skills: dict[str, Any],
) -> None:
    """classify-experiment-type pattern_examples must cover all 5 experiment types."""
    examples = skills["classify-experiment-type"].get("pattern_examples", [])
    example_text = "\n".join(examples)
    for et in sorted(VALID_EXPERIMENT_TYPES):
        assert f"experiment_type = {et}" in example_text, (
            f"Missing pattern_example for experiment_type={et}"
        )


def test_apply_review_dimensions_pattern_examples_minimum_count(
    skills: dict[str, Any],
) -> None:
    """apply-review-dimensions must have >=3 pattern_examples covering normal,
    edge case (no evaluation_dashboard), and silent-type short-circuit."""
    examples = skills["apply-review-dimensions"].get("pattern_examples", [])
    assert len(examples) >= 3, (
        f"apply-review-dimensions has {len(examples)} pattern_examples, need >=3: "
        "normal run, edge case (findings only), silent-type short-circuit"
    )


def test_review_design_has_scope_report_input(skills: dict[str, Any]) -> None:
    """review-design contract must declare scope_report as an optional input."""
    rd = skills["review-design"]
    input_names = [i["name"] for i in rd["inputs"]]
    assert "scope_report" in input_names
    scope_input = next(i for i in rd["inputs"] if i["name"] == "scope_report")
    assert scope_input.get("required") is False


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


_EXP_LENS_SKILLS: Final[list[str]] = sorted(
    name for name in load_yaml(_CONTRACTS_YAML).get("skills", {}) if name.startswith("exp-lens-")
)


@pytest.mark.parametrize("skill_name", _EXP_LENS_SKILLS)
def test_exp_lens_experiment_plan_path_is_required(
    skills: dict[str, Any], skill_name: str
) -> None:
    """experiment_plan_path must be required: true on all exp-lens contracts."""
    contract = skills[skill_name]
    inp = next(
        (i for i in contract["inputs"] if i["name"] == "experiment_plan_path"),
        None,
    )
    assert inp is not None, f"{skill_name}: missing experiment_plan_path input"
    assert inp["required"] is True, f"{skill_name}: experiment_plan_path must be required: true"


@pytest.mark.parametrize("skill_name", _EXP_LENS_SKILLS)
def test_exp_lens_context_path_remains_optional(skills: dict[str, Any], skill_name: str) -> None:
    """context_path must remain required: false on all exp-lens contracts."""
    contract = skills[skill_name]
    inp = next(
        (i for i in contract["inputs"] if i["name"] == "context_path"),
        None,
    )
    assert inp is not None, f"{skill_name}: missing context_path input"
    assert inp["required"] is False, f"{skill_name}: context_path must remain required: false"


def test_skill_contracts_yaml_includes_prepare_research_pr(skills):
    """prepare-research-pr must be registered with prep_path output pattern."""
    _assert_skill_has_patterns(skills, "prepare-research-pr", r"prep_path[ \t]*=[ \t]*/.+")


def test_skill_contracts_yaml_includes_compose_research_pr(skills):
    """compose-research-pr must be registered with pr_url output pattern."""
    _assert_skill_has_patterns(
        skills, "compose-research-pr", r"pr_url[ \t]*=[ \t]*https://github\.com/.*/pull/\d+"
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


def test_infrastructure_missing_verdict_present_in_contract() -> None:
    """T7: resolve-failures contract must include no_test_infrastructure verdict.

    The no_test_infrastructure verdict is emitted when test_check detects that
    the worktree lacks test infrastructure (no Taskfile, command not in PATH).
    """
    contracts = load_yaml(_CONTRACTS_YAML)
    rf = contracts["skills"]["resolve-failures"]
    outputs = {o["name"]: o for o in rf["outputs"]}
    verdict_output = outputs.get("verdict")
    assert verdict_output is not None, "resolve-failures must declare verdict output"
    assert "no_test_infrastructure" in verdict_output["allowed_values"]


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
                if not _check_expected_patterns(example, [pattern]):
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
    contracts = load_yaml(_CONTRACTS_YAML)
    allowed: set[str] = set()
    for skill_data in contracts.get("skills", {}).values():
        for o in skill_data.get("outputs", []):
            if o.get("name") == "verdict":
                allowed.update(o.get("allowed_values", []))
    assert allowed, "No verdict output found in skill_contracts.yaml"

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
        "(?:%%REVIEW_GATE::(LOOP_REQUIRED|CLEAR)%%|verdict[ \\t]*=[ \\t]*approved_with_comments)"
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


def test_skill_contracts_yaml_includes_setup_environment(skills: dict[str, Any]) -> None:
    """setup-environment must be registered with env_mode and verdict patterns."""
    _assert_skill_has_patterns(
        skills,
        "setup-environment",
        r"env_mode[ \t]*=[ \t]*(none|docker|micromamba-host|unavailable)",
    )
    contract = skills["setup-environment"]
    patterns = contract["expected_output_patterns"]
    assert any("verdict" in p for p in patterns), "missing verdict pattern"
    assert any("env_report" in p for p in patterns), "missing env_report pattern"


def test_setup_environment_env_mode_examples_cover_all_modes(
    skills: dict[str, Any],
) -> None:
    """Pattern examples must cover all four env_mode values."""
    contract = skills["setup-environment"]
    examples = contract.get("pattern_examples", [])
    example_text = "\n".join(examples)
    for mode in ("none", "docker", "micromamba-host", "unavailable"):
        assert f"env_mode = {mode}" in example_text, f"missing example for env_mode={mode}"


def test_negative_examples_rejected(skills: dict[str, Any]) -> None:
    """For every skill with negative_examples, each negative example must fail at least
    one pattern.

    Negative examples represent outputs that MUST NOT pass contract validation.
    If all patterns match a negative example, the contract has a false positive.
    """
    failures = []
    for skill_name, contract in skills.items():
        patterns = contract.get("expected_output_patterns", [])
        neg_examples = contract.get("negative_examples", [])
        if not patterns or not neg_examples:
            continue
        for neg_ex in neg_examples:
            if all(re.search(p, neg_ex) for p in patterns):
                failures.append(
                    f"Skill '{skill_name}': negative example matched ALL patterns "
                    f"(must fail at least one):\n  {neg_ex!r}"
                )
    assert not failures, "Negative examples passed all contract patterns:\n" + "\n".join(failures)


# Matches both \s* and [ \t]* whitespace quantifier forms as they appear in loaded YAML strings.
_TOKEN_VALUE_RE = re.compile(r"^([\w-]+)(?:\\s\*|\[ \\t\]\*)=(?:\\s\*|\[ \\t\]\*)(.+)$")


def test_patterns_reject_empty_token_values(skills: dict[str, Any]) -> None:
    """Mandatory value patterns must reject the empty-value case.

    For each pattern with an extractable token=value form, verify that
    an empty value (\'{token} = \\n\') does not produce a false match.
    Patterns whose value group allows empty (re.fullmatch succeeds) are exempt.
    """
    failures = []
    for skill_name, contract in skills.items():
        for pattern in contract.get("expected_output_patterns", []):
            m = _TOKEN_VALUE_RE.match(pattern)
            if not m:
                continue
            token_name, value_group = m.group(1), m.group(2)
            try:
                if re.fullmatch(value_group, ""):
                    continue
            except re.error:
                continue
            synthetic = f"{token_name} = \n"
            if re.search(pattern, synthetic):
                failures.append(
                    f"Skill '{skill_name}': pattern {pattern!r} matched empty value "
                    f"synthetic input {synthetic!r}"
                )
    assert not failures, (
        "Patterns matched empty token values (mandatory patterns must reject empty values):\n"
        + "\n".join(failures)
    )


def test_cross_newline_patterns_anchored(skills: dict[str, Any]) -> None:
    r"""Patterns using \s* adjacent to = must not match cross-newline token values.

    A pattern like 'token\s*=\s*\S+' with \s* (which matches newlines) allows
    cross-newline false positives: 'token = \nother_token = value' matches because
    \s* consumes the newline and \S+ latches onto 'other_token'. Patterns using
    [ \t]* (horizontal whitespace only) are exempt.
    """
    failures = []
    for skill_name, contract in skills.items():
        for pattern in contract.get("expected_output_patterns", []):
            m = _TOKEN_VALUE_RE.match(pattern)
            if not m:
                continue
            token_name = m.group(1)
            if "\\s*" not in pattern:
                continue
            synthetic = f"{token_name} = \nother_token = something\n%%ORDER_UP%%"
            if re.search(pattern, synthetic):
                failures.append(
                    f"Skill '{skill_name}': pattern {pattern!r} matched cross-newline "
                    r"input (\s* consumes newlines — use [ \t]* instead)"
                )
    assert not failures, (
        r"Patterns with \s* matched cross-newline inputs:" + "\n" + "\n".join(failures)
    )


def test_pattern_examples_are_not_trivial(skills: dict[str, Any]) -> None:
    """For every skill with expected_output_patterns and pattern_examples, no pattern_example
    may be identical to any of its patterns (after stripping whitespace).

    Trivial examples (example == pattern) produce a circular test that passes regardless
    of normalizer behavior — they cannot detect formatting-related adjudication failures.
    Examples must contain realistic model output context surrounding the token.
    """
    failures = []
    for skill_name, contract in skills.items():
        patterns = contract.get("expected_output_patterns", [])
        examples = contract.get("pattern_examples", [])
        if not patterns or not examples:
            continue
        for example in examples:
            for pattern in patterns:
                if example.strip() == pattern.strip():
                    failures.append(
                        f"Skill '{skill_name}': pattern_example {example!r} is identical "
                        f"to pattern {pattern!r} — add surrounding context to the example"
                    )
    assert not failures, (
        "Trivial pattern_examples detected (example == pattern). "
        "Examples must contain surrounding model output context:\n" + "\n".join(failures)
    )


def test_delimiter_patterns_have_hr_split_example(skills: dict[str, Any]) -> None:
    r"""For every skill with a ---X--- delimiter pattern, at least one pattern_example must
    contain the HR-split variant (``---\n`` followed by the token name suffix).

    This ensures the normalizer's HR-split handling is exercised by contract tests,
    not just the inline delimiter happy path.
    """
    import re as _re

    failures = []
    for skill_name, contract in skills.items():
        patterns = contract.get("expected_output_patterns", [])
        examples = contract.get("pattern_examples", [])
        if not patterns or not examples:
            continue
        for pattern in patterns:
            if not _re.match(r"^---[/\w]", pattern):
                continue
            name_suffix = pattern[3:]
            hr_split_variant = f"---\n{name_suffix}"
            if not any(hr_split_variant in ex for ex in examples):
                failures.append(
                    f"Skill '{skill_name}': pattern {pattern!r} has no pattern_example "
                    f"containing the HR-split variant {hr_split_variant!r}"
                )
    assert not failures, (
        "Delimiter patterns lack HR-split examples. Add an example with "
        "---\\n<name>--- to each listed skill:\n" + "\n".join(failures)
    )


# ---------------------------------------------------------------------------
# make-plan false-positive escape valve tests
# ---------------------------------------------------------------------------


def test_make_plan_verdict_output(skills):
    """make-plan must declare a verdict output with plan/false_positive values."""
    mp = skills["make-plan"]
    verdict_outputs = [o for o in mp["outputs"] if o["name"] == "verdict"]
    assert len(verdict_outputs) == 1, "make-plan must have exactly one verdict output"
    assert set(verdict_outputs[0]["allowed_values"]) == {"plan", "false_positive"}


def test_make_plan_conditional_write_behavior(skills):
    """make-plan must use conditional write_behavior gated on verdict=plan."""
    mp = skills["make-plan"]
    assert mp["write_behavior"] == "conditional"
    assert mp["write_expected_when"] == ["verdict[ \\t]*=[ \\t]*plan"]


def test_make_plan_examples_cover_verdicts(skills):
    """pattern_examples must include examples for both verdict values."""
    mp = skills["make-plan"]
    examples_text = "\n".join(mp.get("pattern_examples", []))
    assert "verdict = plan" in examples_text
    assert "verdict = false_positive" in examples_text
