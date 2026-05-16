"""Semantic validation rules — loop progress tracking enforcement."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autoskillit.recipe.schema import RecipeStep

from autoskillit.core import (
    SKILL_TOOLS,
    Severity,
    get_logger,
    resolve_skill_name,
)
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.contracts import (
    get_skill_contract,
    load_bundled_manifest,
)
from autoskillit.recipe.registry import RuleFinding, semantic_rule

logger = get_logger(__name__)


def _find_cycle_members(
    graph: dict[str, set[str]], recipe_steps: Mapping[str, RecipeStep]
) -> list[frozenset[str]]:
    """Find all sets of steps that participate in a routing cycle.

    Uses DFS back-edge detection (same pattern as _check_unbounded_cycles).
    Returns a list of frozensets of step names that form cycles.
    """
    visited: set[str] = set()
    rec_stack: set[str] = set()
    cycles: list[frozenset[str]] = []

    def dfs(node: str, path: list[str]) -> None:
        visited.add(node)
        rec_stack.add(node)
        for neighbor in sorted(graph.get(node, set())):
            if neighbor not in recipe_steps:
                continue
            if neighbor not in visited:
                dfs(neighbor, path + [neighbor])
            elif neighbor in rec_stack:
                if neighbor in path:
                    cycle_steps = path[path.index(neighbor) :]
                else:
                    cycle_steps = path
                cycles.append(frozenset(cycle_steps))
        rec_stack.discard(node)

    for step_name in recipe_steps:
        if step_name not in visited:
            dfs(step_name, [step_name])

    return cycles


@semantic_rule(
    name="loop-body-uncaptured-output",
    description=("run_skill steps inside routing cycles must capture declared outputs"),
    severity=Severity.ERROR,
)
def _check_loop_body_capture(ctx: ValidationContext) -> list[RuleFinding]:
    """Flag run_skill steps in cycles that have no capture block despite declared outputs."""
    findings: list[RuleFinding] = []
    manifest = load_bundled_manifest()
    recipe_steps = ctx.recipe.steps

    cycle_sets = _find_cycle_members(ctx.step_graph, recipe_steps)

    for cycle_set in cycle_sets:
        for step_name in cycle_set:
            step = recipe_steps.get(step_name)
            if step is None:
                continue
            if step.tool not in SKILL_TOOLS:
                continue

            skill_cmd = step.with_args.get("skill_command", "")
            name = resolve_skill_name(skill_cmd)
            if not name:
                continue

            contract = get_skill_contract(name, manifest)
            if not contract:
                continue

            if not contract.outputs:
                continue

            if step.capture is None or len(step.capture) == 0:
                cycle_list = sorted(cycle_set)
                findings.append(
                    RuleFinding(
                        rule="loop-body-uncaptured-output",
                        severity=Severity.ERROR,
                        step_name=step_name,
                        message=(
                            f"Step '{step_name}' in loop [{'→'.join(cycle_list)}] "
                            f"invokes skill '{name}' with declared outputs "
                            f"{[o.name for o in contract.outputs]} "
                            f"but has no capture: block"
                        ),
                    )
                )

    return findings
