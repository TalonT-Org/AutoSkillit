# core/

IL-0 foundation layer — zero autoskillit imports; safe for import from hook subprocesses.
Sub-packages: types/ (see types/CLAUDE.md) and runtime/ (see runtime/CLAUDE.md).

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Re-exports public surface |
| `io.py` | `atomic_write`, `ensure_project_temp`, YAML helpers |
| `_cmd_runner.py` | `CmdRunner` protocol, `default_cmd_runner`, `run_git`, `run_gh` — sync subprocess for git/gh CLI |
| `_json.py` | Fast JSON via orjson (with stdlib fallback) — `fast_loads`, `fast_dumps` |
| `logging.py` | Logging configuration |
| `paths.py` | `pkg_root()`, `is_git_worktree()`, `is_git_main_checkout()`, `is_in_git_repo()` |
| `_claude_env.py` | IDE-scrubbing canonical env builder for agent subprocesses |
| `_terminal_table.py` | IL-0 color-agnostic terminal table primitive |
| `_version_snapshot.py` | Process-scoped version snapshot for session telemetry (`lru_cache`'d) |
| `branch_guard.py` | Branch protection helpers |
| `claude_conventions.py` | Skill discovery directory layout constants |
| `github_url.py` | `parse_github_repo` |
| `_plugin_cache.py` | Plugin cache lifecycle: retiring cache, install locking, kitchen registry |
| `_plugin_ids.py` | `DIRECT_PREFIX`, `MARKETPLACE_PREFIX`, `detect_autoskillit_mcp_prefix` (stdlib-only) |
| `_install_detect.py` | `is_dev_install()` — editable-install detection for config resolution |
| `_execution_marker.py` | `execution_marker` async context manager — unified write/heartbeat/cleanup for stale-detector suppression markers |
| `_step_context.py` | `current_step_name`, `current_order_id` ContextVars for pipeline step attribution |
| `feature_flags.py` | `is_feature_enabled()` — IL-0 feature gate resolution primitive |
| `tool_sequence_analysis.py` | Cross-session tool call sequence DFG analysis (stdlib-only) |

## Architecture Notes

All modules are importable without any `autoskillit` package imports (IL-0 hard constraint).
Production code imports from `autoskillit.core`, not from sub-packages directly.
`_terminal_table.py` is re-exported by `cli/_terminal_table.py` as a shim.
