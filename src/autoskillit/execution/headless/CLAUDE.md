# headless/

Headless Claude session orchestration — command prep, subprocess invocation, result construction.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Main module: `run_headless_core()`, `DefaultHeadlessExecutor`, `_execute_claude_headless()` |
| `_headless_git.py` | Git LOC tracking: `_capture_git_head_sha()`, `_compute_loc_changed()` |
| `_headless_path_tokens.py` | Path-token extraction and output-path validation from assistant messages |
| `_headless_recovery.py` | Session recovery: `_recover_from_separate_marker`, `_synthesize_from_write_artifacts` |
| `_headless_result.py` | `SkillResult` construction: `_build_skill_result`, `_build_session_telemetry`, `_apply_budget_guard` |
| `_headless_scan.py` | `_scan_jsonl_write_paths()` — scans stdout JSONL for Write/Edit/Bash tool calls |

## Architecture Notes

The `__init__.py` IS the main module body (not a thin facade). It uses a deferred import for `flush_session_log` to avoid circular imports. `_execute_claude_headless` is the shared path for both `run_skill` (skill session) and `dispatch_food_truck` (fleet) flows.
