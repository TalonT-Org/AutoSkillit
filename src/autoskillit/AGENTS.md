# autoskillit/

Package root — entry points, hook registry, and cross-cutting utilities.

## Architecture Notes

Two distinct root-level patterns exist; placement is governed by
`_LAYER_EXEMPT_STEMS` (`tests/arch/test_layer_enforcement.py:57:62`) and
`test_root_module_allowlist` (`tests/contracts/test_package_gateways.py:400:435`):

- **Stdlib-only hook-callable authorities** — `_recipe_delivery_framing.py` and
  `quota_constraints.py` are safe for hook subprocesses running outside the
  package venv. They have zero internal autoskillit imports and are loaded via
  bare-name sys.path bootstrap (`hooks/guards/quota_guard.py:42`,
  `hooks/quota_post_hook.py:39`). Add a new module here only when both
  properties hold.
- **Headless run_python utilities** — `_llm_triage.py`, `_probe_canary.py`, and
  `_test_filter.py` import autoskillit modules and run inside the package venv
  via the headless `run_python` tool. They are NOT safe for hook subprocesses.

`hook_registry/` lives as a sub-package, not at root, and is governed by the
regular IL-1 import-linter contracts.
