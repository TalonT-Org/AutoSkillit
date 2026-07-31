# cli/

CLI command, subcommand, and interactive workflow tests.

Upgrade fixtures model pre-existing `legacy_home` installations so contract tests exercise
install-over-something as well as install-from-nothing.

## Architecture Notes

`conftest.py` auto-patches `sync_hooks_to_settings()` worktree guard so CLI init tests don't fail in worktree environments. `_fleet_helpers.py` and `_update_checks_helpers.py` are shared factory modules used across split test files for fleet and update-checks functionality respectively. `_split_helpers.py` is a shared module for structural guard helpers (e.g., `_has_pytestmark_cli`) used by multiple split-guard test files.
