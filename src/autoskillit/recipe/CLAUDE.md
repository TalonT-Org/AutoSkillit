# recipe/

IL-2 recipe layer — YAML schema, validation, semantic rules, dataflow analysis.
Sub-package: rules/ (see rules/CLAUDE.md).

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Re-exports `load_recipe`, `validate_recipe_structure`, `analyze_dataflow` |
| `contracts.py` | Re-export facade — contract types, manifest, card, staleness |
| `_contracts_types.py` | 10 dataclasses + 4 regex patterns |
| `_contracts_manifest.py` | Manifest loading + ref extraction utilities |
| `_contracts_card.py` | Card generation, loading, validation |
| `_contracts_staleness.py` | Staleness detection + MCP suggestions |
| `io.py` | `load_recipe`, `list_recipes`, `iter_steps_with_context` |
| `order.py` | `BUNDLED_RECIPE_ORDER` — stable display order registry for Group 0 recipes |
| `loader.py` | Path-based recipe metadata utilities |
| `_api.py` | Re-export facade — `load_and_validate` + orchestration rules |
| `_api_cache.py` | Cache globals + staleness helpers |
| `_api_listing.py` | `list_all` + `validate_from_path` |
| `_cmd_rpc.py` | Re-export facade — `run_python` callables |
| `_cmd_rpc_guards.py` | Counter guards + git workspace ops |
| `_cmd_rpc_merge.py` | Rebase, PR polling, branch management |
| `_cmd_rpc_issues.py` | Issue creation, bundles, audit run dirs |
| `_recipe_ingredients.py` | `format_ingredients_table` + `LoadRecipeResult` TypedDicts |
| `_recipe_composition.py` | `_build_active_recipe` + sub-recipe merging |
| `_rule_helpers.py` | Shared helper utilities for recipe semantic rules |
| `diagrams.py` | Flow diagram generation + staleness detection |
| `_registry_utils.py` | `dir_mtime` — shared mtime helper for registry loaders |
| `experiment_type_registry.py` | `ExperimentTypeSpec`, `load_all_experiment_types`, `is_silent_type` |
| `methodology_tradition_registry.py` | `MethodologyTraditionSpec`, `VenueAppendixDef`, `load_all_methodology_traditions`, `get_methodology_tradition_by_name`, `is_out_of_scope_tradition` |
| `methodology_venue_appendix.py` | `AlternateParentDef`, `MLSubAreaFoldingDef`, `VenueAppendixMatch`, `load_ml_sub_area_folding`, `resolve_venue_appendices` — Stage B venue-appendix resolution |
| `methodology_tradition_router.py` | `TraditionRouterResult`, `UnionRuleDef`, `classify_methodology` — two-stage Tier-C router |
| `methodology_disambiguation.py` | `DisambiguationRuleDef`, `CrossTraditionOverlapDef`, `DisambiguationResult`, `disambiguate`, `load_disambiguation_rules` |
| `registry.py` | `RuleFinding`, `RuleDef`, `BlockRuleDef`, `semantic_rule` decorator |
| `repository.py` | `RecipeRepository` implementation |
| `_analysis.py` | `ValidationContext` + `make_validation_context` |
| `_analysis_graph.py` | `RouteEdge` + `build_recipe_graph` + step graph primitives |
| `_analysis_bfs.py` | `bfs_reachable` + symbolic BFS fact propagation |
| `_analysis_blocks.py` | `extract_blocks` — group steps by block annotation |
| `_analysis_detectors.py` | Dead outputs + ref invalidations + implicit handoffs |
| `_git_helpers.py` | Shared git-remote regex (`_GIT_REMOTE_COMMAND_RE`, `_LITERAL_ORIGIN_RE`) for lint rules |
| `_skill_helpers.py` | Shared helpers for skill-related semantic rules |
| `_skill_placeholder_parser.py` | Bash placeholder extraction from SKILL.md |
| `identity.py` | Recipe identity hashing — content and composite fingerprints |
| `schema.py` | `Recipe`, `RecipeStep`, `DataFlowWarning` |
| `staleness_cache.py` | Staleness cache for contract and diagram freshness checks |
| `validator.py` | `validate_recipe_structure`, `analyze_dataflow` |

## Architecture Notes

`registry.py` uses the `@semantic_rule` decorator pattern (same side-effect registration
as `rules/`). The `_analysis_*.py` modules form an internal BFS-based dataflow analysis
pipeline; callers use `make_validation_context` as the sole entry point.
