# server/

Server tool handler unit tests — kitchen, execution, CI, clone, workspace tools.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | empty |
| `_helpers.py` | Shared test builder utilities for tests/server/ |
| `conftest.py` | Shared fixtures for tests/server/ |
| `test_editable_guard.py` | Unit tests for server/_editable_guard.py — scan_editable_installs_for_worktree |
| `test_factory.py` | Tests for server/_factory.py make_context() composition root |
| `test_factory_recording.py` | Tests for make_context recording/replay runner wiring |
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
| `test_quota_refresh_loop.py` | Tests for _quota_refresh_loop in server/_misc.py |
| `test_release_issue_fail_label.py` | Tests for release_issue fail_label path and fail label cleanup |
| `test_reload_session.py` | Tests for the reload_session MCP tool and supporting helpers |
| `test_resolve_provider_profile.py` | Tests for _resolve_provider_profile four-tier provider resolution in _guards.py |
| `test_run_skill_add_dirs.py` | Contract tests: run_skill passes correct add_dirs to executor (T-OVR-014) |
| `test_server_init_gate.py` | Tests for server init: gate access, visibility, subset management, wire format compliance |
| `test_server_init_session_visibility.py` | Tests for server init: session type visibility, fleet gate boot, feature gate visibility |
| `test_server_tool_registration.py` | Tests for MCP tool registration, config-driven behavior, and schema contracts |
| `test_server_version_telemetry.py` | Tests for server version info, plugin metadata, lazy init, and telemetry |
| `test_service_wrappers.py` | Behavior tests for DefaultRecipeRepository and DefaultMigrationService (REQ-ARCH-006, 007) |
| `test_session_type_tags.py` | Tests for _collect_fleet_tool_tags in server._session_type (Finding 1) |
| `test_set_commit_status.py` | Tests for the set_commit_status MCP tool handler |
| `test_smoke_pipeline.py` | Smoke-test pipeline: structural validation and end-to-end execution tests |
| `test_state.py` | Tests for server/_state.py: server initialization |
| `test_tool_annotation_completeness.py` | Runtime annotation test shield for MCP tool readOnlyHint semantics (layers 2, 3, 4) |
| `test_tool_exception_boundary.py` | Tests for the exception boundary in track_response_size |
| `test_tools_ci.py` | Tests for wait_for_ci, get_ci_status, and wait_for_merge_queue MCP tool handlers |
| `test_tools_ci_enqueue.py` | Tests for enqueue_pr MCP tool handler |
| `test_tools_ci_split.py` | CI split structural guard |
| `test_tools_ci_watch.py` | Tests for wait_for_ci event validation and null coercion |
| `test_tools_clone.py` | Tests for autoskillit server clone tools |
| `test_tools_dispatch.py` | Tests for dispatch_food_truck tool handler and execute_dispatch domain function |
| `test_tools_execution_command.py` | Tests for run_skill command building, timeouts, env, model, and per-invocation markers |
| `test_tools_execution_input_gates.py` | Tests for run_skill input validation gates and CWD checking |
| `test_tools_execution_response.py` | Contract tests: MCP tool response fields use correct enum types |
| `test_tools_execution_results.py` | Tests for run_skill result shapes, failure paths, timing, flush telemetry, and gate checks |
| `test_tools_execution_routing.py` | Tests for run_skill routing, executor delegation, and session skill management |
| `test_tools_git.py` | Tests for classify_fix and merge_worktree MCP tools |
| `test_tools_github.py` | Tests for server/tools_github.py — fetch_github_issue and get_issue_title |
| `test_tools_github_api_tracking.py` | GitHub API tracking tests |
| `test_tools_integrations.py` | Integration tests for issue lifecycle, headless tool diagnostics, and PR ops |
| `test_tools_integrations_release.py` | Tests for release_issue staged lifecycle behaviour |
| `test_tools_issue_lifecycle.py` | Tests for server/tools_issue_lifecycle.py |
| `test_tools_kitchen_envelope.py` | Tests for tools_kitchen.py: hook drift warnings and failure envelopes |
| `test_tools_kitchen_gate.py` | Tests for tools_kitchen.py: gate mechanics, hook config, recipe packs, refresh, misc |
| `test_tools_kitchen_visibility.py` | Tests for tools_kitchen.py: visibility, component management, sous-chef, redisable_subsets |
| `test_tools_label_validation.py` | Tests for label whitelist validation in server tool handlers |
| `test_tools_list_recipes.py` | Tests for autoskillit server list_recipes tool |
| `test_tools_load_recipe.py` | Tests for autoskillit server load_recipe and migrate_recipe tools |
| `test_tools_pr_ops.py` | Tests for server/tools_pr_ops.py |
| `test_tools_recipe.py` | Tests for autoskillit server validate_recipe tool and recipe docstring contracts |
| `test_tools_run_cmd.py` | Tests for run_cmd and run_python MCP tool handlers |
| `test_tools_run_cmd_unit.py` | Unit tests for run_cmd: observability, timing, and headless gate enforcement |
| `test_tools_run_python.py` | Unit tests for run_python: observability and headless gate enforcement |
| `test_tools_bootstrap.py` | Tests for bootstrap composite MCP tools (bootstrap_clone, claim_and_resolve_issue, create_and_publish_branch) |
| `test_tools_run_skill_retry.py` | Tests verifying run_skill_retry was removed and run_skill handles all sessions |
| `test_tools_session_diagnostics.py` | Tests for session diagnostics helpers in tools_github |
| `test_tools_status_kitchen.py` | Tests for server status tools: kitchen status, pipeline report, and telemetry recovery |
| `test_tools_status_mcp_response.py` | Tests for MCP response tracking integration in tools_status handlers |
| `test_tools_status_quota_and_db.py` | Tests for server status tools: quota events, telemetry writing, and DB access |
| `test_tools_status_summaries.py` | Tests for server status tools: token and timing summaries |
| `test_tools_workspace.py` | Tests for autoskillit server workspace tools |
| `test_track_response_size.py` | Tests for the track_response_size decorator in autoskillit.server._notify |
| `test_wire_compat.py` | Wire compatibility tests |

## Architecture Notes

`conftest.py` provides shared fixtures including `tool_ctx` (full-stack L3 context) used across server tests. `_helpers.py` provides shared test builder utilities. The `test_tools_execution_*.py` files test run_skill in focused slices (command, input gates, response, results, routing).
