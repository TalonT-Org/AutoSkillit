# recipe/

Recipe I/O, validation, semantic rules, schema, and bundled recipe tests.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | empty |
| `conftest.py` | Shared fixtures for tests/recipe/ |
| `test_analysis_public_api.py` | Tests for the recipe analysis public API surface |
| `test_anti_pattern_guards.py` | Guards for anti-patterns in recipe definitions |
| `test_api.py` | Tests for recipe/_api.py orchestration API |
| `test_api_split.py` | Structural guard for recipe API split |
| `test_bem_wrapper_structure.py` | Tests for BEM wrapper recipe structure |
| `test_bundled_model_field.py` | Tests that all run_skill steps declare a model: field across bundled recipes |
| `test_bundled_recipe_hidden_policy.py` | Tests for hidden policy in bundled recipes |
| `test_bundled_recipes_general.py` | General structural tests for all bundled recipes |
| `test_bundled_recipes_no_inversions.py` | Guard: no inversion step order violations in bundled recipes |
| `test_bundled_recipes_pipeline_structure.py` | Pipeline structure tests for bundled recipes |
| `test_bundled_recipes_research.py` | Tests for research bundled recipe structure |
| `test_bundled_recipes_research_design.py` | Tests for research-design bundled recipe structure |
| `test_bundled_recipes_review_pr.py` | Tests for review-pr bundled recipe structure |
| `test_callable_contracts.py` | Contract tests for run_python callable inputs and outputs |
| `test_campaign_loader.py` | Tests for campaign recipe loading |
| `test_check_ci_already_passed_routing.py` | Tests for check_ci_already_passed routing logic |
| `test_check_repo_merge_state_routing.py` | Tests for check_repo_merge_state routing logic |
| `test_cmd_rpc.py` | Tests for recipe/_cmd_rpc.py run_python callables |
| `test_cmd_rpc_null_safety.py` | Null safety tests for _cmd_rpc callables |
| `test_contract_verdict_output_required.py` | Contract: verdict output is required in recipe step |
| `test_contracts.py` | Contract tests for recipe schema and recipe step contracts |
| `test_contracts_block_fingerprint.py` | Tests for block fingerprint contract |
| `test_diagnose_ci_subtype_output.py` | Tests for CI subtype diagnosis output |
| `test_diagrams.py` | Tests for recipe flow diagram generation and staleness detection |
| `test_experiment_type_registry.py` | Tests for ExperimentTypeSpec and load_all_experiment_types |
| `test_full_audit_recipe.py` | Tests for the full audit recipe structure |
| `test_hidden_ingredients.py` | Tests for hidden ingredient handling in recipes |
| `test_identity.py` | Tests for recipe identity hashing (content and composite fingerprints) |
| `test_implement_findings_recipe.py` | Tests for implement-findings recipe structure |
| `test_implementation.py` | Tests for implementation recipe structure |
| `test_implementation_groups_pr_decomposition.py` | Tests for implementation groups PR decomposition |
| `test_implementation_groups_review_loop.py` | Tests for implementation groups review loop structure |
| `test_implementation_pr_decomposition.py` | Tests for implementation PR decomposition recipe |
| `test_io_discovery.py` | Tests for recipe I/O discovery (list_recipes, recipe iteration) |
| `test_io_parsing.py` | Tests for recipe YAML parsing and load_recipe |
| `test_io_schema_fields.py` | Tests for recipe schema field validation in I/O layer |
| `test_issue_url_pipeline.py` | Tests for issue URL pipeline in recipe steps |
| `test_loader.py` | Tests for path-based recipe metadata utilities |
| `test_make_campaign_output_schema.py` | Tests for make-campaign recipe output schema |
| `test_merge_prs.py` | Tests for merge-prs recipe structure |
| `test_merge_prs_queue_any.py` | Tests for merge-prs-queue (any strategy) recipe |
| `test_merge_prs_queue_common.py` | Shared queue behavior tests across all queue-capable recipes |
| `test_merge_prs_queue_pmp.py` | Tests for merge-prs-queue (PMP strategy) recipe |
| `test_merge_prs_queue_release_timeout.py` | Release timeout and ci-watch-post-queue-fix retry logic tests |
| `test_merge_sub_recipe_hidden.py` | Tests for hidden sub-recipe merging |
| `test_plan_visualization_step.py` | Tests for plan visualization step in recipes |
| `test_planner_recipe.py` | Tests for the planner recipe structure |
| `test_promote_to_main_wrapper.py` | Tests for the promote-to-main wrapper recipe |
| `test_recipe_ci_watch_event.py` | Tests for CI watch event in recipe steps |
| `test_recipe_order.py` | Tests for BUNDLED_RECIPE_ORDER stable display order registry |
| `test_recipe_scripts.py` | Tests for recipe script callables |
| `test_recipe_temp_substitution.py` | Tests for {{AUTOSKILLIT_TEMP}} substitution in recipe steps |
| `test_remediation_depth_ingredient.py` | Tests for remediation depth ingredient configuration |
| `test_remediation_pr_decomposition.py` | Tests for remediation PR decomposition recipe |
| `test_remediation_recipe.py` | Tests for the remediation recipe structure |
| `test_repository.py` | Tests for DefaultRecipeRepository |
| `test_research_bundle_lifecycle.py` | Tests for research bundle lifecycle in recipes |
| `test_research_context_tracking.py` | Tests for research context tracking in recipes |
| `test_research_implement_recipe.py` | Tests for research-implement sub-recipe structure |
| `test_research_output_mode.py` | Tests for research output mode configuration |
| `test_research_recipe_diag.py` | Tests for research recipe diagnostic output |
| `test_research_review_recipe.py` | Tests for research-review sub-recipe structure |
| `test_research_stage_data_step.py` | Tests for stage-data step in research recipes |
| `test_resolve_ci_routing_invariant.py` | Tests for CI routing invariant in resolve steps |
| `test_review_loop_routing_integration.py` | Integration tests for review loop routing |
| `test_rule_decomposition.py` | Tests for semantic rule decomposition structure |
| `test_rules_actions.py` | Tests for actions semantic validation rule |
| `test_rules_blocks.py` | Tests for blocks semantic validation rule |
| `test_rules_bypass.py` | Tests for bypass semantic validation rule |
| `test_rules_callable_inputs.py` | Tests for callable_inputs semantic validation rule |
| `test_rules_campaign.py` | Tests for campaign semantic validation rule |
| `test_rules_ci.py` | Tests for CI semantic validation rule |
| `test_rules_clone.py` | Tests for clone semantic validation rule |
| `test_rules_cmd.py` | Tests for cmd semantic validation rule |
| `test_rules_conditional_push.py` | Tests for conditional_push semantic validation rule |
| `test_rules_contracts.py` | Tests for contracts semantic validation rule |
| `test_rules_dataflow_capture.py` | Tests for dataflow capture semantic validation rule |
| `test_rules_dataflow_handoff.py` | Tests for dataflow handoff semantic validation rule |
| `test_rules_dataflow_merge.py` | Tests for dataflow merge semantic validation rule |
| `test_rules_dataflow_nullable.py` | Tests for dataflow nullable semantic validation rule |
| `test_rules_dedup.py` | Tests for dedup semantic validation rule |
| `test_rules_features.py` | Tests for features semantic validation rule |
| `test_rules_graph.py` | Tests for graph semantic validation rule |
| `test_rules_inline_script.py` | Tests for inline_script semantic validation rule |
| `test_rules_inputs.py` | Tests for inputs semantic validation rule |
| `test_rules_integration_predicate.py` | Tests for integration_predicate semantic validation rule |
| `test_rules_isolation.py` | Tests for isolation semantic validation rule |
| `test_rules_merge.py` | Tests for merge semantic validation rule |
| `test_rules_merge_base_unpublished.py` | Tests for merge_base_unpublished semantic validation rule |
| `test_rules_merge_queue_push.py` | Tests for merge_queue_push semantic validation rule |
| `test_rules_merge_routing_incomplete.py` | Tests for merge_routing_incomplete semantic validation rule |
| `test_rules_multipart_iteration.py` | Tests for multipart_iteration semantic validation rule |
| `test_rules_on_context_limit.py` | Tests for on_context_limit semantic validation rule |
| `test_rules_on_result_failure_route.py` | Tests for on_result_failure_route semantic validation rule |
| `test_rules_outdated_script_version.py` | Tests for outdated_script_version semantic validation rule |
| `test_rules_packs.py` | Tests for packs semantic validation rule |
| `test_rules_pipeline_internal.py` | Tests for pipeline_internal semantic validation rule |
| `test_rules_predicate_routing.py` | Tests for predicate_routing semantic validation rule |
| `test_rules_project_local_override.py` | Tests for project_local_override semantic validation rule |
| `test_rules_reachability.py` | Tests for reachability semantic validation rule |
| `test_rules_recipe.py` | Tests for recipe-level semantic validation rule |
| `test_rules_registry.py` | Tests for rule registry and decorator |
| `test_rules_shadowed_input.py` | Tests for shadowed_input semantic validation rule |
| `test_rules_skill_command_prefix.py` | Tests for skill_command_prefix semantic validation rule |
| `test_rules_skill_content.py` | Tests for skill_content semantic validation rule |
| `test_rules_skills.py` | Tests for skills semantic validation rule |
| `test_rules_subset_disabled.py` | Tests for subset_disabled semantic validation rule |
| `test_rules_temp_path.py` | Tests for temp_path semantic validation rule |
| `test_rules_tools.py` | Tests for tools semantic validation rule |
| `test_rules_unreachable_model.py` | Tests for unreachable_model semantic validation rule |
| `test_rules_unsatisfied_input.py` | Tests for unsatisfied_input semantic validation rule |
| `test_rules_verdict.py` | Tests for verdict semantic validation rule |
| `test_rules_weak_constraint.py` | Tests for weak_constraint semantic validation rule |
| `test_rules_worktree.py` | Tests for worktree semantic validation rule |
| `test_schema.py` | Tests for Recipe, RecipeStep, and DataFlowWarning schema |
| `test_skill_emit_consistency.py` | Tests for skill emit consistency in recipe steps |
| `test_staleness_cache.py` | Tests for recipe staleness cache |
| `test_sub_recipe_loading.py` | Tests for sub-recipe loading and composition |
| `test_sub_recipe_schema.py` | Tests for sub-recipe schema structure |
| `test_sub_recipe_validation.py` | Tests for sub-recipe validation |
| `test_validator_dataflow.py` | Tests for recipe validator dataflow analysis |
| `test_validator_graph_and_actions.py` | Tests for recipe validator graph and actions analysis |
| `test_validator_skill_hints.py` | Tests for recipe validator skill hints |
| `test_validator_structural.py` | Tests for recipe validator structural analysis |

## Architecture Notes

`conftest.py` provides shared fixtures for recipe tests. The `fixtures/` subdirectory contains YAML test data files including sample recipes and expected diagram output. The `test_rules_*.py` files each test a single semantic validation rule from `recipe/rules/`.
