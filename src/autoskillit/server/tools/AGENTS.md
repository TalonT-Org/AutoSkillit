# tools/

MCP `@mcp.tool()` handlers registered on import (20 tool modules).

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Docstring-only — tools register via `@mcp.tool()` on import |
| `_auto_overrides.py` | Shared `_build_auto_overrides()` factory for server-authoritative ingredient injection |
| `_authority_feedback.py` | Authority-clobber warning builder and structured rejection envelope constructor for server-authoritative ingredient violations (single source of truth shared by open_kitchen, load_recipe, lock_ingredients) |
| `_cancellation_shield.py` | `_cancellation_shield` decorator — catches `asyncio.CancelledError` at MCP tool boundary, returns structured JSON |
| `_types.py` | TypedDict definitions for server tool JSON responses (RunSkillResult, RunCmdResult, ToolFailureEnvelope, etc.) and failure envelope factory helpers |
| `tools_kitchen.py` | `open_kitchen`, `close_kitchen` (gate lifecycle), `recipe://` MCP resource |
| `tools_config.py` | `configure_fleet`, `configure_order` (session config overlay) |
| `tools_agents.py` | `unlock_agent_pack` tool + `agent://` resource templates |
| `tools_ci.py` | `set_commit_status`, `check_repo_merge_state` |
| `tools_ci_watch.py` | `wait_for_ci`, `get_ci_status`, `_auto_trigger_ci` |
| `tools_ci_merge_queue.py` | `toggle_auto_merge`, `enqueue_pr`, `wait_for_merge_queue` |
| `tools_clone.py` | `clone_repo`, `remove_clone`, `push_to_remote`, `register_clone_status`, `batch_cleanup_clones`, `bootstrap_clone` |
| `_claim_helpers.py` | `ClaimDecision`, `_try_claim_with_liveness`, `_get_campaign_state_paths` — shared claiming logic for `claim_issue` and `claim_and_resolve_issue` |
| `_execution_helpers.py` | `_import_and_call`, `_coerce_scalar`, `maybe_promote_work_dir`, `validate_path_arg_anchoring`, `resolve_relative_path_args` — execution helpers for `run_python` (no MCP tools) |
| `_pipeline_deps.py` | `_derive_phase_a_deps` — curated Phase A pipeline dependency derivation from a recipe's routing graph (`open_kitchen` auto-init) |
| `_preflight.py` | `_check_dispatch_feasibility`, `_get_fix_required_hook_matchers`, `filter_steps_by_post_prune` — shared preflight for `open_kitchen` and `dispatch_food_truck` |
| `_ordering_telemetry.py` | `detect_ordering_violations`, `read_session_index_records` — REVIEW_BEFORE_PLAN ordering-violation detection over `sessions.jsonl` records |
| `_serve_helpers.py` | Shared serve-pipeline helpers: `_build_serve_override_stack()`, `_resolve_serve_defer_unresolved()`, `serve_recipe()` — the only legal call site for `load_and_validate` in `server/tools/` |
| `tools_execution.py` | `run_cmd`, `run_python`, `run_skill` |
| `tools_fleet_dispatch.py` | `dispatch_food_truck`, `record_gate_dispatch` |
| `tools_fleet_reset.py` | `reset_dispatch` (full dispatch artifact cleanup) |
| `tools_git.py` | `merge_worktree`, `classify_fix`, `create_unique_branch`, `create_and_publish_branch`, `check_pr_mergeable` |
| `tools_github.py` | `fetch_github_issue`, `get_issue_title`, `report_bug` |
| `tools_issue_headless.py` | `prepare_issue`, `enrich_issues` (headless session tools) |
| `tools_issue_labels.py` | `claim_issue`, `release_issue` (GitHub label management) |
| `tools_issue_composite.py` | `claim_and_resolve_issue` |
| `tools_pr_ops.py` | `get_pr_reviews`, `bulk_close_issues` |
| `tools_recipe.py` | `load_recipe`, `list_recipes`, `validate_recipe`, `migrate_recipe` |
| `tools_status.py` | `kitchen_status`, `get_pipeline_report`, `get_token_summary`, `get_timing_summary`, `analyze_tool_sequences`, `get_quota_events`, `write_telemetry_files`, `read_db` |
| `tools_pipeline_tracker.py` | `record_pipeline_step` (pipeline step tracker init/status) |
| `tools_workspace.py` | `test_check`, `reset_test_dir`, `reset_workspace` |

## Test Files

| File | Purpose |
|------|---------|
| `test_tools_agents.py` | Tests for agent pack registry, MCP resources, and `unlock_agent_pack` |

## Architecture Notes

Side-effect registration (same pattern as `recipe/rules/`). The `server/__init__.py` owns the `mcp` app object; tool modules import it from the server layer. All tools require `readOnlyHint: True` (see `server/AGENTS.md`).
