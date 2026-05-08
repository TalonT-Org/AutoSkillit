"""Tests for contract semantic rules."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import autoskillit.recipe.rules.rules_contracts as _rc
from autoskillit.core.paths import pkg_root
from autoskillit.core.types import Severity
from autoskillit.recipe.contracts import ResultFieldSpec, SkillContract
from autoskillit.recipe.io import load_recipe
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.schema import Recipe, RecipeStep

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_rule_flags_skills_with_empty_output_patterns() -> None:
    """The missing-output-patterns rule exists and emits no warnings on bundled recipes."""
    recipe_path = pkg_root() / "recipes" / "implementation.yaml"
    recipe = load_recipe(recipe_path)
    findings = run_semantic_rules(recipe)
    pattern_findings = [f for f in findings if f.rule == "missing-output-patterns"]
    # With all patterns populated, no warnings should fire
    assert not pattern_findings, (
        f"missing-output-patterns rule fired {len(pattern_findings)} warning(s): "
        + "; ".join(f.message for f in pattern_findings)
    )


def test_pattern_examples_match_rule_fires_on_mismatch(monkeypatch) -> None:
    """pattern-examples-match fires as ERROR when pattern doesn't match any example."""
    manifest = {
        "version": "0.1.0",
        "skills": {
            "audit-impl": {
                "inputs": [],
                "outputs": [{"name": "verdict", "type": "string"}],
                "expected_output_patterns": ["verdict\\s*=\\s*(GO|NO GO)"],
                "pattern_examples": ["verdict = NO_GO\n%%ORDER_UP%%"],  # underscore won't match
            }
        },
    }
    monkeypatch.setattr(_rc, "load_bundled_manifest", lambda: manifest)

    recipe = Recipe(
        name="test",
        description="test",
        steps={
            "run_audit": RecipeStep(
                tool="run_skill",
                on_success="done",
                on_failure="done",
                with_args={"skill_command": "/autoskillit:audit-impl plan.md", "cwd": "/tmp"},
            ),
            "done": RecipeStep(action="stop", message="done"),
        },
        kitchen_rules=["test"],
    )
    findings = run_semantic_rules(recipe)
    rule_findings = [f for f in findings if f.rule == "pattern-examples-match"]
    assert len(rule_findings) == 1
    assert rule_findings[0].severity == Severity.ERROR


def test_missing_pattern_examples_rule_fires_when_examples_absent(monkeypatch) -> None:
    """missing-pattern-examples fires as WARNING when patterns exist but examples absent."""
    manifest = {
        "version": "0.1.0",
        "skills": {
            "audit-impl": {
                "inputs": [],
                "outputs": [{"name": "verdict", "type": "string"}],
                "expected_output_patterns": ["verdict\\s*=\\s*(GO|NO GO)"],
                # No pattern_examples key
            }
        },
    }
    monkeypatch.setattr(_rc, "load_bundled_manifest", lambda: manifest)

    recipe = Recipe(
        name="test",
        description="test",
        steps={
            "run_audit": RecipeStep(
                tool="run_skill",
                on_success="done",
                on_failure="done",
                with_args={"skill_command": "/autoskillit:audit-impl plan.md", "cwd": "/tmp"},
            ),
            "done": RecipeStep(action="stop", message="done"),
        },
        kitchen_rules=["test"],
    )
    findings = run_semantic_rules(recipe)
    rule_findings = [f for f in findings if f.rule == "missing-pattern-examples"]
    assert len(rule_findings) == 1
    assert rule_findings[0].severity == Severity.WARNING


# ---------------------------------------------------------------------------
# write-behavior-consistency rule tests
# ---------------------------------------------------------------------------


def _make_recipe_with_skill(skill_command: str) -> Recipe:
    """Create a minimal recipe with a single run_skill step."""
    step = RecipeStep(
        tool="run_skill",
        with_args={"skill_command": skill_command},
    )
    return Recipe(
        name="test-recipe",
        description="Test recipe for write-behavior-consistency rule",
        version="0.1.0",
        steps={"test_step": step},
    )


def _make_contract(
    *,
    write_behavior: str | None = None,
    write_expected_when: list[str] | None = None,
) -> SkillContract:
    return SkillContract(
        inputs=[],
        outputs=[],
        write_behavior=write_behavior,
        write_expected_when=write_expected_when or [],
    )


def test_write_behavior_invalid_value_flagged() -> None:
    """Invalid write_behavior value triggers an error finding."""
    recipe = _make_recipe_with_skill("/autoskillit:make-plan task")
    contract = _make_contract(write_behavior="invalid")
    with patch(
        "autoskillit.recipe.rules.rules_contracts.get_skill_contract",
        return_value=contract,
    ):
        findings = run_semantic_rules(recipe)
    wb_findings = [f for f in findings if f.rule == "write-behavior-consistency"]
    assert any("Invalid write_behavior" in f.message for f in wb_findings)


def test_conditional_without_patterns_flagged() -> None:
    """conditional without write_expected_when triggers an error."""
    recipe = _make_recipe_with_skill("/autoskillit:make-plan task")
    contract = _make_contract(write_behavior="conditional", write_expected_when=[])
    with patch(
        "autoskillit.recipe.rules.rules_contracts.get_skill_contract",
        return_value=contract,
    ):
        findings = run_semantic_rules(recipe)
    wb_findings = [f for f in findings if f.rule == "write-behavior-consistency"]
    assert any("requires non-empty write_expected_when" in f.message for f in wb_findings)


def test_always_with_patterns_flagged() -> None:
    """always with write_expected_when triggers a warning."""
    recipe = _make_recipe_with_skill("/autoskillit:make-plan task")
    contract = _make_contract(write_behavior="always", write_expected_when=["pattern"])
    with patch(
        "autoskillit.recipe.rules.rules_contracts.get_skill_contract",
        return_value=contract,
    ):
        findings = run_semantic_rules(recipe)
    wb_findings = [f for f in findings if f.rule == "write-behavior-consistency"]
    assert any("must not have write_expected_when" in f.message for f in wb_findings)


def test_invalid_regex_in_patterns_flagged() -> None:
    """Invalid regex in write_expected_when triggers an error."""
    recipe = _make_recipe_with_skill("/autoskillit:make-plan task")
    contract = _make_contract(write_behavior="conditional", write_expected_when=["[invalid"])
    with patch(
        "autoskillit.recipe.rules.rules_contracts.get_skill_contract",
        return_value=contract,
    ):
        findings = run_semantic_rules(recipe)
    wb_findings = [f for f in findings if f.rule == "write-behavior-consistency"]
    assert any("Invalid regex" in f.message for f in wb_findings)


def test_valid_write_behavior_no_findings_on_bundled_recipes() -> None:
    """All bundled recipes must pass write-behavior-consistency without findings."""
    recipes_dir = pkg_root() / "recipes"
    recipe_files = sorted(recipes_dir.glob("*.yaml"))
    assert recipe_files, "No bundled recipes found"

    for recipe_path in recipe_files:
        recipe = load_recipe(recipe_path)
        findings = run_semantic_rules(recipe)
        wb_findings = [f for f in findings if f.rule == "write-behavior-consistency"]
        assert not wb_findings, (
            f"write-behavior-consistency fired on {recipe_path.name}: "
            + "; ".join(f.message for f in wb_findings)
        )


# ---------------------------------------------------------------------------
# always-has-no-write-exit rule tests
# ---------------------------------------------------------------------------


def test_always_write_skill_with_documented_no_write_exit_flagged() -> None:
    """Phrase set for always-has-no-write-exit rule is populated with expected patterns.

    Verifies that the frozenset contains phrases covering the known no-write exit
    patterns (e.g. 'may be 0' from resolve-failures, graceful degradation from
    resolve-review). An empty or incomplete set would silently miss all bugs.
    """
    from autoskillit.recipe.rules.rules_contracts import _ALWAYS_WITH_NO_WRITE_EXIT_PHRASES

    # Verify the phrase set is populated (not empty — would miss all bugs)
    assert len(_ALWAYS_WITH_NO_WRITE_EXIT_PHRASES) > 0
    assert any("may be 0" in p for p in _ALWAYS_WITH_NO_WRITE_EXIT_PHRASES), (
        "Must detect 'may be 0' — the exact phrase in resolve-failures Step 4 "
        "that the investigation identified as the no-write exit signal"
    )
    assert any(
        "graceful" in p.lower() or "skip" in p.lower() for p in _ALWAYS_WITH_NO_WRITE_EXIT_PHRASES
    ), "Must detect graceful degradation phrases — the exact pattern in resolve-review Step 1"


def test_template_args_do_not_skip_rule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Skills with template ARGUMENTS (not template names) must be checked.

    '${{' in skill_command arguments must NOT bypass contract rules.
    Only '${{' in the skill NAME should bypass (handled by resolve_skill_name).
    """
    manifest = {
        "version": "0.1.0",
        "skills": {
            "prepare-pr": {
                "inputs": [],
                "outputs": [{"name": "prep_path", "type": "file_path"}],
                "expected_output_patterns": ["prep_path\\s*=\\s*/.+"],
                "pattern_examples": ["prep_path = /tmp/prep.md"],
                "write_behavior": "always",
                "write_expected_when": [],
            }
        },
    }
    monkeypatch.setattr(_rc, "load_bundled_manifest", lambda: manifest)

    recipe = _make_recipe_with_skill(
        "/autoskillit:prepare-pr ${{ context.plan_path }} ${{ inputs.branch }}"
    )

    skill_dir = tmp_path / "skills_extended" / "prepare-pr"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "If context exhausted, emit prep_path = (empty) — graceful degradation."
    )
    monkeypatch.setattr(_rc, "pkg_root", lambda: tmp_path)

    findings = run_semantic_rules(recipe)
    exit_findings = [f for f in findings if f.rule == "always-has-no-write-exit"]
    assert len(exit_findings) >= 1, (
        "always-has-no-write-exit must fire on prepare-pr even when "
        "skill_command contains template arguments"
    )


def test_unreadable_skill_md_emits_warning_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OSError reading SKILL.md must emit a WARNING finding, not a silent skip."""
    skill_dir = tmp_path / "skills" / "fake-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("placeholder")

    monkeypatch.setattr(_rc, "pkg_root", lambda: tmp_path)

    recipe = _make_recipe_with_skill("/autoskillit:fake-skill")
    contract = _make_contract(write_behavior="always")
    original_read_text = Path.read_text

    def fail_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self.name == "SKILL.md":
            raise OSError("permission denied")
        return original_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    with (
        patch(
            "autoskillit.recipe.rules.rules_contracts.get_skill_contract", return_value=contract
        ),
        patch.object(Path, "read_text", fail_read_text),
    ):
        findings = run_semantic_rules(recipe)

    relevant = [f for f in findings if f.rule == "always-has-no-write-exit"]
    assert len(relevant) == 1
    assert relevant[0].severity == Severity.WARNING
    assert relevant[0].rule == "always-has-no-write-exit"
    assert relevant[0].step_name == "test_step"
    assert "fake-skill" in relevant[0].message


# ---------------------------------------------------------------------------
# result-field-drift tests
# ---------------------------------------------------------------------------


class TestResultFieldDriftRule:
    def test_matching_result_fields_no_finding(self) -> None:
        """result_fields that exactly match PHASE_REQUIRED_KEYS must not fire."""
        recipe = _make_recipe_with_skill(
            "/autoskillit:planner-generate-phases {{AUTOSKILLIT_TEMP}}/planner/analysis.json"
        )
        contract = SkillContract(
            inputs=[],
            outputs=[],
            result_fields=[
                ResultFieldSpec(name="id", type="str", required=True),
                ResultFieldSpec(name="name", type="str", required=True),
                ResultFieldSpec(name="ordering", type="int", required=True),
            ],
        )
        with patch(
            "autoskillit.recipe.rules.rules_contracts.get_skill_contract",
            return_value=contract,
        ):
            findings = run_semantic_rules(recipe)
        drift = [f for f in findings if f.rule == "result-field-drift"]
        assert drift == []

    def test_extra_required_field_fires_error(self) -> None:
        """A required field in result_fields that is NOT in the TypedDict fires ERROR."""
        recipe = _make_recipe_with_skill(
            "/autoskillit:planner-generate-phases {{AUTOSKILLIT_TEMP}}/planner/analysis.json"
        )
        contract = SkillContract(
            inputs=[],
            outputs=[],
            result_fields=[
                ResultFieldSpec(name="id", type="str", required=True),
                ResultFieldSpec(name="name", type="str", required=True),
                ResultFieldSpec(name="ordering", type="int", required=True),
                ResultFieldSpec(name="phantom_field", type="str", required=True),
            ],
        )
        with patch(
            "autoskillit.recipe.rules.rules_contracts.get_skill_contract",
            return_value=contract,
        ):
            findings = run_semantic_rules(recipe)
        drift = [f for f in findings if f.rule == "result-field-drift"]
        assert len(drift) == 1
        assert drift[0].severity == Severity.ERROR
        assert "phantom_field" in drift[0].message
        assert "extra in contract" in drift[0].message

    def test_missing_required_field_fires_error(self) -> None:
        """A TypedDict required key absent from result_fields fires ERROR."""
        recipe = _make_recipe_with_skill(
            "/autoskillit:planner-generate-phases {{AUTOSKILLIT_TEMP}}/planner/analysis.json"
        )
        contract = SkillContract(
            inputs=[],
            outputs=[],
            result_fields=[
                ResultFieldSpec(name="id", type="str", required=True),
                # 'name' and 'ordering' are missing
            ],
        )
        with patch(
            "autoskillit.recipe.rules.rules_contracts.get_skill_contract",
            return_value=contract,
        ):
            findings = run_semantic_rules(recipe)
        drift = [f for f in findings if f.rule == "result-field-drift"]
        assert len(drift) == 1
        assert drift[0].severity == Severity.ERROR
        assert "missing from contract" in drift[0].message

    def test_optional_extra_field_ignored(self) -> None:
        """A field with required=False in result_fields that is not in the TypedDict is ignored."""
        recipe = _make_recipe_with_skill(
            "/autoskillit:planner-generate-phases {{AUTOSKILLIT_TEMP}}/planner/analysis.json"
        )
        contract = SkillContract(
            inputs=[],
            outputs=[],
            result_fields=[
                ResultFieldSpec(name="id", type="str", required=True),
                ResultFieldSpec(name="name", type="str", required=True),
                ResultFieldSpec(name="ordering", type="int", required=True),
                ResultFieldSpec(name="optional_extra", type="str", required=False),
            ],
        )
        with patch(
            "autoskillit.recipe.rules.rules_contracts.get_skill_contract",
            return_value=contract,
        ):
            findings = run_semantic_rules(recipe)
        drift = [f for f in findings if f.rule == "result-field-drift"]
        assert drift == []

    def test_skill_without_result_fields_in_contract_skipped(self) -> None:
        """Skills not in _RESULT_FIELD_DRIFT_SKILLS are ignored by the rule."""
        recipe = _make_recipe_with_skill("/autoskillit:implement-worktree-no-merge")
        findings = run_semantic_rules(recipe)
        drift = [f for f in findings if f.rule == "result-field-drift"]
        assert drift == []

    def test_planner_recipe_has_no_result_field_drift(self) -> None:
        """The bundled planner recipe must not trigger result-field-drift."""
        recipe = load_recipe(pkg_root() / "recipes" / "planner.yaml")
        findings = run_semantic_rules(recipe)
        drift = [f for f in findings if f.rule == "result-field-drift"]
        assert drift == [], f"result-field-drift fired on planner recipe: {drift}"


# ---------------------------------------------------------------------------
# example-covers-all-allowed-values rule tests
# ---------------------------------------------------------------------------


def test_example_covers_all_allowed_values_fires_on_missing_example(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """example-covers-all-allowed-values fires ERROR when an allowed_value has no example."""
    manifest = {
        "version": "0.1.0",
        "skills": {
            "test-skill": {
                "inputs": [],
                "outputs": [
                    {"name": "verdict", "type": "string", "allowed_values": ["go", "stop"]}
                ],
                "expected_output_patterns": ["verdict\\s*=\\s*(go|stop)"],
                "pattern_examples": ["verdict = go\n%%ORDER_UP%%"],  # only "go", missing "stop"
            }
        },
    }
    monkeypatch.setattr(_rc, "load_bundled_manifest", lambda: manifest)
    recipe = _make_recipe_with_skill("/autoskillit:test-skill")
    findings = run_semantic_rules(recipe)
    rule_findings = [f for f in findings if f.rule == "example-covers-all-allowed-values"]
    assert len(rule_findings) >= 1, (
        "example-covers-all-allowed-values must fire when allowed_value 'stop' "
        "has no corresponding pattern_examples entry"
    )
    assert rule_findings[0].severity == Severity.ERROR
    assert "stop" in rule_findings[0].message


def test_example_covers_all_allowed_values_passes_when_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """example-covers-all-allowed-values must not fire when all allowed_values have examples."""
    manifest = {
        "version": "0.1.0",
        "skills": {
            "test-skill": {
                "inputs": [],
                "outputs": [
                    {"name": "verdict", "type": "string", "allowed_values": ["go", "stop"]}
                ],
                "expected_output_patterns": ["verdict\\s*=\\s*(go|stop)"],
                "pattern_examples": [
                    "verdict = go\n%%ORDER_UP%%",
                    "verdict = stop\n%%ORDER_UP%%",
                ],
            }
        },
    }
    monkeypatch.setattr(_rc, "load_bundled_manifest", lambda: manifest)
    recipe = _make_recipe_with_skill("/autoskillit:test-skill")
    findings = run_semantic_rules(recipe)
    rule_findings = [f for f in findings if f.rule == "example-covers-all-allowed-values"]
    assert rule_findings == [], (
        "example-covers-all-allowed-values must not fire when both 'go' and 'stop' "
        "appear in pattern_examples"
    )


# ---------------------------------------------------------------------------
# all-examples-match-all-patterns rule tests
# ---------------------------------------------------------------------------


def test_all_examples_match_all_patterns_fires_on_conditional_pattern(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """all-examples-match-all-patterns fires ERROR when an example fails a pattern."""
    manifest = {
        "version": "0.1.0",
        "skills": {
            "test-skill": {
                "inputs": [],
                "outputs": [{"name": "verdict", "type": "string"}],
                "expected_output_patterns": [
                    "verdict\\s*=\\s*(go|stop)",
                    "%%TAG%%",
                ],
                "pattern_examples": [
                    "verdict = go\n%%TAG%%",  # matches both patterns
                    "verdict = stop",  # missing %%TAG%% — fails second pattern
                ],
            }
        },
    }
    monkeypatch.setattr(_rc, "load_bundled_manifest", lambda: manifest)
    recipe = _make_recipe_with_skill("/autoskillit:test-skill")
    findings = run_semantic_rules(recipe)
    rule_findings = [f for f in findings if f.rule == "all-examples-match-all-patterns"]
    assert len(rule_findings) >= 1, (
        "all-examples-match-all-patterns must fire when 'verdict = stop' example "
        "does not match the '%%TAG%%' pattern"
    )
    assert rule_findings[0].severity == Severity.ERROR
    assert "%%TAG%%" in rule_findings[0].message or "verdict = stop" in rule_findings[0].message


def test_all_examples_match_all_patterns_passes_when_all_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """all-examples-match-all-patterns must not fire when all examples match all patterns."""
    manifest = {
        "version": "0.1.0",
        "skills": {
            "test-skill": {
                "inputs": [],
                "outputs": [{"name": "verdict", "type": "string"}],
                "expected_output_patterns": [
                    "verdict\\s*=\\s*(go|stop)",
                    "%%TAG%%",
                ],
                "pattern_examples": [
                    "verdict = go\n%%TAG%%",
                    "verdict = stop\n%%TAG%%",
                ],
            }
        },
    }
    monkeypatch.setattr(_rc, "load_bundled_manifest", lambda: manifest)
    recipe = _make_recipe_with_skill("/autoskillit:test-skill")
    findings = run_semantic_rules(recipe)
    rule_findings = [f for f in findings if f.rule == "all-examples-match-all-patterns"]
    assert rule_findings == [], (
        "all-examples-match-all-patterns must not fire when all examples match all patterns"
    )


def test_all_examples_match_all_patterns_skips_without_examples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """all-examples-match-all-patterns must not fire when there are no pattern_examples."""
    manifest = {
        "version": "0.1.0",
        "skills": {
            "test-skill": {
                "inputs": [],
                "outputs": [{"name": "verdict", "type": "string"}],
                "expected_output_patterns": ["verdict\\s*=\\s*(go|stop)"],
                # No pattern_examples
            }
        },
    }
    monkeypatch.setattr(_rc, "load_bundled_manifest", lambda: manifest)
    recipe = _make_recipe_with_skill("/autoskillit:test-skill")
    findings = run_semantic_rules(recipe)
    rule_findings = [f for f in findings if f.rule == "all-examples-match-all-patterns"]
    assert rule_findings == [], (
        "all-examples-match-all-patterns must not fire without pattern_examples "
        "(defer to missing-pattern-examples rule)"
    )


# ---------------------------------------------------------------------------
# Integration test: all bundled recipes pass the new contract immunity rules
# ---------------------------------------------------------------------------


def test_bundled_recipes_pass_all_new_contract_rules() -> None:
    """All bundled recipes must pass the three new contract immunity rules with zero ERRORs."""
    new_rules = {
        "example-covers-all-allowed-values",
        "all-examples-match-all-patterns",
        "on-result-values-in-allowed-values",
    }
    recipes_dir = pkg_root() / "recipes"
    recipe_files = sorted(recipes_dir.glob("*.yaml"))
    assert recipe_files, "No bundled recipes found"

    violations: list[str] = []
    for recipe_path in recipe_files:
        recipe = load_recipe(recipe_path)
        findings = run_semantic_rules(recipe)
        for f in findings:
            if f.rule in new_rules and f.severity == Severity.ERROR:
                violations.append(f"{recipe_path.name}:{f.step_name}: [{f.rule}] {f.message}")

    assert not violations, (
        "New contract immunity rules fired on bundled recipes "
        "(ensure Part A contract fixes are applied):\n" + "\n".join(violations)
    )
