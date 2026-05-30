"""Semantic validation rules — graph/routing analysis."""

from __future__ import annotations

from autoskillit.core import SKILL_TOOLS, Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.registry import RuleFinding, semantic_rule

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

                has_retry_exit = any(
                    recipe.steps[s].retries > 0
                    and recipe.steps[s].tool in SKILL_TOOLS
                    and recipe.steps[s].on_exhausted not in cycle_set
                    for s in cycle_steps
                    if s in recipe.steps
                )
                if has_retry_exit:
                    # Check whether the success path of the retrying step stays inside
                    # the cycle. If it does, the retry exit only bounds individual visits
                    # but the outer loop can still iterate unboundedly.
                    retrying_steps = [
                        s
                        for s in cycle_steps
                        if s in recipe.steps
                        and recipe.steps[s].retries > 0
                        and recipe.steps[s].tool in SKILL_TOOLS
                        and recipe.steps[s].on_exhausted not in cycle_set
                    ]
                    # Check whether any non-failure successor of the retrying step
                    # stays within the cycle. Uses the step graph (which includes
                    # on_result routes) rather than on_success alone, so steps that
                    # route via on_result without an explicit on_success are handled.
                    success_stays_in_cycle = False
                    for _s in retrying_steps:
                        _step = recipe.steps[_s]
                        _fail_targets = {
                            t
                            for t in (
                                _step.on_failure,
                                _step.on_exhausted,
                            )
                            if t
                        }
                        if _step.on_context_limit and _step.on_context_limit not in cycle_set:
                            _fail_targets.add(_step.on_context_limit)
                        if _step.on_rate_limit and _step.on_rate_limit not in cycle_set:
                            _fail_targets.add(_step.on_rate_limit)
                        if any(
                            succ in cycle_set
                            for succ in graph.get(_s, set())
                            if succ not in _fail_targets
                        ):
                            success_stays_in_cycle = True
                            break
                    if not success_stays_in_cycle:
                        # Success path exits the cycle — but does it loop back?
                        # BFS from exit targets to check if they can reach any
                        # cycle member through the step graph.
                        exit_targets: set[str] = set()
                        for _rs in retrying_steps:
                            _step_r = recipe.steps[_rs]
                            _fail_targets_r = {
                                t
                                for t in (
                                    _step_r.on_failure,
                                    _step_r.on_exhausted,
                                )
                                if t
                            }
                            if (
                                _step_r.on_context_limit
                                and _step_r.on_context_limit not in cycle_set
                            ):
                                _fail_targets_r.add(_step_r.on_context_limit)
                            if _step_r.on_rate_limit and _step_r.on_rate_limit not in cycle_set:
                                _fail_targets_r.add(_step_r.on_rate_limit)
                            for succ in graph.get(_rs, set()):
                                if succ not in cycle_set and succ not in _fail_targets_r:
                                    exit_targets.add(succ)
                        loops_back = False
                        visited_exit: set[str] = set()
                        frontier = exit_targets
                        while frontier:
                            if frontier & cycle_set:
                                loops_back = True
                                break
                            visited_exit |= frontier
                            nxt: set[str] = set()
                            for f in frontier:
                                nxt |= set(graph.get(f, set())) - visited_exit
                            frontier = nxt
                        if not loops_back:
                            # Truly exits — cycle is bounded
                            rec_stack.discard(node)
                            return

                    # Success path re-enters the cycle — retry exit only bounds
                    # individual step visits, not the outer loop. Emit ERROR.
                    findings.append(
                        RuleFinding(
                            rule="unbounded-cycle",
                            severity=Severity.ERROR,
                            step_name=node,
                            message=(
                                f"Routing cycle detected: {' → '.join(cycle_steps)} → {neighbor}. "
                                f"Step(s) {', '.join(retrying_steps)} have retry exits, but their "
                                f"success paths re-enter the cycle. The inner retry budget resets "
                                f"on each loop iteration, so the outer loop is unbounded. "
                                "Add a check_loop_iteration guard step to enforce a hard "
                                "iteration cap, or route the success path outside the cycle."
                            ),
                        )
                    )
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
                    severity = Severity.WARNING
                    message = (
                        f"Routing cycle detected: {' → '.join(cycle_steps)} → {neighbor}. "
                        f"The cycle has a conditional exit path but no structural bound on "
                        f"iterations. Add 'retries: N' to at least one cycling step "
                        f"to enforce a maximum iteration count."
                    )
                else:
                    severity = Severity.ERROR
                    message = (
                        f"Routing cycle detected: {' → '.join(cycle_steps)} → {neighbor}. "
                        f"No step in this cycle has an exit edge — this cycle has no "
                        f"termination guarantee and will loop forever. Add 'retries: N' "
                        f"with on_exhausted outside the cycle, or route on_failure to a step "
                        f"outside the cycle."
                    )
                findings.append(
                    RuleFinding(
                        rule="unbounded-cycle",
                        severity=severity,
                        step_name=node,
                        message=message,
                    )
                )
        rec_stack.discard(node)

    for step_name in recipe.steps:
        if step_name not in visited:
            dfs(step_name, [step_name])

    return findings
