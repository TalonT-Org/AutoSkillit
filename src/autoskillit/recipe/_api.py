"""Recipe orchestration API: load/validate pipelines, format responses.

Re-export facade. Implementation: _api_cache.py, _api_listing.py.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from autoskillit.core import (
    ProcessStaleError,
    RecipeNotFoundError,
    RecipeSource,
    SkillLister,
    YAMLError,
    get_logger,
    pkg_root,
    resolve_temp_dir,
)
from autoskillit.recipe import _api_cache
from autoskillit.recipe._analysis import make_validation_context

# Re-export for backward compatibility
from autoskillit.recipe._api_cache import (  # noqa: F401
    _LOAD_CACHE,
    _STALENESS_CACHES_CLEARED,
    LoadCache,
    _check_process_staleness,
    _clear_stale_caches,
    _compute_registry_hash,
    _LoadCacheEntry,
    _path_mtime_ns,
    _refresh_staleness_baseline,
)
from autoskillit.recipe._api_listing import (  # noqa: F401
    format_recipe_list_response,
    list_all,
    validate_from_path,
)
from autoskillit.recipe._recipe_composition import (
    _assert_content_integrity,
    _build_active_recipe,
    _prune_skipped_steps,
    _resolve_hidden_inputs_in_content,
    _resolve_skip_guards_in_content,
    _validate_no_dangling_routes,
)
from autoskillit.recipe._recipe_ingredients import (
    DeferredGuard,
    ListRecipesResult,  # noqa: F401
    LoadRecipeResult,
    OpenKitchenResult,  # noqa: F401
    RecipeListItem,  # noqa: F401
    build_ingredient_rows,  # noqa: F401
    format_ingredients_table,
)
from autoskillit.recipe._rule_helpers import (
    _is_failure_sentinel_value,
    extract_sentinel_json_blocks,
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
    _assert_no_raw_placeholders,
    _load_recipe_dict,
    _parse_recipe,
    builtin_recipes_dir,
    builtin_sub_recipes_dir,
    find_recipe_by_name,
    list_recipes,
    substitute_scripts_placeholder,
    substitute_temp_placeholder,
)
from autoskillit.recipe.schema import Recipe
from autoskillit.recipe.validator import (
    compute_recipe_validity,
    filter_version_rule,
    findings_to_dicts,
    run_semantic_rules,
    validate_recipe_structure,
)

logger = get_logger(__name__)


def _t(label: str, t0: float, name: str) -> float:
    """Log elapsed time for a pipeline stage and return current time.

    Uses structlog at DEBUG level; structlog's processor chain handles level
    filtering without requiring an explicit isEnabledFor() guard.
    """
    elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.debug("load_recipe_stage", recipe=name, stage=label, elapsed_ms=round(elapsed_ms, 1))
    return time.perf_counter()


def _infer_stop_failure(name: str, message: str | None) -> bool:
    """Determine whether a stop step represents a failure outcome.

    Parses embedded sentinel JSON first; falls back to name-based heuristic.
    """
    if message:
        for block in extract_sentinel_json_blocks(message):
            try:
                parsed = json.loads(block)
                if isinstance(parsed, dict) and "success" in parsed:
                    return _is_failure_sentinel_value(parsed["success"])
            except (json.JSONDecodeError, ValueError):
                logger.debug("sentinel_json_parse_failed", step=name, raw=block)
                continue
    return "escalate" in name.lower() or "reject" in name.lower()


def _build_stop_step_semantics(recipe: Recipe) -> str:
    stop_steps = {name: step for name, step in recipe.steps.items() if step.action == "stop"}
    if not stop_steps:
        return ""
    lines = [
        "ACTION: STOP STEP SEMANTICS:",
        "- Stop steps are terminal — the pipeline ends when routed to them.",
        "- Do NOT call any MCP tools after a stop step.",
        "- Do NOT attempt recovery, error reporting, or off-recipe actions.",
        "- When routed to a stop step, emit the L3 sentinel block and TERMINATE.",
    ]
    for name, step in stop_steps.items():
        is_failure = _infer_stop_failure(name, step.message)
        success_val = "false" if is_failure else "true"
        lines.append(
            f"- For stop step '{name}': emit the L3 sentinel block with "
            f"success={success_val} and reason=<step message>. Then TERMINATE."
        )
        if step.message:
            lines.append(f"  Stop step '{name}' message: {step.message!r}")
    return "\n".join(lines)


def _build_orchestration_rules(
    recipe: Recipe | None = None, stop_semantics: str | None = None
) -> str:
    parts = [
        "STEP EXECUTION IS NOT DISCRETIONARY:\n"
        "You MUST execute every step the pipeline routes you to. "
        "skip_when_false ingredient references are resolved server-side before the recipe "
        'is served. You may see literal "false" values (skip the step) '
        "or no skip_when_false field at all (step is mandatory). "
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
    recipe_list: list[RecipeInfo] | None = None,
    resolved_defaults: dict[str, str] | None = None,
    ingredient_overrides: dict[str, str] | None = None,
    temp_dir: Path | None = None,
    temp_dir_relpath: str | None = None,
    lister: SkillLister | None = None,
    defer_unresolved: bool = False,
    backend_name: str | None = None,
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
        ``LoadRecipeResult`` dict on success (always has ``valid``, ``suggestions``).

    Raises:
        ProcessStaleError: Package directory was modified since server startup.
        RecipeNotFoundError: Named recipe could not be found.
    """
    if _api_cache._check_process_staleness():
        if not _api_cache._STALENESS_CACHES_CLEARED:
            _api_cache._clear_stale_caches()
        raise ProcessStaleError(
            "Process is running stale code — package directory was modified on disk "
            "since server startup. Restart the MCP server via reload_session."
        )

    _pdir = project_dir if project_dir is not None else Path.cwd()
    pkg_version = _api_cache._get_pkg_version()
    project_recipes_dir = _pdir / ".autoskillit" / "recipes"
    _builtin_dir = builtin_recipes_dir()
    from autoskillit.recipe.experiment_type_registry import (  # noqa: PLC0415
        BUNDLED_EXPERIMENT_TYPES_DIR,
    )
    from autoskillit.recipe.methodology_tradition_registry import (  # noqa: PLC0415
        BUNDLED_METHODOLOGY_TRADITIONS_DIR,
    )

    _exp_types_hash = _api_cache._compute_registry_hash(BUNDLED_EXPERIMENT_TYPES_DIR)
    _user_exp_types_dir = _pdir / ".autoskillit" / "experiment-types"
    _user_exp_hash = _api_cache._compute_registry_hash(_user_exp_types_dir)
    _method_traditions_hash = _api_cache._compute_registry_hash(BUNDLED_METHODOLOGY_TRADITIONS_DIR)
    _user_method_traditions_dir = _pdir / ".autoskillit" / "methodology-traditions"
    _user_method_traditions_hash = _api_cache._compute_registry_hash(_user_method_traditions_dir)
    _temp_relpath = temp_dir_relpath or ".autoskillit/temp"
    cache_key = (
        name,
        _temp_relpath,
        str(_pdir),
        tuple(sorted(suppressed)) if suppressed else (),
        tuple(sorted(ingredient_overrides.items())) if ingredient_overrides else (),
        defer_unresolved,
        _exp_types_hash,
        _user_exp_hash,
        _method_traditions_hash,
        _user_method_traditions_hash,
        backend_name,
    )

    cached = _api_cache._LOAD_CACHE.get(cache_key)

    from autoskillit.recipe import registry as _registry  # noqa: PLC0415

    # lazy-registry: global set by _finalize_registry()
    _rule_hash: str = _registry.RULE_REGISTRY_HASH  # pyright: ignore[reportAttributeAccessIssue]
    if not _rule_hash:
        logger.warning("RULE_REGISTRY_HASH is empty — _finalize_registry() was never called")
    if (
        cached is not None
        and cached.pkg_version == pkg_version
        and cached.rule_registry_hash == _rule_hash
    ):
        pm = _api_cache._path_mtime_ns(project_recipes_dir)
        bm = _api_cache._path_mtime_ns(_builtin_dir)
        rm = _api_cache._path_mtime_ns(cached.recipe_path)
        rs = _api_cache._file_size(cached.recipe_path)
        if (
            pm == cached.project_dir_mtime
            and bm == cached.builtin_dir_mtime
            and rm == cached.recipe_mtime
            and rs == cached.recipe_size
        ):
            logger.debug("load_recipe_cache_hit", recipe=name)
            return cast(LoadRecipeResult, _api_cache._LOAD_CACHE.copy_result(cached.result))

    t0 = time.perf_counter()

    # Stage: find recipe
    if recipe_info is not None:
        match: RecipeInfo | None = recipe_info
        _recipe_list = recipe_list
    else:
        match = find_recipe_by_name(name, _pdir)
        _recipe_list = None
    t0 = _t("find_recipe", t0, name)

    if match is None:
        raise RecipeNotFoundError(f"No recipe named '{name}' found")

    raw = match.content if match.content is not None else match.path.read_text()
    raw = substitute_temp_placeholder(raw, _temp_relpath)
    raw = substitute_scripts_placeholder(raw)
    suggestions: list[dict[str, Any]] = []
    valid = True
    recipe = None
    active_recipe = None
    _skip_resolutions: dict[str, bool | None] = {}
    _pre_prune_steps: dict[str, Any] = {}

    # Determine recipes_dir from source
    if match.source == RecipeSource.BUILTIN:
        recipes_dir = pkg_root() / "recipes"
    else:
        recipes_dir = _pdir / ".autoskillit" / "recipes"

    try:
        # Stage: yaml parse
        data = _load_recipe_dict(match.path, raw_text=raw, temp_dir_relpath=_temp_relpath)
        t0 = _t("yaml_parse", t0, name)

        if isinstance(data, dict) and "steps" in data:
            recipe = _parse_recipe(data)

            from autoskillit.recipe.identity import compute_composite_hash  # noqa: PLC0415

            _recipe_bytes = match.path.read_bytes()
            recipe.content_hash = (
                match.content_hash
                if match.content_hash
                else "sha256:" + hashlib.sha256(_recipe_bytes).hexdigest()
            )
            recipe.composite_hash = compute_composite_hash(
                match.path,
                recipe,
                skills_dir=pkg_root() / "skills",
                project_dir=_pdir,
                content_bytes=_recipe_bytes,
            )

            # Stage: sub-recipe composition (lazy-loaded prefixes)
            active_recipe, combined_recipe = _build_active_recipe(
                recipe, ingredient_overrides, _pdir, _temp_relpath
            )

            # Stage: structural validation on active recipe
            errors = validate_recipe_structure(active_recipe)
            if combined_recipe is not None:
                # Dual validation: also validate the combined (merged) graph
                combined_errors = validate_recipe_structure(combined_recipe)
                errors.extend(f"[combined] {e}" for e in combined_errors)
            t0 = _t("validate_recipe_structure", t0, name)

            # Stage: semantic rules (builds ValidationContext once — shared computation)
            if lister is None:
                from autoskillit.workspace import DefaultSkillResolver  # noqa: PLC0415

                lister = DefaultSkillResolver()

            from autoskillit.core import SkillResolver as _SkillResolver  # noqa: PLC0415

            _skill_resolver = lister if isinstance(lister, _SkillResolver) else None

            known = frozenset(
                r.name
                for r in (_recipe_list if _recipe_list is not None else list_recipes(_pdir).items)
            )
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
                skill_resolver=_skill_resolver,
                backend_name=backend_name,
            )
            semantic_findings = run_semantic_rules(val_ctx)
            semantic_suggestions = findings_to_dicts(semantic_findings)
            t0 = _t("semantic_rules", t0, name)

            _suppressed = suppressed or []
            if name in _suppressed:
                semantic_suggestions = filter_version_rule(semantic_suggestions)
            suggestions.extend(semantic_suggestions)

            # Stage: skip_when_false pruning (Python-side evaluation)
            _pre_prune_steps = dict(active_recipe.steps)
            active_recipe, _skip_resolutions = _prune_skipped_steps(
                active_recipe, ingredient_overrides, defer_unresolved
            )
            if _skip_resolutions:
                raw = _resolve_skip_guards_in_content(raw, _skip_resolutions, _pre_prune_steps)
                _assert_content_integrity(raw, _skip_resolutions, _pre_prune_steps)
            # Post-prune: validate that no surviving step routes to a removed step.
            # Must run inside try so active_recipe and errors are both in scope.
            _dangling_errors = _validate_no_dangling_routes(active_recipe)
            if _dangling_errors:
                errors.extend(f"[post-prune] dangling route: {e}" for e in _dangling_errors)
                raw = ""
            t0 = _t("prune_skipped_steps", t0, name)

            # Stage: hidden ingredient interpolation
            raw = _resolve_hidden_inputs_in_content(raw, active_recipe, ingredient_overrides)
            t0 = _t("resolve_hidden_inputs", t0, name)

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

    _hidden_names = (
        frozenset(
            n
            for n, ing in (active_recipe.ingredients or {}).items()
            if getattr(ing, "hidden", False)
        )
        if active_recipe is not None
        else None
    )
    _assert_no_raw_placeholders(raw, context=name, hidden_ingredient_names=_hidden_names)
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

    _deferred_guard_list: list[DeferredGuard] = []
    for _dg_step, _dg_resolved in _skip_resolutions.items() if _skip_resolutions else []:
        if _dg_resolved is None:
            _dg_step_obj = _pre_prune_steps.get(_dg_step)
            _dg_ref = getattr(_dg_step_obj, "skip_when_false", None) if _dg_step_obj else None
            _dg_ingredient = (
                _dg_ref[len("inputs.") :] if _dg_ref and _dg_ref.startswith("inputs.") else _dg_ref
            )
            _dg_ing_obj = (
                (active_recipe.ingredients or {}).get(_dg_ingredient)
                if active_recipe and _dg_ingredient
                else None
            )
            _dg_default = (
                str(_dg_ing_obj.default)
                if _dg_ing_obj is not None and getattr(_dg_ing_obj, "default", None) is not None
                else None
            )
            if _dg_ingredient is not None:
                _deferred_guard_list.append(
                    {"step": _dg_step, "ingredient": _dg_ingredient, "default": _dg_default}
                )
    if _deferred_guard_list:
        result["deferred_guards"] = _deferred_guard_list

    # Write to cache (only when recipe was found and fully processed)
    if match is not None:
        entry = _api_cache._LoadCacheEntry(
            recipe_path=match.path,
            recipe_mtime=_api_cache._path_mtime_ns(match.path),
            recipe_size=_api_cache._file_size(match.path),
            project_dir_mtime=_api_cache._path_mtime_ns(project_recipes_dir),
            builtin_dir_mtime=_api_cache._path_mtime_ns(_builtin_dir),
            pkg_version=pkg_version,
            rule_registry_hash=_rule_hash,
            result=result,
        )
        _api_cache._LOAD_CACHE.put(cache_key, entry)

    if result.get("valid", False):
        _api_cache._refresh_staleness_baseline()
    return cast(LoadRecipeResult, _api_cache._LOAD_CACHE.copy_result(result))
