# autoskillit/

Package root — entry points, hook registry, and cross-cutting utilities.

## Architecture Notes

Root-level `.py` modules are stdlib-only cross-cutting authorities safe for hook
subprocesses; placement is enforced by `_LAYER_EXEMPT_STEMS`
(`tests/arch/test_layer_enforcement.py:57`) and the `test_root_module_allowlist`
allowlist (`tests/contracts/test_package_gateways.py:400`). Add a new module here
only when it has zero internal autoskillit imports and must run under a hook
subprocess outside the package venv.

`hook_registry.py` and `quota_constraints.py` follow this pattern. `_test_filter.py`
drives `task test-filtered` — it maps changed-file globs to test directory subsets.
`_llm_triage.py` and `smoke_utils/` are callable by headless recipe steps via
`run_python`.
