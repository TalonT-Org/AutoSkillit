"""Tests for worktree-safety semantic rules."""

from __future__ import annotations

from autoskillit.core.types import Severity
from autoskillit.recipe.io import (
    _parse_recipe,
    _parse_step,
    builtin_recipes_dir,
    load_recipe,
)
from autoskillit.recipe.schema import (
    Recipe,
)
from autoskillit.recipe.validator import (
    run_semantic_rules,
)

# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------


def _make_workflow(steps: dict[str, dict]) -> Recipe:
    parsed_steps = {name: _parse_step(data) for name, data in steps.items()}
    return Recipe(name="test", description="test", steps=parsed_steps, kitchen_rules=["test"])


# ---------------------------------------------------------------------------
# Worktree-retry tests
# ---------------------------------------------------------------------------


def test_retry_worktree_cwd_inputs_triggers_error() -> None:
    """retry-worktree step with cwd=inputs.* fires retry-worktree-cwd ERROR."""
    wf = _make_workflow(
        {
            "impl": {
                "tool": "run_skill_retry",
                "with": {"skill_command": "/autoskillit:implement-worktree-no-merge the plan"},
                "capture": {"worktree_path": "${{ result.worktree_path }}"},
                "retry": {"on": "needs_retry", "max_attempts": 1, "on_exhausted": "retry_step"},
                "on_success": "done",
            },
            "retry_step": {
                "tool": "run_skill_retry",
                "with": {
                    "skill_command": "/autoskillit:retry-worktree ${{ context.worktree_path }}",
                    "cwd": "${{ inputs.work_dir }}",
                },
                "retry": {"on": "needs_retry", "max_attempts": 3, "on_exhausted": "done"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert any(f.rule == "retry-worktree-cwd" for f in errors)


def test_retry_worktree_cwd_context_clean() -> None:
    """retry-worktree step with cwd=context.worktree_path has no retry-worktree-cwd finding."""
    wf = _make_workflow(
        {
            "impl": {
                "tool": "run_skill_retry",
                "with": {"skill_command": "/autoskillit:implement-worktree-no-merge the plan"},
                "capture": {"worktree_path": "${{ result.worktree_path }}"},
                "retry": {"on": "needs_retry", "max_attempts": 1, "on_exhausted": "retry_step"},
                "on_success": "done",
            },
            "retry_step": {
                "tool": "run_skill_retry",
                "with": {
                    "skill_command": "/autoskillit:retry-worktree ${{ context.worktree_path }}",
                    "cwd": "${{ context.worktree_path }}",
                },
                "retry": {"on": "needs_retry", "max_attempts": 3, "on_exhausted": "done"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "retry-worktree-cwd" for f in findings)


def test_retry_worktree_cwd_missing_triggers_error() -> None:
    """retry-worktree step with no cwd fires retry-worktree-cwd ERROR."""
    wf = _make_workflow(
        {
            "retry_step": {
                "tool": "run_skill_retry",
                "with": {
                    "skill_command": "/autoskillit:retry-worktree ${{ context.worktree_path }}",
                },
                "retry": {"on": "needs_retry", "max_attempts": 3, "on_exhausted": "done"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert any(f.rule == "retry-worktree-cwd" for f in errors)


def test_retry_worktree_cwd_non_skill_step_ignored() -> None:
    """retry-worktree-cwd rule only fires on skill steps, not run_cmd."""
    wf = _make_workflow(
        {
            "cmd": {
                "tool": "run_cmd",
                "with": {"cmd": "echo hello", "cwd": "${{ inputs.work_dir }}"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    assert not any(f.rule == "retry-worktree-cwd" for f in findings)


def test_worktree_retry_creates_new_triggers() -> None:
    wf = _make_workflow(
        {
            "implement": {
                "tool": "run_skill_retry",
                "with": {
                    "skill_command": (
                        "/autoskillit:implement-worktree-no-merge ${{ context.plan_path }}"
                    ),
                },
                "retry": {"on": "needs_retry", "max_attempts": 3, "on_exhausted": "retry_wt"},
                "capture": {"worktree_path": "${{ result.worktree_path }}"},
                "on_success": "done",
            },
            "retry_wt": {
                "tool": "run_skill_retry",
                "with": {
                    "skill_command": (
                        "/autoskillit:retry-worktree "
                        "${{ context.plan_path }} ${{ context.worktree_path }}"
                    ),
                },
                "retry": {"on": "needs_retry", "max_attempts": 3, "on_exhausted": "done"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert any(
        f.rule == "worktree-retry-creates-new" and "implement" in f.step_name for f in errors
    )


def test_worktree_retry_max_one_triggers_needs_retry_no_restart() -> None:
    wf = _make_workflow(
        {
            "implement": {
                "tool": "run_skill_retry",
                "with": {
                    "skill_command": (
                        "/autoskillit:implement-worktree-no-merge ${{ context.plan_path }}"
                    ),
                },
                "retry": {"on": "needs_retry", "max_attempts": 1, "on_exhausted": "retry_wt"},
                "capture": {"worktree_path": "${{ result.worktree_path }}"},
                "on_success": "done",
            },
            "retry_wt": {
                "tool": "run_skill_retry",
                "with": {
                    "skill_command": (
                        "/autoskillit:retry-worktree "
                        "${{ context.plan_path }} ${{ context.worktree_path }}"
                    ),
                },
                "retry": {"on": "needs_retry", "max_attempts": 3, "on_exhausted": "done"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    # Original assertion: no worktree-retry-creates-new violations (still valid)
    assert not any(f.rule == "worktree-retry-creates-new" for f in findings)
    # New assertion: the needs-retry-no-restart rule DOES fire for this pattern
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert any(
        f.rule == "needs-retry-no-restart" and "implement" in f.step_name for f in errors
    ), "Expected needs-retry-no-restart ERROR — max_attempts:1 with needs_retry is forbidden"


def test_needs_retry_on_worktree_creating_skill_with_attempts_is_error() -> None:
    """max_attempts >= 1 AND on=needs_retry on a worktree-creating skill must be ERROR."""
    wf = _make_workflow(
        {
            "implement": {
                "tool": "run_skill_retry",
                "with": {
                    "skill_command": (
                        "/autoskillit:implement-worktree-no-merge ${{ context.plan_path }}"
                    ),
                },
                "retry": {"on": "needs_retry", "max_attempts": 1, "on_exhausted": "retry_wt"},
                "capture": {"worktree_path": "${{ result.worktree_path }}"},
                "on_success": "done",
            },
            "retry_wt": {
                "tool": "run_skill_retry",
                "with": {
                    "skill_command": (
                        "/autoskillit:retry-worktree "
                        "${{ context.plan_path }} ${{ context.worktree_path }}"
                    ),
                },
                "retry": {"on": "needs_retry", "max_attempts": 3, "on_exhausted": "done"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert any(
        f.rule == "needs-retry-no-restart" and "implement" in f.step_name for f in errors
    ), f"Expected needs-retry-no-restart ERROR on implement step, got: {findings}"


def test_needs_retry_worktree_creating_skill_max_attempts_zero_is_clean() -> None:
    """max_attempts: 0 with on=needs_retry on worktree-creating skill must pass."""
    wf = _make_workflow(
        {
            "implement": {
                "tool": "run_skill_retry",
                "with": {
                    "skill_command": (
                        "/autoskillit:implement-worktree-no-merge ${{ context.plan_path }}"
                    ),
                },
                "retry": {"on": "needs_retry", "max_attempts": 0, "on_exhausted": "retry_wt"},
                "capture": {"worktree_path": "${{ result.worktree_path }}"},
                "on_success": "done",
            },
            "retry_wt": {
                "tool": "run_skill_retry",
                "with": {
                    "skill_command": (
                        "/autoskillit:retry-worktree "
                        "${{ context.plan_path }} ${{ context.worktree_path }}"
                    ),
                },
                "retry": {"on": "needs_retry", "max_attempts": 3, "on_exhausted": "done"},
                "on_success": "done",
            },
            "done": {"action": "stop", "message": "Done."},
        }
    )
    findings = run_semantic_rules(wf)
    errors = [f for f in findings if f.severity == Severity.ERROR]
    assert not any(f.rule == "needs-retry-no-restart" for f in errors), (
        f"Unexpected needs-retry-no-restart ERROR with max_attempts=0: {findings}"
    )


# ---------------------------------------------------------------------------
# TestCloneRootAsWorktreeRule
# ---------------------------------------------------------------------------


class TestCloneRootAsWorktreeRule:
    def test_crw1_rule_in_registry(self) -> None:
        """T_CRW1: clone-root-as-worktree is registered in _RULE_REGISTRY."""
        from autoskillit.recipe.validator import _RULE_REGISTRY

        assert "clone-root-as-worktree" in {r.name for r in _RULE_REGISTRY}

    def _bad_recipe_test_check(self) -> Recipe:
        """Helper: recipe where work_dir is captured from clone_path, test_check uses it."""
        return _parse_recipe(
            {
                "name": "bad-recipe",
                "description": "test",
                "kitchen_rules": ["NEVER use native tools"],
                "steps": {
                    "clone": {
                        "python": "autoskillit.workspace.clone.clone_repo",
                        "with": {"source_dir": "/src", "run_name": "r"},
                        "capture": {"work_dir": "${{ result.clone_path }}"},
                        "on_success": "test",
                        "on_failure": "stop_err",
                    },
                    "test": {
                        "tool": "test_check",
                        "with": {"worktree_path": "${{ context.work_dir }}"},
                        "on_success": "stop_ok",
                        "on_failure": "stop_err",
                    },
                    "stop_ok": {"action": "stop", "message": "ok"},
                    "stop_err": {"action": "stop", "message": "err"},
                },
            }
        )

    def test_crw2_rule_fires_for_test_check(self) -> None:
        """T_CRW2: ERROR when test_check passes work_dir (from clone_path) as worktree_path."""
        recipe = self._bad_recipe_test_check()
        findings = run_semantic_rules(recipe)
        crw = [f for f in findings if f.rule == "clone-root-as-worktree"]
        assert len(crw) >= 1
        assert crw[0].severity == Severity.ERROR
        assert crw[0].step_name == "test"

    def test_crw3_rule_fires_for_merge_worktree(self) -> None:
        """T_CRW3: ERROR when merge_worktree passes work_dir (from clone_path) as worktree_path."""
        recipe = _parse_recipe(
            {
                "name": "bad-merge",
                "description": "test",
                "kitchen_rules": ["NEVER use native tools"],
                "steps": {
                    "clone": {
                        "python": "autoskillit.workspace.clone.clone_repo",
                        "with": {"source_dir": "/src", "run_name": "r"},
                        "capture": {"work_dir": "${{ result.clone_path }}"},
                        "on_success": "merge",
                        "on_failure": "stop_err",
                    },
                    "merge": {
                        "tool": "merge_worktree",
                        "with": {
                            "worktree_path": "${{ context.work_dir }}",
                            "base_branch": "main",
                        },
                        "capture": {"cleanup_succeeded": "${{ result.cleanup_succeeded }}"},
                        "on_success": "stop_ok",
                        "on_failure": "stop_err",
                    },
                    "stop_ok": {"action": "stop", "message": "ok"},
                    "stop_err": {"action": "stop", "message": "err"},
                },
            }
        )
        findings = run_semantic_rules(recipe)
        crw = [f for f in findings if f.rule == "clone-root-as-worktree"]
        assert len(crw) >= 1
        assert crw[0].severity == Severity.ERROR
        assert crw[0].step_name == "merge"

    def test_crw4_passes_for_worktree_from_result_worktree_path(self) -> None:
        """T_CRW4: no finding when worktree_path captured from result.worktree_path (correct)."""
        recipe = _parse_recipe(
            {
                "name": "good-recipe",
                "description": "test",
                "kitchen_rules": ["NEVER use native tools"],
                "steps": {
                    "implement": {
                        "tool": "run_skill_retry",
                        "with": {
                            "skill_command": "/autoskillit:implement-worktree-no-merge plan.md"
                        },
                        "capture": {"implementation_ref": "${{ result.worktree_path }}"},
                        "on_success": "test",
                        "on_failure": "stop_err",
                    },
                    "test": {
                        "tool": "test_check",
                        "with": {"worktree_path": "${{ context.implementation_ref }}"},
                        "on_success": "stop_ok",
                        "on_failure": "stop_err",
                    },
                    "stop_ok": {"action": "stop", "message": "ok"},
                    "stop_err": {"action": "stop", "message": "err"},
                },
            }
        )
        findings = run_semantic_rules(recipe)
        crw = [f for f in findings if f.rule == "clone-root-as-worktree"]
        assert crw == []

    def test_crw5_bundled_recipes_pass_clone_root_rule(self) -> None:
        """T_CRW5: no bundled recipe triggers clone-root-as-worktree."""
        bd = builtin_recipes_dir()
        for yaml_path in sorted(bd.glob("*.yaml")):
            recipe = load_recipe(yaml_path)
            findings = run_semantic_rules(recipe)
            crw = [f for f in findings if f.rule == "clone-root-as-worktree"]
            assert crw == [], f"{yaml_path.name} triggered clone-root-as-worktree: {crw}"
