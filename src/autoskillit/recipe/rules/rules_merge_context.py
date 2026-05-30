"""Semantic rules for merge gate test output context forwarding enforcement."""

from __future__ import annotations

import regex as re

from autoskillit.core import Severity, get_logger
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._rule_helpers import _SKILL_CMD_PATTERN, count_skill_args
from autoskillit.recipe.registry import RuleFinding, semantic_rule
from autoskillit.recipe.schema import RecipeStep

logger = get_logger(__name__)

_FAILED_STEP_PATTERN = re.compile(r"result\.failed_step\s*==\s*['\"](\w+)['\"]")
_TEST_GATE_FAILURES = frozenset({"test_gate", "post_rebase_test_gate"})


def _find_resolve_failures_step(
    start: str, ctx: ValidationContext
) -> tuple[str, RecipeStep] | None:
    """BFS from start to find the first run_skill step invoking resolve-failures.

    Returns (step_name, step) or None.
    """
    visited: set[str] = {start}
    frontier: set[str] = ctx.step_graph.get(start, set()) - visited
    while frontier:
        visited |= frontier
        next_frontier: set[str] = set()
        for name in frontier:
            step = ctx.recipe.steps.get(name)
            if step is None:
                continue
            if step.tool == "run_skill":
                cmd = step.with_args.get("skill_command", "")
                m = _SKILL_CMD_PATTERN.search(cmd)
                if m and m.group(1) == "resolve-failures":
                    return name, step
            next_frontier |= ctx.step_graph.get(name, set()) - visited
        frontier = next_frontier
    return None


@semantic_rule(
    name="merge-test-gate-context-not-forwarded",
    description=(
        "A merge_worktree step routes test_gate or post_rebase_test_gate failures to a "
        "step chain that invokes resolve-failures, but either (1) the merge_worktree "
        "capture block does not include test_stdout and test_stderr fields, or (2) the "
        "downstream resolve-failures invocation has only 3 positional args (worktree, "
        "plan, branch) with no failure context. Without test output context, "
        "resolve-failures cannot diagnose the specific failing tests and defaults to "
        "failure_subtype=unknown, making the merge gate retry loop unwinnable on flakes."
    ),
    severity=Severity.ERROR,
)
def _check_merge_test_gate_context_not_forwarded(
    ctx: ValidationContext,
) -> list[RuleFinding]:
    findings: list[RuleFinding] = []

    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "merge_worktree":
            continue
        if not step.on_result or not step.on_result.conditions:
            continue

        test_gate_routes: set[str] = set()
        for cond in step.on_result.conditions:
            if cond.when is None:
                continue
            m = _FAILED_STEP_PATTERN.search(cond.when)
            if m and m.group(1) in _TEST_GATE_FAILURES:
                test_gate_routes.add(cond.route)

        if not test_gate_routes:
            continue

        has_stdout = any("test_stdout" in v.from_ for v in (step.capture or {}).values())
        has_stderr = any("test_stderr" in v.from_ for v in (step.capture or {}).values())
        capture_ok = has_stdout and has_stderr

        for route_target in test_gate_routes:
            result = _find_resolve_failures_step(route_target, ctx)
            if result is None:
                continue
            rf_step_name, rf_step = result

            if not capture_ok:
                findings.append(
                    RuleFinding(
                        rule="merge-test-gate-context-not-forwarded",
                        severity=Severity.ERROR,
                        step_name=step_name,
                        message=(
                            f"merge_worktree step '{step_name}' routes test_gate/"
                            f"post_rebase_test_gate to a resolve-failures chain "
                            f"(via '{route_target}' → '{rf_step_name}'), but its "
                            f"capture block is missing test_stdout and/or test_stderr. "
                            f"Add 'merge_test_stdout: ${{{{ result.test_stdout }}}}' and "
                            f"'merge_test_stderr: ${{{{ result.test_stderr }}}}' to capture."
                        ),
                    )
                )

            cmd = rf_step.with_args.get("skill_command", "")
            if count_skill_args(cmd) <= 3:
                findings.append(
                    RuleFinding(
                        rule="merge-test-gate-context-not-forwarded",
                        severity=Severity.ERROR,
                        step_name=rf_step_name,
                        message=(
                            f"Step '{rf_step_name}' invokes resolve-failures with only "
                            f"{count_skill_args(cmd)} positional arg(s) (worktree, plan, "
                            f"branch), but is reachable from merge_worktree step "
                            f"'{step_name}' via test_gate/post_rebase_test_gate. "
                            f"Without failure context (ci_conclusion + diagnosis_path), "
                            f"resolve-failures cannot investigate the specific failure. "
                            f"Expand skill_command to 6 args including merge_gate_ci_conclusion "
                            f"and merge_gate_diagnosis_path."
                        ),
                    )
                )

    return findings
