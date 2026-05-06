"""Recipe orchestration API: load/validate pipelines, format responses."""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass as _dc
from pathlib import Path
from typing import Any

from autoskillit.core import (
    LoadResult,
    RecipeSource,
    SkillLister,
    YAMLError,
    get_logger,
    load_yaml,
    pkg_root,
    resolve_temp_dir,
)
from autoskillit.recipe._analysis import make_validation_context
from autoskillit.recipe._recipe_composition import (
    _build_active_recipe,
)
from autoskillit.recipe._recipe_ingredients import (
    ListRecipesResult,  # noqa: F401
    LoadRecipeResult,
    OpenKitchenResult,  # noqa: F401
    RecipeListItem,
    build_ingredient_rows,  # noqa: F401
    format_ingredients_table,
)
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
    list_recipes,
    substitute_temp_placeholder,
)
from autoskillit.recipe.schema import Recipe
from autoskillit.recipe.validator import (
    build_quality_dict,
    compute_recipe_validity,
    filter_version_rule,
    findings_to_dicts,
    run_semantic_rules,
    validate_recipe,
)

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Stage timing helper
# ---------------------------------------------------------------------------


def _t(label: str, t0: float, name: str) -> float:
    """Log elapsed time for a pipeline stage and return current time.

    Uses structlog at DEBUG level; structlog's processor chain handles level
    filtering without requiring an explicit isEnabledFor() guard.
    """
    elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.debug("load_recipe_stage", recipe=name, stage=label, elapsed_ms=round(elapsed_ms, 1))
    return time.perf_counter()


# ---------------------------------------------------------------------------
# Top-level result cache
# ---------------------------------------------------------------------------


def _path_mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _get_pkg_version() -> str:
    from autoskillit import __version__

    return __version__


def _compute_registry_hash(experiment_types_dir: Path) -> str:
    """Compute md5 hash of sorted (path, mtime_ns) pairs for experiment-type YAMLs."""
    if not experiment_types_dir.exists():
        return ""
    entries: list[tuple[str, int]] = []
    for p in sorted(experiment_types_dir.glob("*.yaml")):
        try:
            entries.append((p.name, p.stat().st_mtime_ns))
        except OSError:
            continue
    return hashlib.md5(str(entries).encode(), usedforsecurity=False).hexdigest()


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
        {
            "name": r.name,
            "description": r.description,
            "summary": r.summary,
            "source": r.source.value,
        }
        for r in result.items
    ]
    response: dict[str, object] = {
        "recipes": items,
        "count": len(items),
    }
    if result.errors:
        response["errors"] = [{"file": e.path.name, "error": e.error} for e in result.errors]
    return response


def list_all(
    project_dir: Path | None = None,
    *,
    features: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """List all recipes from project and built-in sources.

    Returns:
        {"recipes": list[{"name", "description", "summary"}]}
        Includes "errors" key when recipes fail to parse.
    """
    from autoskillit.core import is_feature_enabled  # noqa: PLC0415
    from autoskillit.recipe.schema import RecipeKind  # noqa: PLC0415

    _pdir = project_dir if project_dir is not None else Path.cwd()
    _features = features or {}
    fleet_enabled = is_feature_enabled("fleet", _features)
    exclude_kinds = (
        frozenset() if fleet_enabled else frozenset({RecipeKind.CAMPAIGN, RecipeKind.FOOD_TRUCK})
    )
    result = list_recipes(_pdir, exclude_kinds=exclude_kinds)
    return format_recipe_list_response(result)


def validate_from_path(
    path: Path,
    temp_dir_relpath: str = ".autoskillit/temp",
    *,
    lister: SkillLister | None = None,
) -> dict[str, Any]:
    """Validate a recipe YAML file at the given path.

    Args:
        path: Path to the recipe YAML file.
        temp_dir_relpath: Relative path to the temp directory used for
            ``{{AUTOSKILLIT_TEMP}}`` substitution. Defaults to
            ``.autoskillit/temp``.

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
        raw_text = path.read_text(encoding="utf-8")
        substituted = substitute_temp_placeholder(raw_text, temp_dir_relpath)
        data = load_yaml(substituted)
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

    if lister is None:
        from autoskillit.workspace import DefaultSkillResolver  # noqa: PLC0415

        lister = DefaultSkillResolver()

    recipe = _parse_recipe(data)
    errors = validate_recipe(recipe)
    known_skills = frozenset(s.name for s in lister.list_all())
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


def _build_stop_step_semantics(recipe: Recipe) -> str:
    stop_steps = {name: step for name, step in recipe.steps.items() if step.action == "stop"}
    if not stop_steps:
        return ""
    names = ", ".join(f"'{n}'" for n in stop_steps)
    lines = [
        "ACTION: STOP STEP SEMANTICS:",
        f"- Steps {names} are terminal stop steps.",
        "- When routed to a stop step, display its message and TERMINATE immediately.",
        "- Do NOT call any MCP tools after a stop step.",
        "- Do NOT attempt recovery, error reporting, or off-recipe actions.",
        "- A stop step is an INTENTIONAL terminus — the recipe author designed this as"
        " the endpoint.",
    ]
    for name, step in stop_steps.items():
        if step.message:
            lines.append(f"  Stop step '{name}' message: {step.message!r}")
    return "\n".join(lines)


def _build_orchestration_rules(
    recipe: Recipe | None = None, stop_semantics: str | None = None
) -> str:
    parts = [
        "STEP EXECUTION IS NOT DISCRETIONARY:\n"
        "You MUST execute every step the pipeline routes you to. "
        "The ONLY mechanism for skipping a step is skip_when_false evaluating to false. "
        "When skip_when_false evaluates to true (or is absent), the step is MANDATORY. "
        "NEVER skip a step because the PR is small, the diff is trivial, or you judge "
        "the step unnecessary. NEVER replace recipe steps with manual tool calls. "
        "Consequence: skipping PR review steps results in unreviewed code, missing "
        "diff annotations, and no architectural lens analysis."
    ]
    if recipe is not None:
        sem = stop_semantics if stop_semantics is not None else _build_stop_step_semantics(recipe)
        if sem:
            parts.append(sem)
    parts.append(
        "ACTION: ROUTE STEP SEMANTICS:\n"
        '- When you reach a step with action: "route", evaluate the step\'s on_result\n'
        "  conditions against captured context variables. Route to the matching target.\n"
        "- Do NOT call any MCP tools for this step type — routing evaluation IS the step.\n"
        "- If no on_result condition matches and on_failure is defined, follow on_failure."
    )
    return "\n\n".join(parts)


def load_and_validate(
    name: str,
    project_dir: Path | None = None,
    *,
    suppressed: Sequence[str] | None = None,
    recipe_info: RecipeInfo | None = None,
    resolved_defaults: dict[str, str] | None = None,
    ingredient_overrides: dict[str, str] | None = None,
    temp_dir: Path | None = None,
    temp_dir_relpath: str | None = None,
    lister: SkillLister | None = None,
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
    from autoskillit.recipe.experiment_type_registry import (  # noqa: PLC0415
        BUNDLED_EXPERIMENT_TYPES_DIR,
    )
    from autoskillit.recipe.methodology_tradition_registry import (  # noqa: PLC0415
        BUNDLED_METHODOLOGY_TRADITIONS_DIR,
    )

    _exp_types_hash = _compute_registry_hash(BUNDLED_EXPERIMENT_TYPES_DIR)
    _user_exp_types_dir = _pdir / ".autoskillit" / "experiment-types"
    _user_exp_hash = _compute_registry_hash(_user_exp_types_dir)
    _method_traditions_hash = _compute_registry_hash(BUNDLED_METHODOLOGY_TRADITIONS_DIR)
    _user_method_traditions_dir = _pdir / ".autoskillit" / "methodology-traditions"
    _user_method_traditions_hash = _compute_registry_hash(_user_method_traditions_dir)
    cache_key = (
        name,
        str(_pdir),
        tuple(sorted(suppressed)) if suppressed else (),
        tuple(sorted(ingredient_overrides.items())) if ingredient_overrides else (),
        _exp_types_hash,
        _user_exp_hash,
        _method_traditions_hash,
        _user_method_traditions_hash,
    )

    with _LOAD_CACHE_LOCK:
        cached = _LOAD_CACHE.get(cache_key)

    if cached is not None and cached.pkg_version == pkg_version:
        pm = _path_mtime_ns(project_recipes_dir)
        bm = _path_mtime_ns(_builtin_dir)
        rm = _path_mtime_ns(cached.recipe_path)
        rs = _file_size(cached.recipe_path)
        if (
            pm == cached.project_dir_mtime
            and bm == cached.builtin_dir_mtime
            and rm == cached.recipe_mtime
            and rs == cached.recipe_size
        ):
            logger.debug("load_recipe_cache_hit", recipe=name)
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
    _temp_relpath = temp_dir_relpath or ".autoskillit/temp"
    raw = substitute_temp_placeholder(raw, _temp_relpath)
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

            from autoskillit.recipe.identity import compute_composite_hash  # noqa: PLC0415
            from autoskillit.recipe.staleness_cache import (  # noqa: PLC0415
                compute_recipe_hash,
            )

            recipe.content_hash = compute_recipe_hash(match.path)
            recipe.composite_hash = compute_composite_hash(
                match.path,
                recipe,
                skills_dir=pkg_root() / "skills",
                project_dir=_pdir,
            )

            # Stage: sub-recipe composition (lazy-loaded prefixes)
            active_recipe, combined_recipe = _build_active_recipe(
                recipe, ingredient_overrides, _pdir, _temp_relpath
            )

            # Stage: structural validation on active recipe
            errors = validate_recipe(active_recipe)
            if combined_recipe is not None:
                # Dual validation: also validate the combined (merged) graph
                combined_errors = validate_recipe(combined_recipe)
                errors.extend(f"[combined] {e}" for e in combined_errors)
            t0 = _t("validate_recipe", t0, name)

            # Stage: semantic rules (builds ValidationContext once — shared computation)
            if lister is None:
                from autoskillit.workspace import DefaultSkillResolver  # noqa: PLC0415

                lister = DefaultSkillResolver()

            known = frozenset(r.name for r in list_recipes(_pdir).items)
            known_skills = frozenset(s.name for s in lister.list_all())
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
                resolved_temp = temp_dir if temp_dir is not None else resolve_temp_dir(_pdir, None)
                staleness_cache_path = resolved_temp / "recipe_staleness_cache.json"
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
        logger.warning("Recipe YAML parse error", name=name, exc_info=True)
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
        logger.warning("Recipe structure invalid", name=name, exc_info=True)
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
        logger.warning("Recipe file not found or unreadable", name=name, exc_info=True)
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
    if _serving_recipe is not None and _serving_recipe.requires_packs:
        result["requires_packs"] = _serving_recipe.requires_packs
    if _serving_recipe is not None and _serving_recipe.requires_features:
        result["requires_features"] = _serving_recipe.requires_features
    if ing_table:
        result["ingredients_table"] = ing_table
    # Compute once; reused by both fields to avoid a second traversal of recipe.steps.
    # Two delivery paths are intentional: orchestration_rules embeds the text for Channel A
    # (open_kitchen response / system prompt); stop_step_semantics is a dedicated field for
    # Channel B consumers (load_recipe docstring injection) that need the text in isolation.
    _stop_semantics = _build_stop_step_semantics(recipe) if recipe else ""
    result["orchestration_rules"] = _build_orchestration_rules(
        recipe, stop_semantics=_stop_semantics
    )
    result["stop_step_semantics"] = _stop_semantics
    result["content_hash"] = recipe.content_hash if recipe else ""
    result["composite_hash"] = recipe.composite_hash if recipe else ""
    result["recipe_version"] = recipe.recipe_version if recipe else None

    # Write to cache (only when recipe was found and fully processed)
    if match is not None:
        entry = _LoadCacheEntry(
            recipe_path=match.path,
            recipe_mtime=_path_mtime_ns(match.path),
            recipe_size=_file_size(match.path),
            project_dir_mtime=_path_mtime_ns(project_recipes_dir),
            builtin_dir_mtime=_path_mtime_ns(_builtin_dir),
            pkg_version=pkg_version,
            result=result,
        )
        with _LOAD_CACHE_LOCK:
            _LOAD_CACHE[cache_key] = entry

    return result
