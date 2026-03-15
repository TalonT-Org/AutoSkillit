"""Recipe orchestration API: load/validate pipelines, format responses."""

from __future__ import annotations

import dataclasses
import re
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass as _dc
from pathlib import Path
from typing import Any, TypedDict

from autoskillit.core import LoadResult, RecipeSource, YAMLError, get_logger, load_yaml, pkg_root
from autoskillit.recipe._analysis import make_validation_context
from autoskillit.recipe.contracts import (
    check_contract_staleness,
    load_recipe_card,
    stale_to_suggestions,
    validate_recipe_cards,
)
from autoskillit.recipe.diagrams import (
    check_diagram_staleness,
    diagram_stale_to_suggestions,
    load_recipe_diagram,
)
from autoskillit.recipe.io import (
    RecipeInfo,
    _parse_recipe,
    builtin_recipes_dir,
    builtin_sub_recipes_dir,
    find_recipe_by_name,
    find_sub_recipe_by_name,
    list_recipes,
)
from autoskillit.recipe.io import (
    load_recipe as _load_recipe_from_path,
)
from autoskillit.recipe.schema import Recipe, StepResultCondition, StepResultRoute
from autoskillit.recipe.validator import (
    build_quality_dict,
    compute_recipe_validity,
    filter_version_rule,
    findings_to_dicts,
    run_semantic_rules,
    validate_recipe,
)
from autoskillit.workspace import SkillResolver

_logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Schema contract: handler → formatter boundary
# ---------------------------------------------------------------------------


def _ingredient_sort_key(name: str, required: bool, default: object) -> tuple[int, str]:
    """Sort ingredients: required > auto-detect > flags > optional > constants."""
    if required and default is None:
        return (0, name)
    if default == "":
        return (1, name)
    if default in ("true", "false"):
        return (2, name)
    if default is None:
        return (3, name)
    return (4, name)  # has a non-empty default (constants, rarely changed)


def format_ingredients_table(
    recipe: Any, resolved_defaults: dict[str, str] | None = None
) -> str | None:
    """Build a pre-formatted ingredients table from a parsed Recipe.

    When ``resolved_defaults`` is provided, auto-detect ingredients (``default: ""``)
    use the resolved value instead of showing "auto-detect".
    """
    ingredients = getattr(recipe, "ingredients", None)
    if not ingredients:
        return None

    raw: list[tuple[str, str, str, tuple[int, str]]] = []
    for name, ing in ingredients.items():
        if getattr(ing, "hidden", False):
            continue  # skip hidden ingredients (not shown to agent)
        desc = getattr(ing, "description", "")
        required = getattr(ing, "required", False)
        default = getattr(ing, "default", None)
        sort_key = _ingredient_sort_key(name, required, default)
        if default is None and required:
            default_str, name_str = "(required)", f"{name} *"
        elif default == "":
            resolved = (resolved_defaults or {}).get(name)
            default_str = resolved if resolved else "auto-detect"
            name_str = name
        elif default == "true":
            default_str, name_str = "on", name
        elif default == "false":
            default_str, name_str = "off", name
        elif default is None:
            default_str, name_str = "--", name
        else:
            default_str, name_str = str(default), name
        raw.append((name_str, desc, default_str, sort_key))

    if not raw:
        return None

    raw.sort(key=lambda r: r[3])
    rows = [(r[0], r[1], r[2]) for r in raw]

    nw = max(len(r[0]) for r in rows)
    dw = max(len(r[1]) for r in rows)
    dfw = max(len(r[2]) for r in rows)
    nw = max(nw, 4)
    dw = max(dw, 11)
    dfw = max(dfw, 7)
    out: list[str] = []
    out.append(f"| {'Name':>{nw}} | {'Description':<{dw}} | {'Default':>{dfw}} |")
    out.append(f"| {'-' * (nw - 1)}: | {'-' * dw} | {'-' * (dfw - 1)}: |")
    for name_str, desc, default_str in rows:
        out.append(f"| {name_str:>{nw}} | {desc:<{dw}} | {default_str:>{dfw}} |")
    return "\n".join(out)


class LoadRecipeResult(TypedDict, total=False):
    """Typed schema for the load_recipe handler → formatter boundary."""

    content: str
    diagram: str | None
    suggestions: list[dict[str, Any]]
    valid: bool
    kitchen_rules: list[str]
    error: str
    greeting: str
    ingredients_table: str


class RecipeListItem(TypedDict):
    """Typed schema for a single recipe entry in the list_recipes response."""

    name: str
    description: str
    summary: str


class ListRecipesResult(TypedDict, total=False):
    """Typed schema for the list_recipes handler → formatter boundary."""

    recipes: list[RecipeListItem]
    count: int
    errors: list[dict[str, str]]


# ---------------------------------------------------------------------------
# Stage timing helper
# ---------------------------------------------------------------------------


def _t(label: str, t0: float, name: str) -> float:
    """Log elapsed time for a pipeline stage and return current time.

    Uses structlog at DEBUG level; structlog's processor chain handles level
    filtering without requiring an explicit isEnabledFor() guard.
    """
    elapsed_ms = (time.perf_counter() - t0) * 1000
    _logger.debug("load_recipe_stage", recipe=name, stage=label, elapsed_ms=round(elapsed_ms, 1))
    return time.perf_counter()


# ---------------------------------------------------------------------------
# Top-level result cache
# ---------------------------------------------------------------------------


def _file_mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _dir_mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def _get_pkg_version() -> str:
    from autoskillit import __version__

    return __version__


@_dc
class _LoadCacheEntry:
    recipe_path: Path
    recipe_mtime: int
    recipe_size: int
    project_dir_mtime: int
    builtin_dir_mtime: int
    pkg_version: str
    result: LoadRecipeResult


_LOAD_CACHE: dict[tuple, _LoadCacheEntry] = {}
_LOAD_CACHE_LOCK = threading.Lock()


def format_recipe_list_response(result: LoadResult[RecipeInfo]) -> dict[str, object]:
    """Build the MCP response dict for the list_recipes tool."""
    items: list[RecipeListItem] = [
        {"name": r.name, "description": r.description, "summary": r.summary} for r in result.items
    ]
    response: dict[str, object] = {
        "recipes": items,
        "count": len(items),
    }
    if result.errors:
        response["errors"] = [{"file": e.path.name, "error": e.error} for e in result.errors]
    return response


def list_all(project_dir: Path | None = None) -> dict[str, Any]:
    """List all recipes from project and built-in sources.

    Returns:
        {"recipes": list[{"name", "description", "summary"}]}
        Includes "errors" key when recipes fail to parse.
    """
    _pdir = project_dir if project_dir is not None else Path.cwd()
    result = list_recipes(_pdir)
    return format_recipe_list_response(result)


def _drop_sub_recipe_step(recipe: Any, step_name: str) -> Any:
    """Return a new Recipe with the named sub_recipe placeholder step removed."""
    new_steps = {k: v for k, v in recipe.steps.items() if k != step_name}
    return Recipe(
        name=recipe.name,
        description=recipe.description,
        summary=recipe.summary,
        ingredients=recipe.ingredients,
        steps=new_steps,
        kitchen_rules=recipe.kitchen_rules,
        version=recipe.version,
        experimental=recipe.experimental,
    )


def _merge_sub_recipe(parent: Any, placeholder_name: str, sub: Any) -> Any:
    """Replace the sub_recipe placeholder step with the sub-recipe's steps.

    Algorithm:
    1. Compute a safe name prefix from the sub-recipe name.
    2. For each step in sub, create a prefixed copy with routing fixed:
       - Routes to "done" → parent placeholder's on_success
       - Routes to "escalate" → parent placeholder's on_failure
       - Routes to other sub-recipe step names → add prefix
    3. Insert sub-recipe steps in place of the placeholder.
    4. Merge ingredients: add sub-recipe's non-hidden ingredients into parent.
    5. Merge kitchen_rules: union (deduplicated), sub-recipe rules appended.
    """
    placeholder = parent.steps[placeholder_name]
    on_success = placeholder.on_success or "done"
    on_failure = placeholder.on_failure or "escalate"

    # Build prefix: "sprint-prefix" → "sprint_prefix_", "my-sub" → "my_sub_"
    raw_prefix = re.sub(r"[^a-z0-9]", "_", (sub.name or placeholder_name).lower())
    if not raw_prefix.endswith("_"):
        raw_prefix += "_"
    prefix = raw_prefix

    sub_step_names = set(sub.steps.keys())

    def _fix_route(target: str | None) -> str | None:
        if target is None:
            return None
        if target == "done":
            return on_success
        if target == "escalate":
            return on_failure
        if target in sub_step_names:
            return prefix + target
        return target

    def _fix_result_route(route: Any) -> Any:
        if route is None:
            return None
        if route.conditions:
            return StepResultRoute(
                conditions=[
                    StepResultCondition(when=c.when, route=_fix_route(c.route) or "")
                    for c in route.conditions
                ]
            )
        return StepResultRoute(
            field=route.field,
            routes={k: (_fix_route(v) or v) for k, v in route.routes.items()},
        )

    prefixed_steps: dict[str, Any] = {}
    for sub_step_name, sub_step in sub.steps.items():
        new_name = prefix + sub_step_name
        new_step = dataclasses.replace(
            sub_step,
            on_success=_fix_route(sub_step.on_success),
            on_failure=_fix_route(sub_step.on_failure),
            on_context_limit=_fix_route(sub_step.on_context_limit),
            on_exhausted=_fix_route(sub_step.on_exhausted) or "escalate",
            on_result=_fix_result_route(sub_step.on_result),
        )
        prefixed_steps[new_name] = new_step

    # Assemble new steps dict: sub-recipe steps injected in place of placeholder
    new_steps: dict[str, Any] = {}
    for step_name, step in parent.steps.items():
        if step_name == placeholder_name:
            new_steps.update(prefixed_steps)
        else:
            new_steps[step_name] = step

    # Merge ingredients: sub-recipe non-hidden ingredients into parent
    merged_ingredients = dict(parent.ingredients)
    for ing_name, ing in sub.ingredients.items():
        if ing_name not in merged_ingredients:
            merged_ingredients[ing_name] = ing

    # Merge kitchen_rules: union (parent first, then sub-recipe additions)
    seen_rules: set[str] = set(parent.kitchen_rules)
    merged_rules = list(parent.kitchen_rules)
    for rule in sub.kitchen_rules:
        if rule not in seen_rules:
            merged_rules.append(rule)
            seen_rules.add(rule)

    return Recipe(
        name=parent.name,
        description=parent.description,
        summary=parent.summary,
        ingredients=merged_ingredients,
        steps=new_steps,
        kitchen_rules=merged_rules,
        version=parent.version,
        experimental=parent.experimental,
    )


def _build_active_recipe(
    recipe: Any,
    ingredient_overrides: dict[str, str] | None,
    project_dir: Path,
) -> tuple[Any, Any | None]:
    """Return (active_recipe, combined_recipe | None).

    active_recipe: the Recipe to serve to the agent.
        - If no sub_recipe steps: returns recipe unchanged.
        - If sub_recipe step with gate=false: returns recipe with sub_recipe step dropped.
        - If sub_recipe step with gate=true: returns the merged (combined) recipe.

    combined_recipe: the merged Recipe if any gate was true, else None.
        Used to run dual validation (REQ-VALID-004).
    """
    overrides = ingredient_overrides or {}
    sub_recipe_steps = [
        (name, step) for name, step in recipe.steps.items() if step.sub_recipe is not None
    ]
    if not sub_recipe_steps:
        return recipe, None

    combined: Any | None = None
    working = recipe

    for step_name, step in sub_recipe_steps:
        gate_name = step.gate or ""
        gate_ingredient = working.ingredients.get(gate_name)
        gate_default = gate_ingredient.default if gate_ingredient else "false"
        gate_value = overrides.get(gate_name, gate_default or "false")

        if gate_value.lower() in ("true", "1", "yes"):
            sr_path = find_sub_recipe_by_name(step.sub_recipe, project_dir)
            if sr_path is None:
                raise FileNotFoundError(
                    f"Sub-recipe '{step.sub_recipe}' not found. "
                    f"Expected in recipes/sub-recipes/{step.sub_recipe}.yaml"
                )
            sub_recipe = _load_recipe_from_path(sr_path)
            working = _merge_sub_recipe(working, step_name, sub_recipe)
            combined = working
        else:
            working = _drop_sub_recipe_step(working, step_name)

    return working, combined


def validate_from_path(path: Path) -> dict[str, Any]:
    """Validate a recipe YAML file at the given path.

    Returns:
        {"valid": bool, "errors": list, "quality": dict, "semantic": list, "contracts": list}
        On file/parse error: {"error": str}
    """
    if not path.is_file():
        return {
            "valid": False,
            "findings": [{"error": f"File not found: {path}"}],
        }

    try:
        data = load_yaml(path)
    except YAMLError as exc:
        return {
            "valid": False,
            "findings": [{"error": f"YAML parse error: {exc}"}],
        }

    if not isinstance(data, dict):
        return {
            "valid": False,
            "findings": [{"error": "File must contain a YAML mapping"}],
        }

    recipe = _parse_recipe(data)
    errors = validate_recipe(recipe)
    known_skills = frozenset(s.name for s in SkillResolver().list_all())
    ctx = make_validation_context(recipe, available_skills=known_skills)
    report = ctx.dataflow
    semantic_findings = run_semantic_rules(ctx)

    quality = build_quality_dict(report)
    semantic = findings_to_dicts(semantic_findings)

    contract_findings: list[dict[str, Any]] = []
    recipes_dir = path.parent
    recipe_name = path.stem
    contract = load_recipe_card(recipe_name, recipes_dir)
    if contract:
        contract_findings = validate_recipe_cards(recipe, contract)

    valid = compute_recipe_validity(errors, semantic_findings, contract_findings)

    return {
        "valid": valid,
        "errors": errors,
        "quality": quality,
        "findings": semantic,
        "contracts": contract_findings,
    }


def load_and_validate(
    name: str,
    project_dir: Path | None = None,
    *,
    suppressed: Sequence[str] | None = None,
    recipe_info: RecipeInfo | None = None,
    resolved_defaults: dict[str, str] | None = None,
    ingredient_overrides: dict[str, str] | None = None,
) -> LoadRecipeResult:
    """Load a recipe by name and run full validation.

    Args:
        name: Recipe name (without .yaml extension).
        project_dir: Directory to search (defaults to cwd).
        suppressed: Recipe names for which the version-outdated rule is silenced.
        recipe_info: Optional pre-resolved ``RecipeInfo`` from the repository's
            mtime-cached list. When provided, ``find_recipe_by_name`` is skipped.
        ingredient_overrides: Optional dict of ingredient name → value to override
            recipe defaults. Used to activate hidden features (e.g., sprint_mode).

    Returns:
        {"content": str, "suggestions": list, "valid": bool}
        On not-found: {"error": str}
    """
    _pdir = project_dir if project_dir is not None else Path.cwd()
    pkg_version = _get_pkg_version()
    project_recipes_dir = _pdir / ".autoskillit" / "recipes"
    _builtin_dir = builtin_recipes_dir()
    cache_key = (
        name,
        str(_pdir),
        tuple(sorted(suppressed)) if suppressed else (),
        tuple(sorted(ingredient_overrides.items())) if ingredient_overrides else (),
    )

    with _LOAD_CACHE_LOCK:
        cached = _LOAD_CACHE.get(cache_key)

    if cached is not None and cached.pkg_version == pkg_version:
        pm = _dir_mtime_ns(project_recipes_dir)
        bm = _dir_mtime_ns(_builtin_dir)
        rm = _file_mtime_ns(cached.recipe_path)
        rs = _file_size(cached.recipe_path)
        if (
            pm == cached.project_dir_mtime
            and bm == cached.builtin_dir_mtime
            and rm == cached.recipe_mtime
            and rs == cached.recipe_size
        ):
            _logger.debug("load_recipe_cache_hit", recipe=name)
            return cached.result

    t0 = time.perf_counter()

    # Stage: find recipe
    if recipe_info is not None:
        match: RecipeInfo | None = recipe_info
    else:
        match = find_recipe_by_name(name, _pdir)
    t0 = _t("find_recipe", t0, name)

    if match is None:
        return {"error": f"No recipe named '{name}' found"}

    raw = match.content if match.content is not None else match.path.read_text()
    suggestions: list[dict[str, Any]] = []
    valid = True
    recipe = None
    active_recipe = None

    # Determine recipes_dir from source
    if match.source == RecipeSource.BUILTIN:
        recipes_dir = pkg_root() / "recipes"
    else:
        recipes_dir = _pdir / ".autoskillit" / "recipes"

    try:
        # Stage: yaml parse
        data = load_yaml(raw)
        t0 = _t("yaml_parse", t0, name)

        if isinstance(data, dict) and "steps" in data:
            recipe = _parse_recipe(data)

            # Stage: sub-recipe composition (lazy-loaded prefixes)
            active_recipe, combined_recipe = _build_active_recipe(
                recipe, ingredient_overrides, _pdir
            )

            # Stage: structural validation on active recipe
            errors = validate_recipe(active_recipe)
            if combined_recipe is not None:
                # Dual validation: also validate the combined (merged) graph
                combined_errors = validate_recipe(combined_recipe)
                errors.extend(f"[combined] {e}" for e in combined_errors if e not in errors)
            t0 = _t("validate_recipe", t0, name)

            # Stage: semantic rules (builds ValidationContext once — shared computation)
            known = frozenset(r.name for r in list_recipes(_pdir).items)
            known_skills = frozenset(s.name for s in SkillResolver().list_all())
            sub_recipes_dir = builtin_sub_recipes_dir()
            known_sub_recipes: frozenset[str] = (
                frozenset(p.stem for p in sub_recipes_dir.glob("*.yaml"))
                if sub_recipes_dir.is_dir()
                else frozenset()
            )
            project_sub_dir = _pdir / ".autoskillit" / "recipes" / "sub-recipes"
            if project_sub_dir.is_dir():
                known_sub_recipes |= frozenset(p.stem for p in project_sub_dir.glob("*.yaml"))
            val_ctx = make_validation_context(
                active_recipe,
                available_recipes=known,
                available_skills=known_skills,
                available_sub_recipes=known_sub_recipes,
                project_dir=_pdir,
            )
            semantic_findings = run_semantic_rules(val_ctx)
            semantic_suggestions = findings_to_dicts(semantic_findings)
            t0 = _t("semantic_rules", t0, name)

            _suppressed = suppressed or []
            if name in _suppressed:
                semantic_suggestions = filter_version_rule(semantic_suggestions)
            suggestions.extend(semantic_suggestions)

            # Stage: contract card
            contract = load_recipe_card(name, recipes_dir)
            contract_findings: list[dict[str, Any]] = []
            if contract:
                contract_findings = validate_recipe_cards(active_recipe, contract)
                suggestions.extend(contract_findings)
            t0 = _t("contract_card", t0, name)

            # Stage: staleness check
            if contract:
                staleness_cache_path = (
                    _pdir / ".autoskillit" / "temp" / "recipe_staleness_cache.json"
                )
                stale = check_contract_staleness(
                    contract, recipe_path=match.path, cache_path=staleness_cache_path
                )
                suggestions.extend(stale_to_suggestions(stale))
            t0 = _t("staleness_check", t0, name)

            # Stage: diagram
            if check_diagram_staleness(name, recipes_dir, match.path):
                suggestions.extend(diagram_stale_to_suggestions(name))
            t0 = _t("diagram", t0, name)

            valid = compute_recipe_validity(errors, semantic_findings, contract_findings)
        else:
            t0 = _t("yaml_parse", t0, name)

    except YAMLError as exc:
        _logger.warning("Recipe YAML parse error", name=name, exc_info=True)
        suggestions.append(
            {
                "rule": "validation-error",
                "severity": "error",
                "step": "(validation-pipeline)",
                "message": f"YAML parse error: {exc}",
            }
        )
        valid = False
    except ValueError as exc:
        _logger.warning("Recipe structure invalid", name=name, exc_info=True)
        suggestions.append(
            {
                "rule": "validation-error",
                "severity": "error",
                "step": "(validation-pipeline)",
                "message": f"Invalid recipe structure: {exc}",
            }
        )
        valid = False
    except (FileNotFoundError, OSError) as exc:
        _logger.warning("Recipe file not found or unreadable", name=name, exc_info=True)
        suggestions.append(
            {
                "rule": "validation-error",
                "severity": "error",
                "step": "(validation-pipeline)",
                "message": f"File error: {exc}",
            }
        )
        valid = False

    # Load pre-generated diagram
    diagram: str | None = load_recipe_diagram(name, recipes_dir)

    # Build pre-formatted ingredients table from active_recipe (has merged/filtered ingredients)
    _serving_recipe = active_recipe if active_recipe is not None else recipe
    ing_table = (
        format_ingredients_table(_serving_recipe, resolved_defaults=resolved_defaults)
        if _serving_recipe is not None
        else None
    )

    result: LoadRecipeResult = {
        "content": raw,
        "diagram": diagram,
        "suggestions": suggestions,
        "valid": valid,
    }
    if _serving_recipe is not None and _serving_recipe.kitchen_rules:
        result["kitchen_rules"] = _serving_recipe.kitchen_rules
    if ing_table:
        result["ingredients_table"] = ing_table

    # Write to cache (only when recipe was found and fully processed)
    if match is not None:
        entry = _LoadCacheEntry(
            recipe_path=match.path,
            recipe_mtime=_file_mtime_ns(match.path),
            recipe_size=_file_size(match.path),
            project_dir_mtime=_dir_mtime_ns(project_recipes_dir),
            builtin_dir_mtime=_dir_mtime_ns(_builtin_dir),
            pkg_version=pkg_version,
            result=result,
        )
        with _LOAD_CACHE_LOCK:
            _LOAD_CACHE[cache_key] = entry

    return result
