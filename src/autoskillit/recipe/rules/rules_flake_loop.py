"""Semantic rules for flake-suspected unwinnable loop detection in merge gate cycles."""

from __future__ import annotations

import regex as re

from autoskillit.core import Severity, get_logger
from autoskillit.recipe._analysis import ValidationContext, bfs_reachable
from autoskillit.recipe.registry import RuleFinding, semantic_rule

logger = get_logger(__name__)

_SKILL_CMD_PATTERN = re.compile(r"/(?:autoskillit:)?([\w-]+)")
_ARG_TOKEN_PATTERN = re.compile(r"\$\{\{[^}]+\}\}|[^\s]+")


def _count_skill_args(skill_command: str) -> int:
    """Count positional args in a skill_command after the skill name."""
    tokens = _ARG_TOKEN_PATTERN.findall(skill_command)
    return max(0, len(tokens) - 1)


@semantic_rule(
    name="flake-suspected-unwinnable-loop",
    description=(
        "A run_skill step invoking resolve-failures routes flake_suspected to a step "
        "that is part of a cycle passing through a merge_worktree step, AND the "
        "resolve-failures invocation has no failure context arguments (only 3 args: "
        "worktree, plan, branch). Without failure context, resolve-failures cannot "
        "investigate the specific failing tests, always produces flake_suspected, "
        "and the merge gate retry loop is structurally unwinnable on non-reproducible flakes."
    ),
    severity=Severity.ERROR,
)
def _check_flake_suspected_unwinnable_loop(ctx: ValidationContext) -> list[RuleFinding]:
    findings: list[RuleFinding] = []

    for step_name, step in ctx.recipe.steps.items():
        if step.tool != "run_skill":
            continue

        cmd = step.with_args.get("skill_command", "")
        m = _SKILL_CMD_PATTERN.search(cmd)
        if not m or m.group(1) != "resolve-failures":
            continue

        flake_target: str | None = None
        if step.on_result and step.on_result.conditions:
            for cond in step.on_result.conditions:
                if cond.when and "flake_suspected" in cond.when:
                    flake_target = cond.route
                    break

        if flake_target is None:
            continue

        reachable = bfs_reachable(ctx.step_graph, flake_target)
        reachable.add(flake_target)

        merge_in_cycle = any(
            ctx.recipe.steps.get(r) is not None and ctx.recipe.steps[r].tool == "merge_worktree"
            for r in reachable
        )

        if not merge_in_cycle:
            continue

        if _count_skill_args(cmd) <= 3:
            findings.append(
                RuleFinding(
                    rule="flake-suspected-unwinnable-loop",
                    severity=Severity.ERROR,
                    step_name=step_name,
                    message=(
                        f"Step '{step_name}' invokes resolve-failures with only "
                        f"{_count_skill_args(cmd)} positional arg(s) and routes "
                        f"flake_suspected → '{flake_target}', which leads back through "
                        f"a merge_worktree step. Without failure context (ci_conclusion + "
                        f"diagnosis_path), resolve-failures cannot diagnose the specific "
                        f"failing tests, will always produce flake_suspected on a local-pass "
                        f"flake, and the loop is unwinnable. Add a diagnose_merge_gate step "
                        f"before this step and expand skill_command to 6 args including "
                        f"merge_gate_ci_conclusion and merge_gate_diagnosis_path."
                    ),
                )
            )

    return findings
