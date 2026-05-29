# autoskillit/

Package root — entry points, hook registry, and cross-cutting utilities.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Package exports |
| `__main__.py` | `python -m autoskillit` entry point |
| `_llm_triage.py` | Contract staleness triage (Haiku subprocess) |
| `smoke_utils.py` | Callables for smoke-test pipeline `run_python` steps |
| `hook_registry.py` | `HookDef`, `HOOK_REGISTRY`, `generate_hooks_json` |
| `_test_filter.py` | Test filter manifest: glob-to-test-directory mapping |
| `version.py` | Version health utilities (IL-0) |

## Architecture Notes

`hook_registry.py` is stdlib-only (safe for hook subprocesses). `_test_filter.py` drives
`task test-filtered` — it maps changed-file globs to test directory subsets. `_llm_triage.py`
and `smoke_utils.py` are callable by headless recipe steps via `run_python`.
