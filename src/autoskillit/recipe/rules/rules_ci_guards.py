"""Semantic rules for CI structural guard checks (applicability, loops, enqueue, cwd/branch)."""

from __future__ import annotations

import regex as re

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext, _extract_routing_edges
from autoskillit.recipe.registry import RuleFinding, semantic_rule

_CI_APPLICABLE_RE = re.compile(r"ci_applicable")
_PRIMARY_CI_EVENT_RE = re.compile(r"context\.(conflict_)?ci_event\s*\}\}")


@semantic_rule(
    name="ci-wait-requires-applicability-guard",
    description=(
        "A wait_for_ci step whose event comes from check_repo_merge_state must have "
        "an upstream action:route step that checks ci_applicable to prevent timeout "
        "waste when no CI workflows apply."
    ),
    severity=Severity.ERROR,
)
def _check_ci_wait_requires_applicability_guard(ctx: ValidationContext) -> list[RuleFinding]:
    from collections import deque

    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "wait_for_ci":
            continue
        event_val = (step.with_args or {}).get("event", "")
        if not isinstance(event_val, str) or not _PRIMARY_CI_EVENT_RE.search(event_val):
            continue
        has_guard = False
        initial_preds = ctx.predecessors.get(step_name, set())
        visited: set[str] = set(initial_preds)
        queue: deque[str] = deque(initial_preds)
        while queue and not has_guard:
            node = queue.popleft()
            pred_step = ctx.recipe.steps.get(node)
            if pred_step is None:
                continue
            if pred_step.tool == "wait_for_ci":
                has_guard = True
                break
            if getattr(pred_step, "action", None) == "route":
                if pred_step.on_result and pred_step.on_result.conditions:
                    for cond in pred_step.on_result.conditions:
                        if cond.when and _CI_APPLICABLE_RE.search(cond.when):
                            has_guard = True
                            break
            if not has_guard:
                new_preds = ctx.predecessors.get(node, set()) - visited
                visited.update(new_preds)
                queue.extend(new_preds)
        if not has_guard:
            findings.append(
                RuleFinding(
                    rule="ci-wait-requires-applicability-guard",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' calls wait_for_ci with a ci_event from "
                        f"check_repo_merge_state but has no upstream action:route step "
                        f"that checks ci_applicable. When ci_applicable=false, "
                        f"wait_for_ci will exhaust its timeout budget polling for CI runs "
                        f"that will never appear."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="ci-timed-out-self-loop-unguarded",
    description=(
        "A wait_for_ci step whose on_result routes timed_out back to itself "
        "must have a check_loop_iteration guard on that path to prevent unbounded looping. "
        "timed_out means CI is still running — it should be polled with a bounded iteration "
        "count, not routed to a bare self-loop with no cap."
    ),
    severity=Severity.ERROR,
)
def _check_ci_timed_out_self_loop_unguarded(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "wait_for_ci":
            continue
        if not step.on_result or not step.on_result.conditions:
            continue
        conditions = step.on_result.conditions
        timed_out_routes: list[str] = []
        for cond in conditions:
            if cond.when is not None and "timed_out" in cond.when:
                timed_out_routes.append(cond.route)
        for route in timed_out_routes:
            if route == step_name:
                findings.append(
                    RuleFinding(
                        rule="ci-timed-out-self-loop-unguarded",
                        severity=Severity.ERROR,
                        step_name=step_name,
                        message=(
                            f"Step '{step_name}' has a timed_out self-loop with no "
                            f"check_loop_iteration guard on the path. "
                            f"Unbounded polling can result from this pattern."
                        ),
                    )
                )
    return findings


@semantic_rule(
    name="enqueue-missing-ci-gate",
    description=(
        "Flags enqueue_pr steps reachable from recipe entry without a wait_for_ci ancestor. "
        "Premature queue enrollment (before CI passes) causes predictable GitHub rejection."
    ),
    severity=Severity.ERROR,
)
def _check_enqueue_missing_ci_gate(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []

    enqueue_steps: set[str] = {
        name for name, step in ctx.recipe.steps.items() if step.tool == "enqueue_pr"
    }
    if not enqueue_steps:
        return findings

    ci_gate_steps: set[str] = {
        name for name, step in ctx.recipe.steps.items() if step.tool == "wait_for_ci"
    }
    for name, step in ctx.recipe.steps.items():
        if getattr(step, "action", None) == "route" and step.on_result:
            if step.on_result.conditions and any(
                c.when and _CI_APPLICABLE_RE.search(c.when) for c in step.on_result.conditions
            ):
                ci_gate_steps.add(name)

    # Build a direct routing graph without skip_when_false bypass edges.
    # Bypass edges allow CI gates to be sidestepped in the compiled step_graph,
    # which would produce false positives here (a wait_for_ci bypassed when
    # open_pr=false is not a real ungated path to enqueue_pr).
    step_names = set(ctx.recipe.steps)
    direct_graph: dict[str, set[str]] = {name: set() for name in step_names}
    for name, step in ctx.recipe.steps.items():
        for edge in _extract_routing_edges(step):
            if edge.edge_type == "exhausted" and step.action is not None:
                continue
            if edge.target in step_names:
                direct_graph[name].add(edge.target)

    # Entry steps: those not pointed to by any step in the direct graph.
    has_predecessor: set[str] = {s for succs in direct_graph.values() for s in succs}
    entry_steps = step_names - has_predecessor
    if not entry_steps:
        entry_steps = step_names

    # Forward BFS from all entry points; CI gates are barriers — visited but
    # not expanded. This yields every step reachable from the recipe entry
    # without every path first crossing a wait_for_ci guard.
    reachable_without_gate: set[str] = set()
    queue: list[str] = list(entry_steps)
    while queue:
        node = queue.pop()
        if node in reachable_without_gate:
            continue
        reachable_without_gate.add(node)
        if node in ci_gate_steps:
            continue
        for successor in direct_graph.get(node, set()):
            if successor not in reachable_without_gate:
                queue.append(successor)

    for enqueue_name in enqueue_steps:
        if enqueue_name in reachable_without_gate:
            findings.append(
                RuleFinding(
                    rule="enqueue-missing-ci-gate",
                    severity=Severity.ERROR,
                    step_name=enqueue_name,
                    message=(
                        f"Step '{enqueue_name}' calls enqueue_pr but is reachable from "
                        "recipe entry without passing through a wait_for_ci step. "
                        "Add a CI gate (wait_for_ci) before enqueue to prevent premature "
                        "queue enrollment."
                    ),
                )
            )
    return findings


# ---------------------------------------------------------------------------
# ci-cwd-branch-context-mismatch rule
# ---------------------------------------------------------------------------

_WORKTREE_BRANCH_PATTERNS: frozenset[str] = frozenset(
    {
        "worktree_branch_name",
        "worktree_branch",
    }
)

_CLONE_CWD_PATTERNS: frozenset[str] = frozenset(
    {
        "work_dir",
        "clone_path",
        "clone_dir",
    }
)

_CONTEXT_VAR_RE = re.compile(r"\$\{\{\s*context\.(\w+)\s*\}\}")


def _extract_context_var(value: str) -> str | None:
    """Extract context variable name from value using partial search (not fullmatch)."""
    m = _CONTEXT_VAR_RE.search(value)
    return m.group(1) if m else None


def _is_worktree_branch_ref(var_name: str) -> bool:
    return any(pat in var_name for pat in _WORKTREE_BRANCH_PATTERNS)


def _is_clone_cwd_ref(var_name: str) -> bool:
    return any(pat in var_name for pat in _CLONE_CWD_PATTERNS)


@semantic_rule(
    name="ci-cwd-branch-context-mismatch",
    description=(
        "wait_for_ci step watches a worktree branch but cwd references the clone root. "
        "git rev-parse HEAD in the clone root returns the wrong SHA, causing "
        "_validate_run_matches_scope to silently reject valid CI runs."
    ),
    severity=Severity.ERROR,
)
def _check_ci_cwd_branch_context_mismatch(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "wait_for_ci":
            continue
        with_args = step.with_args or {}

        if with_args.get("head_sha"):
            continue

        branch_val = with_args.get("branch", "")
        cwd_val = with_args.get("cwd", "")

        branch_var = _extract_context_var(str(branch_val))
        cwd_var = _extract_context_var(str(cwd_val))

        if not branch_var or not cwd_var:
            continue

        if _is_worktree_branch_ref(branch_var) and _is_clone_cwd_ref(cwd_var):
            findings.append(
                RuleFinding(
                    rule="ci-cwd-branch-context-mismatch",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' watches worktree branch "
                        f"(context.{branch_var}) but cwd uses clone root "
                        f"(context.{cwd_var}). git rev-parse HEAD in the clone root "
                        f"returns the batch/base branch SHA, not the worktree branch SHA. "
                        f"Use cwd: '${{{{ context.worktree_path }}}}' or pass head_sha explicitly."
                    ),
                )
            )
    return findings
