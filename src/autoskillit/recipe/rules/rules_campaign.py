"""Semantic validation rules for campaign recipes."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

import regex as re

from autoskillit.core import RECIPE_PACK_REGISTRY, DispatchGateType, Severity, get_logger
from autoskillit.recipe._analysis import ValidationContext
from autoskillit.recipe._rule_helpers import _SENTINEL_JSON_RE, _is_failure_sentinel_value
from autoskillit.recipe.registry import RuleFinding, semantic_rule
from autoskillit.recipe.schema import CAMPAIGN_REF_RE, CampaignDispatch, RecipeKind

if TYPE_CHECKING:
    from autoskillit.recipe.schema import Recipe

logger = get_logger(__name__)

_KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# _run_dispatch() auto-injects these from dispatch-level fields (e.g. task: from the
# dispatch task: field), so campaigns that rely on this injection pattern should not
# be flagged for not explicitly forwarding them in the ingredients block.
_AUTO_INJECTED_CAMPAIGN_INGREDIENTS: frozenset[str] = frozenset({"task"})


def _extract_sentinel_fields(recipe: Recipe) -> frozenset[str]:
    """Extract declared field names from all sentinel stop step JSON examples."""
    fields: set[str] = set()
    for step in recipe.steps.values():
        if step.action != "stop" or not step.message:
            continue
        if "sentinel" not in step.message.lower():
            continue
        for match in _SENTINEL_JSON_RE.finditer(step.message):
            try:
                parsed = json.loads(match.group(1))
                if isinstance(parsed, dict):
                    fields.update(parsed.keys())
            except (json.JSONDecodeError, ValueError):
                continue
    return frozenset(fields)


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
    name="campaign-dangling-ingredient",
    description=(
        "Campaign ingredients should be forwarded to dispatches whose target recipe declares them"
    ),
    severity=Severity.WARNING,
)
def _check_campaign_dangling_ingredient(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    campaign_ingredients = set(ctx.recipe.ingredients.keys())
    if not campaign_ingredients:
        return []
    findings: list[RuleFinding] = []
    for d in ctx.recipe.dispatches:
        # Gated dispatches are conditional — they may not run at all, so requiring
        # them to forward every campaign ingredient would produce false positives.
        if d.gate:
            continue
        target = _load_dispatch_target(d, ctx.project_dir)
        if target is None:
            continue
        forwarded_keys = set(d.ingredients.keys())
        for ing_name in campaign_ingredients:
            if ing_name in _AUTO_INJECTED_CAMPAIGN_INGREDIENTS:
                continue
            if ing_name in target.ingredients and ing_name not in forwarded_keys:
                findings.append(
                    RuleFinding(
                        rule="campaign-dangling-ingredient",
                        severity=Severity.WARNING,
                        step_name="(top-level)",
                        message=(
                            f"Campaign ingredient {ing_name!r} is declared by target "
                            f"recipe {d.recipe!r} (dispatch {d.name!r}) but is not "
                            f"forwarded in the dispatch's ingredients block. The sub-recipe "
                            f"will use its own default instead of the campaign-level value."
                        ),
                    )
                )
    return findings


@semantic_rule(
    name="dispatch-required-ingredient-provided",
    description=(
        "Target recipe required ingredients (no default) must be provided by the dispatch"
    ),
    severity=Severity.ERROR,
)
def _check_dispatch_required_ingredient_provided(
    ctx: ValidationContext,
) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    findings: list[RuleFinding] = []
    for d in ctx.recipe.dispatches:
        if d.gate:
            continue
        target = _load_dispatch_target(d, ctx.project_dir)
        if target is None:
            continue
        effective_ingredients = set(d.ingredients.keys())
        for auto in _AUTO_INJECTED_CAMPAIGN_INGREDIENTS:
            effective_ingredients.add(auto)
        for key, ing in target.ingredients.items():
            if ing.required and ing.default is None and key not in effective_ingredients:
                findings.append(
                    RuleFinding(
                        rule="dispatch-required-ingredient-provided",
                        severity=Severity.ERROR,
                        step_name="(top-level)",
                        message=(
                            f"Dispatch {d.name!r} targets recipe {d.recipe!r} which "
                            f"declares ingredient {key!r} as required (no default), "
                            f"but the dispatch does not provide it. "
                            f"Provided: {sorted(d.ingredients)}."
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
_RESULT_FIELD_RE = re.compile(r"^\$\{\{\s*result\.([\w-]+)\s*\}\}$")


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
        for key, entry in d.capture.items():
            if not _RESULT_TEMPLATE_RE.match(entry.from_.strip()):
                findings.append(
                    RuleFinding(
                        rule="dispatch-capture-value-references-result",
                        severity=Severity.ERROR,
                        step_name="(top-level)",
                        message=(
                            f"Dispatch {d.name!r} capture[{key!r}] value {entry.from_!r} must use "
                            "${{ result.<field_name> }} syntax."
                        ),
                    )
                )
    return findings


@semantic_rule(
    name="dispatch-capture-field-in-sentinel",
    description="Captured result fields should appear in target recipe's sentinel message",
    severity=Severity.ERROR,
)
def _check_dispatch_capture_field_in_sentinel(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    findings: list[RuleFinding] = []
    for d in ctx.recipe.dispatches:
        if d.gate or not d.capture:
            continue
        target = _load_dispatch_target(d, ctx.project_dir)
        if target is None:
            continue
        sentinel_fields = _extract_sentinel_fields(target)
        if not sentinel_fields:
            continue
        for cap_key, cap_val in d.capture.items():
            match = _RESULT_FIELD_RE.match(cap_val.from_.strip())
            if not match:
                continue
            field_name = match.group(1)
            if field_name not in sentinel_fields:
                findings.append(
                    RuleFinding(
                        rule="dispatch-capture-field-in-sentinel",
                        severity=Severity.ERROR,
                        step_name="(top-level)",
                        message=(
                            f"Dispatch {d.name!r} captures {cap_key!r} as "
                            f"${{{{ result.{field_name} }}}} but target recipe "
                            f"{d.recipe!r} has no sentinel stop step listing "
                            f"field {field_name!r}. "
                            f"Known sentinel fields: {sorted(sentinel_fields)}."
                        ),
                    )
                )
    return findings


def _extract_sentinel_fields_per_stop(recipe: Recipe) -> list[frozenset[str]]:
    """Extract field sets from each individual sentinel stop step.

    Returns a list of frozensets, one per success sentinel stop, rather than
    the union. This enables per-path validation. Failure-terminal sentinels
    (those whose example JSON contains ``"success": false``) are excluded
    because failure paths do not produce captured result fields.
    """
    per_stop: list[frozenset[str]] = []
    for step in recipe.steps.values():
        if step.action != "stop" or not step.message:
            continue
        if "sentinel" not in step.message.lower():
            continue
        fields: set[str] = set()
        is_failure_sentinel = False
        for match in _SENTINEL_JSON_RE.finditer(step.message):
            try:
                parsed = json.loads(match.group(1))
                if isinstance(parsed, dict):
                    _sv = parsed.get("success")
                    if _is_failure_sentinel_value(_sv):
                        is_failure_sentinel = True
                        break
                    fields.update(parsed.keys())
            except (json.JSONDecodeError, ValueError):
                logger.debug("sentinel_json_parse_failed", step=step.name, raw=match.group(1))
                continue
        if not is_failure_sentinel and fields:
            per_stop.append(frozenset(fields))
    return per_stop


@semantic_rule(
    name="dispatch-capture-field-in-all-sentinels",
    description="Captured result fields must appear in ALL sentinel stop paths of target recipe",
    severity=Severity.ERROR,
)
def _check_dispatch_capture_field_in_all_sentinels(
    ctx: ValidationContext,
) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    findings: list[RuleFinding] = []
    for d in ctx.recipe.dispatches:
        if d.gate or not d.capture:
            continue
        target = _load_dispatch_target(d, ctx.project_dir)
        if target is None:
            continue
        per_stop = _extract_sentinel_fields_per_stop(target)
        if len(per_stop) < 2:
            continue
        for cap_key, cap_val in d.capture.items():
            match = _RESULT_FIELD_RE.match(cap_val.from_.strip())
            if not match:
                continue
            field_name = match.group(1)
            missing_in = [i for i, fields in enumerate(per_stop) if field_name not in fields]
            if missing_in:
                findings.append(
                    RuleFinding(
                        rule="dispatch-capture-field-in-all-sentinels",
                        severity=Severity.ERROR,
                        step_name="(top-level)",
                        message=(
                            f"Dispatch {d.name!r} captures {cap_key!r} as "
                            f"${{{{ result.{field_name} }}}} but not all sentinel "
                            f"stop paths in target recipe {d.recipe!r} emit "
                            f"field {field_name!r}. The field is missing from "
                            f"{len(missing_in)} of {len(per_stop)} sentinel paths. "
                            f"All sentinel paths must emit all captured fields."
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


_VALID_GATE_TYPES: frozenset[DispatchGateType] = frozenset({DispatchGateType.CONFIRM})


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
        if not (d.message or "").strip():
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
    name="campaign-path-coherence",
    description=(
        "Detects dispatches that invoke worktree-creating recipes "
        "without re-capturing worktree_path"
    ),
    severity=Severity.ERROR,
)
def _check_campaign_path_coherence(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    findings: list[RuleFinding] = []
    for d in ctx.recipe.dispatches:
        if d.gate:
            continue
        target = _load_dispatch_target(d, ctx.project_dir)
        if target is None:
            continue
        for step in target.steps.values():
            if step.capture and "worktree_path" in step.capture:
                if "worktree_path" not in d.capture:
                    findings.append(
                        RuleFinding(
                            rule="campaign-path-coherence",
                            severity=Severity.ERROR,
                            step_name="(top-level)",
                            message=(
                                f"Dispatch '{d.name}' invokes recipe '{d.recipe}' which "
                                f"captures worktree_path at step '{step.name}', but the "
                                f"dispatch does not re-capture worktree_path. Downstream "
                                f"dispatches will receive a stale worktree path."
                            ),
                        )
                    )
                break
    return findings


@semantic_rule(
    name="campaign-path-type-enforce",
    description=(
        "Validates that worktree_relative_path ingredients have "
        "a corresponding worktree_path anchor"
    ),
    severity=Severity.ERROR,
)
def _check_campaign_path_type_enforce(ctx: ValidationContext) -> list[RuleFinding]:
    if ctx.recipe.kind != RecipeKind.CAMPAIGN:
        return []
    findings: list[RuleFinding] = []
    for d in ctx.recipe.dispatches:
        if d.gate:
            continue
        target = _load_dispatch_target(d, ctx.project_dir)
        if target is None:
            continue
        for key, ing in target.ingredients.items():
            if getattr(ing, "type", None) == "worktree_relative_path":
                if "worktree_path" not in d.ingredients:
                    findings.append(
                        RuleFinding(
                            rule="campaign-path-type-enforce",
                            severity=Severity.ERROR,
                            step_name="(top-level)",
                            message=(
                                f"Dispatch '{d.name}' provides ingredient '{key}' "
                                f"(type: worktree_relative_path) without a corresponding "
                                f"worktree_path anchor."
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
                        f"capture={d.capture!r}. Gate dispatches produce no L3 session "
                        "output and must not specify capture."
                    ),
                )
            )
    return findings
