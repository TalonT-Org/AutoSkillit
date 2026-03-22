"""L2 recipe domain — schema, I/O, validation, and contract management."""

from __future__ import annotations

from autoskillit.core import get_logger

_logger = get_logger(__name__)

# Rule registration — import triggers @semantic_rule registration.
from autoskillit.recipe import rules_bypass as _rules_bypass  # noqa: E402 F401
from autoskillit.recipe import rules_ci as _rules_ci  # noqa: E402 F401
from autoskillit.recipe import rules_clone as _rules_clone  # noqa: E402 F401
from autoskillit.recipe import rules_contracts as _rules_contracts  # noqa: E402 F401
from autoskillit.recipe import rules_dataflow as _rules_dataflow  # noqa: E402 F401
from autoskillit.recipe import rules_graph as _rules_graph  # noqa: E402 F401
from autoskillit.recipe import rules_inputs as _rules_inputs  # noqa: E402 F401
from autoskillit.recipe import rules_merge as _rules_merge  # noqa: E402 F401
from autoskillit.recipe import rules_recipe as _rules_recipe  # noqa: E402 F401
from autoskillit.recipe import rules_skill_content as _rules_skill_content  # noqa: E402 F401
from autoskillit.recipe import rules_skills as _rules_skills  # noqa: E402 F401
from autoskillit.recipe import rules_tools as _rules_tools  # noqa: E402 F401
from autoskillit.recipe import rules_verdict as _rules_verdict  # noqa: E402 F401
from autoskillit.recipe import rules_worktree as _rules_worktree  # noqa: E402 F401
from autoskillit.recipe._api import (  # noqa: E402
    ListRecipesResult,
    LoadRecipeResult,
    RecipeListItem,
    build_ingredient_rows,
    format_ingredients_table,
    format_recipe_list_response,
    list_all,
    load_and_validate,
    validate_from_path,
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
from autoskillit.recipe.io import (  # noqa: E402
    builtin_sub_recipes_dir,
    find_recipe_by_name,
    find_sub_recipe_by_name,
    iter_steps_with_context,
    list_recipes,
    load_recipe,
)
from autoskillit.recipe.loader import parse_recipe_metadata  # noqa: E402
from autoskillit.recipe.repository import DefaultRecipeRepository  # noqa: E402
from autoskillit.recipe.schema import (  # noqa: E402
    AUTOSKILLIT_VERSION_KEY,
    DataFlowReport,
    Recipe,
    RecipeInfo,
    RecipeIngredient,
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
    "ListRecipesResult",
    "LoadRecipeResult",
    "RecipeListItem",
    "build_ingredient_rows",
    "Recipe",
    "RecipeInfo",
    "RecipeIngredient",
    "RecipeStep",
    "AUTOSKILLIT_VERSION_KEY",
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
]
