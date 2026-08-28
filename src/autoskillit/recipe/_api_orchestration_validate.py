"""Phase 3 of the load pipeline: run the validation pipeline.

The 19 ordered stages (yaml_parse, structural, lister, pre-prune semantic,
prune, raw guards, route consistency, rate-limit, effective routing, sweep,
post-sweep validation, post-prune semantic, false-positive filter, version
suppression, hidden-input interpolation, contract card, contract staleness,
diagram staleness, validity computation) MUST keep their exact statement
order — the order is the contract enforced by
``tests/arch/test_pipeline_ordering.py``.

Moved 2026-08-28 from ``_api_orchestration.py`` under issue #4905.

Monkeypatch contract: every monkeypatchable call site is redirected through
``_orch.{name}`` so the existing 13-name test monkeypatch suite continues
to work after decomposition.
"""
from __future__ import annotations

from typing import Any

import autoskillit.recipe._api_orchestration as _orch
from autoskillit.core import FinalizedRecipeProjection, RecipeFlowEdge, RecipeStepGuard, YAMLError
from autoskillit.recipe._analysis import make_validation_context
from autoskillit.recipe._api_orchestration_parse import _parse_and_compose
from autoskillit.recipe._api_orchestration_types import _LoadPipelineInputs, _ValidationResult
from autoskillit.recipe._binding import bind_recipe
from autoskillit.recipe._recipe_composition import (
    _analysis_edges_from_effective_routes,
    _assert_content_integrity,
    _DeferredGuardState,
    _derive_rate_limit_routes,
    _effective_routing_edges,
    _effective_routing_target_errors,
    _prune_skipped_steps,
    _resolve_hidden_inputs_in_content,
    _sweep_unreachable_steps,
    _validate_post_sweep_effective_graph,
    _validate_route_consistency,
)
from autoskillit.recipe._recipe_raw_repair import _resolve_skip_guards_in_content
from autoskillit.recipe._rule_helpers import filter_pruning_false_positives
from autoskillit.recipe.schema import Recipe, RecipeStep

__all__ = ["_record_pipeline_error", "_run_validation_pipeline"]


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
    _deferred_guard_state: dict[str, _DeferredGuardState] = {}
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
        t0 = _orch._t("yaml_parse", t0, name)
        if active_recipe is None:
            # _parse_and_compose returned no active recipe (e.g. non-dict
            # payload or compose failure). Raise so the except clause records
            # a structured suggestion instead of letting AssertionError
            # propagate uncaught.
            raise ValueError("Recipe did not produce an active recipe")
        t0 = _orch._t("validate_recipe_structure", t0, name)

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
        _pre_prune_findings = _orch.run_semantic_rules(_pre_prune_val_ctx)
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
        _effective_flow_edges = _effective_routing_edges(active_recipe, _deferred_guard_state)
        _pre_sweep_route_errors = _effective_routing_target_errors(
            active_recipe, _effective_flow_edges
        )
        if _pre_sweep_route_errors:
            errors.extend(
                f"[post-prune] effective route: {error}" for error in _pre_sweep_route_errors
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
        _post_sweep_errors = _validate_post_sweep_effective_graph(
            active_recipe,
            _effective_flow_edges,
            pre_sweep_route_errors=_pre_sweep_route_errors,
        )
        if _post_sweep_errors:
            errors.extend(f"[post-prune] {error}" for error in _post_sweep_errors)
            raw = ""
        _effective_analysis_edges = _analysis_edges_from_effective_routes(
            active_recipe, _effective_flow_edges
        )
        t0 = _orch._t("prune_skipped_steps", t0, name)

        # Stage: semantic rules
        from autoskillit.recipe.io import builtin_sub_recipes_dir

        recipe_infos = (
            _recipe_list if _recipe_list is not None else _orch.list_recipes(_pdir).items
        )
        known = frozenset(r.name for r in recipe_infos)
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
        if include_finalized_projection and not _pre_sweep_route_errors and not _post_sweep_errors:
            from autoskillit.recipe.validator import _finalize_delivery_segments

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
                ordered_steps=_orch._finalize_recipe_steps(active_recipe, _deferred_guard_state),
                ingredient_names=frozenset(active_recipe.ingredients),
                delivery_segments=_delivery_segments,
                ordered_step_guards=tuple(
                    RecipeStepGuard(step_name, step.skip_when_true[8:], step.on_success)
                    for step_name, step in active_recipe.steps.items()
                    if step.skip_when_true is not None and step.on_success is not None
                ),
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
        semantic_findings = _orch.run_semantic_rules(val_ctx)
        semantic_suggestions = _orch.findings_to_dicts(semantic_findings)
        t0 = _orch._t("semantic_rules", t0, name)

        if _skip_resolutions and any(v is not True for v in _skip_resolutions.values()):
            semantic_findings = filter_pruning_false_positives(
                semantic_findings, _pre_prune_findings
            )
            semantic_suggestions = _orch.findings_to_dicts(semantic_findings)
            _graph_aware_rules = {"capture-inversion-detection", "dead-output"}
            _pre_prune_keys = {(f.rule, f.step_name) for f in _pre_prune_findings}
            for _f in semantic_findings:
                if (
                    _f.rule in _graph_aware_rules
                    and (_f.rule, _f.step_name) not in _pre_prune_keys
                ):
                    _orch.logger.debug(
                        "pruning_filter_new_finding",
                        recipe=name,
                        rule=_f.rule,
                        step=_f.step_name,
                        message=_f.message,
                    )

        _suppressed = suppressed or []
        if name in _suppressed:
            from autoskillit.recipe.validator import filter_version_rule

            semantic_suggestions = filter_version_rule(semantic_suggestions)
        suggestions.extend(semantic_suggestions)

        # Stage: hidden ingredient interpolation
        raw = _resolve_hidden_inputs_in_content(raw, active_recipe, ingredient_overrides)
        t0 = _orch._t("resolve_hidden_inputs", t0, name)

        # Stages: contract card + staleness + diagram
        contract = _orch.load_recipe_card(name, recipes_dir)
        contract_findings: list[dict[str, Any]] = []
        if contract:
            contract_findings = _orch.validate_recipe_cards(active_recipe, contract)
            suggestions.extend(contract_findings)
        t0 = _orch._t("contract_card", t0, name)
        if contract:
            staleness_cache_path = _effective_temp_dir / "recipe_staleness_cache.json"
            stale = _orch.check_contract_staleness(
                contract,
                recipe_path=match.path,
                cache_path=staleness_cache_path,
                resolver=_skill_resolver,
                project_root=_pdir,
            )
            from autoskillit.recipe.contracts import stale_to_suggestions

            suggestions.extend(stale_to_suggestions(stale))
        t0 = _orch._t("staleness_check", t0, name)
        from autoskillit.recipe.diagrams import (
            check_diagram_staleness,
            diagram_stale_to_suggestions,
        )

        if check_diagram_staleness(name, recipes_dir, match.path):
            suggestions.extend(diagram_stale_to_suggestions(name))
        t0 = _orch._t("diagram", t0, name)
        valid = _orch.compute_recipe_validity(errors, semantic_findings, contract_findings)

    except YAMLError as exc:
        _orch.logger.warning("Recipe YAML parse error", name=name, exc_info=True)
        _record_pipeline_error(suggestions, "YAML parse error", exc)
        valid = False
    except ValueError as exc:
        _orch.logger.warning("Recipe structure invalid", name=name, exc_info=True)
        _record_pipeline_error(suggestions, "Invalid recipe structure", exc)
        valid = False
    except (FileNotFoundError, OSError) as exc:
        _orch.logger.warning("Recipe file not found or unreadable", name=name, exc_info=True)
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
