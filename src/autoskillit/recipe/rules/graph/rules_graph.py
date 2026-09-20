"""Semantic validation rules — graph/routing analysis."""

from __future__ import annotations

from autoskillit.core import SKILL_TOOLS, Severity
from autoskillit.recipe._analysis import ValidationContext, bfs_reachable
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule

_STRUCTURAL_ON_RESULT_TOOLS = {"run_python", "wait_for_ci"}


@semantic_rule(
    name="unbounded-cycle",
    description="Routing cycle with no structural termination guarantee",
    severity=Severity.ERROR,
)
def _check_unbounded_cycles(ctx: ValidationContext) -> list[RuleFinding]:
    recipe = ctx.recipe
    graph = ctx.step_graph
    findings: list[RuleFinding] = []
    reported_cycles: set[frozenset[str]] = set()

    visited: set[str] = set()
    rec_stack: set[str] = set()

    def retry_cycle_outcome(
        cycle_steps: list[str],
        cycle_set: set[str],
        node: str,
        neighbor: str,
    ) -> tuple[RuleFinding | None, bool]:
        """Return a retry-cycle finding and whether its DFS call must stop."""
        retrying_steps = [
            step_name
            for step_name in cycle_steps
            if step_name in recipe.steps
            and recipe.steps[step_name].retries > 0
            and recipe.steps[step_name].tool in SKILL_TOOLS
            and recipe.steps[step_name].on_exhausted not in cycle_set
        ]
        if not retrying_steps:
            return None, False

        exit_targets: set[str] = set()
        for step_name in retrying_steps:
            step = recipe.steps[step_name]
            success_routes: set[str] = set()
            if step.on_result:
                if step.on_result.conditions:
                    success_routes = {condition.route for condition in step.on_result.conditions}
                elif step.on_result.routes:
                    success_routes = set(step.on_result.routes.values())
            if step.on_success:
                success_routes.add(step.on_success)
            failure_targets = {target for target in (step.on_failure, step.on_exhausted) if target}
            if any(
                successor in cycle_set
                for successor in success_routes
                if successor not in failure_targets
            ):
                break
            failure_targets.update(
                target
                for target in (step.on_context_limit, step.on_rate_limit)
                if target and target not in cycle_set
            )
            exit_targets.update(
                successor
                for successor in graph.get(step_name, set())
                if successor not in cycle_set and successor not in failure_targets
            )
        else:
            if not any(
                cycle_set & bfs_reachable(graph, target).union({target}) for target in exit_targets
            ):
                return None, True

        return (
            make_finding(
                rule_name="unbounded-cycle",
                step_name=node,
                message=(
                    f"Routing cycle detected: {' → '.join(cycle_steps)} → {neighbor}. "
                    f"Step(s) {', '.join(retrying_steps)} have retry exits, but their "
                    f"success paths re-enter the cycle. The inner retry budget resets "
                    f"on each loop iteration, so the outer loop is unbounded. "
                    "Add a check_loop_iteration guard step to enforce a hard "
                    "iteration cap, or route the success path outside the cycle."
                ),
            ),
            True,
        )

    def collect_merge_queue_branch_findings() -> list[RuleFinding]:
        """Collect unguarded dropped-CI branches that can return to queue watching."""
        mq_steps = {
            name: step
            for name, step in recipe.steps.items()
            if step.tool == "wait_for_merge_queue"
            and step.on_result is not None
            and step.on_result.conditions
        }
        enqueue_tools = {"enqueue_pr", "wait_for_merge_queue"}
        mq_step_names = set(mq_steps)
        branch_findings: list[RuleFinding] = []
        for step_name, step in mq_steps.items():
            assert step.on_result is not None
            max_drops = (
                int(step.with_args.get("max_merge_group_drops", 0)) if step.with_args else 0
            )
            if max_drops >= 1:
                continue
            for condition in step.on_result.conditions:
                if condition.when is None or "dropped_merge_group_ci" not in condition.when:
                    continue
                target = condition.route
                if target not in recipe.steps:
                    continue
                target_step = recipe.steps[target]
                if target_step.tool == "run_python" and target_step.on_result is not None:
                    continue
                if target_step.tool in enqueue_tools:
                    continue
                reachable = bfs_reachable(graph, target).union({target})
                if not reachable & mq_step_names:
                    continue
                branch_label = (
                    condition.when.split("==")[-1].strip().strip("'\"")
                    if "==" in condition.when
                    else condition.when
                )
                branch_findings.append(
                    make_finding(
                        rule_name="unbounded-cycle",
                        step_name=step_name,
                        message=f"Per-branch cycle: {step_name}[{branch_label}] → "
                        f"{target} reaches wait_for_merge_queue without a "
                        f"direct guard step. The {branch_label} branch has no "
                        f"run_python guard at its immediate route target, so "
                        f"the re-enqueue loop is unbounded. Add a "
                        f"check_dropped_merge_group_ci_loop guard step.",
                    )
                )
        return branch_findings

    def dfs(node: str, path: list[str]) -> None:
        visited.add(node)
        rec_stack.add(node)
        for neighbor in sorted(graph.get(node, set())):
            if neighbor not in recipe.steps:
                continue  # dead reference — caught by validate_recipe_structure
            if neighbor not in visited:
                dfs(neighbor, path + [neighbor])
            elif neighbor in rec_stack:
                # Reconstruct the cycle steps from the path
                if neighbor in path:
                    cycle_steps = path[path.index(neighbor) :]
                else:
                    cycle_steps = path
                cycle_key = frozenset(cycle_steps)
                if cycle_key in reported_cycles:
                    rec_stack.discard(node)
                    return
                reported_cycles.add(cycle_key)
                cycle_set = set(cycle_steps)

                has_on_result_exit = False
                for s in cycle_steps:
                    if s not in recipe.steps:
                        continue
                    step = recipe.steps[s]
                    if step.tool not in _STRUCTURAL_ON_RESULT_TOOLS or step.on_result is None:
                        continue
                    targets: set[str] = set()
                    if step.on_result.conditions:
                        targets = {c.route for c in step.on_result.conditions}
                    elif step.on_result.routes:
                        targets = set(step.on_result.routes.values())
                    routes_out = targets - cycle_set
                    if routes_out:
                        has_on_result_exit = True
                        break

                if has_on_result_exit:
                    rec_stack.discard(node)
                    return

                retry_finding, retry_stops = retry_cycle_outcome(
                    cycle_steps,
                    cycle_set,
                    node,
                    neighbor,
                )
                if retry_finding is not None:
                    findings.append(retry_finding)
                if retry_stops:
                    rec_stack.discard(node)
                    return

                # Conditional exit: on_failure pointing outside the cycle (unbounded but escapable)
                has_failure_exit = any(
                    recipe.steps[s].on_failure is not None
                    and recipe.steps[s].on_failure not in cycle_set
                    for s in cycle_steps
                    if s in recipe.steps
                )

                if has_failure_exit:
                    message = (
                        f"Routing cycle detected: {' → '.join(cycle_steps)} → {neighbor}. "
                        f"The cycle has a conditional exit path but no structural bound on "
                        f"iterations. Add 'retries: N' to at least one cycling step "
                        f"to enforce a maximum iteration count."
                    )
                else:
                    message = (
                        f"Routing cycle detected: {' → '.join(cycle_steps)} → {neighbor}. "
                        f"No step in this cycle has an exit edge — this cycle has no "
                        f"termination guarantee and will loop forever. Add 'retries: N' "
                        f"with on_exhausted outside the cycle, or route on_failure to a step "
                        f"outside the cycle."
                    )
                findings.append(
                    make_finding(
                        rule_name="unbounded-cycle",
                        step_name=node,
                        message=message,
                        severity=Severity.ERROR,
                    )
                )
        rec_stack.discard(node)

    for step_name in recipe.steps:
        if step_name not in visited:
            dfs(step_name, [step_name])

    findings.extend(collect_merge_queue_branch_findings())
    return findings
