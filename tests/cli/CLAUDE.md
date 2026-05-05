# cli/

CLI command, subcommand, and interactive workflow tests.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | empty |
| `_fleet_helpers.py` | Shared helpers for fleet CLI tests |
| `_update_checks_helpers.py` | Shared factory helpers for update-checks test files |
| `conftest.py` | CLI test fixtures — auto-patches the worktree guard for sync_hooks_to_settings() |
| `test_ansi.py` | Tests for cli/_ansi.py terminal color utilities |
| `test_app_main.py` | Tests for autoskillit.cli.app.main() entry point behaviour |
| `test_cli_hooks.py` | Tests for the cli/_hooks.py unified hook registration helpers |
| `test_cli_marketplace.py` | Tests for the cli/_marketplace.py module |
| `test_cli_prompts.py` | Tests for the cli/_prompts.py module |
| `test_cli_serve_logging.py` | Tests for serve() two-phase logging initialization |
| `test_compute_exit_code.py` | Tests for _compute_exit_code in cli/_fleet.py (T-RESUMABLE-9) |
| `test_cook_env_scrub.py` | Launch-site env-scrub contract tests for _launch_cook_session and cook() |
| `test_cook_features.py` | Tests: cook CLI features — subset gate, recipes CLI, fleet display categories |
| `test_cook_ide_isolation.py` | End-to-end regression canary: _launch_cook_session under simulated IDE state |
| `test_cook_interactive.py` | Tests for the cook CLI command (interactive skill session) |
| `test_cook_profile.py` | Tests for --profile flag in cook command — env injection and validation |
| `test_cook_order_command.py` | Tests: cook CLI order command — script validation, command building, env injection |
| `test_cook_order_picker.py` | Tests: cook CLI order command — recipe picker, resume flows, session parsing |
| `test_cook_order_prompt.py` | Tests: cook CLI order command — system prompt content, MCP prefix selection, ownership |
| `test_cook_workspace.py` | Tests: cook CLI workspace init and clean commands |
| `test_doctor.py` | Tests for CLI doctor command and related utilities |
| `test_doctor_migration.py` | Tests for doctor quota cache schema, install classification, version consistency, and drift |
| `test_doctor_scripts.py` | Tests for doctor script/recipe version health checks |
| `test_doctor_split.py` | Structural guards: test_doctor.py split into three files (P1-F02) |
| `test_features_cli.py` | Tests for the features CLI subcommand |
| `test_fleet_campaign.py` | Tests: fleet CLI campaign command |
| `test_fleet_campaign_preview.py` | Tests: fleet_campaign shows preview + confirmation before launch |
| `test_fleet_dispatch.py` | Tests: fleet CLI dispatch command |
| `test_fleet_list.py` | Tests: fleet CLI list command |
| `test_fleet_session.py` | Tests: _launch_fleet_session forwards ingredients_table to prompt builder |
| `test_fleet_split.py` | Structural guard for fleet test split |
| `test_fleet_status.py` | Tests: fleet CLI status command |
| `test_food_truck_prompt.py` | Group E tests: L2 food truck prompt builder — sous-chef subset, inversions, budget guidance |
| `test_init.py` | Tests for CLI init, config, and serve-related commands |
| `test_input_tty_contracts.py` | Structural enforcement: every input() call in cli/ must go through timed_prompt() |
| `test_install.py` | Tests for CLI install, upgrade, and quota-related commands |
| `test_install_info.py` | Tests for cli/_install_info.py — install classification and update policy |
| `test_installed_plugins_file.py` | Unit tests for the InstalledPluginsFile repository |
| `test_interactive_subprocess_contracts.py` | Structural enforcement: CLI subprocess.run calls that inherit the terminal must be wrapped in terminal_guard() |
| `test_l3_orchestrator_prompt.py` | Group K tests: L3 campaign dispatcher prompt builder — 10 sections, tool surface, sentinel format |
| `test_mcp_names.py` | Tests for cli/_mcp_names.py — MCP prefix detection |
| `test_menu.py` | Tests: shared selection menu primitive |
| `test_onboarding.py` | Tests for first-run detection and guided onboarding menu |
| `test_order_resume.py` | Tests for order CLI infra-exit detection and auto-resume via NamedResume |
| `test_orchestrator_prompt_contract.py` | Tests for orchestrator prompt contract: failure predicates and dispatch consistency |
| `test_plugin_cache.py` | Tests for _plugin_cache: retiring cache, install locking, kitchen registry |
| `test_preview.py` | Tests for the shared pre-launch preview module (_preview.py) |
| `test_reap.py` | Tests for _reap_stale_dispatches in cli/_fleet.py (Group J) |
| `test_reap_sidecar_check.py` | Tests for _reap_stale_dispatches sidecar-aware status transition (T-RESUMABLE-10) |
| `test_reload_loop.py` | Tests for the session reload sentinel and loop mechanics |
| `test_restart.py` | Tests for cli/_restart.py — NoReturn process restart contract |
| `test_routing_completeness.py` | Enum routing completeness: every orchestrator-visible RetryReason must have a routing rule in the orchestrator prompt |
| `test_serve_sigterm.py` | Regression guard: serve() uses event-loop-routed signal handling (issue #745) |
| `test_session_launch.py` | Tests for cli/_session_launch.py — _run_interactive_session contract |
| `test_session_picker.py` | Tests for cli/_session_picker.py |
| `test_signal_guard.py` | Tests for _fleet_signal_guard in cli/_fleet.py (Group J) |
| `test_startup_budget.py` | Integration test: serve() must call anyio.run() within the startup timing budget |
| `test_subprocess_env_contracts.py` | Structural contract: every subprocess.run(["autoskillit",...]) in CLI must inject AUTOSKILLIT_SKIP_STALE_CHECK |
| `test_terminal.py` | Tests for cli/_terminal.py terminal_guard() context manager |
| `test_update_checks_fetch.py` | Tests for cli/_update_checks.py — UC-9 fetch-cache regression, UC-11/12 lifecycle/transitions |
| `test_update_checks_guards.py` | Tests for cli/_update_checks.py — UC-1 early-return guards, UC-2 signal gatherers |
| `test_update_checks_prompt.py` | Tests for cli/_update_checks.py — UC-3 through UC-10: prompt consolidation, yes/no paths, dismissal |
| `test_update_checks_split.py` | Structural guard for update_checks split |
| `test_update_command.py` | Tests for cli/_update.py — first-class update command |
| `test_workspace.py` | Tests for cli._workspace — age partitioning, display, and confirmation |

## Architecture Notes

`conftest.py` auto-patches `sync_hooks_to_settings()` worktree guard so CLI init tests don't fail in worktree environments. `_fleet_helpers.py` and `_update_checks_helpers.py` are shared factory modules used across split test files for fleet and update-checks functionality respectively.
