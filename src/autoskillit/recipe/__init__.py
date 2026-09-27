"""IL-2 recipe domain — schema, I/O, validation, and contract management."""

from __future__ import annotations

from autoskillit.core import get_logger

logger = get_logger(__name__)

# Submodule imports — ensure each shim module is bound as an attribute of
# ``autoskillit.recipe`` so that pre-Part-D callers (e.g.,
# ``autoskillit.recipe.methodology_disambiguation``) keep working after the
# move into ``autoskillit.recipe.methodology.*``. Python only exposes a
# submodule as a parent-package attribute when the parent package triggers
# the load; importing the new path does not retroactively bind the old name.
# __init__.py files must be pure re-export facades (no module-scope function
# defs), so this loop runs inline rather than through a helper function.
import importlib as _importlib

_LEGACY_SHIM_MODULES: tuple[str, ...] = (
    "_analysis",
    "_analysis_bfs",
    "_analysis_blocks",
    "_analysis_detectors",
    "_analysis_graph",
    "_cmd_rpc",
    "_cmd_rpc_guards",
    "_cmd_rpc_issues",
    "_cmd_rpc_merge",
    "_contracts_card",
    "_contracts_manifest",
    "_contracts_staleness",
    "_contracts_types",
    "_git_helpers",
    "_io_loading",
    "_recipe_composition",
    "_recipe_ingredients",
    "_recipe_raw_repair",
    "_registry_utils",
    "_rule_helpers",
    "_skill_helpers",
    "_skill_placeholder_parser",
    "contracts",
    "experiment_type_registry",
    "methodology_disambiguation",
    "methodology_tradition_registry",
    "methodology_tradition_router",
    "methodology_venue_appendix",
    "staleness_cache",
    "_api",
    "_api_cache",
    "_api_listing",
    "_api_orchestration",
    "_api_orchestration_assemble",
    "_api_orchestration_cache",
    "_api_orchestration_match",
    "_api_orchestration_parse",
    "_api_orchestration_text",
    "_api_orchestration_types",
    "_api_orchestration_validate",
)

for _name in _LEGACY_SHIM_MODULES:
    _importlib.import_module(f"{__name__}.{_name}")
del _importlib, _name

# Rule registration — import triggers @semantic_rule registration.
from autoskillit.recipe import registry as _reg
from autoskillit.recipe._binding import bind_recipe, bind_step_invocation
from autoskillit.recipe.analysis._analysis import (
    RouteEdge,
    _extract_routing_edges,
)
from autoskillit.recipe.api._api import (
    format_recipe_list_response,
    list_all,
    load_and_validate,
    validate_from_path,
)
from autoskillit.recipe.contracts.contracts import (
    OutcomeInvariantEntry,
    ResultFieldSpec,
    SkillContract,
    SkillInput,
    SkillOutput,
    StaleItem,
    SuccessQualifierEntry,
    check_contract_staleness,
    generate_recipe_card,
    get_skill_contract,
    load_bundled_manifest,
    load_recipe_card,
    resolve_input_specs,
    resolve_skill_name,
    validate_recipe_cards,
)
from autoskillit.recipe.contracts.staleness_cache import (
    StalenessEntry,
    compute_recipe_hash,
    read_staleness_cache,
    write_staleness_cache,
)
from autoskillit.recipe.diagrams import (
    annotate_diagram_with_pruning,
    check_diagram_staleness,
    diagram_stale_to_suggestions,
    load_recipe_diagram,
)
from autoskillit.recipe.identity import (
    check_rerun_detection,
    find_prior_runs,
)
from autoskillit.recipe.ingredients._recipe_ingredients import (
    ListRecipesResult,
    LoadRecipeResult,
    OpenKitchenResult,
    RecipeListItem,
    build_ingredient_rows,
    format_ingredients_table,
)
from autoskillit.recipe.io import (
    GROUP_LABELS,
    all_validated_recipe_names,
    all_validated_recipe_paths,
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
    step_byte_ranges_from_yaml,
)
from autoskillit.recipe.loader import parse_recipe_metadata
from autoskillit.recipe.methodology.experiment_type_registry import (
    BUNDLED_EXPERIMENT_TYPES_DIR,
    ExperimentTypeSpec,
    get_experiment_type_by_name,
    is_silent_type,
    load_all_experiment_types,
    load_types_from_dir,
    parse_experiment_type,
)
from autoskillit.recipe.methodology.methodology_disambiguation import (
    CrossTraditionOverlapDef,
    DisambiguationExceptionDef,
    DisambiguationResult,
    DisambiguationRuleDef,
    disambiguate,
    load_disambiguation_rules,
)
from autoskillit.recipe.methodology.methodology_tradition_registry import (
    BUNDLED_METHODOLOGY_TRADITIONS_DIR,
    MethodologyTraditionSpec,
    VenueAppendixDef,
    get_methodology_tradition_by_name,
    is_out_of_scope_tradition,
    load_all_methodology_traditions,
    load_traditions_from_dir,
    parse_methodology_tradition,
)
from autoskillit.recipe.methodology.methodology_tradition_router import (
    TraditionRouterResult,
    UnionRuleDef,
    classify_methodology,
)
from autoskillit.recipe.methodology.methodology_venue_appendix import (
    AlternateParentDef,
    MLSubAreaFoldingDef,
    VenueAppendixMatch,
    load_ml_sub_area_folding,
    resolve_venue_appendices,
)
from autoskillit.recipe.repository import DefaultRecipeRepository
from autoskillit.recipe.rules import rules_actions as _rules_actions  # noqa: F401
from autoskillit.recipe.rules import (  # noqa: F401
    rules_audit_impl_plan_scope as _rules_audit_impl_plan_scope,
)
from autoskillit.recipe.rules import (  # noqa: F401
    rules_audit_impl_topology as _rules_audit_impl_topology,
)
from autoskillit.recipe.rules import (  # noqa: F401
    rules_audit_outcome_routing as _rules_audit_outcome_routing,
)
from autoskillit.recipe.rules import (  # noqa: F401
    rules_backend_compat as _rules_backend_compat,
)
from autoskillit.recipe.rules import rules_blocks as _rules_blocks  # noqa: F401
from autoskillit.recipe.rules import rules_bypass as _rules_bypass  # noqa: F401
from autoskillit.recipe.rules import (
    rules_callable_scope as _rules_callable_scope,  # noqa: F401
)
from autoskillit.recipe.rules import rules_cmd as _rules_cmd  # noqa: F401
from autoskillit.recipe.rules import (  # noqa: F401
    rules_commit_guard_regression_route as _rules_commit_guard_regression_route,
)
from autoskillit.recipe.rules import (  # noqa: F401
    rules_contract_recovery as _rules_contract_recovery,
)
from autoskillit.recipe.rules import rules_contracts as _rules_contracts  # noqa: F401
from autoskillit.recipe.rules import (  # noqa: F401
    rules_criterion_schema_drift as _rules_criterion_schema_drift,
)
from autoskillit.recipe.rules import (  # noqa: F401
    rules_failure_verdict_bypass as _rules_failure_verdict_bypass,
)
from autoskillit.recipe.rules import rules_features as _rules_features  # noqa: F401
from autoskillit.recipe.rules import rules_fixing as _rules_fixing  # noqa: F401
from autoskillit.recipe.rules import rules_flake_loop as _rules_flake_loop  # noqa: F401
from autoskillit.recipe.rules import rules_food_truck as _rules_food_truck  # noqa: F401
from autoskillit.recipe.rules import (  # noqa: F401
    rules_gitignored_deliverable as _rules_gitignored_deliverable,
)
from autoskillit.recipe.rules import (  # noqa: F401
    rules_ingredient_step_name as _rules_ingredient_step_name,
)
from autoskillit.recipe.rules import rules_inline_script as _rules_inline_script  # noqa: F401
from autoskillit.recipe.rules import rules_inputs as _rules_inputs  # noqa: F401
from autoskillit.recipe.rules import (  # noqa: F401
    rules_inventory_gate_bilateral as _rules_inventory_gate_bilateral,
)
from autoskillit.recipe.rules import rules_isolation as _rules_isolation  # noqa: F401
from autoskillit.recipe.rules import (
    rules_issue_scope_threading as _rules_issue_scope_threading,  # noqa: F401
)
from autoskillit.recipe.rules import (
    rules_loop_artifact_scope as _rules_loop_artifact_scope,  # noqa: F401
)
from autoskillit.recipe.rules import rules_loop_counter as _rules_loop_counter  # noqa: F401
from autoskillit.recipe.rules import rules_loop_progress as _rules_loop_progress  # noqa: F401
from autoskillit.recipe.rules import rules_merge as _rules_merge  # noqa: F401
from autoskillit.recipe.rules import (  # noqa: F401
    rules_merge_context as _rules_merge_context,
)
from autoskillit.recipe.rules import (  # noqa: F401
    rules_merge_enrollment as _rules_merge_enrollment,
)
from autoskillit.recipe.rules import rules_merge_guards as _rules_merge_guards  # noqa: F401
from autoskillit.recipe.rules import (  # noqa: F401
    rules_merge_push_symmetry as _rules_merge_push_symmetry,
)
from autoskillit.recipe.rules import rules_merge_queue as _rules_merge_queue  # noqa: F401
from autoskillit.recipe.rules import rules_merge_routing as _rules_merge_routing  # noqa: F401
from autoskillit.recipe.rules import rules_merge_wait as _rules_merge_wait  # noqa: F401
from autoskillit.recipe.rules import rules_model as _rules_model  # noqa: F401
from autoskillit.recipe.rules import (  # noqa: F401
    rules_note_shape_contradiction as _rules_note_shape_contradiction,
)
from autoskillit.recipe.rules import (
    rules_optional_capture as _rules_optional_capture,  # noqa: F401
)
from autoskillit.recipe.rules import rules_packs as _rules_packs  # noqa: F401
from autoskillit.recipe.rules import (  # noqa: F401
    rules_phoropter_adjacency as _rules_phoropter_adjacency,
)
from autoskillit.recipe.rules import rules_plan_set_gate as _rules_plan_set_gate  # noqa: F401
from autoskillit.recipe.rules import (  # noqa: F401
    rules_pseudocode_sync as _rules_pseudocode_sync,
)
from autoskillit.recipe.rules import rules_reachability as _rules_reachability  # noqa: F401
from autoskillit.recipe.rules import rules_recipe as _rules_recipe  # noqa: F401
from autoskillit.recipe.rules import rules_remediation as _rules_remediation  # noqa: F401
from autoskillit.recipe.rules import rules_route_gate as _rules_route_gate  # noqa: F401
from autoskillit.recipe.rules import rules_skill_content as _rules_skill_content  # noqa: F401
from autoskillit.recipe.rules import (
    rules_skill_content_content_structure as _rules_skill_content_content_structure,  # noqa: F401
)
from autoskillit.recipe.rules import (
    rules_skill_content_github_api_safety as _rules_skill_content_github_api_safety,  # noqa: F401
)
from autoskillit.recipe.rules import (
    rules_skill_content_shell_safety as _rules_skill_content_shell_safety,  # noqa: F401
)
from autoskillit.recipe.rules import (
    rules_skill_content_skill_contract as _rules_skill_content_skill_contract,  # noqa: F401
)
from autoskillit.recipe.rules import (  # noqa: F401
    rules_skill_write_path_alignment as _rules_skill_write_path_alignment,
)
from autoskillit.recipe.rules import rules_skills as _rules_skills  # noqa: F401
from autoskillit.recipe.rules import (  # noqa: F401
    rules_skip_inviting_notes as _rules_skip_inviting_notes,
)
from autoskillit.recipe.rules import (  # noqa: F401
    rules_stamp_ownership as _rules_stamp_ownership,
)
from autoskillit.recipe.rules import rules_step_naming as _rules_step_naming  # noqa: F401
from autoskillit.recipe.rules import (  # noqa: F401
    rules_stop_sentinel_direction as _rules_stop_sentinel_direction,
)
from autoskillit.recipe.rules import rules_temp_path as _rules_temp_path  # noqa: F401
from autoskillit.recipe.rules import (
    rules_terminal_convergence as _rules_terminal_convergence,  # noqa: F401
)
from autoskillit.recipe.rules import rules_tools as _rules_tools  # noqa: F401
from autoskillit.recipe.rules import rules_verdict as _rules_verdict  # noqa: F401
from autoskillit.recipe.rules import (
    rules_verdict_context as _rules_verdict_context,  # noqa: F401
)
from autoskillit.recipe.rules import (
    rules_verdict_degradation as _rules_verdict_degradation,  # noqa: F401
)
from autoskillit.recipe.rules import rules_worktree as _rules_worktree  # noqa: F401
from autoskillit.recipe.rules.campaign import (
    rules_campaign_capture as _rules_campaign_capture,  # noqa: F401
)
from autoskillit.recipe.rules.campaign import (
    rules_campaign_deps as _rules_campaign_deps,  # noqa: F401
)
from autoskillit.recipe.rules.campaign import (
    rules_campaign_dispatch as _rules_campaign_dispatch,  # noqa: F401
)
from autoskillit.recipe.rules.campaign import (
    rules_campaign_flow as _rules_campaign_flow,  # noqa: F401
)
from autoskillit.recipe.rules.campaign import (
    rules_campaign_ingredients as _rules_campaign_ingredients,  # noqa: F401
)
from autoskillit.recipe.rules.ci import rules_ci as _rules_ci  # noqa: F401
from autoskillit.recipe.rules.ci import rules_ci_conflict as _rules_ci_conflict  # noqa: F401
from autoskillit.recipe.rules.ci import rules_ci_guards as _rules_ci_guards  # noqa: F401
from autoskillit.recipe.rules.ci import (
    rules_ci_merge_queue as _rules_ci_merge_queue,  # noqa: F401
)
from autoskillit.recipe.rules.dataflow import rules_clone as _rules_clone  # noqa: F401
from autoskillit.recipe.rules.dataflow import rules_dataflow as _rules_dataflow  # noqa: F401
from autoskillit.recipe.rules.dataflow import (
    rules_dataflow_callable as _rules_dataflow_callable,  # noqa: F401
)
from autoskillit.recipe.rules.dataflow import (
    rules_dataflow_handoff as _rules_dataflow_handoff,  # noqa: F401
)
from autoskillit.recipe.rules.dataflow import (
    rules_dataflow_multipart as _rules_dataflow_multipart,  # noqa: F401
)
from autoskillit.recipe.rules.graph import rules_graph as _rules_graph  # noqa: F401
from autoskillit.recipe.rules.graph import (
    rules_graph_output as _rules_graph_output,  # noqa: F401
)
from autoskillit.recipe.rules.graph import (
    rules_graph_review as _rules_graph_review,  # noqa: F401
)
from autoskillit.recipe.rules.graph import (
    rules_graph_routes as _rules_graph_routes,  # noqa: F401
)
from autoskillit.recipe.rules.graph import (
    rules_graph_summary as _rules_graph_summary,  # noqa: F401
)
from autoskillit.recipe.schema import (
    AUTOSKILLIT_VERSION_KEY,
    CAMPAIGN_REF_RE,
    NON_INTERACTIVE_KINDS,
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
from autoskillit.recipe.validator import (
    RuleFinding,
    analyze_dataflow,
    make_validation_context,
    run_semantic_rules,
    validate_recipe_structure,
)

_reg._finalize_registry()  # pyright: ignore[reportAttributeAccessIssue]  # lazy-registry: method added by _register_rule_module() side effects
del _reg

from autoskillit.recipe._binding import (
    RuntimeBindingError,
    bind_runtime_skill_invocation,
    compute_skill_contract_identity,
)
from autoskillit.recipe.contracts.contracts import (
    AuditAuthorityPublicationSpec,
    AuditOutputMode,
    select_audit_output_contract,
)
from autoskillit.recipe.validator import (
    edge_routes_success,
)

__all__ = [
    "GROUP_LABELS",
    "all_validated_recipe_names",
    "all_validated_recipe_paths",
    "group_rank",
    "ListRecipesResult",
    "LoadRecipeResult",
    "OpenKitchenResult",
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
    "AuditAuthorityPublicationSpec",
    "AuditOutputMode",
    "StaleItem",
    "StalenessEntry",
    "compute_recipe_hash",
    "read_staleness_cache",
    "write_staleness_cache",
    "RuleFinding",
    "load_recipe",
    "step_byte_ranges_from_yaml",
    "list_recipes",
    "find_recipe_by_name",
    "iter_steps_with_context",
    "validate_recipe_structure",
    "make_validation_context",
    "run_semantic_rules",
    "analyze_dataflow",
    "edge_routes_success",
    "check_contract_staleness",
    "generate_recipe_card",
    "get_skill_contract",
    "load_bundled_manifest",
    "load_recipe_card",
    "resolve_input_specs",
    "resolve_skill_name",
    "select_audit_output_contract",
    "validate_recipe_cards",
    "OutcomeInvariantEntry",
    "ResultFieldSpec",
    "SkillContract",
    "SkillInput",
    "SkillOutput",
    "SuccessQualifierEntry",
    "DefaultRecipeRepository",
    "parse_recipe_metadata",
    "load_and_validate",
    "bind_recipe",
    "bind_runtime_skill_invocation",
    "bind_step_invocation",
    "compute_skill_contract_identity",
    "RuntimeBindingError",
    "validate_from_path",
    "list_all",
    "format_ingredients_table",
    "format_recipe_list_response",
    "load_recipe_diagram",
    "check_diagram_staleness",
    "diagram_stale_to_suggestions",
    "annotate_diagram_with_pruning",
    "builtin_sub_recipes_dir",
    "find_sub_recipe_by_name",
    "BUNDLED_EXPERIMENT_TYPES_DIR",
    "BUNDLED_METHODOLOGY_TRADITIONS_DIR",
    "ExperimentTypeSpec",
    "load_types_from_dir",
    "parse_experiment_type",
    "get_experiment_type_by_name",
    "is_silent_type",
    "load_all_experiment_types",
    "MethodologyTraditionSpec",
    "parse_methodology_tradition",
    "load_traditions_from_dir",
    "get_methodology_tradition_by_name",
    "is_out_of_scope_tradition",
    "load_all_methodology_traditions",
    "VenueAppendixDef",
    "AlternateParentDef",
    "MLSubAreaFoldingDef",
    "VenueAppendixMatch",
    "load_ml_sub_area_folding",
    "resolve_venue_appendices",
    "CrossTraditionOverlapDef",
    "DisambiguationExceptionDef",
    "DisambiguationResult",
    "DisambiguationRuleDef",
    "disambiguate",
    "load_disambiguation_rules",
    # --- methodology tradition router ---
    "TraditionRouterResult",
    "UnionRuleDef",
    "classify_methodology",
    # --- rerun detection ---
    "check_rerun_detection",
    "find_prior_runs",
    "CampaignDispatch",
    "NON_INTERACTIVE_KINDS",
    "RecipeKind",
    # --- routing-edge facade (canonical package-level re-export of the
    #     recipe/_analysis_graph definitions; enables consumers in other
    #     packages to reuse _extract_routing_edges without crossing
    #     REQ-ARCH-001's submodule-import boundary) ---
    "RouteEdge",
    "_extract_routing_edges",
    "find_campaign_by_name",
    "list_campaign_recipes",
    "load_campaign_recipes_in_packs",
]
