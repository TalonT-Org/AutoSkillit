"""audit-impl diff topology mismatch: SHA-mode in pre-merge topology."""

from __future__ import annotations

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._analysis_bfs import bfs_reachable
from autoskillit.recipe.contracts import resolve_skill_name
from autoskillit.recipe.registry import RuleFinding, semantic_rule

_SHA_INDICATORS = ("base_sha", "impl_base_sha")


def _is_sha_mode(skill_cmd: str) -> bool:
    return any(ind in skill_cmd for ind in _SHA_INDICATORS)


def _runs_in_worktree_context(step_with_args: dict[str, str]) -> bool:
    cwd = step_with_args.get("cwd", "")
    return "worktree_path" in cwd


def _is_food_truck(ctx: ValidationContext) -> bool:
    return getattr(ctx.recipe, "kind", None) is not None and str(
        getattr(ctx.recipe, "kind", "")
    ).upper() in ("FOOD_TRUCK", "FOODTRUCK")


@semantic_rule(
    name="audit-impl-diff-topology-mismatch",
    description=(
        "An audit-impl step using SHA-mode (skill_command references a sha variable) "
        "that runs against a non-worktree cwd should have a merge_worktree predecessor. "
        "Without one, in pre-merge topology the base_branch HEAD equals the SHA, "
        "so the diff formula produces empty results unless the SKILL.md uses ..HEAD "
        "instead of ..{base_branch}."
    ),
    severity=Severity.WARNING,
)
def _check_audit_impl_diff_topology(ctx: ValidationContext) -> list[RuleFinding]:
    if _is_food_truck(ctx):
        return []

    inverted: dict[str, set[str]] = {}
    for src, dsts in ctx.step_graph.items():
        for dst in dsts:
            inverted.setdefault(dst, set()).add(src)

    findings = []
    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_skill":
            continue
        skill = resolve_skill_name(step.with_args.get("skill_command", ""))
        if skill != "audit-impl":
            continue

        skill_cmd = step.with_args.get("skill_command", "")
        if not _is_sha_mode(skill_cmd):
            continue

        if _runs_in_worktree_context(step.with_args):
            continue

        ancestors = bfs_reachable(inverted, step_name)
        has_merge_predecessor = any(
            ctx.recipe.steps[a].tool == "merge_worktree"
            for a in ancestors
            if a in ctx.recipe.steps
        )
        if not has_merge_predecessor:
            findings.append(
                RuleFinding(
                    rule="audit-impl-diff-topology-mismatch",
                    severity=Severity.WARNING,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' invokes audit-impl with SHA-mode "
                        f"(skill_command references a sha variable) against a "
                        f"non-worktree cwd, but no merge_worktree step is reachable "
                        f"as a predecessor. In pre-merge topology, the SHA diff formula "
                        f"may produce empty results unless the SKILL.md uses ..HEAD "
                        f"instead of ..{{base_branch}}."
                    ),
                )
            )
    return findings
