"""Campaign dispatch structure and naming rules."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

import regex as re

from autoskillit.core import RECIPE_PACK_REGISTRY, Severity, get_logger
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._rule_helpers import _load_dispatch_target
from autoskillit.recipe.registry import RuleFinding, semantic_rule
from autoskillit.recipe.schema import RecipeKind

if TYPE_CHECKING:
    from autoskillit.recipe.schema import Recipe

logger = get_logger(__name__)

_KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


@semantic_rule(
    name="campaign-kind-is-campaign",
    description="Recipe with dispatches must declare kind: campaign",
    severity=Severity.ERROR,
)
def _check_campaign_kind_is_campaign(ctx: ValidationContext) -> list[RuleFinding]:
    if not ctx.recipe.dispatches:
        return []
    if ctx.recipe.kind == RecipeKind.CAMPAIGN:
        return []
    return [
        RuleFinding(
            rule="campaign-kind-is-campaign",
            severity=Severity.ERROR,
            step_name="(top-level)",
            message=(
                "Recipe has dispatches but kind is not 'campaign'. "
                "Set 'kind: campaign' in the recipe header."
            ),
        )
    ]


@semantic_rule(
    name="campaign-has-dispatches",
    description="Campaign recipe must have at least one dispatch",
    severity=Severity.ERROR,
)
def _check_campaign_has_dispatches(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    if ctx.recipe.dispatches:
        return []
    return [
        RuleFinding(
            rule="campaign-has-dispatches",
            severity=Severity.ERROR,
            step_name="(top-level)",
            message="Campaign recipe must have at least one dispatch in 'dispatches'.",
        )
    ]


@semantic_rule(
    name="dispatch-names-unique",
    description="Dispatch names within a campaign must be unique",
    severity=Severity.ERROR,
)
def _check_dispatch_names_unique(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    counts = Counter(d.name for d in ctx.recipe.dispatches)
    findings: list[RuleFinding] = []
    for name, count in counts.items():
        if count > 1:
            findings.append(
                RuleFinding(
                    rule="dispatch-names-unique",
                    severity=Severity.ERROR,
                    step_name="(top-level)",
                    message=f"Dispatch name {name!r} appears {count} times; names must be unique.",
                )
            )
    return findings


@semantic_rule(
    name="dispatch-names-kebab-case",
    description="Dispatch names should use kebab-case",
    severity=Severity.WARNING,
)
def _check_dispatch_names_kebab_case(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    findings: list[RuleFinding] = []
    for d in ctx.recipe.dispatches:
        if not _KEBAB_RE.match(d.name):
            findings.append(
                RuleFinding(
                    rule="dispatch-names-kebab-case",
                    severity=Severity.WARNING,
                    step_name="(top-level)",
                    message=(
                        f"Dispatch name {d.name!r} is not kebab-case. "
                        "Use lowercase letters, digits, and hyphens only."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="dispatch-recipe-exists",
    description="Each dispatch must reference a known recipe name",
    severity=Severity.ERROR,
)
def _check_dispatch_recipe_exists(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    if not ctx.available_recipes:
        return []
    findings: list[RuleFinding] = []
    for d in ctx.recipe.dispatches:
        if d.gate:
            continue
        if d.recipe not in ctx.available_recipes:
            findings.append(
                RuleFinding(
                    rule="dispatch-recipe-exists",
                    severity=Severity.ERROR,
                    step_name="(top-level)",
                    message=(
                        f"Dispatch {d.name!r} references recipe {d.recipe!r} "
                        "which is not in the known recipe set."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="dispatch-recipe-is-standard",
    description="Campaign dispatches must not target other campaign recipes",
    severity=Severity.ERROR,
)
def _check_dispatch_recipe_is_standard(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    findings: list[RuleFinding] = []
    for d in ctx.recipe.dispatches:
        if d.gate:
            continue
        target = _load_dispatch_target(d, ctx.project_dir)
        if target is None:
            continue
        if target.kind == RecipeKind.CAMPAIGN:
            findings.append(
                RuleFinding(
                    rule="dispatch-recipe-is-standard",
                    severity=Severity.ERROR,
                    step_name="(top-level)",
                    message=(
                        f"Dispatch {d.name!r} targets recipe {d.recipe!r} which is itself a "
                        "campaign recipe. Campaign nesting is not supported."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="dispatch-recipe-in-declared-packs",
    description="Dispatch target recipes should belong to the campaign's declared packs",
    severity=Severity.WARNING,
)
def _check_dispatch_recipe_in_declared_packs(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    if not ctx.recipe.requires_recipe_packs:
        return []
    findings: list[RuleFinding] = []
    for d in ctx.recipe.dispatches:
        if d.gate:
            continue
        if d.recipe in ctx.recipe.allowed_recipes:
            continue
        target = _load_dispatch_target(d, ctx.project_dir)
        if target is None:
            continue
        if not (set(target.categories) & set(ctx.recipe.requires_recipe_packs)):
            findings.append(
                RuleFinding(
                    rule="dispatch-recipe-in-declared-packs",
                    severity=Severity.WARNING,
                    step_name="(top-level)",
                    message=(
                        f"Dispatch {d.name!r} targets recipe {d.recipe!r} whose categories "
                        f"{target.categories!r} do not overlap with the campaign's declared "
                        f"packs {ctx.recipe.requires_recipe_packs!r}."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="campaign-requires-recipe-packs-exist",
    description="Pack names in requires_recipe_packs must be in RECIPE_PACK_REGISTRY",
    severity=Severity.WARNING,
)
def _check_campaign_requires_recipe_packs_exist(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    findings: list[RuleFinding] = []
    seen: set[str] = set()
    for pack_name in ctx.recipe.requires_recipe_packs:
        if pack_name not in RECIPE_PACK_REGISTRY and pack_name not in seen:
            seen.add(pack_name)
            findings.append(
                RuleFinding(
                    rule="campaign-requires-recipe-packs-exist",
                    severity=Severity.WARNING,
                    step_name="(top-level)",
                    message=(
                        f"Pack {pack_name!r} in requires_recipe_packs is not in "
                        f"RECIPE_PACK_REGISTRY. Known packs: {sorted(RECIPE_PACK_REGISTRY)}"
                    ),
                )
            )
    return findings


@semantic_rule(
    name="campaign-task-non-empty",
    description="Each dispatch must have a non-empty task description",
    severity=Severity.ERROR,
)
def _check_campaign_task_non_empty(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    findings: list[RuleFinding] = []
    for d in ctx.recipe.dispatches:
        if d.gate:
            continue
        if not d.task.strip():
            findings.append(
                RuleFinding(
                    rule="campaign-task-non-empty",
                    severity=Severity.ERROR,
                    step_name="(top-level)",
                    message=f"Dispatch {d.name!r} has an empty 'task' field.",
                )
            )
    return findings
