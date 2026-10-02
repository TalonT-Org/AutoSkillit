# autoskillit/

Package root — entry points, hook registry, and cross-cutting utilities.

## Architecture Notes

Two distinct root-level patterns exist. Which root `.py` files may exist is enforced by
`test_root_module_allowlist` (`tests/contracts/test_package_gateways.py`).

- **Stdlib-only hook-callable authorities** — the `.py` entries of
  `_PUBLIC_PLUGIN_ASSET_NAMES` (`workspace/_installed/_projection_assets.py`). Hook
  subprocesses running outside the package venv load them by bare name after putting
  the plugin root on `sys.path` (for example the `quota_constraints` imports in
  `hooks/guards/quota_guard.py` and `hooks/quota_post_hook.py`). A root module a hook
  imports this way MUST be listed in `_PUBLIC_PLUGIN_ASSET_NAMES`, or it is absent from
  every installed plugin tree (Claude projections, Codex generations, marketplace
  installs). `test_hooks_are_stdlib_only` and `test_hook_root_dependencies_are_published`
  (`tests/arch/test_hooks_are_stdlib_only.py`) discover these imports from source and
  enforce publication and stdlib-only self-containment; `TestInstalledTreeImportClosure`
  and `test_projected_hook_commands_execute_without_error`
  (`tests/contracts/test_projection_hook_relocatability.py`) prove they resolve and run
  inside generated trees. Because the same property — no autoskillit imports — makes
  them safe for hook subprocesses, the same modules also act as the canonical internal
  authority for the same logic (the ``is_parent_assistant_record`` predicate is consumed
  by both the hook guard and the in-process pipeline). Add a new module here only when
  both properties hold.
- **Headless run_python utilities** — `_llm_triage.py`, `_probe_canary.py`, and
  `_test_filter.py` import autoskillit modules and run inside the package venv
  via the headless `run_python` tool. They are NOT safe for hook subprocesses.

`hook_registry/` lives as a sub-package, not at root, and is governed by the
regular IL-1 import-linter contracts.
