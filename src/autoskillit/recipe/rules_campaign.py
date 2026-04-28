"""Semantic validation rules for campaign recipes."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import RECIPE_PACK_REGISTRY, Severity, get_logger
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe.registry import RuleFinding, semantic_rule
from autoskillit.recipe.schema import CAMPAIGN_REF_RE, CampaignDispatch, RecipeKind

if TYPE_CHECKING:
    from autoskillit.recipe.schema import Recipe

logger = get_logger(__name__)

_KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def _load_dispatch_target(dispatch: CampaignDispatch, project_dir: Path | None) -> Recipe | None:
    """Load the target recipe for a dispatch. Returns None if not loadable."""
    if project_dir is None:
        return None
    try:
        from autoskillit.recipe.io import find_recipe_by_name, load_recipe  # noqa: PLC0415

        info = find_recipe_by_name(dispatch.recipe, project_dir)
        if info is None:
            return None
        return load_recipe(info.path)
    except Exception:
        logger.warning("dispatch_target_load_failed", recipe=dispatch.recipe, exc_info=True)
        return None


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
    name="dispatch-ingredients-keys-in-target-schema",
    description="Dispatch ingredient keys must exist in the target recipe's ingredients",
    severity=Severity.ERROR,
)
def _check_dispatch_ingredients_keys_in_target_schema(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    findings: list[RuleFinding] = []
    for d in ctx.recipe.dispatches:
        if d.gate:
            continue
        if not d.ingredients:
            continue
        target = _load_dispatch_target(d, ctx.project_dir)
        if target is None:
            continue
        for key in d.ingredients:
            if key not in target.ingredients:
                findings.append(
                    RuleFinding(
                        rule="dispatch-ingredients-keys-in-target-schema",
                        severity=Severity.ERROR,
                        step_name="(top-level)",
                        message=(
                            f"Dispatch {d.name!r} passes ingredient {key!r} to recipe "
                            f"{d.recipe!r}, but that recipe does not declare ingredient {key!r}. "
                            f"Known ingredients: {sorted(target.ingredients)}"
                        ),
                    )
                )
    return findings


@semantic_rule(
    name="dispatch-ingredient-values-are-strings",
    description="All dispatch ingredient values must be strings",
    severity=Severity.ERROR,
)
def _check_dispatch_ingredient_values_are_strings(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    findings: list[RuleFinding] = []
    for d in ctx.recipe.dispatches:
        for key, val in d.ingredients.items():
            if not isinstance(val, str):
                findings.append(
                    RuleFinding(
                        rule="dispatch-ingredient-values-are-strings",
                        severity=Severity.ERROR,
                        step_name="(top-level)",
                        message=(
                            f"Dispatch {d.name!r} ingredient {key!r} has non-string value "
                            f"{val!r} ({type(val).__name__}). YAML auto-coercion detected — "
                            "quote the value in YAML."
                        ),
                    )
                )
    return findings


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
                            f"Circular dependency detected in dispatches: {' → '.join(cycle)}"
                        ),
                    )
                )
        in_stack.discard(node)

    for name in list(adjacency):
        if name not in visited:
            dfs(name, [name])

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


_IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_RESULT_TEMPLATE_RE = re.compile(r"^\$\{\{\s*result\.[\w-]+\s*\}\}$")


@semantic_rule(
    name="dispatch-capture-keys-are-identifiers",
    description="Capture keys must be valid Python identifiers",
    severity=Severity.ERROR,
)
def _check_dispatch_capture_keys_are_identifiers(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    findings = []
    for d in ctx.recipe.dispatches:
        for key in d.capture:
            if not _IDENT_RE.match(key):
                findings.append(
                    RuleFinding(
                        rule="dispatch-capture-keys-are-identifiers",
                        severity=Severity.ERROR,
                        step_name="(top-level)",
                        message=(
                            f"Dispatch {d.name!r} capture key {key!r} is not a valid"
                            " identifier. Use only letters, digits, and underscores"
                            " (must start with letter or _)."
                        ),
                    )
                )
    return findings


@semantic_rule(
    name="dispatch-capture-value-references-result",
    description="Capture values must use ${{ result.field }} syntax",
    severity=Severity.ERROR,
)
def _check_dispatch_capture_value_references_result(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    findings = []
    for d in ctx.recipe.dispatches:
        for key, val in d.capture.items():
            if not _RESULT_TEMPLATE_RE.match(val.strip()):
                findings.append(
                    RuleFinding(
                        rule="dispatch-capture-value-references-result",
                        severity=Severity.ERROR,
                        step_name="(top-level)",
                        message=(
                            f"Dispatch {d.name!r} capture[{key!r}] value {val!r} must use "
                            "${{ result.<field_name> }} syntax."
                        ),
                    )
                )
    return findings


def _build_ancestors(name: str, adjacency: dict[str, list[str]]) -> set[str]:
    """Transitive closure of depends_on for a given dispatch name."""
    ancestors: set[str] = set()
    queue = list(adjacency.get(name, []))
    while queue:
        dep = queue.pop()
        if dep not in ancestors:
            ancestors.add(dep)
            queue.extend(adjacency.get(dep, []))
    return ancestors


@semantic_rule(
    name="campaign-ingredient-refs-have-prior-capture",
    description="${{ campaign.key }} in ingredients must be captured by an ancestor dispatch",
    severity=Severity.ERROR,
)
def _check_campaign_ingredient_refs_have_prior_capture(
    ctx: ValidationContext,
) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    adjacency = {d.name: list(d.depends_on) for d in ctx.recipe.dispatches}
    dispatch_by_name = {d.name: d for d in ctx.recipe.dispatches}
    findings = []
    for d in ctx.recipe.dispatches:
        ancestors = _build_ancestors(d.name, adjacency)
        available_captures: set[str] = set()
        for ancestor_name in ancestors:
            ancestor = dispatch_by_name.get(ancestor_name)
            if ancestor:
                available_captures.update(ancestor.capture.keys())
        for ing_key, ing_val in d.ingredients.items():
            if not isinstance(ing_val, str):
                continue
            for ref in CAMPAIGN_REF_RE.findall(ing_val):
                if ref not in available_captures:
                    findings.append(
                        RuleFinding(
                            rule="campaign-ingredient-refs-have-prior-capture",
                            severity=Severity.ERROR,
                            step_name="(top-level)",
                            message=(
                                f"Dispatch {d.name!r} ingredient {ing_key!r} references "
                                f"${{{{ campaign.{ref} }}}} but no ancestor dispatch "
                                f"(via depends_on) captures {ref!r}. "
                                f"Available captured keys: {sorted(available_captures)}"
                            ),
                        )
                    )
    return findings


@semantic_rule(
    name="autoskillit-version-compatible",
    description="Campaign recipe version requirement must be satisfied by installed version",
    severity=Severity.WARNING,
)
def _check_autoskillit_version_compatible(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    if not ctx.recipe.version:
        return []
    try:
        from importlib.metadata import version  # noqa: PLC0415

        from packaging.version import Version  # noqa: PLC0415

        installed = Version(version("autoskillit"))
        required = Version(ctx.recipe.version)
        if required > installed:
            return [
                RuleFinding(
                    rule="autoskillit-version-compatible",
                    severity=Severity.WARNING,
                    step_name="(top-level)",
                    message=(
                        f"Campaign requires autoskillit>={ctx.recipe.version} "
                        f"but installed version is {installed}."
                    ),
                )
            ]
    except Exception:
        logger.warning("autoskillit_version_check_failed", exc_info=True)
    return []


_VALID_GATE_TYPES: frozenset[str] = frozenset({"confirm"})


@semantic_rule(
    name="gate-dispatch-valid-type",
    description="gate value must be 'confirm'",
    severity=Severity.ERROR,
)
def _check_gate_dispatch_valid_type(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    findings: list[RuleFinding] = []
    for d in ctx.recipe.dispatches:
        if d.gate is None:
            continue
        if d.gate not in _VALID_GATE_TYPES:
            findings.append(
                RuleFinding(
                    rule="gate-dispatch-valid-type",
                    severity=Severity.ERROR,
                    step_name="(top-level)",
                    message=(
                        f"Dispatch {d.name!r} has gate={d.gate!r} which is not a valid "
                        f"gate type. Supported types: {sorted(_VALID_GATE_TYPES)}"
                    ),
                )
            )
    return findings


@semantic_rule(
    name="gate-dispatch-has-message",
    description="A gate dispatch must have a non-empty message",
    severity=Severity.ERROR,
)
def _check_gate_dispatch_has_message(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    findings: list[RuleFinding] = []
    for d in ctx.recipe.dispatches:
        if d.gate is None:
            continue
        if not d.message.strip():
            findings.append(
                RuleFinding(
                    rule="gate-dispatch-has-message",
                    severity=Severity.ERROR,
                    step_name="(top-level)",
                    message=(
                        f"Dispatch {d.name!r} has gate={d.gate!r} but 'message' is empty. "
                        "A non-empty message is required for gate dispatches."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="gate-dispatch-no-recipe",
    description="A gate dispatch must not specify recipe or task",
    severity=Severity.ERROR,
)
def _check_gate_dispatch_no_recipe(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    findings: list[RuleFinding] = []
    for d in ctx.recipe.dispatches:
        if d.gate is None:
            continue
        if d.recipe or d.task:
            findings.append(
                RuleFinding(
                    rule="gate-dispatch-no-recipe",
                    severity=Severity.ERROR,
                    step_name="(top-level)",
                    message=(
                        f"Dispatch {d.name!r} has gate={d.gate!r} but also specifies "
                        f"recipe={d.recipe!r} or task={d.task!r}. "
                        "Gate dispatches must not specify recipe or task."
                    ),
                )
            )
    return findings


@semantic_rule(
    name="gate-dispatch-no-capture",
    description="A gate dispatch must not specify capture",
    severity=Severity.ERROR,
)
def _check_gate_dispatch_no_capture(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    findings: list[RuleFinding] = []
    for d in ctx.recipe.dispatches:
        if d.gate is None:
            continue
        if d.capture:
            findings.append(
                RuleFinding(
                    rule="gate-dispatch-no-capture",
                    severity=Severity.ERROR,
                    step_name="(top-level)",
                    message=(
                        f"Dispatch {d.name!r} has gate={d.gate!r} but also specifies "
                        f"capture={d.capture!r}. Gate dispatches produce no L2 session "
                        "output and must not specify capture."
                    ),
                )
            )
    return findings
