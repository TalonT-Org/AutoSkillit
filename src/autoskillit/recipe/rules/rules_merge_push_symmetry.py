"""Semantic rule for merge_worktree success-fallthrough push symmetry (R9)."""

from __future__ import annotations

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._analysis_bfs import _build_success_step_graph
from autoskillit.recipe.registry import RuleFinding, make_finding, semantic_rule
from autoskillit.recipe.schema import RecipeStep


@semantic_rule(
    name="merge-site-push-symmetry",
    description=(
        "A merge_worktree step's success fallthrough does not reach push_to_remote "
        "before the next merge_worktree or recipe-terminal step. Without the push, "
        "the local branch advances un-published, guaranteeing ref_coherence "
        "divergence at the next merge site (issue #4274 root cause)."
    ),
    severity=Severity.WARNING,
)
def _check_merge_site_push_symmetry(ctx: ValidationContext) -> list[RuleFinding]:
    """Verify each merge_worktree's success path reaches push_to_remote first.

    Issue #4274 root cause: pre_remediation_merge success routed directly to
    ``remediate`` without a push_to_remote step in between. Every successful
    merge must push the local branch before the next merge site or the recipe
    terminal — otherwise ref_coherence divergence is structurally guaranteed.

    Algorithm:
    1. Find every ``merge_worktree`` step.
    2. Identify its success-fallthrough route (the unconditional ``on_result``
       condition, falling back to ``on_success``).
    3. BFS forward on the success-path graph from that target.
    4. Fire if a ``merge_worktree`` step is reached before any
       ``push_to_remote`` step (the push must come first on the success path).
    """
    findings: list[RuleFinding] = []
    success_graph = _build_success_step_graph(ctx.recipe)

    def _success_fallthrough_target(step: RecipeStep) -> str | None:
        on_result = step.on_result
        if on_result is not None and on_result.conditions:
            for cond in on_result.conditions:
                if cond.when is None:
                    return cond.route
        return step.on_success

    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "merge_worktree":
            continue
        target = _success_fallthrough_target(step)
        if target is None:
            continue

        visited: set[str] = set()
        queue: list[str] = [target]
        push_found = False
        earlier_merge = None
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            current_step = ctx.recipe.steps.get(current)
            if current_step is None:
                continue
            if current_step.tool == "push_to_remote":
                push_found = True
                break
            if current_step.tool == "merge_worktree":
                earlier_merge = current
                break
            queue.extend(success_graph.get(current, set()))

        if earlier_merge is not None:
            findings.append(
                make_finding(
                    rule_name="merge-site-push-symmetry",
                    step_name=step_name,
                    message=(
                        f"merge_worktree step '{step_name}' success fallthrough "
                        f"reaches '{earlier_merge}' before any push_to_remote — "
                        f"insert an inter-part push step before '{earlier_merge}' "
                        f"to close the ref_coherence divergence window."
                    ),
                )
            )
        elif not push_found:
            findings.append(
                make_finding(
                    rule_name="merge-site-push-symmetry",
                    step_name=step_name,
                    message=(
                        f"merge_worktree step '{step_name}' success fallthrough "
                        f"never reaches push_to_remote — insert a push_to_remote "
                        f"step before the next merge_worktree or recipe terminal."
                    ),
                )
            )

    return findings
