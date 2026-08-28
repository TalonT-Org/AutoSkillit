"""Recipe API facade: load/validate pipelines live in ``_api_orchestration.py``.

Re-exports the public surface (``load_and_validate``, ``list_all``,
``format_recipe_list_response``, ``validate_from_path``) plus the cache,
listing, ingredients, and orchestration helpers. Implementation:
``_api_cache.py``, ``_api_listing.py``, ``_api_orchestration.py``,
``_api_orchestration_assemble.py``, ``_api_orchestration_cache.py``,
``_api_orchestration_match.py``, ``_api_orchestration_parse.py``,
``_api_orchestration_text.py``, ``_api_orchestration_types.py``,
``_api_orchestration_validate.py``.
"""

from __future__ import annotations

from autoskillit.core import (
    SkillLister,  # noqa: F401 — preserved for lister_targets substring check
    resolve_temp_dir,  # noqa: F401 — preserved for tests
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
from autoskillit.recipe._api_orchestration import load_and_validate  # noqa: F401
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
from autoskillit.recipe._rule_helpers import filter_pruning_false_positives  # noqa: F401
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
from autoskillit.recipe.validator import (  # noqa: F401
    _finalize_delivery_segments,
    compute_recipe_validity,
    filter_version_rule,
    findings_to_dicts,
    run_semantic_rules,
    validate_recipe_structure,
)
