# headless/

Headless Claude session orchestration — command prep, subprocess invocation, result construction.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Facade: `run_headless_core()`, `DefaultHeadlessExecutor`, re-exports |
| `_headless_helpers.py` | Session helpers: `_session_log_dir`, `_resolve_model`, `PostSessionMetrics` |
| `_headless_execute.py` | Subprocess core: `_execute_claude_headless()` — shared skill/fleet path |
| `_headless_git.py` | Git LOC tracking: `_capture_git_head_sha()`, `_compute_loc_changed()` |
| `_headless_path_tokens.py` | Path-token extraction and output-path validation from assistant messages |
| `_headless_recovery.py` | Session recovery: `_recover_from_separate_marker`, `_synthesize_from_write_artifacts` |
| `_headless_result.py` | `SkillResult` construction: `_build_skill_result` (evidence/telemetry moved to `_headless_evidence.py`) |
| `_headless_evidence.py` | Evidence computation (`_compute_write_evidence`, `_adapt_agent_result`), audit recording (`_capture_failure`, `_apply_budget_guard`), telemetry builders (`_build_session_telemetry`, `_build_error_path_telemetry`) |
| `_headless_scan.py` | `_scan_jsonl_write_paths()` — scans stdout JSONL for Write/Edit/Bash tool calls; uses `core/bash_write_targets` for precise write-target extraction |

## Architecture Notes

The `__init__.py` is a facade with public API (`run_headless_core`, `DefaultHeadlessExecutor`) and re-exports from all submodules. `_execute_claude_headless` in `_headless_execute.py` is the shared subprocess execution path for both `run_skill` (leaf) and `dispatch_food_truck` (fleet) flows. It uses a deferred import for `flush_session_log` to avoid circular imports.
