"""Semantic validation rules — loop counter scope isolation."""

from __future__ import annotations

import regex as _re

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext, bfs_reachable
from autoskillit.recipe._analysis_bfs import all_paths_cross
from autoskillit.recipe._analysis_graph import _extract_routing_edges
from autoskillit.recipe._rule_helpers import _build_graph_without_nodes
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule
from autoskillit.recipe.schema import Recipe, RecipeStep

_CTX_VAR_RE = _re.compile(r"\$\{\{\s*context\.(\w+)\s*\}\}")


def _is_check_loop_iteration_guard(step: RecipeStep) -> bool:
    """Return True if ``step`` is a ``check_loop_iteration`` smoke_utils guard.

    These steps increment their counter via ``capture`` rather than resetting
    it; the rule's dominator candidate filter must exclude them so parallel
    guards sharing a counter don't masquerade as resets.
    """
    return (
        step.tool == "run_python"
        and step.with_args.get("callable") == "autoskillit.smoke_utils.check_loop_iteration"
    )


def _build_yaml_predecessor_map(ctx: ValidationContext) -> dict[str, set[str]]:
    preds: dict[str, set[str]] = {}
    for name, step in ctx.recipe.steps.items():
        for edge in _extract_routing_edges(step):
            if edge.target in ctx.recipe.steps:
                preds.setdefault(edge.target, set()).add(name)
    return preds


def _has_disconnected_preds(
    preds: set[str],
    modified_graph: dict[str, set[str]],
) -> tuple[str, str] | None:
    """Check if any two predecessors are mutually unreachable in the modified graph."""
    preds_list = sorted(preds)
    if len(preds_list) < 2:
        return None
    p1 = preds_list[0]
    reachable_from_p1 = bfs_reachable(modified_graph, p1) | {p1}
    for p2 in preds_list[1:]:
        if p2 not in reachable_from_p1:
            reachable_from_p2 = bfs_reachable(modified_graph, p2) | {p2}
            if p1 not in reachable_from_p2:
                return (p1, p2)
    return None


@semantic_rule(
    name="loop-counter-cross-path-sharing",
    description=(
        "A check_loop_iteration guard is reachable from two structurally "
        "disconnected entry paths that share the same counter variable"
    ),
    severity=Severity.ERROR,
)
def _check_loop_counter_cross_path_sharing(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    recipe = ctx.recipe
    graph = ctx.step_graph

    yaml_preds = _build_yaml_predecessor_map(ctx)

    for step_name, step in recipe.steps.items():
        if step.tool != "run_python":
            continue
        if step.with_args.get("callable") != "autoskillit.smoke_utils.check_loop_iteration":
            continue

        current_iter_expr = step.with_args.get("current_iteration", "")
        m = _CTX_VAR_RE.search(current_iter_expr)
        if not m:
            continue
        counter_var = m.group(1)

        if counter_var not in step.capture:
            continue

        forward = bfs_reachable(graph, step_name)
        backward = bfs_reachable(yaml_preds, step_name)
        full_cycle = frozenset((forward & backward) | {step_name})

        if len(full_cycle) < 3 or len(full_cycle) > 10:
            continue

        has_test_step = any(
            (s := recipe.steps.get(sn)) is not None and s.tool == "test_check" for sn in full_cycle
        )
        if not has_test_step:
            continue

        modified_graph = _build_graph_without_nodes(graph, full_cycle)

        guard_steps = {
            sn
            for sn, s in recipe.steps.items()
            if s.tool == "run_python"
            and s.with_args.get("callable") == "autoskillit.smoke_utils.check_loop_iteration"
        }

        external_preds: dict[str, set[str]] = {}
        for member in full_cycle:
            member_step = recipe.steps.get(member)
            if member_step and member_step.tool == "test_check":
                continue
            for pred in yaml_preds.get(member, set()):
                if pred not in full_cycle and pred not in guard_steps:
                    external_preds.setdefault(member, set()).add(pred)

        for member in list(external_preds):
            external_preds[member] = {
                p
                for p in external_preds[member]
                if not (yaml_preds.get(p, set()) and yaml_preds[p] <= full_cycle)
            }
        external_preds = {k: v for k, v in external_preds.items() if v}

        all_ext = {p for ps in external_preds.values() for p in ps}
        if len(all_ext) < 2:
            continue

        pair = _has_disconnected_preds(all_ext, modified_graph)
        if pair is None:
            continue

        p1, p2 = pair
        findings.append(
            make_finding(
                rule_name="loop-counter-cross-path-sharing",
                step_name=step_name,
                message=(
                    f"Step '{step_name}' uses counter '{counter_var}' but is "
                    f"reachable from structurally disconnected entry paths: "
                    f"'{p1}' and '{p2}' cannot reach each other without "
                    f"traversing the cycle. Use separate counter variables "
                    f"for each independent failure path."
                ),
            )
        )

    return findings


@semantic_rule(
    name="loop-guard-before-verify",
    description=(
        "A check_loop_iteration guard fires before the verify step, "
        "causing the last valid fix attempt to be discarded"
    ),
    severity=Severity.WARNING,
)
def _check_loop_guard_before_verify(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    recipe = ctx.recipe

    for step_name, step in recipe.steps.items():
        if step.tool != "run_python":
            continue
        if step.with_args.get("callable") != "autoskillit.smoke_utils.check_loop_iteration":
            continue

        if step.on_result is None:
            continue

        non_exit_route: str | None = None
        for cond in step.on_result.conditions:
            if cond.when and "max_exceeded" in cond.when:
                continue
            non_exit_route = cond.route
            break

        if non_exit_route is None or non_exit_route not in recipe.steps:
            continue

        verify_step = recipe.steps[non_exit_route]
        if verify_step.tool not in ("test_check", "run_skill"):
            continue

        failure_target = verify_step.on_failure
        if failure_target is None or failure_target not in recipe.steps:
            continue

        fix_step = recipe.steps[failure_target]
        fix_edges = _extract_routing_edges(fix_step)
        routes_to_guard = any(edge.target == step_name for edge in fix_edges)

        if routes_to_guard:
            findings.append(
                make_finding(
                    rule_name="loop-guard-before-verify",
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' increments the loop counter before the "
                        f"verify step '{non_exit_route}' runs (pattern: "
                        f"'{failure_target}' → '{step_name}' → '{non_exit_route}'). "
                        f"The Nth valid fix is discarded because the counter fires "
                        f"before the fix is verified. Reorder to: "
                        f"'{failure_target}' → '{non_exit_route}' → '{step_name}'."
                    ),
                )
            )

    return findings


_WRAPPER_LOOP_EXEMPT_COUNTERS: frozenset[str] = frozenset(
    {
        "group_iteration_count",
    }
)
"""Counters exempt from the cross-cycle reset requirement.

These counters guard run-wide safety ceilings (e.g. group_iteration_count caps the
total number of recipe-group iterations across the entire pipeline run) and must
NOT be reset per audit-remediation cycle — doing so would defeat the safety
ceiling and allow indefinite repetition. Counter names are stable across
recipes; step names vary, so we key the exemption on counter variable rather
than step name."""


@semantic_rule(
    name="loop-counter-not-reset-on-outer-cycle",
    description=(
        "An inner check_loop_iteration guard's counter variable is reachable "
        "from an outer check_loop_iteration guard's non-max_exceeded route "
        "without passing through a step that resets the inner counter"
    ),
    severity=Severity.ERROR,
)
def _check_loop_counter_not_reset_on_outer_cycle(ctx: ValidationContext) -> list[RuleFinding]:
    """Detect temporal counter sharing across outer audit-remediation cycles.

    When the audit-remediation outer loop re-enters the implementation
    sub-cycle, any inner guard whose counter variable is captured along that
    path (e.g. test_fix_loop_count) will accumulate across outer iterations
    unless a step resets it via autoskillit.smoke_utils.init_counter.

    Scoped to outer guards whose counter variable indicates an audit-
    remediation cycle (audit_remediation_count) — other outer/inner guard
    relationships (e.g. merge_fix wrapping merge_rebase) have separate
    reset mechanisms and are out of scope for this rule.

    Bilateral cycle-membership: an inner guard is only considered in-scope
    when it is BOTH forward-reachable from the outer guard's non-exit route
    AND backward-reachable to the outer guard. This structurally excludes
    post-audit terminal guards (CI watch, stall recovery, etc.) that lie
    downstream of the audit-remediation cycle but cannot return to it.
    """
    findings: list[RuleFinding] = []
    recipe = ctx.recipe
    graph = ctx.step_graph

    guard_steps: dict[str, str] = {}
    for step_name, step in recipe.steps.items():
        if step.tool != "run_python":
            continue
        if step.with_args.get("callable") != "autoskillit.smoke_utils.check_loop_iteration":
            continue
        current_iter_expr = step.with_args.get("current_iteration", "")
        m = _CTX_VAR_RE.search(current_iter_expr)
        if not m:
            continue
        guard_steps[step_name] = m.group(1)

    if len(guard_steps) < 2:
        return findings

    audit_outer_guards = {
        name for name, counter in guard_steps.items() if "audit_remediation" in counter
    }
    if not audit_outer_guards:
        return findings

    for outer_name in audit_outer_guards:
        outer_step = recipe.steps[outer_name]
        if outer_step.on_result is None:
            continue

        non_exit_target: str | None = None
        for cond in outer_step.on_result.conditions:
            if cond.when and "max_exceeded" in cond.when:
                continue
            non_exit_target = cond.route
            break

        if non_exit_target is None or non_exit_target not in recipe.steps:
            continue

        forward_reachable = bfs_reachable(graph, non_exit_target)
        forward_reachable.add(non_exit_target)
        cycle_candidates = bfs_reachable(ctx.predecessors, outer_name)

        for inner_name, inner_counter in guard_steps.items():
            if inner_name in audit_outer_guards:
                continue

            if inner_counter in _WRAPPER_LOOP_EXEMPT_COUNTERS:
                continue

            if inner_name not in forward_reachable:
                continue

            if inner_name not in cycle_candidates:
                continue

            # Dominator check: at least one reset step must dominate
            # ``inner_name`` on every path from ``non_exit_target``. The prior
            # existential-path intersection (``forward & backward``) accepted
            # any reset reachable in the bilateral region — false-negative for
            # branching re-entry where the reset sits on only one branch.
            #
            # Candidate filter:
            # - ``inner_name`` is excluded because every ``check_loop_iteration``
            #   captures its own counter (self-loop), and ``all_paths_cross``
            #   returns True whenever ``candidate == target``. Without this
            #   filter, every cyclic guard would trivially "dominate itself"
            #   and the rule would silently never fire.
            # - Other ``check_loop_iteration`` guards sharing the counter are
            #   excluded because their ``capture`` is an INCREMENT (the guard
            #   runs to consume one iteration), not a reset. Including them
            #   would treat every parallel guard as a "reset" and produce
            #   false-positive findings on bundled recipes that have multiple
            #   guards sharing a counter across parallel branches.
            # - The actual reset is a step using
            #   ``autoskillit.smoke_utils.init_counter`` whose ``capture``
            #   publishes the new value. We identify it by callable.
            reset_steps = [
                sn
                for sn in forward_reachable
                if sn != inner_name
                and sn in recipe.steps
                and inner_counter in recipe.steps[sn].capture
                and not _is_check_loop_iteration_guard(recipe.steps[sn])
            ]

            has_reset = any(
                all_paths_cross(graph, non_exit_target, reset_sn, inner_name)
                for reset_sn in reset_steps
            )

            if not has_reset:
                findings.append(
                    make_finding(
                        rule_name="loop-counter-not-reset-on-outer-cycle",
                        step_name=inner_name,
                        message=(
                            f"Inner guard '{inner_name}' uses counter "
                            f"'{inner_counter}' but is reachable from "
                            f"audit-remediation guard '{outer_name}' via "
                            f"'{non_exit_target}' without a reset step. Add "
                            f"a step using "
                            f"'autoskillit.smoke_utils.init_counter' to "
                            f"capture '{inner_counter}' on this path so each "
                            f"audit-remediation cycle gets a fresh budget."
                        ),
                    )
                )

    return findings


# ---------------------------------------------------------------------------
# shared-counter-cross-site-without-push-symmetry
#
# Catches the structural shape of issue #4274: two ``check_loop_iteration``
# guards sharing a counter variable, each preceded by a ``merge_worktree``-
# tool step, where the two merge sites disagree on whether their success path
# reaches a ``push_to_remote``-tool step. The asymmetry means one merge site
# consumes push retries with a "fresh push" reset while the other does not,
# exhausting the shared counter on whichever site lacks push protection.
# ---------------------------------------------------------------------------


def _find_nearest_merge_worktree_ancestor(
    recipe: Recipe,
    guard_step: str,
    yaml_preds: dict[str, set[str]],
) -> str | None:
    """Walk ``yaml_preds`` backward from ``guard_step`` to the nearest ``merge_worktree`` step.

    Returns the merge_worktree step name, or ``None`` if no such step is
    reachable upstream. Skips guard steps themselves (they are not merge sites).
    """
    visited: set[str] = set()
    queue = list(yaml_preds.get(guard_step, set()))
    while queue:
        node = queue.pop()
        if node in visited:
            continue
        visited.add(node)
        step = recipe.steps.get(node)
        if step is not None and step.tool == "merge_worktree":
            return node
        queue.extend(yaml_preds.get(node, set()))
    return None


def _unconditional_success_route(step: RecipeStep) -> str | None:
    """Return the unconditional success route of a merge_worktree step.

    The unconditional route is the ``on_result`` condition with no ``when``
    clause (the "default" route), falling back to ``on_success`` if there is
    no ``on_result``. Returns ``None`` if neither is declared.
    """
    if step.on_result and step.on_result.conditions:
        for cond in step.on_result.conditions:
            if cond.when is None:
                return cond.route
    return step.on_success


def _merge_push_symmetric_to_guard(recipe: Recipe, merge_step_name: str) -> bool:
    """Return True iff merge's unconditional success route is a push_to_remote step.

    Checks the immediate next step in the success path. A push_to_remote step
    that is reachable only after traversing sub-recipe composition or the
    full pipeline loop is not credited — such "coincidental" pushes do not
    represent intentional push-after-merge protection at the merge site being
    audited.

    Matches the bundled recipe topology:
    - ``merge`` (the main merge_worktree) routes unconditionally to
      ``inter_part_push`` (a ``push_to_remote`` step) → True.
    - ``pre_remediation_merge`` routes unconditionally to ``remediate`` (a
      ``run_skill`` step) → False — the pipeline eventually pushes via
      ``ref_push_pre_remediation`` but only after looping back through the
      audit cycle, which does not constitute push symmetry at this merge site.
    """
    merge_step = recipe.steps.get(merge_step_name)
    if merge_step is None or merge_step.tool != "merge_worktree":
        return False
    route = _unconditional_success_route(merge_step)
    if route is None:
        return False
    target = recipe.steps.get(route)
    return target is not None and target.tool == "push_to_remote"


@semantic_rule(
    name="shared-counter-cross-site-without-push-symmetry",
    description=(
        "Two check_loop_iteration guards share a counter variable across "
        "structurally distinct merge_worktree sites that disagree on whether "
        "their success path reaches a push_to_remote step before the next "
        "merge site"
    ),
    severity=Severity.ERROR,
)
def _check_shared_counter_cross_site_without_push_symmetry(
    ctx: ValidationContext,
) -> list[RuleFinding]:
    """Flag two guards sharing a counter whose merge ancestors disagree on push.

    This is the rule that catches issue #4274 — the ``ref_push_count`` shared
    between ``check_ref_push_loop`` (preceded by ``merge``, whose success
    route is the ``push_to_remote`` step ``inter_part_push``) and
    ``check_ref_push_loop_pre_remediation`` (preceded by ``pre_remediation_merge``,
    whose success route is the ``run_skill`` step ``remediate``, no immediate
    push). The asymmetry means one merge site pushes fresh retries while the
    other accumulates, exhausting the shared counter.

    The per-guard dominator fix to ``loop-counter-not-reset-on-outer-cycle``
    does NOT reach this defect because the two guard sites are entered from
    unrelated points in the graph (the GO-path bypass in ``remediation.yaml``
    forks at ``audit_impl``'s ``on_result``, not at any step downstream of
    either guard's bilateral cycle). Catching the #4274 shape requires
    reasoning about push symmetry across structurally distinct merge sites
    sharing one counter — exactly what this rule does.
    """
    findings: list[RuleFinding] = []
    recipe = ctx.recipe
    yaml_preds = _build_yaml_predecessor_map(ctx)

    # Collect guard steps and their counter variables
    guard_steps: dict[str, str] = {}
    for step_name, step in recipe.steps.items():
        if step.tool != "run_python":
            continue
        if step.with_args.get("callable") != "autoskillit.smoke_utils.check_loop_iteration":
            continue
        current_iter_expr = step.with_args.get("current_iteration", "")
        m = _CTX_VAR_RE.search(current_iter_expr)
        if not m:
            continue
        guard_steps[step_name] = m.group(1)

    if len(guard_steps) < 2:
        return findings

    # Group guards by counter variable
    guards_by_counter: dict[str, list[str]] = {}
    for guard_name, counter_var in guard_steps.items():
        guards_by_counter.setdefault(counter_var, []).append(guard_name)

    # For each shared-counter group with >=2 guards, find nearest merge_worktree
    # ancestor of each and check push symmetry.
    for counter_var, group_guards in guards_by_counter.items():
        if len(group_guards) < 2:
            continue

        guard_sites: list[
            tuple[str, str | None, bool]
        ] = []  # (guard_name, merge_ancestor, push_symmetric)
        for guard_name in group_guards:
            merge_ancestor = _find_nearest_merge_worktree_ancestor(recipe, guard_name, yaml_preds)
            if merge_ancestor is None:
                continue  # not a merge-site guard
            push_symmetric = _merge_push_symmetric_to_guard(recipe, merge_ancestor)
            guard_sites.append((guard_name, merge_ancestor, push_symmetric))

        if len(guard_sites) < 2:
            continue  # nothing to compare

        push_values = {push_sym for _, _, push_sym in guard_sites}
        if len(push_values) < 2:
            continue  # all guards agree on push symmetry

        # Disagreement: fire one finding naming all guard sites
        site_descriptions = [
            f"'{guard_name}' (merge predecessor '{merge_ancestor}', "
            f"{'pushes' if push_sym else 'no immediate push'})"
            for guard_name, merge_ancestor, push_sym in guard_sites
        ]
        findings.append(
            make_finding(
                rule_name="shared-counter-cross-site-without-push-symmetry",
                step_name=guard_sites[0][0],
                message=(
                    f"Counter '{counter_var}' is shared across guards "
                    f"{', '.join(site_descriptions)} whose merge_worktree "
                    f"predecessors disagree on push symmetry. One merge site "
                    f"pushes fresh retries after consuming the counter while "
                    f"the other does not, deterministically exhausting the "
                    f"shared counter. Use a separate counter per merge site "
                    f"or ensure every merge_worktree's success route is a "
                    f"push_to_remote step."
                ),
            )
        )

    return findings
