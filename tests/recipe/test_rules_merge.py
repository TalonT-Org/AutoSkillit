"""Tests for recipe/rules_merge.py semantic rules."""

from __future__ import annotations

import pytest

from autoskillit.core import Severity
from autoskillit.recipe._analysis import _build_step_graph, bfs_reachable
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.registry import run_semantic_rules
from autoskillit.recipe.rules.rules_merge import _RECOVERABLE_FAILED_STEPS, _TERMINAL_FAILED_STEPS
from autoskillit.recipe.schema import Recipe, RecipeStep, StepResultCondition, StepResultRoute

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


# ---------------------------------------------------------------------------
# T-DM-1: DIRTY_MAIN_REPO in _RECOVERABLE_FAILED_STEPS
# ---------------------------------------------------------------------------


def test_dirty_main_repo_in_recoverable_steps() -> None:
    """MergeFailedStep.DIRTY_MAIN_REPO must be in _RECOVERABLE_FAILED_STEPS."""
    from autoskillit.core.types import MergeFailedStep

    assert MergeFailedStep.DIRTY_MAIN_REPO in _RECOVERABLE_FAILED_STEPS, (
        "DIRTY_MAIN_REPO must be recoverable so the merge worktree can be retried "
        "after main_repo_guard cleans dirty state"
    )


# T-DM-2
@pytest.mark.parametrize("recipe_name", ["implementation", "remediation", "implementation-groups"])
def test_bundled_recipes_route_dirty_main_repo(recipe_name: str) -> None:
    """All bundled recipes must route DIRTY_MAIN_REPO in their merge_worktree on_result."""
    from autoskillit.core.types import MergeFailedStep

    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")

    merge_steps = {
        name: step
        for name, step in recipe.steps.items()
        if getattr(step, "tool", None) == "merge_worktree"
    }
    assert merge_steps, f"{recipe_name}: no merge_worktree step found"

    for step_name, step in merge_steps.items():
        if step.on_result is None:
            continue  # tested separately by other rules
        matched = set()
        for cond in step.on_result.conditions:
            if cond.when is None:
                continue
            if "dirty_main_repo" in cond.when.lower():
                matched.add(MergeFailedStep.DIRTY_MAIN_REPO)
        assert MergeFailedStep.DIRTY_MAIN_REPO in matched, (
            f"{recipe_name}: merge_worktree step '{step_name}' does not route "
            f"DIRTY_MAIN_REPO in on_result. Add a condition like "
            f"${{{{ result.failed_step == 'DIRTY_MAIN_REPO' }}}} to route to the "
            f"appropriate recovery step."
        )


def test_every_merge_failed_step_is_classified() -> None:
    """Every MergeFailedStep enum member must appear in exactly one of
    _RECOVERABLE_FAILED_STEPS or _TERMINAL_FAILED_STEPS."""
    from autoskillit.core.types import MergeFailedStep

    all_values = {member.value for member in MergeFailedStep}
    classified = _RECOVERABLE_FAILED_STEPS | _TERMINAL_FAILED_STEPS
    overlap = _RECOVERABLE_FAILED_STEPS & _TERMINAL_FAILED_STEPS

    assert overlap == set(), (
        f"Steps appear in BOTH recoverable and terminal sets: {sorted(overlap)}"
    )
    assert all_values == classified, (
        f"Unclassified MergeFailedStep members: {sorted(all_values - classified)}. "
        f"Every new MergeFailedStep value must be added to either "
        f"_RECOVERABLE_FAILED_STEPS or _TERMINAL_FAILED_STEPS in rules_merge.py."
    )


def test_ref_coherence_in_recoverable_steps() -> None:
    """MergeFailedStep.REF_COHERENCE must be in _RECOVERABLE_FAILED_STEPS."""
    from autoskillit.core.types import MergeFailedStep

    assert MergeFailedStep.REF_COHERENCE in _RECOVERABLE_FAILED_STEPS, (
        "REF_COHERENCE must be recoverable so the recipe can push the diverged "
        "branch and retry the merge"
    )


@pytest.mark.parametrize("recipe_name", ["implementation", "remediation", "implementation-groups"])
def test_bundled_recipes_pass_merge_routing_incomplete(recipe_name: str) -> None:
    """All bundled recipes must have zero merge-routing-incomplete findings."""
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-routing-incomplete"]
    assert flagged == [], f"{recipe_name}: merge-routing-incomplete findings: {flagged}"


@pytest.mark.parametrize("recipe_name", ["implementation", "remediation", "implementation-groups"])
def test_bundled_recipes_route_ref_coherence(recipe_name: str) -> None:
    """All bundled recipes must route REF_COHERENCE in their merge_worktree on_result."""

    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")

    merge_steps = {
        name: step
        for name, step in recipe.steps.items()
        if getattr(step, "tool", None) == "merge_worktree"
    }
    assert merge_steps, f"{recipe_name}: no merge_worktree step found"

    for step_name, step in merge_steps.items():
        assert step.on_result is not None
        conditions = step.on_result.conditions
        ancestry_matches = [
            (index, condition)
            for index, condition in enumerate(conditions)
            if condition.when
            and "ref_coherence" in condition.when.lower()
            and "remote_is_ancestor" in condition.when.lower()
        ]
        fallback_matches = [
            (index, condition)
            for index, condition in enumerate(conditions)
            if condition.when
            and "ref_coherence" in condition.when.lower()
            and "remote_is_ancestor" not in condition.when.lower()
        ]

        assert len(ancestry_matches) == 1, (
            f"{recipe_name}: merge_worktree step '{step_name}' must have exactly one "
            "ancestry-aware REF_COHERENCE arm"
        )
        assert len(fallback_matches) == 1, (
            f"{recipe_name}: merge_worktree step '{step_name}' must have exactly one "
            "REF_COHERENCE fallback arm"
        )

        ancestry_index, ancestry_condition = ancestry_matches[0]
        fallback_index, fallback_condition = fallback_matches[0]
        assert ancestry_index < fallback_index
        assert fallback_condition.route == "release_issue_failure"

        graph = _build_step_graph(recipe)
        reachable = bfs_reachable(graph, ancestry_condition.route) | {ancestry_condition.route}
        assert any(
            recipe.steps[step_name].tool == "push_to_remote"
            for step_name in reachable
            if step_name in recipe.steps
        ), f"{recipe_name}: route {ancestry_condition.route} cannot reach push_to_remote"


def _make_recipe(steps: dict[str, RecipeStep]) -> Recipe:
    """Minimal recipe factory for rules_merge tests."""
    return Recipe(
        name="test-rules-merge",
        description="Test recipe for merge routing rules.",
        version="0.2.0",
        kitchen_rules=["test"],
        steps=steps,
    )


def _conditions_for(*step_values: str) -> list[StepResultCondition]:
    """Build on_result conditions for the given failed_step values."""
    return [
        StepResultCondition(
            route="recover",
            when=f"result.failed_step == '{v}'",
        )
        for v in step_values
    ]


def test_no_merge_worktree_step_is_clean() -> None:
    """Recipe without merge_worktree → no findings."""
    recipe = _make_recipe(
        {
            "entry": RecipeStep(tool="run_cmd", with_args={"cmd": "echo x"}, on_success="done"),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-routing-incomplete"]
    assert flagged == []


def test_merge_worktree_no_on_result_is_clean() -> None:
    """merge_worktree present but no on_result → no finding."""
    recipe = _make_recipe(
        {
            "merge": RecipeStep(
                tool="merge_worktree",
                with_args={"worktree_path": "/tmp/wt", "base_branch": "main"},
                on_success="done",
                on_failure="done",
            ),
            "recover": RecipeStep(action="stop", message="recover"),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-routing-incomplete"]
    assert flagged == []


def test_merge_worktree_all_recoverable_steps_routed_is_clean() -> None:
    """on_result conditions cover all four recoverable steps → no finding."""
    all_values = list(_RECOVERABLE_FAILED_STEPS)
    recipe = _make_recipe(
        {
            "merge": RecipeStep(
                tool="merge_worktree",
                with_args={"worktree_path": "/tmp/wt", "base_branch": "main"},
                on_result=StepResultRoute(conditions=_conditions_for(*all_values)),
                on_success="done",
                on_failure="done",
            ),
            "recover": RecipeStep(action="stop", message="recover"),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-routing-incomplete"]
    assert flagged == []


def test_merge_worktree_missing_one_recoverable_step_is_error() -> None:
    """Covers all but one recoverable step → ERROR, message lists the missing one."""
    all_values = list(_RECOVERABLE_FAILED_STEPS)
    present = all_values[:-1]  # all but the last
    missing = all_values[-1]

    recipe = _make_recipe(
        {
            "merge": RecipeStep(
                tool="merge_worktree",
                with_args={"worktree_path": "/tmp/wt", "base_branch": "main"},
                on_result=StepResultRoute(conditions=_conditions_for(*present)),
                on_success="done",
                on_failure="done",
            ),
            "recover": RecipeStep(action="stop", message="recover"),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-routing-incomplete"]
    assert len(flagged) == 1
    assert flagged[0].severity == Severity.ERROR
    assert missing in flagged[0].message


def test_merge_worktree_missing_all_recoverable_steps_is_error() -> None:
    """has on_result but no step matches _RECOVERABLE_FAILED_STEPS → ERROR, all in message."""
    recipe = _make_recipe(
        {
            "merge": RecipeStep(
                tool="merge_worktree",
                with_args={"worktree_path": "/tmp/wt", "base_branch": "main"},
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(route="done", when="result.failed_step == 'other'")
                    ]
                ),
                on_success="done",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-routing-incomplete"]
    assert len(flagged) == 1
    assert flagged[0].severity == Severity.ERROR
    for step_value in _RECOVERABLE_FAILED_STEPS:
        assert step_value in flagged[0].message


def test_merge_without_commit_guard_fires() -> None:
    """test_check → merge_worktree with no commit_guard predecessor → ERROR."""
    recipe = _make_recipe(
        {
            "test": RecipeStep(
                tool="test_check",
                with_args={"worktree_path": "/tmp/wt"},
                on_success="merge",
                on_failure="abort",
            ),
            "merge": RecipeStep(
                tool="merge_worktree",
                with_args={"worktree_path": "/tmp/wt", "base_branch": "main"},
                on_success="done",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
            "abort": RecipeStep(action="stop", message="abort"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-without-commit-guard"]
    assert len(flagged) == 1, (
        f"Expected exactly 1 merge-without-commit-guard ERROR, got: {flagged}"
    )
    assert flagged[0].severity == Severity.ERROR
    assert flagged[0].step_name == "merge"


def test_merge_with_commit_guard_step_name_is_clean() -> None:
    """Step named commit_guard* immediately before merge_worktree → no finding."""
    recipe = _make_recipe(
        {
            "test": RecipeStep(
                tool="test_check",
                with_args={"worktree_path": "/tmp/wt"},
                on_success="commit_guard",
                on_failure="abort",
            ),
            "commit_guard": RecipeStep(
                tool="run_cmd",
                with_args={
                    "cmd": (
                        "cd /tmp/wt && "
                        'if [ -n "$(git status --porcelain)" ]; '
                        "then git add -A && git commit -m 'chore: pending'; fi"
                    ),
                    "cwd": "/tmp/wt",
                },
                on_success="merge",
                on_failure="merge",
            ),
            "merge": RecipeStep(
                tool="merge_worktree",
                with_args={"worktree_path": "/tmp/wt", "base_branch": "main"},
                on_success="done",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
            "abort": RecipeStep(action="stop", message="abort"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-without-commit-guard"]
    assert flagged == [], f"Unexpected merge-without-commit-guard finding: {flagged}"


def test_merge_with_git_commit_in_cmd_is_clean() -> None:
    """run_cmd step with 'git commit' in cmd immediately before merge → no finding."""
    recipe = _make_recipe(
        {
            "pre_merge": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "git add -A && git commit -m 'fix' || true"},
                on_success="merge",
                on_failure="merge",
            ),
            "merge": RecipeStep(
                tool="merge_worktree",
                with_args={"worktree_path": "/tmp/wt", "base_branch": "main"},
                on_success="done",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-without-commit-guard"]
    assert flagged == [], f"Unexpected merge-without-commit-guard finding: {flagged}"


def test_multiple_merge_steps_each_checked_independently() -> None:
    """Two merge_worktree steps: one complete, one incomplete → one ERROR."""
    all_values = list(_RECOVERABLE_FAILED_STEPS)

    recipe = _make_recipe(
        {
            "merge_ok": RecipeStep(
                tool="merge_worktree",
                with_args={"worktree_path": "/tmp/wt1", "base_branch": "main"},
                on_result=StepResultRoute(conditions=_conditions_for(*all_values)),
                on_success="done",
                on_failure="done",
            ),
            "merge_bad": RecipeStep(
                tool="merge_worktree",
                with_args={"worktree_path": "/tmp/wt2", "base_branch": "main"},
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(
                            route="done", when=f"result.failed_step == '{all_values[0]}'"
                        )
                    ]
                ),
                on_success="done",
                on_failure="done",
            ),
            "recover": RecipeStep(action="stop", message="recover"),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-routing-incomplete"]
    assert len(flagged) == 1
    assert flagged[0].step_name == "merge_bad"


# ---------------------------------------------------------------------------
# merge-fix-cycle-without-iteration-guard rule tests
# ---------------------------------------------------------------------------


def test_merge_fix_cycle_without_guard_fires() -> None:
    """merge→fix→test→merge cycle without check_loop_iteration → ERROR."""
    recipe = _make_recipe(
        {
            "commit_guard": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "git commit -m 'x' || true"},
                on_success="merge",
                on_failure="merge",
            ),
            "merge": RecipeStep(
                tool="merge_worktree",
                with_args={"worktree_path": "/tmp/wt", "base_branch": "main"},
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(route="fix", when="result.failed_step == 'rebase'"),
                    ]
                ),
                on_success="done",
                on_failure="done",
            ),
            "fix": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": "/autoskillit:resolve-failures /tmp/wt",
                    "step_name": "fix",
                },
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(route="test", when="result.verdict == 'real_fix'"),
                    ]
                ),
                on_failure="done",
            ),
            "test": RecipeStep(
                tool="test_check",
                with_args={"worktree_path": "/tmp/wt"},
                on_success="commit_guard",
                on_failure="fix",
            ),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-fix-cycle-without-iteration-guard"]
    assert len(flagged) == 1, f"Expected 1 ERROR, got: {flagged}"
    assert flagged[0].severity == Severity.ERROR


def test_merge_fix_cycle_with_guard_is_clean() -> None:
    """merge→fix→test→check_merge_fix_loop→commit_guard→merge → no finding."""
    recipe = _make_recipe(
        {
            "commit_guard": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "git commit -m 'x' || true"},
                on_success="merge",
                on_failure="merge",
            ),
            "merge": RecipeStep(
                tool="merge_worktree",
                with_args={"worktree_path": "/tmp/wt", "base_branch": "main"},
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(route="fix", when="result.failed_step == 'rebase'"),
                    ]
                ),
                on_success="done",
                on_failure="done",
            ),
            "fix": RecipeStep(
                tool="run_skill",
                with_args={
                    "skill_command": "/autoskillit:resolve-failures /tmp/wt",
                    "step_name": "fix",
                },
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(route="test", when="result.verdict == 'real_fix'"),
                    ]
                ),
                on_failure="done",
            ),
            "test": RecipeStep(
                tool="test_check",
                with_args={"worktree_path": "/tmp/wt"},
                on_success="check_merge_fix_loop",
                on_failure="fix",
            ),
            "check_merge_fix_loop": RecipeStep(
                tool="run_python",
                with_args={
                    "callable": "autoskillit.smoke_utils.check_loop_iteration",
                    "current_iteration": "${{ context.merge_fix_count }}",
                    "max_iterations": "3",
                },
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(
                            route="release_issue_failure",
                            when="${{ result.max_exceeded }} == true",
                        ),
                    ]
                ),
                on_success="commit_guard",
                on_failure="release_issue_failure",
            ),
            "release_issue_failure": RecipeStep(action="stop", message="failure"),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-fix-cycle-without-iteration-guard"]
    assert flagged == [], f"Unexpected finding: {flagged}"


@pytest.mark.parametrize("recipe_name", ["implementation", "remediation", "implementation-groups"])
def test_bundled_recipes_merge_fix_cycle_guarded(recipe_name: str) -> None:
    """All bundled recipes with merge→fix cycle must have check_merge_fix_loop guard."""
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-fix-cycle-without-iteration-guard"]
    assert flagged == [], f"{recipe_name}: {flagged}"


# ---------------------------------------------------------------------------
# gh-pr-merge-silent-success-routing rule tests
# ---------------------------------------------------------------------------


def _make_gh_pr_merge_recipe(*, on_failure: str) -> Recipe:
    """Minimal recipe with a critical gh-pr-merge run_cmd step."""
    return _make_recipe(
        {
            "merge_pr": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "gh pr merge '123' --squash --auto", "cwd": "/tmp/work"},
                on_success="done",
                on_failure=on_failure,
            ),
            "register_clone_success": RecipeStep(action="stop", message="success"),
            "release_issue_failure": RecipeStep(action="stop", message="failure"),
            "verify_queue_enrollment": RecipeStep(
                tool="wait_for_merge_queue",
                with_args={},
                on_failure="release_issue_failure",
            ),
            "done": RecipeStep(action="stop", message="done"),
        }
    )


def _make_cleanup_gh_pr_merge_recipe() -> Recipe:
    """Recipe with an optional cleanup step that uses gh pr merge — exempt from the rule."""
    return _make_recipe(
        {
            "cleanup_merge": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "gh pr merge '123' --squash", "cwd": "/tmp/work"},
                on_success="register_clone_success",
                on_failure="register_clone_success",
                optional=True,
            ),
            "register_clone_success": RecipeStep(action="stop", message="success"),
            "done": RecipeStep(action="stop", message="done"),
        }
    )


def test_rule_fires_on_merge_cmd_failure_to_success_terminal() -> None:
    """A gh-pr-merge step with on_failure=register_clone_success must produce an ERROR finding."""
    recipe = _make_gh_pr_merge_recipe(on_failure="register_clone_success")
    findings = run_semantic_rules(recipe)
    assert any(
        f.rule == "gh-pr-merge-silent-success-routing" and f.severity == Severity.ERROR
        for f in findings
    )


def test_rule_does_not_fire_on_optional_cleanup_step() -> None:
    """Cleanup steps (optional=True) are exempt from the silent-success-degradation rule."""
    recipe = _make_cleanup_gh_pr_merge_recipe()
    findings = run_semantic_rules(recipe)
    assert not any(f.rule == "gh-pr-merge-silent-success-routing" for f in findings)


def test_rule_does_not_fire_on_correct_escalation_routing() -> None:
    """A merge step routing on_failure to an escalation target produces no finding."""
    recipe = _make_gh_pr_merge_recipe(on_failure="release_issue_failure")
    findings = run_semantic_rules(recipe)
    assert not any(f.rule == "gh-pr-merge-silent-success-routing" for f in findings)


def test_rule_does_not_fire_on_verify_queue_enrollment_routing() -> None:
    """A merge step routing on_failure to verify_queue_enrollment produces no finding."""
    recipe = _make_gh_pr_merge_recipe(on_failure="verify_queue_enrollment")
    findings = run_semantic_rules(recipe)
    assert not any(f.rule == "gh-pr-merge-silent-success-routing" for f in findings)


@pytest.mark.parametrize("recipe_name", ["implementation", "remediation", "implementation-groups"])
def test_bundled_recipes_have_no_silent_success_degradation(recipe_name: str) -> None:
    """No bundled recipe may have a merge step whose on_failure reaches a success terminal."""
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    findings = run_semantic_rules(recipe)
    silent_success = [f for f in findings if f.rule == "gh-pr-merge-silent-success-routing"]
    assert silent_success == [], (
        f"Silent success degradation found in {recipe_name}: {silent_success}"
    )


# ---------------------------------------------------------------------------
# release-issue-on-unconfirmed-merge rule tests
# ---------------------------------------------------------------------------


def test_rule_fires_when_release_issue_reachable_from_timeout() -> None:
    """release_issue reachable from a merge-wait timeout exit → ERROR finding."""
    recipe = _make_recipe(
        {
            "wait_queue": RecipeStep(
                tool="wait_for_merge_queue",
                with_args={},
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(
                            route="release_timeout",
                            when="result.pr_state == timeout",
                        )
                    ]
                ),
                on_success="done",
                on_failure="release_timeout",
            ),
            "release_timeout": RecipeStep(
                tool="release_issue",
                with_args={"issue_url": "owner/repo#1"},
                on_success="done",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    assert any(f.rule == "release-issue-on-unconfirmed-merge" for f in findings), (
        f"Expected release-issue-on-unconfirmed-merge finding, got: {findings}"
    )


def test_rule_does_not_fire_when_register_clone_unconfirmed_used() -> None:
    """Timeout routes to register_clone_status(status=unconfirmed) → no finding."""
    recipe = _make_recipe(
        {
            "wait_queue": RecipeStep(
                tool="wait_for_merge_queue",
                with_args={},
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(
                            route="register_unconf",
                            when="result.pr_state == timeout",
                        )
                    ]
                ),
                on_success="done",
                on_failure="register_unconf",
            ),
            "register_unconf": RecipeStep(
                tool="register_clone_status",
                with_args={"clone_path": "/some/path", "status": "unconfirmed"},
                on_success="done",
                on_failure="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    assert not any(f.rule == "release-issue-on-unconfirmed-merge" for f in findings), (
        f"Unexpected release-issue-on-unconfirmed-merge finding: "
        f"{[f for f in findings if f.rule == 'release-issue-on-unconfirmed-merge']}"
    )


# ---------------------------------------------------------------------------
# merge-enrollment-auto-consistency rule tests
# ---------------------------------------------------------------------------


def test_rule_flags_auto_step_reachable_from_no_auto_route() -> None:
    """A gh pr merge --auto step reachable from an auto_merge_available=false
    routing condition must emit a finding."""
    recipe = _make_recipe(
        {
            "route_queue_mode": RecipeStep(
                action="route",
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(
                            route="enroll_with_auto",
                            when="context.auto_merge_available == 'true'",
                        ),
                        StepResultCondition(
                            route="enroll_no_auto",
                            when="context.auto_merge_available == 'false'",
                        ),
                    ],
                ),
            ),
            "enroll_with_auto": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "gh pr merge '42' --squash --auto", "cwd": "/tmp"},
                on_success="wait_queue",
            ),
            "enroll_no_auto": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "gh pr merge '42' --squash", "cwd": "/tmp"},
                on_success="wait_queue",
                on_failure="reenter",
            ),
            "wait_queue": RecipeStep(
                tool="wait_for_merge_queue",
                with_args={},
                on_success="done",
                on_failure="reenter",
            ),
            "reenter": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "gh pr merge '42' --squash --auto", "cwd": "/tmp"},
                on_success="wait_queue",
            ),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-enrollment-auto-consistency"]
    assert len(flagged) >= 1
    flagged_steps = {f.step_name for f in flagged}
    assert "reenter" in flagged_steps


@pytest.mark.parametrize("recipe_name", ["implementation", "remediation", "implementation-groups"])
def test_merge_enrollment_auto_consistency_passes_after_migration(recipe_name: str) -> None:
    """After migration, no recipe should have --auto steps reachable from
    auto_merge_available=false routing arms."""
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    findings = run_semantic_rules(recipe)
    auto_findings = [f for f in findings if f.rule == "merge-enrollment-auto-consistency"]
    assert auto_findings == [], f"{recipe_name}: {auto_findings}"


def test_rule_passes_when_auto_steps_only_reachable_from_auto_route() -> None:
    """Steps using --auto that are only reachable from auto_merge_available=true
    routing conditions should not emit findings."""
    recipe = _make_recipe(
        {
            "route_queue_mode": RecipeStep(
                action="route",
                on_result=StepResultRoute(
                    conditions=[
                        StepResultCondition(
                            route="enroll_with_auto",
                            when="context.auto_merge_available == 'true'",
                        ),
                        StepResultCondition(
                            route="enroll_no_auto",
                            when="context.auto_merge_available == 'false'",
                        ),
                    ],
                ),
            ),
            "enroll_with_auto": RecipeStep(
                tool="run_cmd",
                with_args={"cmd": "gh pr merge '42' --squash --auto", "cwd": "/tmp"},
                on_success="done",
            ),
            "enroll_no_auto": RecipeStep(
                tool="enqueue_pr",
                with_args={
                    "pr_number": "42",
                    "target_branch": "main",
                    "auto_merge_available": "false",
                },
                on_success="done",
            ),
            "done": RecipeStep(action="stop", message="done"),
        }
    )
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-enrollment-auto-consistency"]
    assert flagged == []


# ---------------------------------------------------------------------------
# T-IPP-1: inter-part push wired between merge and routing step
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("recipe_name", ["implementation", "remediation", "implementation-groups"])
def test_bundled_recipes_push_between_parts(recipe_name: str) -> None:
    """Multi-part recipes must push the feature branch between parts.

    The merge step's success path must reach a push_to_remote step
    before looping back for the next part.  Pre-remediation merge steps
    are excluded — they clean up before replanning, not between parts.
    """
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")

    merge_steps = {
        name: step
        for name, step in recipe.steps.items()
        if getattr(step, "tool", None) == "merge_worktree" and "pre_remediation" not in name
    }
    assert merge_steps, f"{recipe_name}: no merge_worktree step found"

    for step_name, step in merge_steps.items():
        success_route = None
        if step.on_result and step.on_result.conditions:
            for cond in step.on_result.conditions:
                if cond.when is None:
                    success_route = cond.route
                    break
        if success_route is None:
            success_route = step.on_success

        if success_route is None:
            continue

        push_step = recipe.steps.get(success_route)
        assert push_step is not None, f"{step_name} routes to {success_route} which does not exist"
        assert getattr(push_step, "tool", None) == "push_to_remote", (
            f"{step_name} success route {success_route} must be a push_to_remote step, "
            f"got tool={getattr(push_step, 'tool', None)}"
        )


def test_pre_remediation_merge_success_pushes_before_next_merge() -> None:
    """pre_remediation_merge's success fallthrough must reach push_to_remote.

    Without an inter_part_push-pre_remediation step, a successful pre_remediation_merge
    routes to ``remediate`` directly, advancing the local branch without publishing it
    to the remote. This guarantees ref_coherence divergence at the next merge site
    (issue #4274). The fix inserts a push step between pre_remediation_merge's success
    fallthrough and ``remediate``, mirroring the ``merge → inter_part_push`` pattern.
    """
    from autoskillit.recipe._analysis_bfs import _build_success_step_graph

    recipe = load_recipe(builtin_recipes_dir() / "remediation.yaml")

    step = recipe.steps["pre_remediation_merge"]
    success_route = None
    if step.on_result and step.on_result.conditions:
        for cond in step.on_result.conditions:
            if cond.when is None:
                success_route = cond.route
                break
    if success_route is None:
        success_route = step.on_success

    assert success_route is not None, "pre_remediation_merge must have a success route"
    assert success_route != "remediate", (
        "pre_remediation_merge success fallthrough routes directly to remediate "
        "without a push — this is the missing-push root cause of issue #4274"
    )

    graph = _build_success_step_graph(recipe)
    visited = set()
    queue = [success_route]
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        if current not in recipe.steps:
            continue
        current_step = recipe.steps[current]
        if getattr(current_step, "tool", None) == "push_to_remote":
            return  # Found a push_to_remote step on the success fallthrough
        if getattr(current_step, "tool", None) == "merge_worktree":
            pytest.fail(
                f"pre_remediation_merge success fallthrough reaches merge_worktree "
                f"step '{current}' before any push_to_remote — "
                f"the visited path was: {visited}"
            )
        queue.extend(graph.get(current, set()))

    pytest.fail(
        f"pre_remediation_merge success fallthrough never reaches push_to_remote; "
        f"visited: {visited}"
    )


def test_check_ref_push_loop_max_exceeded_routes_through_verify() -> None:
    """Both ref-push guards route max_exceeded to verify_ref_push_exhaustion.

    Issue #4274 compounding factor #4: when max_exceeded fires, the recipe
    routes directly to release_issue_failure, applying a fail label even when
    the local branch is still a clean fast-forward of remote (the benign
    push-recoverable state). The fix inserts ``verify_ref_push_exhaustion``
    as an authoritative re-check that routes the benign case to
    ``register_clone_unconfirmed`` (preserving the in-progress label).
    """
    recipe = load_recipe(builtin_recipes_dir() / "remediation.yaml")

    verify_step = recipe.steps.get("verify_ref_push_exhaustion")
    assert verify_step is not None, (
        "remediation.yaml must define verify_ref_push_exhaustion; "
        "the graceful re-check terminal is missing"
    )
    assert getattr(verify_step, "tool", None) == "run_python", (
        f"verify_ref_push_exhaustion must use run_python to call check_ref_state; "
        f"got tool={getattr(verify_step, 'tool', None)!r}"
    )

    for guard_name in ("check_ref_push_loop", "check_ref_push_loop_pre_remediation"):
        guard = recipe.steps[guard_name]
        max_exceeded_routes = [
            c.route
            for c in (guard.on_result.conditions if guard.on_result else [])
            if c.when and "max_exceeded" in c.when
        ]
        assert len(max_exceeded_routes) == 1, (
            f"{guard_name} must have exactly one max_exceeded route; got {max_exceeded_routes}"
        )
        assert max_exceeded_routes[0] == "verify_ref_push_exhaustion", (
            f"{guard_name} max_exceeded arm must route to "
            f"verify_ref_push_exhaustion for the authoritative re-check; "
            f"got {max_exceeded_routes[0]!r}"
        )


def test_verify_ref_push_exhaustion_routes_benign_state_to_register_clone() -> None:
    """verify_ref_push_exhaustion routes remote_is_ancestor=true to register_clone_unconfirmed.

    When the local branch is a clean fast-forward of remote, the work is
    audit-approved and push-recoverable. The recipe must preserve the
    in-progress label (route to register_clone_unconfirmed), not apply the
    fail label.
    """
    recipe = load_recipe(builtin_recipes_dir() / "remediation.yaml")

    verify_step = recipe.steps["verify_ref_push_exhaustion"]
    assert verify_step.on_result is not None, (
        "verify_ref_push_exhaustion must declare on_result conditions"
    )
    ancestry_route = None
    for cond in verify_step.on_result.conditions:
        if cond.when and "remote_is_ancestor" in cond.when:
            ancestry_route = cond.route
            break
    assert ancestry_route == "register_clone_unconfirmed", (
        f"verify_ref_push_exhaustion must route remote_is_ancestor=true to "
        f"register_clone_unconfirmed (preserves in-progress label); "
        f"got {ancestry_route!r}"
    )


# ---------------------------------------------------------------------------
# Step 1a: merge-routing-cross-site-consistency rule tests
# ---------------------------------------------------------------------------


def _push_recovery_subgraph(terminal: str = "done", *, prefix: str = "") -> dict:
    """Build a subgraph whose ancestry arm reaches a real push_to_remote step.

    check_ref_push_loop -> check_loop_iteration guard -> ref_push (push_to_remote)
    -> retry the merge barrier. This satisfies _classify_recovery_class == "push_recovery".

    When *prefix* is provided, step names and successor references are prefixed
    (e.g. ``"a"`` -> ``"check_ref_push_loop_a"``, ``"ref_push_a"``) so the same
    subgraph can be reused for both merge sites in a single recipe.
    """
    loop_name = f"check_ref_push_loop{prefix}"
    push_name = f"ref_push{prefix}"
    retry_name = f"retry_merge{prefix}"
    return {
        loop_name: RecipeStep(
            tool="run_python",
            with_args={
                "callable": "autoskillit.smoke_utils.check_loop_iteration",
                "current_iteration": "${{ context.ref_push_count }}",
                "max_iterations": "3",
            },
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(
                        route="release_issue_failure",
                        when="${{ result.max_exceeded }} == true",
                    ),
                ]
            ),
            on_success=push_name,
            on_failure="release_issue_failure",
        ),
        push_name: RecipeStep(
            tool="push_to_remote",
            with_args={
                "clone_path": "${{ context.work_dir }}",
                "remote_url": "${{ context.remote_url }}",
                "branch": "${{ context.merge_target }}",
            },
            on_success=retry_name,
            on_failure="release_issue_failure",
        ),
        retry_name: RecipeStep(
            tool="merge_worktree",
            with_args={
                "worktree_path": "${{ context.implementation_ref }}",
                "base_branch": "${{ context.merge_target }}",
            },
            on_success=terminal,
            on_failure="release_issue_failure",
        ),
        "release_issue_failure": RecipeStep(action="stop", message="failed"),
    }


def _direct_remediation_subgraph(terminal: str = "done", *, prefix: str = "") -> dict:
    """Build a subgraph whose ancestry arm reaches make-plan (direct_remediate).

    check_loop_iteration -> make-plan (autoskillit skill) -> terminal.

    When *prefix* is provided, step names and successor references are prefixed
    (e.g. ``"a"`` -> ``"check_direct_loop_a"``, ``"make_plan_a"``).
    """
    loop_name = f"check_direct_loop{prefix}"
    plan_name = f"make_plan{prefix}"
    return {
        loop_name: RecipeStep(
            tool="run_python",
            with_args={
                "callable": "autoskillit.smoke_utils.check_loop_iteration",
                "current_iteration": "${{ context.direct_count }}",
                "max_iterations": "3",
            },
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(
                        route="release_issue_failure",
                        when="${{ result.max_exceeded }} == true",
                    ),
                ]
            ),
            on_success=plan_name,
            on_failure="release_issue_failure",
        ),
        plan_name: RecipeStep(
            tool="run_skill",
            with_args={
                "skill_command": "/autoskillit:make-plan plan.md",
                "cwd": "${{ context.worktree_path }}",
            },
            on_success=terminal,
            on_failure="release_issue_failure",
        ),
        "release_issue_failure": RecipeStep(action="stop", message="failed"),
    }


def _two_merge_recipe(*, site_a_class: str, site_b_class: str) -> Recipe:
    """Build a recipe with two merge_worktree steps.

    site_a_class and site_b_class pick which recovery subgraph the ancestry-aware
    ref_coherence arm at each site reaches: "push" or "direct".
    """
    site_a = (
        _push_recovery_subgraph(terminal="retry_merge_b", prefix="_a")
        if site_a_class == "push"
        else _direct_remediation_subgraph("retry_merge_b", prefix="_a")
    )
    site_b = (
        _push_recovery_subgraph(terminal="done", prefix="_b")
        if site_b_class == "push"
        else _direct_remediation_subgraph("done", prefix="_b")
    )

    common = {
        "merge_a": RecipeStep(
            tool="merge_worktree",
            with_args={
                "worktree_path": "${{ context.implementation_ref }}",
                "base_branch": "main",
            },
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(
                        route="check_ref_push_loop_a"
                        if site_a_class == "push"
                        else "check_direct_loop_a",
                        when=(
                            "result.failed_step == 'ref_coherence' "
                            "and result.remote_is_ancestor == true"
                        ),
                    ),
                    StepResultCondition(
                        route="release_issue_failure",
                        when="result.failed_step == 'ref_coherence'",
                    ),
                    StepResultCondition(
                        route="release_issue_failure",
                        when="result.failed_step == 'dirty_tree'",
                    ),
                    StepResultCondition(
                        route="release_issue_failure",
                        when="result.failed_step == 'test_gate'",
                    ),
                    StepResultCondition(
                        route="release_issue_failure",
                        when="result.failed_step == 'post_rebase_test_gate'",
                    ),
                    StepResultCondition(
                        route="release_issue_failure",
                        when="result.failed_step == 'rebase'",
                    ),
                    StepResultCondition(
                        route="release_issue_failure",
                        when="result.failed_step == 'dirty_main_repo'",
                    ),
                    StepResultCondition(route="done", when=None),
                ]
            ),
            on_success="done",
            on_failure="release_issue_failure",
        ),
        "merge_b": RecipeStep(
            tool="merge_worktree",
            with_args={
                "worktree_path": "${{ context.implementation_ref }}",
                "base_branch": "main",
            },
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(
                        route="check_ref_push_loop_b"
                        if site_b_class == "push"
                        else "check_direct_loop_b",
                        when=(
                            "result.failed_step == 'ref_coherence' "
                            "and result.remote_is_ancestor == true"
                        ),
                    ),
                    StepResultCondition(
                        route="release_issue_failure",
                        when="result.failed_step == 'ref_coherence'",
                    ),
                    StepResultCondition(
                        route="release_issue_failure",
                        when="result.failed_step == 'dirty_tree'",
                    ),
                    StepResultCondition(
                        route="release_issue_failure",
                        when="result.failed_step == 'test_gate'",
                    ),
                    StepResultCondition(
                        route="release_issue_failure",
                        when="result.failed_step == 'post_rebase_test_gate'",
                    ),
                    StepResultCondition(
                        route="release_issue_failure",
                        when="result.failed_step == 'rebase'",
                    ),
                    StepResultCondition(
                        route="release_issue_failure",
                        when="result.failed_step == 'dirty_main_repo'",
                    ),
                    StepResultCondition(route="done", when=None),
                ]
            ),
            on_success="done",
            on_failure="release_issue_failure",
        ),
        "release_issue_failure": RecipeStep(action="stop", message="failed"),
        "done": RecipeStep(action="stop", message="done"),
    }

    # Wire per-site guard names to the matching subgraph. The subgraph helpers
    # already produced prefixed step names when called with prefix="_a"/"_b".
    if site_a_class == "push":
        site_a_subgraph = {
            "check_ref_push_loop_a": site_a["check_ref_push_loop_a"],
            "ref_push_a": site_a["ref_push_a"],
            "retry_merge_a": site_a["retry_merge_a"],
        }
    else:
        site_a_subgraph = {
            "check_direct_loop_a": site_a["check_direct_loop_a"],
            "make_plan_a": site_a["make_plan_a"],
        }
    if site_b_class == "push":
        site_b_subgraph = {
            "check_ref_push_loop_b": site_b["check_ref_push_loop_b"],
            "ref_push_b": site_b["ref_push_b"],
            "retry_merge_b": site_b["retry_merge_b"],
        }
    else:
        site_b_subgraph = {
            "check_direct_loop_b": site_b["check_direct_loop_b"],
            "make_plan_b": site_b["make_plan_b"],
        }

    steps = {**common, **site_a_subgraph, **site_b_subgraph}
    return _make_recipe(steps)


def test_cross_site_consistency_drifts_between_merge_worktree_sites() -> None:
    """Two merge_worktree steps with ancestry arms reaching different recovery
    classes (push vs direct) trigger exactly one cross-site finding.

    The finding must identify both merge steps, their routes, and the observed classes.
    """
    recipe = _two_merge_recipe(site_a_class="push", site_b_class="direct")
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-routing-cross-site-consistency"]
    assert len(flagged) == 1, f"Expected exactly 1 finding, got: {flagged}"
    msg = flagged[0].message
    assert "merge_a" in msg and "merge_b" in msg, (
        f"Finding message must identify both merge steps, got: {msg}"
    )
    assert "push_recovery" in msg and "direct_remediate" in msg, (
        f"Finding message must identify both recovery classes, got: {msg}"
    )


def test_cross_site_consistency_matching_arms_is_clean() -> None:
    """Two merge_worktree steps whose ancestry arms reach the same recovery class
    (push) emit no cross-site finding."""
    recipe = _two_merge_recipe(site_a_class="push", site_b_class="push")
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-routing-cross-site-consistency"]
    assert flagged == [], f"Expected no finding, got: {flagged}"


def test_cross_site_consistency_classified_vs_unclassified_is_mismatch() -> None:
    """A classified-vs-unclassified pair is a mismatch, not silently equivalent.

    site_a reaches push_recovery (classified); site_b has the ancestry arm route
    going to a non-recovery target (unclassified). Different unclassified routes
    at multiple sites also constitute a mismatch.
    """
    # site_b uses an unclassified terminal target for its ancestry arm
    steps = {
        "merge_a": RecipeStep(
            tool="merge_worktree",
            with_args={
                "worktree_path": "${{ context.implementation_ref }}",
                "base_branch": "main",
            },
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(
                        route="check_ref_push_loop_a",
                        when=(
                            "result.failed_step == 'ref_coherence' "
                            "and result.remote_is_ancestor == true"
                        ),
                    ),
                    StepResultCondition(
                        route="release_issue_failure",
                        when="result.failed_step == 'ref_coherence'",
                    ),
                    StepResultCondition(
                        route="release_issue_failure",
                        when="result.failed_step == 'dirty_tree'",
                    ),
                    StepResultCondition(
                        route="release_issue_failure",
                        when="result.failed_step == 'test_gate'",
                    ),
                    StepResultCondition(
                        route="release_issue_failure",
                        when="result.failed_step == 'post_rebase_test_gate'",
                    ),
                    StepResultCondition(
                        route="release_issue_failure",
                        when="result.failed_step == 'rebase'",
                    ),
                    StepResultCondition(
                        route="release_issue_failure",
                        when="result.failed_step == 'dirty_main_repo'",
                    ),
                    StepResultCondition(route="done", when=None),
                ]
            ),
            on_success="done",
            on_failure="release_issue_failure",
        ),
        "merge_b": RecipeStep(
            tool="merge_worktree",
            with_args={
                "worktree_path": "${{ context.implementation_ref }}",
                "base_branch": "main",
            },
            on_result=StepResultRoute(
                conditions=[
                    StepResultCondition(
                        route="merge_b_recovery",
                        when=(
                            "result.failed_step == 'ref_coherence' "
                            "and result.remote_is_ancestor == true"
                        ),
                    ),
                    StepResultCondition(
                        route="release_issue_failure",
                        when="result.failed_step == 'ref_coherence'",
                    ),
                    StepResultCondition(
                        route="release_issue_failure",
                        when="result.failed_step == 'dirty_tree'",
                    ),
                    StepResultCondition(
                        route="release_issue_failure",
                        when="result.failed_step == 'test_gate'",
                    ),
                    StepResultCondition(
                        route="release_issue_failure",
                        when="result.failed_step == 'post_rebase_test_gate'",
                    ),
                    StepResultCondition(
                        route="release_issue_failure",
                        when="result.failed_step == 'rebase'",
                    ),
                    StepResultCondition(
                        route="release_issue_failure",
                        when="result.failed_step == 'dirty_main_repo'",
                    ),
                    StepResultCondition(route="done", when=None),
                ]
            ),
            on_success="done",
            on_failure="release_issue_failure",
        ),
        "check_ref_push_loop_a": RecipeStep(
            tool="run_python",
            with_args={
                "callable": "autoskillit.smoke_utils.check_loop_iteration",
                "current_iteration": "${{ context.ref_push_count }}",
                "max_iterations": "3",
            },
            on_success="ref_push_a",
            on_failure="release_issue_failure",
        ),
        "ref_push_a": RecipeStep(
            tool="push_to_remote",
            with_args={
                "clone_path": "${{ context.work_dir }}",
                "remote_url": "${{ context.remote_url }}",
                "branch": "main",
            },
            on_success="retry_a",
            on_failure="release_issue_failure",
        ),
        "retry_a": RecipeStep(
            tool="merge_worktree",
            with_args={
                "worktree_path": "${{ context.implementation_ref }}",
                "base_branch": "main",
            },
            on_success="done",
            on_failure="release_issue_failure",
        ),
        # unclassified recovery — bare route step, no recovery signature
        "merge_b_recovery": RecipeStep(
            tool="run_python",
            with_args={"callable": "autoskillit.recipe._cmd_rpc.compute_branch"},
            on_success="done",
            on_failure="release_issue_failure",
        ),
        "release_issue_failure": RecipeStep(action="stop", message="failed"),
        "done": RecipeStep(action="stop", message="done"),
    }
    recipe = _make_recipe(steps)
    findings = run_semantic_rules(recipe)
    flagged = [f for f in findings if f.rule == "merge-routing-cross-site-consistency"]
    assert len(flagged) >= 1, (
        f"Expected mismatch finding when classified vs unclassified arms diverge, got: {flagged}"
    )


# ---------------------------------------------------------------------------
# Step 6: _classify_recovery_class integration check
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("recipe_name", ["implementation", "remediation", "implementation-groups"])
def test_bundled_recipes_ancestry_arm_classifies_as_push_recovery(
    recipe_name: str,
) -> None:
    """The ancestry-aware ref_coherence arm must classify as 'push_recovery' for
    every bundled merge_worktree step.

    Supplements test_bundled_recipes_route_ref_coherence with a behavioral
    classification assertion rather than only structural reachability.
    """
    from autoskillit.recipe._analysis import make_validation_context
    from autoskillit.recipe.rules import rules_merge

    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    ctx = make_validation_context(recipe)

    merge_steps = {
        name: step
        for name, step in recipe.steps.items()
        if getattr(step, "tool", None) == "merge_worktree"
    }
    assert merge_steps, f"{recipe_name}: no merge_worktree step found"

    for step_name, step in merge_steps.items():
        assert step.on_result is not None
        ancestry_condition = None
        for condition in step.on_result.conditions:
            if (
                condition.when
                and "ref_coherence" in condition.when.lower()
                and "remote_is_ancestor" in condition.when.lower()
            ):
                ancestry_condition = condition
                break
        assert ancestry_condition is not None, (
            f"{recipe_name}: merge_worktree '{step_name}' missing ancestry arm"
        )
        cls = rules_merge._classify_recovery_class(ancestry_condition.route, ctx)
        assert cls == "push_recovery", (
            f"{recipe_name}: merge '{step_name}' ancestry arm route "
            f"'{ancestry_condition.route}' classified as {cls!r}, expected 'push_recovery'"
        )


# ---------------------------------------------------------------------------
# merge-site-push-symmetry rule tests (issue #4274, Part B Step 9)
# ---------------------------------------------------------------------------


def test_merge_site_push_symmetry_merge_with_push_does_not_fire() -> None:
    """Merge whose success fallthrough reaches push_to_remote — rule must NOT fire."""
    steps = {
        "merge_a": RecipeStep(
            tool="merge_worktree",
            with_args={"worktree_path": "/tmp/a", "base_branch": "main"},
            on_result=StepResultRoute(conditions=[StepResultCondition(route="push_a")]),
        ),
        "push_a": RecipeStep(
            tool="push_to_remote",
            on_success="done",
            on_failure="done",
        ),
        "merge_b": RecipeStep(
            tool="merge_worktree",
            with_args={"worktree_path": "/tmp/b", "base_branch": "main"},
            on_result=StepResultRoute(conditions=[StepResultCondition(route="done")]),
        ),
        "done": RecipeStep(action="stop", message="Done. Emit sentinel: {}"),
    }
    recipe = _make_recipe(steps)
    findings = run_semantic_rules(recipe)
    rule_findings = [f for f in findings if f.rule == "merge-site-push-symmetry"]
    assert rule_findings == [], (
        f"merge with push in success path must NOT fire merge-site-push-symmetry; "
        f"got findings: {[(f.rule, f.message) for f in rule_findings]}"
    )


def test_merge_site_push_symmetry_merge_without_push_fires() -> None:
    """Merge without a push on the success fallthrough — rule fires."""
    steps = {
        "merge_a": RecipeStep(
            tool="merge_worktree",
            with_args={"worktree_path": "/tmp/a", "base_branch": "main"},
            on_result=StepResultRoute(conditions=[StepResultCondition(route="merge_b")]),
        ),
        "merge_b": RecipeStep(
            tool="merge_worktree",
            with_args={"worktree_path": "/tmp/b", "base_branch": "main"},
            on_result=StepResultRoute(conditions=[StepResultCondition(route="done")]),
        ),
        "done": RecipeStep(action="stop", message="Done. Emit sentinel: {}"),
    }
    recipe = _make_recipe(steps)
    findings = run_semantic_rules(recipe)
    rule_findings = [f for f in findings if f.rule == "merge-site-push-symmetry"]
    assert len(rule_findings) == 1
    assert rule_findings[0].severity == Severity.WARNING
    assert rule_findings[0].step_name == "merge_a"
    assert "merge_b" in rule_findings[0].message


def test_merge_site_push_symmetry_push_on_failure_only_fires() -> None:
    """Push reachable only from failure route, NOT success fallthrough — rule fires."""
    steps = {
        "merge_a": RecipeStep(
            tool="merge_worktree",
            with_args={"worktree_path": "/tmp/a", "base_branch": "main"},
            on_success="merge_b",
            on_failure="push_a",
        ),
        "push_a": RecipeStep(
            tool="push_to_remote",
            on_success="merge_b",
            on_failure="done",
        ),
        "merge_b": RecipeStep(
            tool="merge_worktree",
            with_args={"worktree_path": "/tmp/b", "base_branch": "main"},
            on_result=StepResultRoute(conditions=[StepResultCondition(route="done")]),
        ),
        "done": RecipeStep(action="stop", message="Done. Emit sentinel: {}"),
    }
    recipe = _make_recipe(steps)
    findings = run_semantic_rules(recipe)
    rule_findings = [f for f in findings if f.rule == "merge-site-push-symmetry"]
    assert len(rule_findings) == 1
    assert rule_findings[0].step_name == "merge_a"
    assert "merge_b" in rule_findings[0].message
