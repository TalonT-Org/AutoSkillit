"""Campaign dispatch dependency graph validation rules."""

from __future__ import annotations

from autoskillit.core import Severity
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.registry import RuleFinding, semantic_rule
from autoskillit.recipe.schema import RecipeKind


@semantic_rule(
    name="depends-on-refers-to-valid-dispatches",
    description="depends_on entries must reference known dispatch names",
    severity=Severity.ERROR,
)
def _check_depends_on_refers_to_valid_dispatches(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    all_names = {d.name for d in ctx.recipe.dispatches}
    findings: list[RuleFinding] = []
    for d in ctx.recipe.dispatches:
        for dep in d.depends_on:
            if dep not in all_names:
                findings.append(
                    RuleFinding(
                        rule="depends-on-refers-to-valid-dispatches",
                        severity=Severity.ERROR,
                        step_name="(top-level)",
                        message=(
                            f"Dispatch {d.name!r} depends_on {dep!r} which is not a known "
                            f"dispatch name. Known names: {sorted(all_names)}"
                        ),
                    )
                )
    return findings


@semantic_rule(
    name="depends-on-acyclic",
    description="Dispatch depends_on graph must be acyclic",
    severity=Severity.ERROR,
)
def _check_depends_on_acyclic(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    adjacency: dict[str, list[str]] = {d.name: list(d.depends_on) for d in ctx.recipe.dispatches}
    visited: set[str] = set()
    in_stack: set[str] = set()
    findings: list[RuleFinding] = []

    def dfs(node: str, path: list[str]) -> None:
        visited.add(node)
        in_stack.add(node)
        for neighbor in adjacency.get(node, []):
            if neighbor not in adjacency:
                continue
            if neighbor not in visited:
                dfs(neighbor, path + [neighbor])
            elif neighbor in in_stack:
                cycle_start = path.index(neighbor) if neighbor in path else 0
                cycle = path[cycle_start:] + [neighbor]
                findings.append(
                    RuleFinding(
                        rule="depends-on-acyclic",
                        severity=Severity.ERROR,
                        step_name="(top-level)",
                        message=(
                            f"Circular dependency detected in dispatches: {' -> '.join(cycle)}"
                        ),
                    )
                )
        in_stack.discard(node)

    for name in list(adjacency):
        if name not in visited:
            dfs(name, [name])

    return findings


@semantic_rule(
    name="campaign-dispatch-depends-on-is-sequential",
    description="Each dispatch's depends_on must have at most one entry (linear chain only)",
    severity=Severity.ERROR,
)
def _check_campaign_dispatch_depends_on_is_sequential(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    findings: list[RuleFinding] = []
    for d in ctx.recipe.dispatches:
        if len(d.depends_on) > 1:
            findings.append(
                RuleFinding(
                    rule="campaign-dispatch-depends-on-is-sequential",
                    severity=Severity.ERROR,
                    step_name="(top-level)",
                    message=(
                        f"Dispatch {d.name!r} has {len(d.depends_on)} entries in depends_on "
                        f"({d.depends_on!r}). Campaign dispatches must form a linear chain: "
                        "each dispatch may depend on at most one predecessor."
                    ),
                )
            )
    return findings
