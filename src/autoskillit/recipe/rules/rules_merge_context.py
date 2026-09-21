"""Semantic rules for merge gate test output context forwarding enforcement."""

from __future__ import annotations

import regex as re

from autoskillit.core import Severity, get_logger
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._rule_helpers import _SKILL_CMD_PATTERN, count_skill_args
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule
from autoskillit.recipe.rules.rules_merge_routing import _FAILED_STEP_PATTERN
from autoskillit.recipe.schema import RecipeStep
from autoskillit.smoke_utils import (
    DIAGNOSE_OPTIONAL_RESULT_PARAMS as _DIAGNOSE_OPTIONAL_RESULT_PARAMS,
)
from autoskillit.smoke_utils import (
    DIAGNOSE_RESULT_PARAMS as _DIAGNOSE_RESULT_PARAMS,
)

logger = get_logger(__name__)

_TEST_GATE_FAILURES = frozenset({"test_gate", "post_rebase_test_gate"})
_CONTEXT_REF = re.compile(r"^\$\{\{\s*context\.(\w+)\s*\}\}$")


def _failed_step_routes(step: RecipeStep) -> dict[str, set[str]]:
    """Return result.failed_step categories and their direct route targets."""
    routes_by_category: dict[str, set[str]] = {}
    if not step.on_result or not step.on_result.conditions:
        return routes_by_category
    for condition in step.on_result.conditions:
        if condition.when is None:
            continue
        match = _FAILED_STEP_PATTERN.search(condition.when)
        if match:
            routes_by_category.setdefault(match.group(1), set()).add(condition.route)
    return routes_by_category


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


def _find_diagnose_merge_gate_step(
    start: str, ctx: ValidationContext
) -> tuple[str, RecipeStep] | None:
    """BFS from start to find the first run_python step calling diagnose_merge_gate.

    Stops traversal through any merge_worktree step that already captures
    result.failed_step, since that step re-establishes context independently.

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
            if step.tool == "run_python":
                callable_val = str(step.with_args.get("callable", ""))
                if callable_val == "autoskillit.smoke_utils.diagnose_merge_gate":
                    return name, step
            if step.tool == "merge_worktree" and any(
                "failed_step" in v.from_ for v in (step.capture or {}).values()
            ):
                continue
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
        routes_by_category = _failed_step_routes(step)
        test_gate_routes: set[str] = set().union(
            *(routes_by_category.get(category, set()) for category in _TEST_GATE_FAILURES)
        )

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
                    make_finding(
                        rule_name="merge-test-gate-context-not-forwarded",
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
            invocation = ctx.binding_projection.for_step(rf_step_name)
            has_failure_context = (
                invocation is not None
                and bool(invocation.skill_inputs)
                and all(
                    (value := invocation.skill_input(name)) is not None and value.is_present
                    for name in ("ci_conclusion", "diagnosis_path")
                )
            ) or count_skill_args(cmd) > 3
            if not has_failure_context:
                findings.append(
                    make_finding(
                        rule_name="merge-test-gate-context-not-forwarded",
                        step_name=rf_step_name,
                        message=(
                            f"Step '{rf_step_name}' invokes resolve-failures without "
                            f"bound ci_conclusion and diagnosis_path inputs, but is "
                            f"reachable from merge_worktree step "
                            f"'{step_name}' via test_gate/post_rebase_test_gate. "
                            f"Without failure context (ci_conclusion + diagnosis_path), "
                            f"resolve-failures cannot investigate the specific failure. "
                            f"Bind merge_gate_ci_conclusion and "
                            f"merge_gate_diagnosis_path."
                        ),
                    )
                )

    return findings


@semantic_rule(
    name="merge-diagnosis-context-not-captured",
    description=(
        "Every diagnose_merge_gate result parameter must bind a context key captured "
        "from the same merge_worktree result; conditional result fields must use "
        "optional_string captures."
    ),
    severity=Severity.ERROR,
)
def _check_merge_diagnosis_context_not_captured(
    ctx: ValidationContext,
) -> list[RuleFinding]:
    findings: list[RuleFinding] = []

    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "merge_worktree":
            continue
        failed_step_routes: set[str] = set().union(*_failed_step_routes(step).values())

        if not failed_step_routes:
            continue

        for route_target in failed_step_routes:
            target = ctx.recipe.steps.get(route_target)
            result = (
                (route_target, target)
                if target is not None
                and target.tool == "run_python"
                and target.with_args.get("callable")
                == "autoskillit.smoke_utils.diagnose_merge_gate"
                else _find_diagnose_merge_gate_step(route_target, ctx)
            )
            if result is None:
                continue
            dg_step_name, dg_step = result
            for param in _DIAGNOSE_RESULT_PARAMS:
                binding = dg_step.with_args.get(param, "")
                match = _CONTEXT_REF.fullmatch(str(binding))
                if match is None:
                    findings.append(
                        make_finding(
                            rule_name="merge-diagnosis-context-not-captured",
                            step_name=dg_step_name,
                            message=(
                                f"diagnose_merge_gate step '{dg_step_name}' does not bind "
                                f"{param} from context."
                            ),
                        )
                    )
                    continue
                key = match.group(1)
                capture = (step.capture or {}).get(key)
                if capture is None or capture.from_ != f"${{{{ result.{param} }}}}":
                    findings.append(
                        make_finding(
                            rule_name="merge-diagnosis-context-not-captured",
                            step_name=step_name,
                            message=(
                                f"merge_worktree step '{step_name}' does not capture "
                                f"context.{key} from result.{param} for '{dg_step_name}'."
                            ),
                        )
                    )
                elif (
                    param in _DIAGNOSE_OPTIONAL_RESULT_PARAMS
                    and capture.value_type != "optional_string"
                ):
                    findings.append(
                        make_finding(
                            rule_name="merge-diagnosis-context-not-captured",
                            step_name=step_name,
                            message=(
                                f"merge_worktree step '{step_name}' must capture "
                                f"context.{key} from result.{param} as optional_string."
                            ),
                        )
                    )

    return findings
