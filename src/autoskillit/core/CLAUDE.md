# core/

IL-0 foundation layer — zero autoskillit imports; safe for import from hook subprocesses.
Sub-packages: types/ (see types/CLAUDE.md) and runtime/ (see runtime/CLAUDE.md).

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Re-exports public surface |
| `io.py` | `atomic_write`, `ensure_project_temp`, YAML helpers |
| `logging.py` | Logging configuration |
| `paths.py` | `pkg_root()`, `is_git_worktree()` |
| `_claude_env.py` | IDE-scrubbing canonical env builder for claude subprocesses |
| `_terminal_table.py` | IL-0 color-agnostic terminal table primitive |
| `_version_snapshot.py` | Process-scoped version snapshot for session telemetry (`lru_cache`'d) |
| `branch_guard.py` | Branch protection helpers |
| `claude_conventions.py` | Skill discovery directory layout constants |
| `github_url.py` | `parse_github_repo` |
| `_plugin_cache.py` | Plugin cache lifecycle: retiring cache, install locking, kitchen registry |
| `_plugin_ids.py` | `DIRECT_PREFIX`, `MARKETPLACE_PREFIX`, `detect_autoskillit_mcp_prefix` (stdlib-only) |
| `_install_detect.py` | `is_dev_install()` — editable-install detection for config resolution |
| `feature_flags.py` | `is_feature_enabled()` — IL-0 feature gate resolution primitive |
| `tool_sequence_analysis.py` | Cross-session tool call sequence DFG analysis (stdlib-only) |

## Architecture Notes

All modules are importable without any `autoskillit` package imports (IL-0 hard constraint).
Production code imports from `autoskillit.core`, not from sub-packages directly.
`_terminal_table.py` is re-exported by `cli/_terminal_table.py` as a shim.
