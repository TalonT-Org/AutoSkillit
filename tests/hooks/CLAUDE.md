# hooks/

Hook script behavior, registration, and bridge tests.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | empty |
| `test_fmt_status.py` | Tests for autoskillit.hooks.formatters._fmt_status |
| `test_hook_config_bridge.py` | Regression tests for the quota_guard.py → .hook_config.json bridge |
| `test_hook_executability.py` | Tests for hook command executability — validates invocation path via subprocess |
| `test_hook_registration_coverage.py` | Structural test: every hook script is registered in HOOK_REGISTRY |
| `test_hook_registry.py` | Tests for hook_registry.py — L0 hook identity model |
| `test_hook_settings.py` | Tests for the shared stdlib-only quota hook settings resolver |
| `test_hook_sync.py` | Sync tests: verify parallel stdlib-only hook scripts stay aligned with server code |
| `test_lint_after_edit_hook.py` | Tests for lint_after_edit_hook.py PostToolUse hook |
| `test_quota_check.py` | Tests for the quota_check PreToolUse hook |
| `test_quota_post_check.py` | Tests for the quota_post_check PostToolUse hook |
| `test_recipe_write_advisor.py` | Tests for autoskillit.hooks.guards.recipe_write_advisor |
| `test_review_gate_post_hook.py` | Tests for the review_gate_post_hook PostToolUse hook |
| `test_review_loop_gate.py` | Tests for the review_loop_gate PreToolUse hook |
| `test_session_start_reminder.py` | Tests for the SessionStart hook — session_start_hook.py |
| `test_token_summary_appender.py` | Tests for the token_summary_appender PostToolUse hook |
| `test_write_guard.py` | Tests for write_guard.py PreToolUse hook |

## Architecture Notes

`test_token_summary_appender.py` covers the hook registration and end-to-end behavior. The related split test files (`test_token_summary_core.py`, `test_token_summary_filters.py`) and shared helpers (`_token_summary_helpers.py`) live in `tests/infra/` — see `tests/infra/CLAUDE.md`.
