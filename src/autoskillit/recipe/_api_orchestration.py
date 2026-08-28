"""Recipe load-and-validate orchestration. See issue #4860."""

import dataclasses
import hashlib
import json
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from autoskillit.core import (
    BackendCapabilities,
    FinalizedRecipeProjection,
    FinalizedRecipeStep,
    ProcessStaleError,
    RecipeFlowEdge,
    RecipeNotFoundError,
    RecipeSource,
    SkillLister,
    YAMLError,
    build_parameter_forwarding_rules,
    get_logger,
    pkg_root,
    resolve_temp_dir,
)
from autoskillit.recipe import _api_cache
from autoskillit.recipe._analysis import make_validation_context
from autoskillit.recipe._api_cache import _LoadCacheEntry
from autoskillit.recipe._binding import bind_recipe
from autoskillit.recipe._io_loading import (
    assert_no_raw_placeholders,
    load_recipe_dict_with_declarations,
)
from autoskillit.recipe._recipe_composition import (
    _analysis_edges_from_effective_routes,
    _assert_content_integrity,
    _build_active_recipe,
    _derive_rate_limit_routes,
    _effective_routing_edges,
    _prune_skipped_steps,
    _resolve_hidden_inputs_in_content,
    _sweep_unreachable_steps,
    _validate_effective_routing_edges,
    _validate_no_dangling_routes,
    _validate_route_consistency,
)
from autoskillit.recipe._recipe_ingredients import (
    DeferredGuard,
    LoadRecipeResult,
    format_ingredients_table,
)
from autoskillit.recipe._recipe_raw_repair import _resolve_skip_guards_in_content
from autoskillit.recipe._rule_helpers import (
    _is_failure_sentinel_value,
    extract_sentinel_json_blocks,
    filter_pruning_false_positives,
)
from autoskillit.recipe.contracts import (
    check_contract_staleness,
    load_recipe_card,
    stale_to_suggestions,
    validate_recipe_cards,
)
from autoskillit.recipe.diagrams import (
    annotate_diagram_with_pruning,
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
    substitute_scripts_placeholder,
    substitute_temp_placeholder,
)
from autoskillit.recipe.schema import Recipe, RecipeStep
from autoskillit.recipe.validator import (
    _finalize_delivery_segments,
    compute_recipe_validity,
    filter_version_rule,
    findings_to_dicts,
    run_semantic_rules,
    validate_recipe_structure,
)

logger = get_logger(__name__)


def _canonical_string_map(mapping: dict[str, str] | None) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(mapping.items())) if mapping else ()


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
        "or no skip_when_false field at all (step is mandatory). Resolved content "
        "contains neither skip_when_false nor its configuration-only on_skip continuation. "
        "NEVER skip a step because the PR is small, the diff is trivial, or you judge "
        "the step unnecessary. NEVER replace recipe steps with manual tool calls. "
        "Consequence: skipping PR review steps results in unreviewed code, missing "
        "diff annotations, and no architectural lens analysis."
    ]
    forwarding_rules = build_parameter_forwarding_rules()
    if forwarding_rules:
        parts.append(forwarding_rules)
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


def _finalize_recipe_steps(
    recipe: Recipe,
    deferred_guard_state: dict[str, tuple[str, str | None]],
) -> tuple[FinalizedRecipeStep, ...]:
    """Freeze the execution-relevant fields of the active recipe steps."""
    return tuple(
        FinalizedRecipeStep(
            name=name,
            tool=step.tool,
            skill_name=step.skill_name,
            provider=step.provider,
            model=step.model,
            with_args=dict(step.with_args),
            stale_threshold=step.stale_threshold,
            idle_output_timeout=step.idle_output_timeout,
            action=step.action,
            skip_when_false=(
                deferred_guard_state[name][0]
                if name in deferred_guard_state
                else step.skip_when_false
            ),
        )
        for name, step in recipe.steps.items()
    )


@dataclasses.dataclass(frozen=True, slots=True)
class _LoadPipelineInputs:
    name: str
    pdir: Path
    cache_key: tuple[Any, ...]
    cacheable: bool
    pkg_version: str
    rule_registry_hash: str
    project_recipes_dir: Path
    builtin_dir: Path
    effective_temp_dir: Path
    temp_dir_relpath: str
    normalized_recipe_info: RecipeInfo | None
    recipe_list: list[RecipeInfo] | None
    suppressed: Sequence[str] | None
    resolved_defaults: dict[str, str] | None
    ingredient_overrides: dict[str, str] | None
    lister: SkillLister | None
    defer_unresolved: bool
    backend_name: str | None
    effective_backend_map: dict[str, str] | None
    backend_capabilities_map: dict[str, BackendCapabilities] | None
    backend_origin_map: dict[str, str] | None
    include_finalized_projection: bool


@dataclasses.dataclass(frozen=True, slots=True)
class _ValidationResult:
    match: RecipeInfo
    recipes_dir: Path  # distinct from project_recipes_dir
    recipe: Recipe | None
    active_recipe: Recipe | None
    raw_declared: str  # pre-substitution recipe text (cached to skip re-read)
    raw: str
    errors: list[str]
    suggestions: list[dict[str, Any]]
    skip_resolutions: dict[str, bool | None]
    pre_prune_steps: dict[str, RecipeStep]
    deferred_guard_state: dict[str, tuple[str, str | None]]
    unreachable_step_names: tuple[str, ...]
    effective_flow_edges: tuple[RecipeFlowEdge, ...]
    finalized_projection: FinalizedRecipeProjection | None
    valid: bool


def _resolve_cache_inputs(
    name: str,
    project_dir: Path | None,
    *,
    suppressed: Sequence[str] | None,
    recipe_info: RecipeInfo | None,
    recipe_list: list[RecipeInfo] | None,
    resolved_defaults: dict[str, str] | None,
    ingredient_overrides: dict[str, str] | None,
    temp_dir: Path | None,
    temp_dir_relpath: str | None,
    lister: SkillLister | None,
    defer_unresolved: bool,
    backend_name: str | None,
    effective_backend_map: dict[str, str] | None,
    backend_capabilities_map: dict[str, BackendCapabilities] | None,
    backend_origin_map: dict[str, str] | None,
    include_finalized_projection: bool,
) -> _LoadPipelineInputs:
    """Process staleness check + cache-key construction + rule_hash bundling."""
    if _api_cache._check_process_staleness():
        if not _api_cache._STALENESS_CACHES_CLEARED:
            _api_cache._clear_stale_caches()
        raise ProcessStaleError(
            "Process is running stale code — package directory was modified on disk "
            "since server startup."
        )

    _pdir = (project_dir if project_dir is not None else Path.cwd()).absolute()
    pkg_version = _api_cache._get_pkg_version()
    project_recipes_dir = _pdir / ".autoskillit" / "recipes"
    builtin_dir = builtin_recipes_dir()
    from autoskillit.recipe.experiment_type_registry import (  # noqa: PLC0415
        BUNDLED_EXPERIMENT_TYPES_DIR,
    )
    from autoskillit.recipe.methodology_tradition_registry import (  # noqa: PLC0415
        BUNDLED_METHODOLOGY_TRADITIONS_DIR,
    )

    _exp_types_hash = _api_cache._compute_registry_hash(BUNDLED_EXPERIMENT_TYPES_DIR)
    _user_exp_hash = _api_cache._compute_registry_hash(_pdir / ".autoskillit" / "experiment-types")
    _method_traditions_hash = _api_cache._compute_registry_hash(BUNDLED_METHODOLOGY_TRADITIONS_DIR)
    _user_method_traditions_hash = _api_cache._compute_registry_hash(
        _pdir / ".autoskillit" / "methodology-traditions"
    )
    _temp_relpath = temp_dir_relpath or ".autoskillit/temp"
    _default_temp_dir = resolve_temp_dir(_pdir, None).absolute()
    _effective_temp_dir = temp_dir.absolute() if temp_dir is not None else _default_temp_dir
    _temp_dir_key = None if _effective_temp_dir == _default_temp_dir else str(_effective_temp_dir)
    _normalized_recipe_info = (
        dataclasses.replace(recipe_info, path=recipe_info.path.absolute())
        if recipe_info is not None
        else None
    )
    _recipe_info_key = (
        (
            str(_normalized_recipe_info.path),
            _normalized_recipe_info.source.value,
            _normalized_recipe_info.content_hash,
            (
                hashlib.sha256(_normalized_recipe_info.content.encode()).hexdigest()
                if _normalized_recipe_info.content is not None
                else None
            ),
        )
        if _normalized_recipe_info is not None
        else None
    )
    _recipe_list_key = (
        tuple(sorted({info.name for info in recipe_list})) if recipe_list is not None else None
    )
    cacheable = lister is None
    _ml_sub_area_path = BUNDLED_METHODOLOGY_TRADITIONS_DIR / "_ml_sub_area_folding.yaml"
    _manifest_mtime = _api_cache._path_mtime_ns(pkg_root() / "recipe" / "skill_contracts.yaml")
    _manifest_size = _api_cache._file_size(pkg_root() / "recipe" / "skill_contracts.yaml")
    _budgets_mtime = _api_cache._path_mtime_ns(pkg_root() / "recipe" / "block_budgets.yaml")
    _budgets_size = _api_cache._file_size(pkg_root() / "recipe" / "block_budgets.yaml")
    _ml_sub_area_mtime = _api_cache._path_mtime_ns(_ml_sub_area_path)
    _ml_sub_area_size = _api_cache._file_size(_ml_sub_area_path)
    cache_key = (
        name,
        _temp_relpath,
        _temp_dir_key,
        str(_pdir),
        tuple(sorted(suppressed)) if suppressed else (),
        _recipe_info_key,
        _recipe_list_key,
        _canonical_string_map(resolved_defaults),
        _canonical_string_map(ingredient_overrides),
        defer_unresolved,
        _exp_types_hash,
        _user_exp_hash,
        _method_traditions_hash,
        _user_method_traditions_hash,
        backend_name,
        _canonical_string_map(effective_backend_map),
        tuple(sorted(backend_capabilities_map.items())) if backend_capabilities_map else (),
        _canonical_string_map(backend_origin_map),
        include_finalized_projection,
        _manifest_mtime,
        _manifest_size,
        _budgets_mtime,
        _budgets_size,
        _ml_sub_area_mtime,
        _ml_sub_area_size,
    )

    from autoskillit.recipe import registry as _registry  # noqa: PLC0415

    # lazy-registry: global set by _finalize_registry()
    _rule_hash: str = _registry.RULE_REGISTRY_HASH  # pyright: ignore[reportAttributeAccessIssue]
    if not _rule_hash:
        logger.warning("RULE_REGISTRY_HASH is empty — _finalize_registry() was never called")

    return _LoadPipelineInputs(
        name=name,
        pdir=_pdir,
        cache_key=cache_key,
        cacheable=cacheable,
        pkg_version=pkg_version,
        rule_registry_hash=_rule_hash,
        project_recipes_dir=project_recipes_dir,
        builtin_dir=builtin_dir,
        effective_temp_dir=_effective_temp_dir,
        temp_dir_relpath=_temp_relpath,
        normalized_recipe_info=_normalized_recipe_info,
        recipe_list=recipe_list,
        suppressed=suppressed,
        resolved_defaults=resolved_defaults,
        ingredient_overrides=ingredient_overrides,
        lister=lister,
        defer_unresolved=defer_unresolved,
        backend_name=backend_name,
        effective_backend_map=effective_backend_map,
        backend_capabilities_map=backend_capabilities_map,
        backend_origin_map=backend_origin_map,
        include_finalized_projection=include_finalized_projection,
    )


def _resolve_recipe_match(
    name: str, pipeline_inputs: _LoadPipelineInputs, t0: float
) -> tuple[_ValidationResult, float]:
    """Find the recipe, derive ``recipes_dir``, init state. Thread ``t0`` for the chain."""
    if pipeline_inputs.normalized_recipe_info is not None:
        match: RecipeInfo | None = pipeline_inputs.normalized_recipe_info
    else:
        match = find_recipe_by_name(name, pipeline_inputs.pdir)
    t0 = _t("find_recipe", t0, name)

    if match is None:
        raise RecipeNotFoundError(f"No recipe named '{name}' found")

    raw_declared = match.content if match.content is not None else match.path.read_text()
    raw = substitute_temp_placeholder(raw_declared, pipeline_inputs.temp_dir_relpath)
    raw = substitute_scripts_placeholder(raw)

    if match.source == RecipeSource.BUILTIN:
        recipes_dir = pkg_root() / "recipes"
    else:
        recipes_dir = pipeline_inputs.pdir / ".autoskillit" / "recipes"

    return (
        _ValidationResult(
            match=match,
            recipes_dir=recipes_dir,
            recipe=None,
            active_recipe=None,
            raw_declared=raw_declared,
            raw=raw,
            errors=[],
            suggestions=[],
            skip_resolutions={},
            pre_prune_steps={},
            deferred_guard_state={},
            unreachable_step_names=(),
            effective_flow_edges=(),
            finalized_projection=None,
            valid=False,
        ),
        t0,
    )


def _parse_and_compose(
    match: RecipeInfo,
    raw_declared: str,
    temp_dir_relpath: str,
    pdir: Path,
    ingredient_overrides: dict[str, str] | None,
) -> tuple[Recipe | None, Recipe | None, Recipe | None, list[str]]:
    """YAML parse, content hashing, structural validation, sub-recipe composition.

    Returns ``(recipe, source_recipe, active_recipe, errors)``; ``recipe`` is
    ``None`` when ``data`` is not a dict. ``source_recipe`` is a frozen copy of
    ``recipe.steps`` retained for the post-prune route-consistency check.
    """
    data, _declared = load_recipe_dict_with_declarations(
        match.path, raw_text=raw_declared, temp_dir_relpath=temp_dir_relpath
    )
    if not isinstance(data, dict):
        return None, None, None, []

    recipe = _parse_recipe(data, declared_data=_declared)
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
        project_dir=pdir,
        content_bytes=_recipe_bytes,
    )
    source_recipe = dataclasses.replace(
        recipe,
        steps={n: dataclasses.replace(step) for n, step in recipe.steps.items()},
    )
    errors = validate_recipe_structure(source_recipe)
    active_recipe, combined_recipe = _build_active_recipe(
        source_recipe, ingredient_overrides, pdir, temp_dir_relpath
    )
    if active_recipe is None:
        # Contract violation: _build_active_recipe returned None for a dict
        # payload. Append a structured error so the caller sees it instead of
        # an uncaught AssertionError.
        errors.append("_build_active_recipe returned None")
    if combined_recipe is not None:
        combined_errors = validate_recipe_structure(combined_recipe)
        errors.extend(f"[combined] {e}" for e in combined_errors)
    elif active_recipe is not None and any(
        step.sub_recipe is not None for step in source_recipe.steps.values()
    ):
        active_errors = validate_recipe_structure(active_recipe)
        errors.extend(f"[active] {error}" for error in active_errors if error not in errors)
    return recipe, source_recipe, active_recipe, errors


def _run_validation_pipeline(
    partial: _ValidationResult, pipeline_inputs: _LoadPipelineInputs, t0: float
) -> _ValidationResult:
    """YAML parse -> structural -> pruning -> semantic -> hidden-inputs ->
    contract -> staleness -> diagram. YAMLError/ValueError/OSError caught here so
    ``valid=False`` is in scope.
    """
    match = partial.match
    recipes_dir = partial.recipes_dir
    raw = partial.raw
    suggestions: list[dict[str, Any]] = []
    valid = False
    errors: list[str] = []
    recipe: Recipe | None = None
    source_recipe: Recipe | None = None
    active_recipe: Recipe | None = None
    _skip_resolutions: dict[str, bool | None] = {}
    _deferred_guard_state: dict[str, tuple[str, str | None]] = {}
    _unreachable_step_names: tuple[str, ...] = ()
    _effective_flow_edges: tuple[RecipeFlowEdge, ...] = ()
    _pre_prune_steps: dict[str, RecipeStep] = {}
    _finalized_projection: FinalizedRecipeProjection | None = None

    name = pipeline_inputs.name
    _pdir = pipeline_inputs.pdir
    lister = pipeline_inputs.lister
    backend_name = pipeline_inputs.backend_name
    effective_backend_map = pipeline_inputs.effective_backend_map
    backend_capabilities_map = pipeline_inputs.backend_capabilities_map
    backend_origin_map = pipeline_inputs.backend_origin_map
    ingredient_overrides = pipeline_inputs.ingredient_overrides
    defer_unresolved = pipeline_inputs.defer_unresolved
    suppressed = pipeline_inputs.suppressed
    include_finalized_projection = pipeline_inputs.include_finalized_projection
    _effective_temp_dir = pipeline_inputs.effective_temp_dir
    _temp_relpath = pipeline_inputs.temp_dir_relpath
    raw_declared = partial.raw_declared
    _recipe_list = (
        pipeline_inputs.recipe_list if pipeline_inputs.normalized_recipe_info is not None else None
    )

    try:
        # Stages: yaml parse + structural validation + sub-recipe composition.
        # _parse_and_compose performs the YAML parse and isinstance gate; the
        # outer pipeline calls it directly to avoid a duplicate parse.
        recipe, source_recipe, active_recipe, errors = _parse_and_compose(
            match, raw_declared, _temp_relpath, _pdir, ingredient_overrides
        )
        t0 = _t("yaml_parse", t0, name)
        if active_recipe is None:
            # _parse_and_compose returned no active recipe (e.g. non-dict
            # payload or compose failure). Raise so the except clause records
            # a structured suggestion instead of letting AssertionError
            # propagate uncaught.
            raise ValueError("Recipe did not produce an active recipe")
        t0 = _t("validate_recipe_structure", t0, name)

        if lister is None:
            from autoskillit.workspace import DefaultSkillResolver  # noqa: PLC0415

            lister = DefaultSkillResolver()
        from autoskillit.core import SkillResolver as _SkillResolver  # noqa: PLC0415

        _skill_resolver = lister if isinstance(lister, _SkillResolver) else None

        # Stage: skip_when_false pruning. MUST run before semantic rules so
        # pruned steps are never seen by rules like backend-incompatible-skill.
        _pre_prune_bindings = bind_recipe(active_recipe, ingredient_values=ingredient_overrides)
        _pre_prune_val_ctx = make_validation_context(
            active_recipe,
            project_dir=_pdir,
            backend_name=backend_name,
            skill_resolver=_skill_resolver,
            effective_backend_map=effective_backend_map,
            backend_capabilities_map=backend_capabilities_map,
            backend_origin_map=backend_origin_map,
            binding_projection=_pre_prune_bindings,
        )
        _pre_prune_findings = run_semantic_rules(_pre_prune_val_ctx)
        _pre_prune_steps = dict(active_recipe.steps)
        active_recipe, _skip_resolutions, _deferred_guard_state = _prune_skipped_steps(
            active_recipe, ingredient_overrides, defer_unresolved
        )
        if active_recipe is None:
            # _prune_skipped_steps returned None — contract violation. Raise
            # so the except clause records a structured suggestion.
            raise ValueError("_prune_skipped_steps returned None")
        _source_pre_prune_steps = dict(source_recipe.steps) if source_recipe else {}
        _source_recipe, _source_skip_resolutions, _source_deferred_guard_state = (
            _prune_skipped_steps(source_recipe, ingredient_overrides, defer_unresolved)
            if source_recipe
            else (None, {}, {})
        )
        if _source_skip_resolutions and _source_recipe is not None:
            raw = _resolve_skip_guards_in_content(
                raw, _source_skip_resolutions, _source_pre_prune_steps
            )
            _assert_content_integrity(raw, _source_skip_resolutions, _source_pre_prune_steps)
        if _source_recipe is not None:
            _route_consistency_errors = _validate_route_consistency(raw, _source_recipe)
            if _route_consistency_errors:
                errors.extend(
                    f"[post-prune] route consistency: {e}" for e in _route_consistency_errors
                )
                raw = ""
        active_recipe = _derive_rate_limit_routes(active_recipe)
        _dangling_errors = _validate_no_dangling_routes(active_recipe)
        if _dangling_errors:
            errors.extend(f"[post-prune] dangling route: {e}" for e in _dangling_errors)
            raw = ""
        _effective_flow_edges = _effective_routing_edges(active_recipe, _deferred_guard_state)
        _effective_route_errors = _validate_effective_routing_edges(
            active_recipe, _effective_flow_edges
        )
        if _effective_route_errors:
            errors.extend(
                f"[post-prune] effective route: {error}" for error in _effective_route_errors
            )
            raw = ""
        active_recipe, _unreachable_step_names = _sweep_unreachable_steps(
            active_recipe, _effective_flow_edges
        )
        _deferred_guard_state = {
            step_name: state
            for step_name, state in _deferred_guard_state.items()
            if step_name in active_recipe.steps
        }
        _effective_flow_edges = _effective_routing_edges(active_recipe, _deferred_guard_state)
        _effective_analysis_edges = _analysis_edges_from_effective_routes(
            active_recipe, _effective_flow_edges
        )
        t0 = _t("prune_skipped_steps", t0, name)

        # Stage: semantic rules
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
        _post_prune_bindings = bind_recipe(active_recipe, ingredient_values=ingredient_overrides)
        if include_finalized_projection and not _effective_route_errors:
            _ordered_step_names = tuple(active_recipe.steps)
            _delivery_segments, _delivery_segment_errors = _finalize_delivery_segments(
                active_recipe, _effective_flow_edges
            )
            if _delivery_segment_errors:
                errors.extend(
                    f"[post-prune] delivery segments: {error}"
                    for error in _delivery_segment_errors
                )
                raw = ""
            _finalized_projection = FinalizedRecipeProjection(
                binding_projection=_post_prune_bindings,
                ordered_step_names=_ordered_step_names,
                entrypoint=next(iter(_ordered_step_names), ""),
                ordered_flow_edges=_effective_flow_edges,
                ordered_steps=_finalize_recipe_steps(active_recipe, _deferred_guard_state),
                ingredient_names=frozenset(active_recipe.ingredients),
                delivery_segments=_delivery_segments,
            )
        val_ctx = make_validation_context(
            active_recipe,
            available_recipes=known,
            available_skills=known_skills,
            available_sub_recipes=known_sub_recipes,
            project_dir=_pdir,
            skill_resolver=_skill_resolver,
            backend_name=backend_name,
            effective_backend_map=effective_backend_map,
            backend_capabilities_map=backend_capabilities_map,
            backend_origin_map=backend_origin_map,
            binding_projection=_post_prune_bindings,
            effective_routing_edges=_effective_analysis_edges,
        )
        semantic_findings = run_semantic_rules(val_ctx)
        semantic_suggestions = findings_to_dicts(semantic_findings)
        t0 = _t("semantic_rules", t0, name)

        if _skip_resolutions and any(v is not True for v in _skip_resolutions.values()):
            semantic_findings = filter_pruning_false_positives(
                semantic_findings, _pre_prune_findings
            )
            semantic_suggestions = findings_to_dicts(semantic_findings)
            _graph_aware_rules = {"capture-inversion-detection", "dead-output"}
            _pre_prune_keys = {(f.rule, f.step_name) for f in _pre_prune_findings}
            for _f in semantic_findings:
                if (
                    _f.rule in _graph_aware_rules
                    and (_f.rule, _f.step_name) not in _pre_prune_keys
                ):
                    logger.debug(
                        "pruning_filter_new_finding",
                        recipe=name,
                        rule=_f.rule,
                        step=_f.step_name,
                        message=_f.message,
                    )

        _suppressed = suppressed or []
        if name in _suppressed:
            semantic_suggestions = filter_version_rule(semantic_suggestions)
        suggestions.extend(semantic_suggestions)

        # Stage: hidden ingredient interpolation
        raw = _resolve_hidden_inputs_in_content(raw, active_recipe, ingredient_overrides)
        t0 = _t("resolve_hidden_inputs", t0, name)

        # Stages: contract card + staleness + diagram
        contract = load_recipe_card(name, recipes_dir)
        contract_findings: list[dict[str, Any]] = []
        if contract:
            contract_findings = validate_recipe_cards(active_recipe, contract)
            suggestions.extend(contract_findings)
        t0 = _t("contract_card", t0, name)
        if contract:
            staleness_cache_path = _effective_temp_dir / "recipe_staleness_cache.json"
            stale = check_contract_staleness(
                contract,
                recipe_path=match.path,
                cache_path=staleness_cache_path,
                resolver=_skill_resolver,
                project_root=_pdir,
            )
            suggestions.extend(stale_to_suggestions(stale))
        t0 = _t("staleness_check", t0, name)
        if check_diagram_staleness(name, recipes_dir, match.path):
            suggestions.extend(diagram_stale_to_suggestions(name))
        t0 = _t("diagram", t0, name)
        valid = compute_recipe_validity(errors, semantic_findings, contract_findings)

    except YAMLError as exc:
        logger.warning("Recipe YAML parse error", name=name, exc_info=True)
        _record_pipeline_error(suggestions, "YAML parse error", exc)
        valid = False
    except ValueError as exc:
        logger.warning("Recipe structure invalid", name=name, exc_info=True)
        _record_pipeline_error(suggestions, "Invalid recipe structure", exc)
        valid = False
    except (FileNotFoundError, OSError) as exc:
        logger.warning("Recipe file not found or unreadable", name=name, exc_info=True)
        _record_pipeline_error(suggestions, "File error", exc)
        valid = False

    return _ValidationResult(
        match=match,
        recipes_dir=recipes_dir,
        recipe=recipe,
        active_recipe=active_recipe,
        raw_declared=raw_declared,
        raw=raw,
        errors=errors,
        suggestions=suggestions,
        skip_resolutions=_skip_resolutions,
        pre_prune_steps=_pre_prune_steps,
        deferred_guard_state=_deferred_guard_state,
        unreachable_step_names=_unreachable_step_names,
        effective_flow_edges=_effective_flow_edges,
        finalized_projection=_finalized_projection,
        valid=valid,
    )


def _record_pipeline_error(
    suggestions: list[dict[str, Any]], prefix: str, exc: BaseException
) -> None:
    """Append a single validation-error suggestion to ``suggestions``."""
    suggestions.append(
        {
            "rule": "validation-error",
            "severity": "error",
            "step": "(validation-pipeline)",
            "message": f"{prefix}: {exc}",
        }
    )


def _assemble_load_result(
    pipeline_result: _ValidationResult, pipeline_inputs: _LoadPipelineInputs
) -> LoadRecipeResult:
    """Build the user-visible ``LoadRecipeResult`` and write the cache entry.

    Cache write uses two nested guards: ``if match is not None:`` (defense
    against ``RecipeNotFoundError`` short-circuit) and ``if cacheable:``
    (caller-supplied non-None lister disables caching).
    """
    match = pipeline_result.match
    recipes_dir = pipeline_result.recipes_dir
    raw = pipeline_result.raw
    errors = pipeline_result.errors
    suggestions = pipeline_result.suggestions
    valid = pipeline_result.valid
    recipe = pipeline_result.recipe
    active_recipe = pipeline_result.active_recipe
    _skip_resolutions = pipeline_result.skip_resolutions
    _pre_prune_steps = pipeline_result.pre_prune_steps
    _deferred_guard_state = pipeline_result.deferred_guard_state
    _unreachable_step_names = pipeline_result.unreachable_step_names
    _effective_flow_edges = pipeline_result.effective_flow_edges
    _finalized_projection = pipeline_result.finalized_projection

    name = pipeline_inputs.name
    project_recipes_dir = pipeline_inputs.project_recipes_dir
    builtin_dir = pipeline_inputs.builtin_dir
    pkg_version = pipeline_inputs.pkg_version
    _rule_hash = pipeline_inputs.rule_registry_hash
    cache_key = pipeline_inputs.cache_key
    cacheable = pipeline_inputs.cacheable
    resolved_defaults = pipeline_inputs.resolved_defaults
    include_finalized_projection = pipeline_inputs.include_finalized_projection

    diagram: str | None = load_recipe_diagram(name, recipes_dir)
    if diagram is not None and _skip_resolutions:
        diagram = annotate_diagram_with_pruning(diagram, _skip_resolutions)

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
    assert_no_raw_placeholders(raw, context=name, hidden_ingredient_names=_hidden_names)
    result: dict[str, Any] = {
        "content": raw,
        "errors": errors,
        "diagram": diagram,
        "suggestions": suggestions,
        "valid": valid,
    }
    if _serving_recipe is not None and _serving_recipe.summary:
        result["summary"] = _serving_recipe.summary
    if _serving_recipe is not None and _serving_recipe.kitchen_rules:
        result["kitchen_rules"] = _serving_recipe.kitchen_rules
    if _serving_recipe is not None and _serving_recipe.requires_packs:
        result["requires_packs"] = _serving_recipe.requires_packs
    if _serving_recipe is not None and _serving_recipe.requires_features:
        result["requires_features"] = _serving_recipe.requires_features
    if ing_table:
        result["ingredients_table"] = ing_table
    # Two delivery paths: orchestration_rules embeds text for Channel A;
    # stop_step_semantics is a dedicated field for Channel B.
    _stop_semantics = _build_stop_step_semantics(active_recipe) if active_recipe else ""
    result["orchestration_rules"] = _build_orchestration_rules(
        active_recipe, stop_semantics=_stop_semantics
    )
    result["stop_step_semantics"] = _stop_semantics
    result["content_hash"] = recipe.content_hash if recipe else ""
    result["composite_hash"] = recipe.composite_hash if recipe else ""
    result["recipe_version"] = recipe.recipe_version if recipe else None

    _deferred_guard_list: list[DeferredGuard] = []
    for _dg_step, (_dg_ref, _dg_target) in _deferred_guard_state.items():
        if _skip_resolutions.get(_dg_step) is None:
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
    if active_recipe is not None:
        if include_finalized_projection and _finalized_projection is not None:
            result["_finalized_projection"] = _finalized_projection
        result["post_prune_step_names"] = list(active_recipe.steps.keys())
        result["unreachable_step_names"] = list(_unreachable_step_names)
        _step_names_set = set(active_recipe.steps)
        result["post_prune_routing_edges"] = sorted(
            {edge.target for edge in _effective_flow_edges if edge.target in _step_names_set}
        )
    # Two nested guards: outer against RecipeNotFoundError short-circuit,
    # inner against non-None lister (cacheable).
    if match is not None:
        entry = _LoadCacheEntry(
            recipe_path=match.path,
            recipe_mtime=_api_cache._path_mtime_ns(match.path),
            recipe_size=_api_cache._file_size(match.path),
            project_dir_mtime=_api_cache._path_mtime_ns(project_recipes_dir),
            builtin_dir_mtime=_api_cache._path_mtime_ns(builtin_dir),
            pkg_version=pkg_version,
            rule_registry_hash=_rule_hash,
            result=result,
        )
        if cacheable:
            _api_cache._LOAD_CACHE.put(cache_key, entry)

    return cast(LoadRecipeResult, result)


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
    effective_backend_map: dict[str, str] | None = None,
    backend_capabilities_map: dict[str, BackendCapabilities] | None = None,
    backend_origin_map: dict[str, str] | None = None,
    include_finalized_projection: bool = False,
) -> LoadRecipeResult:
    """Load a recipe by name and run full validation.

    Raises:
        ProcessStaleError: Package directory was modified since server startup.
        RecipeNotFoundError: Named recipe could not be found.
    """
    t0 = time.perf_counter()

    pipeline_inputs = _resolve_cache_inputs(
        name,
        project_dir,
        suppressed=suppressed,
        recipe_info=recipe_info,
        recipe_list=recipe_list,
        resolved_defaults=resolved_defaults,
        ingredient_overrides=ingredient_overrides,
        temp_dir=temp_dir,
        temp_dir_relpath=temp_dir_relpath,
        lister=lister,
        defer_unresolved=defer_unresolved,
        backend_name=backend_name,
        effective_backend_map=effective_backend_map,
        backend_capabilities_map=backend_capabilities_map,
        backend_origin_map=backend_origin_map,
        include_finalized_projection=include_finalized_projection,
    )

    # Cache fast-path: only when cacheable AND cached entry matches.
    cached = (
        _api_cache._LOAD_CACHE.get(pipeline_inputs.cache_key)
        if pipeline_inputs.cacheable
        else None
    )
    if (
        cached is not None
        and cached.pkg_version == pipeline_inputs.pkg_version
        and cached.rule_registry_hash == pipeline_inputs.rule_registry_hash
    ):
        pm = _api_cache._path_mtime_ns(pipeline_inputs.project_recipes_dir)
        bm = _api_cache._path_mtime_ns(pipeline_inputs.builtin_dir)
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

    partial, t0 = _resolve_recipe_match(name, pipeline_inputs, t0)
    pipeline_result = _run_validation_pipeline(partial, pipeline_inputs, t0)
    result = _assemble_load_result(pipeline_result, pipeline_inputs)

    if result.get("valid", False):
        _api_cache._refresh_staleness_baseline()
    return cast(LoadRecipeResult, _api_cache._LOAD_CACHE.copy_result(result))
