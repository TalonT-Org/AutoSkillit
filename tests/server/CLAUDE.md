# server/

Server tool handler unit tests — kitchen, execution, CI, clone, workspace tools.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | empty |
| `_helpers.py` | Shared test builder utilities for tests/server/ |
| `_type_coercion_fixtures.py` | Test fixtures for _import_and_call annotation-aware type coercion |
| `conftest.py` | Shared fixtures for tests/server/ |
| `test_editable_guard.py` | Unit tests for server/_editable_guard.py — scan_editable_installs_for_worktree |
| `test_factory.py` | Tests for server/_factory.py make_context() composition root |
| `test_factory_recording.py` | Tests for make_context recording/replay runner wiring |
| `test_factory_backend_coherence.py` | Tests for backend coherence enforcement in make_context() |
| `test_factory_codex_backend_gate.py` | Tests for codex_backend feature flag gating in make_context() |
| `test_factory_guards_integration.py` | Integration tests for factory→guards ambient env contamination path |
| `test_git.py` | Tests for server/git.py perform_merge() |
| `test_git_merge_dirty_check.py` | Tests for the pre-merge dirty check in perform_merge (Layer 3) |
| `test_guards_module.py` | Smoke test: all 6 guards are importable from _guards |
| `test_headless_session.py` | Headless session kitchen visibility via AUTOSKILLIT_HEADLESS=1 |
| `test_helpers_gate.py` | Contract tests: server helpers gate response schema |
| `test_helpers_tier_guards.py` | Tests for tier-aware guard helpers in server._guards |
| `test_kitchen_lifecycle.py` | Kitchen lifecycle tests |
| `test_lifespan.py` | Tests that the FastMCP lifespan calls recorder.finalize() on server shutdown |
| `test_lifespan_readiness_structural.py` | AST structural guard for _autoskillit_lifespan readiness invariants |
| `test_mcp_overrides.py` | Tests for MCP tool ingredient_overrides parameter propagation |
| `test_misc_module.py` | Contract tests: server._misc module |
| `test_no_raw_signal_handler.py` | AST guard: no raw signal.signal(SIGTERM, ...) in cli/app.py |
| `test_perform_merge_editable_guard.py` | Integration tests verifying perform_merge() aborts before cleanup on poisoned installs |
| `test_profile_to_env.py` | Tests for _profile_to_env — ProviderProfileDef to env dict conversion in _guards.py |
| `test_quota_refresh_loop.py` | Tests for _quota_refresh_loop in server/_misc.py |
| `test_release_issue_fail_label.py` | Tests for release_issue fail_label path and fail label cleanup |
| `test_reload_session.py` | Tests for the reload_session MCP tool and supporting helpers |
| `test_research_smoke_pipeline.py` | Research recipe smoke pipeline: gated E2E tests (RESEARCH_SMOKE_TEST=1) |
| `test_resolve_model_as_profile.py` | Tests for _resolve_model_as_profile — model-value-as-provider-profile resolution in _guards.py |
| `test_resolve_provider_profile.py` | Tests for _resolve_provider_profile six-tier provider resolution in _guards.py |
| `test_run_skill_add_dirs.py` | Contract tests: run_skill passes correct add_dirs to executor (T-OVR-014) |
| `test_run_skill_resume.py` | Tests for resume_session_id threading from run_skill through executor |
| `test_server_init_gate.py` | Tests for server init: gate access, visibility, subset management, wire format compliance |
| `test_server_init_session_visibility.py` | Tests for server init: session type visibility, fleet gate boot, feature gate visibility |
| `test_server_tool_registration.py` | Tests for MCP tool registration, config-driven behavior, and schema contracts |
| `test_server_version_telemetry.py` | Tests for server version info, plugin metadata, lazy init, and telemetry |
| `test_service_wrappers.py` | Behavior tests for DefaultRecipeRepository and DefaultMigrationService (REQ-ARCH-006, 007) |
| `test_session_type_tags.py` | Tests for _collect_fleet_tool_tags in server._session_type (Finding 1) |
| `test_set_commit_status.py` | Tests for the set_commit_status MCP tool handler |
| `test_smoke_pipeline.py` | Smoke-test pipeline: structural validation and end-to-end execution tests |
| `test_smoke_recipe_scope_guard.py` | Structural guard: smoke_recipe fixture must not use scope='module' |
| `test_state.py` | Tests for server/_state.py: server initialization |
| `test_tool_annotation_completeness.py` | Runtime annotation test shield for MCP tool readOnlyHint semantics (layers 2, 3, 4) |
| `test_tool_exception_boundary.py` | Tests for the exception boundary in track_response_size |
| `test_tools_ci.py` | Tests for wait_for_ci, get_ci_status, and wait_for_merge_queue MCP tool handlers |
| `test_tools_ci_enqueue.py` | Tests for enqueue_pr MCP tool handler |
| `test_tools_ci_merge_state.py` | Tests for check_repo_merge_state MCP tool handler |
| `test_tools_ci_split.py` | CI split structural guard |
| `test_tools_ci_watch.py` | Tests for wait_for_ci event validation, null coercion, and auto_trigger recovery |
| `test_tools_clone.py` | Tests for autoskillit server clone tools |
| `test_tools_dispatch.py` | Tests for dispatch_food_truck execution lifecycle: lock, success envelope, PID, quota, cleanup |
| `test_tools_dispatch_halt.py` | Tests for dispatch_food_truck campaign halt enforcement gate |
| `test_tools_dispatch_params.py` | Tests for dispatch_food_truck parameter passthrough: resume, idle_timeout, marketplace |
| `test_tools_dispatch_split.py` | Dispatch split structural guard |
| `test_tools_dispatch_validation.py` | Tests for dispatch_food_truck validation: gates, input, and semantic validation |
| `test_tools_execution_command.py` | Tests for run_skill command building, timeouts, env, model, and per-invocation markers |
| `test_tools_execution_input_gates.py` | Tests for run_skill input validation gates and CWD checking |
| `test_tools_execution_provider.py` | Tests for provider_extras/profile_name forwarding through run_skill() |
| `test_tools_execution_response.py` | Contract tests: MCP tool response fields use correct enum types |
| `test_tools_execution_results.py` | Tests for run_skill result shapes, failure paths, timing, flush telemetry, and gate checks |
| `test_tools_execution_routing.py` | Tests for run_skill routing, executor delegation, and session skill management |
| `test_tools_execution_step_resolution.py` | Tests for server-side recipe step parameter resolution in run_skill (output_dir, stale_threshold, idle_output_timeout, step_provider auto-filled from cached recipe step definitions) |
| `test_tools_execution_backend_mixing.py` | Integration tests for per-step backend mixing in run_skill() — Codex backend + ANTHROPIC_BASE_URL derives backend_override='claude-code' |
| `test_tools_execution_write_prefix.py` | Tests for allowed_write_prefix computation decoupled from read_only |
| `test_tools_git.py` | Tests for merge_worktree core flow: happy path, test gate, rebase abort, bypass prevention |
| `test_tools_git_branch.py` | Tests for create_unique_branch and check_pr_mergeable tools |
| `test_tools_git_classify_fix.py` | Tests for classify_fix tool |
| `test_tools_git_merge_cleanup.py` | Tests for merge_worktree cleanup reporting and warnings |
| `test_tools_git_merge_guards.py` | Tests for merge_worktree remote tracking guard, timing, and merge commit detection |
| `test_tools_git_split.py` | Git split structural guard |
| `test_tools_github.py` | Tests for server/tools_github.py — fetch_github_issue and get_issue_title |
| `test_tools_github_api_tracking.py` | GitHub API tracking tests |
| `test_tools_github_provider.py` | Tests for provider_name forwarding through the report_bug call chain |
| `test_tools_integrations.py` | Integration tests for issue lifecycle, headless tool diagnostics, and PR ops |
| `test_tools_integrations_release.py` | Tests for release_issue staged lifecycle behaviour |
| `test_claim_liveness.py` | Tests for liveness-aware claiming — dead dispatch recovery, alive dispatch blocking, shared helper parity |
| `test_tools_issue_lifecycle.py` | Tests for server/tools/tools_issue_headless.py and server/tools/tools_issue_labels.py |
| `test_tools_kitchen_cache_poison.py` | Cross-tool cache-poison regression: open_kitchen(ingredients_only=True) must not corrupt subsequent load_recipe calls for the same recipe |
| `test_tools_kitchen_envelope.py` | Tests for tools_kitchen.py: hook drift warnings and failure envelopes |
| `test_tools_kitchen_gate.py` | Tests for tools_kitchen.py: gate toggle, review gate cleanup, kitchen_id, misc |
| `test_tools_kitchen_gate_features.py` | Tests for tools_kitchen.py: recipe packs, quota refresh, ingredients_only, project_dir |
| `test_tools_kitchen_gate_hook_config.py` | Tests for tools_kitchen.py: hook config lifecycle, overlay, and quota guard tool |
| `test_tools_kitchen_gate_split.py` | Kitchen gate split structural guard |
| `test_tools_kitchen_visibility.py` | Tests for tools_kitchen.py: visibility, component management, sous-chef, redisable_subsets |
| `test_lock_ingredients.py` | Tests for the lock_ingredients MCP tool and _write_ingredient_locks helper |
| `test_run_skill_locks.py` | Tests for server-side ingredient lock enforcement in run_skill (incl. resume exemption) |
| `test_pipeline_tracker.py` | Tests for record_pipeline_step MCP tool — init, status, gaps, and get_pipeline_report tracker integration |
| `test_run_skill_pipeline_deps.py` | Tests for _check_pipeline_deps server-side pipeline dependency enforcement in run_skill |
| `test_no_path_cwd_in_tools.py` | Regression guard: Path.cwd() must not appear in server tool handlers |
| `test_open_kitchen_staleness.py` | Tests for ProcessStaleError propagation through open_kitchen — failure envelope with staleness context |
| `test_open_kitchen_deferred_recall.py` | Tests for active_recipe_steps assignment in the _is_deferred_recall=True path |
| `test_tools_label_validation.py` | Tests for label whitelist validation in server tool handlers |
| `test_tools_list_recipes.py` | Tests for autoskillit server list_recipes tool |
| `test_tools_load_recipe.py` | Tests for autoskillit server load_recipe tool |
| `test_tools_migrate_recipe.py` | Tests for autoskillit server migrate_recipe tool |
| `test_tools_pr_ops.py` | Tests for server/tools_pr_ops.py |
| `test_tools_recipe.py` | Tests for autoskillit server validate_recipe tool and recipe docstring contracts |
| `test_tools_report_bug.py` | Tests for report_bug MCP tool handler and supporting helpers (_parse_fingerprint, _extract_block, _parse_prepare_result, _parse_enrich_result) |
| `test_tools_run_cmd.py` | Tests for run_cmd and run_python MCP tool handlers |
| `test_tools_run_cmd_unit.py` | Unit tests for run_cmd: observability, timing, and headless gate enforcement |
| `test_tools_run_python.py` | Unit tests for run_python: observability and headless gate enforcement |
| `test_tools_run_python_cwd.py` | Tests for run_python work_dir path resolution: anchors relative output_dir to work_dir |
| `test_clone_result_exhaustiveness.py` | Structural test enforcing exhaustive CloneResult gate handling in _require_clone_success |
| `test_subprocess_validation.py` | Tests for _run_subprocess cwd validation (empty, relative, nonexistent paths) |
| `test_tools_bootstrap.py` | Tests for bootstrap composite MCP tools (bootstrap_clone, claim_and_resolve_issue, create_and_publish_branch) |
| `test_tools_config.py` | Tests for `configure_fleet` and `configure_order` MCP tools |
| `test_tools_run_skill_retry.py` | Tests verifying run_skill_retry was removed and run_skill handles all sessions |
| `test_tools_session_diagnostics.py` | Tests for session diagnostics helpers in tools_github |
| `test_tools_status_kitchen.py` | Tests for server status tools: kitchen status, pipeline report, and telemetry recovery |
| `test_tools_status_mcp_response.py` | Tests for MCP response tracking integration in tools_status handlers |
| `test_tools_status_quota_and_db.py` | Tests for server status tools: quota events, telemetry writing, and DB access |
| `test_tools_status_summaries.py` | Tests for server status tools: token and timing summaries |
| `test_tools_types_module.py` | Tests for server/tools/_types.py TypedDict imports |
| `test_tools_workspace.py` | Tests for autoskillit server workspace tools |
| `test_tools_agents.py` | Tests for agent pack registry, MCP resources, and `unlock_agent_pack` |
| `test_track_response_size.py` | Tests for the track_response_size decorator in autoskillit.server._notify |
| `test_wire_compat.py` | Wire compatibility tests |

| `test_tools_observability_scope.py` | Tests for bound_contextvars exception scope completeness: exception-path coverage, AST structural guard, any() assertion ban |
## Architecture Notes

`conftest.py` provides shared fixtures including `tool_ctx` (full-stack L3 context) used across server tests. `_helpers.py` provides shared test builder utilities. The `test_tools_execution_*.py` files test run_skill in focused slices (command, input gates, response, results, routing).
