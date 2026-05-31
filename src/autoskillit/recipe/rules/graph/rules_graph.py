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
                    retrying_steps = [
                        s
                        for s in cycle_steps
                        if s in recipe.steps
                        and recipe.steps[s].retries > 0
                        and recipe.steps[s].tool in SKILL_TOOLS
                        and recipe.steps[s].on_exhausted not in cycle_set
                    ]
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
                            rec_stack.discard(node)
                            return

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

    # --- Per-branch analysis for wait_for_merge_queue steps ---
    # The DFS above uses aggregate suppression: if ANY step in a cycle has
    # an on_result exit, the whole cycle is suppressed. This misses the case
    # where one branch (e.g. dropped_merge_group_ci) has no guard while
    # sibling branches (ejected, dropped_healthy) do. Analyze each branch
    # independently using BFS reachability.
    mq_steps = {
        name: step
        for name, step in recipe.steps.items()
        if step.tool == "wait_for_merge_queue"
        and step.on_result is not None
        and step.on_result.conditions
    }
    enqueue_tools = {"enqueue_pr", "wait_for_merge_queue"}
    for step_name, step in mq_steps.items():
        assert step.on_result is not None
        max_drops = int(step.with_args.get("max_merge_group_drops", 0)) if step.with_args else 0
        if max_drops >= 1:
            continue
        for cond in step.on_result.conditions:
            if cond.when is None:
                continue
            if "dropped_merge_group_ci" not in cond.when:
                continue
            target = cond.route
            if target not in recipe.steps:
                continue
            target_step = recipe.steps[target]
            if target_step.tool == "run_python" and target_step.on_result is not None:
                continue
            if target_step.tool in enqueue_tools:
                continue
            bfs_visited: set[str] = set()
            bfs_frontier: set[str] = {target}
            reaches_mq = False
            while bfs_frontier:
                bfs_frontier -= bfs_visited
                if not bfs_frontier:
                    break
                for n in bfs_frontier:
                    if n in mq_steps and n != step_name:
                        reaches_mq = True
                    elif n == step_name:
                        reaches_mq = True
                if reaches_mq:
                    break
                bfs_visited |= bfs_frontier
                next_bfs: set[str] = set()
                for n in bfs_frontier:
                    next_bfs |= graph.get(n, set())
                bfs_frontier = next_bfs
            if reaches_mq:
                branch_label = (
                    cond.when.split("==")[-1].strip().strip("'\"")
                    if "==" in cond.when
                    else cond.when
                )
                findings.append(
                    RuleFinding(
                        rule="unbounded-cycle",
                        severity=Severity.ERROR,
                        step_name=step_name,
                        message=(
                            f"Per-branch cycle: {step_name}[{branch_label}] → "
                            f"{target} reaches wait_for_merge_queue without a "
                            f"direct guard step. The {branch_label} branch has no "
                            f"run_python guard at its immediate route target, so "
                            f"the re-enqueue loop is unbounded. Add a "
                            f"check_dropped_merge_group_ci_loop guard step."
                        ),
                    )
                )

    return findings
