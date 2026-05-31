"""Semantic validation rules — graph/routing analysis."""

from __future__ import annotations

from autoskillit.core import SKILL_TOOLS, Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.registry import RuleFinding, semantic_rule

_STRUCTURAL_ON_RESULT_TOOLS = {"run_python", "wait_for_ci", "wait_for_merge_queue"}


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

                structural_steps = [
                    s
                    for s in cycle_steps
                    if s in recipe.steps
                    and recipe.steps[s].tool in _STRUCTURAL_ON_RESULT_TOOLS
                    and recipe.steps[s].on_result is not None
                ]
                if structural_steps:
                    unguarded_branches: list[str] = []
                    all_bounded = True
                    for s in structural_steps:
                        step = recipe.steps[s]
                        on_result = step.on_result
                        assert on_result is not None  # guaranteed by list comprehension filter
                        conditions = on_result.conditions or []
                        routes_map = on_result.routes or {}
                        branch_items: list[tuple[str | None, str]] = []
                        if conditions:
                            branch_items = [(c.when, c.route) for c in conditions]
                        elif routes_map:
                            branch_items = [(k, v) for k, v in routes_map.items()]
                        for label, target in branch_items:
                            if target not in cycle_set:
                                continue
                            target_step = recipe.steps.get(target)
                            if (
                                target_step is not None
                                and target_step.tool in _STRUCTURAL_ON_RESULT_TOOLS
                                and target_step.on_result is not None
                            ):
                                guard_targets: set[str] = set()
                                if target_step.on_result.conditions:
                                    guard_targets = {
                                        c.route for c in target_step.on_result.conditions
                                    }
                                elif target_step.on_result.routes:
                                    guard_targets = set(target_step.on_result.routes.values())
                                if guard_targets - cycle_set:
                                    continue
                            branch_desc = label if label else "(default)"
                            unguarded_branches.append(f"{s}[{branch_desc}]→{target}")
                            all_bounded = False

                    if all_bounded:
                        rec_stack.discard(node)
                        return

                    if unguarded_branches:
                        findings.append(
                            RuleFinding(
                                rule="unbounded-cycle",
                                severity=Severity.ERROR,
                                step_name=node,
                                message=(
                                    f"Routing cycle detected: {' → '.join(cycle_steps)} → "
                                    f"{neighbor}. Unguarded re-entering branch(es): "
                                    f"{', '.join(unguarded_branches)}. Each re-entering "
                                    f"on_result branch must route through a direct guard "
                                    f"step (run_python/wait_for_ci with an on_result exit "
                                    f"outside the cycle) before re-entering the cycle."
                                ),
                            )
                        )
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
                        _success_routes: set[str] = set()
                        if _step.on_result:
                            if _step.on_result.conditions:
                                _success_routes = {c.route for c in _step.on_result.conditions}
                            elif _step.on_result.routes:
                                _success_routes = set(_step.on_result.routes.values())
                        if _step.on_success:
                            _success_routes.add(_step.on_success)
                        _fail_targets = {t for t in (_step.on_failure, _step.on_exhausted) if t}
                        if any(
                            succ in cycle_set
                            for succ in _success_routes
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
