# hooks/

Hook script behavior, registration, and bridge tests.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | empty |
| `test_fmt_status.py` | Tests for autoskillit.hooks.formatters._fmt_status |
| `test_hook_dispatch.py` | Tests for the stable hook dispatcher (_dispatch.py) — resolution, retired mapping, graceful degrade |
| `test_hook_config_bridge.py` | Regression tests for the quota_guard.py → .hook_config.json bridge |
| `test_hook_executability.py` | Tests for hook command executability — validates invocation path via subprocess |
| `test_hook_registration_coverage.py` | Structural test: every hook script is registered in HOOK_REGISTRY |
| `test_hook_registry.py` | Tests for hook_registry.py — L0 hook identity model |
| `test_hook_settings.py` | Tests for the shared stdlib-only quota hook settings resolver |
| `test_hook_sync.py` | Sync tests: verify parallel stdlib-only hook scripts stay aligned with server code |
| `test_lint_after_edit_hook.py` | Tests for lint_after_edit_hook.py PostToolUse hook |
| `test_hook_output_contract.py` | Contract tests: no PostToolUse hook forwards raw tool_response in output |
| `test_quota_check.py` | Tests for the quota_check PreToolUse hook |
| `test_quota_post_check.py` | Tests for the quota_post_check PostToolUse hook |
| `test_recipe_write_advisor.py` | Tests for autoskillit.hooks.guards.recipe_write_advisor |
| `test_review_gate_post_hook.py` | Tests for the review_gate_post_hook PostToolUse hook |
| `test_review_loop_gate.py` | Tests for the review_loop_gate PreToolUse hook |
| `test_session_start_hook.py` | Session-scope enforcement tests for session_start_hook.py |
| `test_session_start_reminder.py` | Tests for the SessionStart hook — session_start_hook.py |
| `test_skill_load_post_hook.py` | Tests for skill_load_post_hook.py PostToolUse hook |
| `test_token_summary_appender.py` | Token summary hook script existence and source quality (2 tests); behavioral and unit tests live in tests/infra/ |
| `test_write_guard.py` | Tests for write_guard.py PreToolUse hook |
| `test_planner_result_naming_guard.py` | Tests for planner_result_naming_guard.py PreToolUse hook |
| `test_codex_hooks.py` | Tests for cli/_hooks_codex.py — AST scan, hook generation, sync idempotency |
| `test_recipe_contract_freshness.py` | Tests for the recipe-contract-freshness pre-commit hook |
| `test_command_classification.py` | Tests for the shared _command_classification.py command classification primitives |
| `test_pr_create_guard.py` | Tests for pr_create_guard.py interpreter bypass detection |
| `test_planner_gh_discovery_guard.py` | Tests for planner_gh_discovery_guard.py interpreter bypass detection |
| `test_ingredient_lock_guard.py` | Tests for ingredient_lock_guard.py PreToolUse hook: deny/allow, fail-open, pipeline scoping |
| `test_remove_clone_guard_isolation.py` | Integration tests for remove_clone_guard _check_sync with clone-isolated origin topology |
| `test_pipeline_step_post_hook.py` | Tests for pipeline_step_post_hook.py PostToolUse hook — step completion marking, progress banner, fail-open paths |
| `test_pipeline_step_guard.py` | Tests for pipeline_step_guard.py PreToolUse advisory guard — dep-unmet warning, fail-open paths |

## Architecture Notes

`test_token_summary_appender.py` contains only script-existence and source-quality checks (2 tests). All behavioral tests (early exit, PR editing, fail-open, efficiency table), unit tests (`_canonical`, `_humanize`, `_format_table`, `_unwrap_mcp_response`), and order_id isolation tests live in `tests/infra/test_token_summary_core.py`, `tests/infra/test_token_summary_filters.py`, and `tests/infra/test_token_summary_v1_compat.py`. Shared helpers are in `tests/infra/_token_summary_helpers.py`.
