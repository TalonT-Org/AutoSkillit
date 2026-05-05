# cli/

IL-3 CLI layer — entry points for all user-facing commands.
Sub-packages: doctor/ (see doctor/CLAUDE.md), fleet/ (see fleet/CLAUDE.md),
session/ (see session/CLAUDE.md), ui/ (see ui/CLAUDE.md), update/ (see update/CLAUDE.md).

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Package marker |
| `app.py` | CLI entry: `serve`, `init`, `config`, `skills`, `recipes`, `doctor`, `update`, etc. |
| `_restart.py` | `perform_restart()` → `NoReturn`: sets `SKIP_UPDATE_CHECK`, calls `os.execv` |
| `_hooks.py` | `PreToolUse` hook registration helpers |
| `_init_helpers.py` | `autoskillit init` implementation helpers |
| `_installed_plugins.py` | `InstalledPluginsFile` — canonical accessor for `installed_plugins.json` |
| `_install_info.py` | `InstallInfo`, `InstallType`, `detect_install()`, `comparison_branch()`, `dismissal_window()`, `upgrade_command()` |
| `_marketplace.py` | Plugin install/upgrade |
| `_mcp_names.py` | MCP prefix detection |
| `_onboarding.py` | First-run detection + guided menu |
| `_prompts.py` | Orchestrator prompt builder |
| `_preview.py` | Shared pre-launch preview: flow diagram + ingredient table display |
| `_serve_guard.py` | Async signal-guarded MCP server bootstrap (extracted from `app.py`) |
| `_features.py` | `features` subcommand group: list/status commands for feature gate inspection |
| `_workspace.py` | Workspace clean helpers |
| `_sessions.py` | `sessions analyze` CLI subcommand for cross-session DFG visualization |

## Architecture Notes

`app.py` is the Click application root; all sub-packages register their subcommand groups
against the root Click group. `_serve_guard.py` was extracted from `app.py` to isolate
the asyncio/signal machinery for testability.
