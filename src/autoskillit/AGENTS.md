# autoskillit/

Package root — entry points, hook registry, and cross-cutting utilities.

## Architecture Notes

`hook_registry.py` is stdlib-only (safe for hook subprocesses). `_test_filter.py` drives
`task test-filtered` — it maps changed-file globs to test directory subsets. `_llm_triage.py`
and `smoke_utils/` are callable by headless recipe steps via `run_python`.
