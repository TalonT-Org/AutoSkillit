"""Tests for data-flow semantic rules — handoff consumer, callable contracts, python capture."""

from __future__ import annotations

import textwrap

import pytest
import yaml

from autoskillit.core.types import Severity
from autoskillit.recipe.io import _parse_recipe
from autoskillit.recipe.validator import run_semantic_rules
from tests.recipe.conftest import _make_workflow

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


# ---------------------------------------------------------------------------
# TestUncapturedHandoffConsumerRule (1e–1h)
# ---------------------------------------------------------------------------


class TestUncapturedHandoffConsumerRule:
    def test_uncaptured_handoff_consumer_rule_is_registered(self) -> None:
        """1e: uncaptured-handoff-consumer rule is in _RULE_REGISTRY."""
        from autoskillit.recipe.registry import _RULE_REGISTRY

        assert any(spec.name == "uncaptured-handoff-consumer" for spec in _RULE_REGISTRY)

    def test_uncaptured_handoff_consumer_fires_for_empty_output_skill(self) -> None:
        """1f: rule fires WARNING when outputs:[] producer precedes file_path consumer.

        Uses audit-friction (real contract: outputs: []) as producer and
        review-approach (real contract: optional plan_path: file_path) as consumer.
        The consumer's skill_command does NOT include context.plan_path.
        """
        steps = {
            "friction": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:audit-friction"},
                "on_success": "review",
            },
            "review": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:review-approach some topic"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
        recipe = _make_workflow(steps)
        findings = run_semantic_rules(recipe)
        handoff = [f for f in findings if f.rule == "uncaptured-handoff-consumer"]
        assert len(handoff) >= 1
        assert any(f.severity == Severity.WARNING for f in handoff)
        assert any("plan_path" in f.message for f in handoff)

    def test_uncaptured_handoff_consumer_silent_when_context_ref_present(self) -> None:
        """1g: rule is silent when consumer's file-path input is wired via context ref."""
        steps = {
            "friction": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:audit-friction"},
                "on_success": "review",
            },
            "review": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:review-approach ${{ context.plan_path }}"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
        recipe = _make_workflow(steps)
        findings = run_semantic_rules(recipe)
        handoff = [f for f in findings if f.rule == "uncaptured-handoff-consumer"]
        assert handoff == []

    def test_uncaptured_handoff_consumer_silent_when_no_file_path_inputs(self) -> None:
        """1h: rule is silent when consumer has no file-path or directory-path inputs.

        Uses audit-friction (outputs: []) as producer and make-plan (single task: string
        input — no file-path inputs) as consumer. The rule must be silent.
        """
        steps = {
            "friction": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:audit-friction"},
                "on_success": "plan",
            },
            "plan": {
                "tool": "run_skill",
                "with": {"skill_command": "/autoskillit:make-plan some task"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
        recipe = _make_workflow(steps)
        findings = run_semantic_rules(recipe)
        handoff = [f for f in findings if f.rule == "uncaptured-handoff-consumer"]
        assert handoff == []


# ---------------------------------------------------------------------------
# TestCallableContracts
# ---------------------------------------------------------------------------


class TestCallableContracts:
    """Tests for callable output contracts (run_python steps)."""

    def test_callable_contract_check_review_loop(self) -> None:
        """The callable contract for check_review_loop must declare its output fields."""
        from autoskillit.recipe.contracts import get_callable_contract

        contract = get_callable_contract("autoskillit.smoke_utils.check_review_loop")
        assert contract is not None
        declared = {out.name for out in contract.outputs}
        assert "next_iteration" in declared
        assert "max_exceeded" in declared

    def test_callable_contract_missing_returns_none(self) -> None:
        """get_callable_contract returns None for unknown callable paths."""
        from autoskillit.recipe.contracts import get_callable_contract

        contract = get_callable_contract("nonexistent.module.function")
        assert contract is None


# ---------------------------------------------------------------------------
# TestPythonCaptureOutputCoverageRule
# ---------------------------------------------------------------------------


class TestPythonCaptureOutputCoverageRule:
    def test_python_capture_declared_key_no_warning(self) -> None:
        """A run_python capture referencing a declared callable output key
        must not produce an undeclared-python-capture-key warning."""
        recipe_yaml = textwrap.dedent("""\
            name: python-capture-valid
            description: test
            steps:
              check:
                tool: run_python
                with:
                  callable: "autoskillit.smoke_utils.check_review_loop"
                  pr_number: "42"
                  cwd: "/tmp"
                capture:
                  loop_count: "${{ result.next_iteration }}"
                on_result:
                  - when: "${{ result.max_exceeded }} == false"
                    route: review
                  - route: done
              review:
                tool: run_skill
                with:
                  skill_command: /autoskillit:review-pr
                on_success: done
                on_failure: done
              done:
                action: stop
                message: Done
        """)
        recipe = _parse_recipe(yaml.safe_load(recipe_yaml))
        findings = run_semantic_rules(recipe)
        undeclared = [f for f in findings if f.rule == "undeclared-python-capture-key"]
        assert undeclared == []

    def test_python_capture_undeclared_key_fires_warning(self) -> None:
        """A run_python step referencing result.nonexistent must fire a warning."""
        recipe_yaml = textwrap.dedent("""\
            name: python-capture-invalid
            description: test
            steps:
              check:
                tool: run_python
                with:
                  callable: "autoskillit.smoke_utils.check_review_loop"
                  pr_number: "42"
                  cwd: "/tmp"
                capture:
                  bad: "${{ result.nonexistent }}"
                on_success: done
                on_failure: done
              done:
                action: stop
                message: Done
        """)
        recipe = _parse_recipe(yaml.safe_load(recipe_yaml))
        findings = run_semantic_rules(recipe)
        undeclared = [f for f in findings if f.rule == "undeclared-python-capture-key"]
        assert len(undeclared) == 1
        assert undeclared[0].severity == Severity.WARNING
        assert "nonexistent" in undeclared[0].message

    def test_python_capture_no_contract_fires_warning(self) -> None:
        """A run_python step with an unknown callable must warn about missing contract."""
        recipe_yaml = textwrap.dedent("""\
            name: python-capture-no-contract
            description: test
            steps:
              check:
                tool: run_python
                with:
                  callable: "some.unknown.callable"
                capture:
                  val: "${{ result.output }}"
                on_success: done
                on_failure: done
              done:
                action: stop
                message: Done
        """)
        recipe = _parse_recipe(yaml.safe_load(recipe_yaml))
        findings = run_semantic_rules(recipe)
        undeclared = [f for f in findings if f.rule == "undeclared-python-capture-key"]
        assert len(undeclared) == 1
        assert "no callable contract entry" in undeclared[0].message

    def test_python_on_result_undeclared_ref_fires_warning(self) -> None:
        """A run_python on_result condition referencing an undeclared field must fire."""
        recipe_yaml = textwrap.dedent("""\
            name: python-on-result-invalid
            description: test
            steps:
              check:
                tool: run_python
                with:
                  callable: "autoskillit.smoke_utils.check_review_loop"
                  pr_number: "42"
                  cwd: "/tmp"
                on_result:
                  - when: "${{ result.has_blocking }} == true"
                    route: review
                  - route: done
              review:
                tool: run_skill
                with:
                  skill_command: /autoskillit:review-pr
                on_success: done
                on_failure: done
              done:
                action: stop
                message: Done
        """)
        recipe = _parse_recipe(yaml.safe_load(recipe_yaml))
        findings = run_semantic_rules(recipe)
        undeclared = [f for f in findings if f.rule == "undeclared-python-capture-key"]
        assert len(undeclared) == 1
        assert "has_blocking" in undeclared[0].message

    def test_non_python_step_not_flagged(self) -> None:
        """run_skill steps must not be flagged by the python capture rule."""
        recipe_yaml = textwrap.dedent("""\
            name: skill-step-ignored
            description: test
            steps:
              run:
                tool: run_skill
                with:
                  skill_command: /autoskillit:implement-worktree-no-merge ${{ inputs.plan }}
                capture:
                  wp: "${{ result.worktree_path }}"
                on_success: done
                on_failure: done
              done:
                action: stop
                message: Done
        """)
        recipe = _parse_recipe(yaml.safe_load(recipe_yaml))
        findings = run_semantic_rules(recipe)
        undeclared = [f for f in findings if f.rule == "undeclared-python-capture-key"]
        assert undeclared == []
