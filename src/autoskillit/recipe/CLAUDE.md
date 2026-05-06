# recipe/

IL-2 recipe layer — YAML schema, validation, semantic rules, dataflow analysis.
Sub-package: rules/ (see rules/CLAUDE.md).

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Re-exports `load_recipe`, `validate_recipe`, `analyze_dataflow` |
| `contracts.py` | Contract card generation + staleness triage |
| `io.py` | `load_recipe`, `list_recipes`, `iter_steps_with_context` |
| `order.py` | `BUNDLED_RECIPE_ORDER` — stable display order registry for Group 0 recipes |
| `loader.py` | Path-based recipe metadata utilities |
| `_api.py` | Orchestration API |
| `_cmd_rpc.py` | `run_python` callables for externalized recipe cmd scripts |
| `_recipe_ingredients.py` | `format_ingredients_table` + `LoadRecipeResult` TypedDicts |
| `_recipe_composition.py` | `_build_active_recipe` + sub-recipe merging |
| `diagrams.py` | Flow diagram generation + staleness detection |
| `experiment_type_registry.py` | `ExperimentTypeSpec`, `load_all_experiment_types` |
| `methodology_tradition_registry.py` | `MethodologyTraditionSpec`, `VenueAppendixDef`, `load_all_methodology_traditions`, `get_methodology_tradition_by_name`, `is_out_of_scope_tradition` |
| `methodology_venue_appendix.py` | `AlternateParentDef`, `MLSubAreaFoldingEntry`, `VenueAppendixMatch`, `load_ml_sub_area_folding`, `resolve_venue_appendices` — Stage B venue-appendix resolution |
| `methodology_tradition_router.py` | `TraditionRouterResult`, `UnionRuleDef`, `classify_methodology` — two-stage Tier-C router |
| `methodology_disambiguation.py` | `DisambiguationRuleDef`, `CrossTraditionOverlapDef`, `DisambiguationResult`, `disambiguate`, `load_disambiguation_rules` |
| `registry.py` | `RuleFinding`, `RuleSpec`, `semantic_rule` decorator |
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
| `validator.py` | `validate_recipe`, `analyze_dataflow` |

## Architecture Notes

`registry.py` uses the `@semantic_rule` decorator pattern (same side-effect registration
as `rules/`). The `_analysis_*.py` modules form an internal BFS-based dataflow analysis
pipeline; callers use `make_validation_context` as the sole entry point.
