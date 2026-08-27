"""Recipe API facade: load/validate pipelines live in ``_api_orchestration.py``.

Re-exports the public surface (``load_and_validate``, ``list_all``,
``format_recipe_list_response``, ``validate_from_path``) plus the cache,
listing, ingredients, and orchestration helpers. Implementation:
``_api_cache.py``, ``_api_listing.py``, ``_api_orchestration.py``.
"""

from __future__ import annotations

import json
import time

from autoskillit.core import (
    SkillLister,  # noqa: F401 — preserved for lister_targets substring check
    build_parameter_forwarding_rules,
    get_logger,
    resolve_temp_dir,  # noqa: F401 — preserved for tests
)
from autoskillit.recipe._analysis import (  # noqa: F401
    _extract_routing_edges,
    make_validation_context,
)
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
from autoskillit.recipe._binding import bind_recipe  # noqa: F401
from autoskillit.recipe._io_loading import (
    assert_no_raw_placeholders,  # noqa: F401
    load_recipe_dict_with_declarations,  # noqa: F401
)
from autoskillit.recipe._recipe_composition import (  # noqa: F401
    _assert_content_integrity,
    _build_active_recipe,
    _derive_rate_limit_routes,
    _prune_skipped_steps,
    _resolve_hidden_inputs_in_content,
    _validate_no_dangling_routes,
    _validate_route_consistency,
)
from autoskillit.recipe._recipe_ingredients import (
    DeferredGuard,  # noqa: F401
    ListRecipesResult,  # noqa: F401
    LoadRecipeResult,  # noqa: F401 — preserved for tests
    OpenKitchenResult,  # noqa: F401
    RecipeListItem,  # noqa: F401
    build_ingredient_rows,  # noqa: F401
    format_ingredients_table,  # noqa: F401 — preserved for tests
)
from autoskillit.recipe._recipe_raw_repair import _resolve_skip_guards_in_content  # noqa: F401
from autoskillit.recipe._rule_helpers import (
    _is_failure_sentinel_value,
    extract_sentinel_json_blocks,
    filter_pruning_false_positives,  # noqa: F401
)
from autoskillit.recipe.contracts import (  # noqa: F401
    check_contract_staleness,
    load_recipe_card,
    stale_to_suggestions,
    validate_recipe_cards,
)
from autoskillit.recipe.diagrams import (  # noqa: F401
    annotate_diagram_with_pruning,
    check_diagram_staleness,
    diagram_stale_to_suggestions,
    load_recipe_diagram,
)
from autoskillit.recipe.io import (  # noqa: F401
    RecipeInfo,
    _parse_recipe,
    builtin_recipes_dir,
    builtin_sub_recipes_dir,
    find_recipe_by_name,
    list_recipes,
    substitute_scripts_placeholder,
    substitute_temp_placeholder,
)
from autoskillit.recipe.schema import Recipe
from autoskillit.recipe.validator import (  # noqa: F401
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


# Re-export the orchestrator entry point last (after the helpers above are defined).
# This must be at module bottom to break the circular import: _api_orchestration
# imports these helpers from _api during its own initialization.
from autoskillit.recipe._api_orchestration import (  # noqa: F401,E402
    load_and_validate,
)
