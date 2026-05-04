"""IL-2 recipe domain — schema, I/O, validation, and contract management."""

from __future__ import annotations

from autoskillit.core import get_logger

logger = get_logger(__name__)

# Rule registration — import triggers @semantic_rule registration.
from autoskillit.recipe._api import (  # noqa: E402
    format_recipe_list_response,
    list_all,
    load_and_validate,
    validate_from_path,
)
from autoskillit.recipe._recipe_ingredients import (  # noqa: E402
    ListRecipesResult,
    LoadRecipeResult,
    RecipeListItem,
    build_ingredient_rows,
    format_ingredients_table,
)
from autoskillit.recipe.contracts import (  # noqa: E402
    StaleItem,
    check_contract_staleness,
    generate_recipe_card,
    get_skill_contract,
    load_bundled_manifest,
    load_recipe_card,
    resolve_skill_name,
    validate_recipe_cards,
)
from autoskillit.recipe.diagrams import (  # noqa: E402
    check_diagram_staleness,
    diagram_stale_to_suggestions,
    load_recipe_diagram,
)
from autoskillit.recipe.experiment_type_registry import (  # noqa: E402
    ExperimentTypeSpec,
    load_all_experiment_types,
)
from autoskillit.recipe.identity import (  # noqa: E402
    check_rerun_detection,
    find_prior_runs,
)
from autoskillit.recipe.io import (  # noqa: E402
    GROUP_LABELS,
    builtin_sub_recipes_dir,
    find_campaign_by_name,
    find_recipe_by_name,
    find_sub_recipe_by_name,
    group_rank,
    iter_steps_with_context,
    list_campaign_recipes,
    list_recipes,
    load_campaign_recipes_in_packs,
    load_recipe,
)
from autoskillit.recipe.loader import parse_recipe_metadata  # noqa: E402
from autoskillit.recipe.repository import DefaultRecipeRepository  # noqa: E402
from autoskillit.recipe.rules import rules_actions as _rules_actions  # noqa: E402 F401
from autoskillit.recipe.rules import rules_blocks as _rules_blocks  # noqa: E402 F401
from autoskillit.recipe.rules import rules_bypass as _rules_bypass  # noqa: E402 F401
from autoskillit.recipe.rules import rules_campaign as _rules_campaign  # noqa: E402 F401
from autoskillit.recipe.rules import rules_ci as _rules_ci  # noqa: E402 F401
from autoskillit.recipe.rules import rules_clone as _rules_clone  # noqa: E402 F401
from autoskillit.recipe.rules import rules_cmd as _rules_cmd  # noqa: E402 F401
from autoskillit.recipe.rules import rules_contracts as _rules_contracts  # noqa: E402 F401
from autoskillit.recipe.rules import rules_dataflow as _rules_dataflow  # noqa: E402 F401
from autoskillit.recipe.rules import rules_features as _rules_features  # noqa: E402 F401
from autoskillit.recipe.rules import rules_fixing as _rules_fixing  # noqa: E402 F401
from autoskillit.recipe.rules import rules_graph as _rules_graph  # noqa: E402 F401
from autoskillit.recipe.rules import rules_inline_script as _rules_inline_script  # noqa: E402 F401
from autoskillit.recipe.rules import rules_inputs as _rules_inputs  # noqa: E402 F401
from autoskillit.recipe.rules import rules_isolation as _rules_isolation  # noqa: E402 F401
from autoskillit.recipe.rules import rules_merge as _rules_merge  # noqa: E402 F401
from autoskillit.recipe.rules import rules_merge_queue as _rules_merge_queue  # noqa: E402 F401
from autoskillit.recipe.rules import rules_packs as _rules_packs  # noqa: E402 F401
from autoskillit.recipe.rules import rules_reachability as _rules_reachability  # noqa: E402 F401
from autoskillit.recipe.rules import rules_recipe as _rules_recipe  # noqa: E402 F401
from autoskillit.recipe.rules import rules_skill_content as _rules_skill_content  # noqa: E402 F401
from autoskillit.recipe.rules import rules_skills as _rules_skills  # noqa: E402 F401
from autoskillit.recipe.rules import rules_temp_path as _rules_temp_path  # noqa: E402 F401
from autoskillit.recipe.rules import rules_tools as _rules_tools  # noqa: E402 F401
from autoskillit.recipe.rules import rules_verdict as _rules_verdict  # noqa: E402 F401
from autoskillit.recipe.rules import rules_worktree as _rules_worktree  # noqa: E402 F401
from autoskillit.recipe.schema import (  # noqa: E402
    AUTOSKILLIT_VERSION_KEY,
    CAMPAIGN_REF_RE,
    CampaignDispatch,
    DataFlowReport,
    Recipe,
    RecipeBlock,
    RecipeInfo,
    RecipeIngredient,
    RecipeKind,
    RecipeStep,
    StepResultCondition,
    StepResultRoute,
)
from autoskillit.recipe.staleness_cache import (  # noqa: E402
    StalenessEntry,
    compute_recipe_hash,
    read_staleness_cache,
    write_staleness_cache,
)
from autoskillit.recipe.validator import (  # noqa: E402
    RuleFinding,
    analyze_dataflow,
    make_validation_context,
    run_semantic_rules,
    validate_recipe,
)

__all__ = [
    "GROUP_LABELS",
    "group_rank",
    "ListRecipesResult",
    "LoadRecipeResult",
    "RecipeListItem",
    "build_ingredient_rows",
    "Recipe",
    "RecipeBlock",
    "RecipeInfo",
    "RecipeIngredient",
    "RecipeStep",
    "AUTOSKILLIT_VERSION_KEY",
    "CAMPAIGN_REF_RE",
    "StepResultCondition",
    "StepResultRoute",
    "DataFlowReport",
    "StaleItem",
    "StalenessEntry",
    "compute_recipe_hash",
    "read_staleness_cache",
    "write_staleness_cache",
    "RuleFinding",
    "load_recipe",
    "list_recipes",
    "find_recipe_by_name",
    "iter_steps_with_context",
    "validate_recipe",
    "make_validation_context",
    "run_semantic_rules",
    "analyze_dataflow",
    "check_contract_staleness",
    "generate_recipe_card",
    "get_skill_contract",
    "load_bundled_manifest",
    "load_recipe_card",
    "resolve_skill_name",
    "validate_recipe_cards",
    "DefaultRecipeRepository",
    "parse_recipe_metadata",
    "load_and_validate",
    "validate_from_path",
    "list_all",
    "format_ingredients_table",
    "format_recipe_list_response",
    "load_recipe_diagram",
    "check_diagram_staleness",
    "diagram_stale_to_suggestions",
    "builtin_sub_recipes_dir",
    "find_sub_recipe_by_name",
    "ExperimentTypeSpec",
    "load_all_experiment_types",
    "check_rerun_detection",
    "find_prior_runs",
    "CampaignDispatch",
    "RecipeKind",
    "find_campaign_by_name",
    "list_campaign_recipes",
    "load_campaign_recipes_in_packs",
]
