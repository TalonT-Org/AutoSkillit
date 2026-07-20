# formatters/

PostToolUse output formatters — MCP JSON to Markdown-KV reformatter (30-77% token reduction).

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Package marker (no imports) |
| `pretty_output_hook.py` | Dispatch entrypoint: intercepts MCP tool responses, routes to per-tool formatters |
| `_fmt_primitives.py` | Shared primitives: `_CHECK_MARK`, `_CROSS_MARK`, payload dataclasses, token formatter |
| `_fmt_response_spill.py` | Standalone artifact metadata trust, containment, size, and digest validation |
| `_fmt_execution.py` | Formatters for `run_skill`, `run_cmd`, `test_check`, `merge_worktree` |
| `_fmt_dispatch.py` | Formatter for `dispatch_food_truck` artifact-backed nested data |
| `_fmt_recipe.py` | Formatters for `load_recipe`, `open_kitchen`, `list_recipes` |
| `_fmt_recipe_compact.py` | Deterministic, semantics-preserving recipe display compaction (field stripping, indentation halving, orchestration-rules message dedup) |
| `_fmt_status.py` | Formatters for `get_token_summary`, `get_timing_summary`, `kitchen_status` |

## Architecture Notes

All `_fmt_*` modules use bare relative imports (`from _fmt_primitives import ...`) because hook scripts run as standalone executables with this directory as CWD — not via the Python package system. `pretty_output_hook.py` is the only entry point.

**Error-fidelity invariant:** `_format_response` in `pretty_output_hook.py` enforces a single-exit-point gate: if a payload dict has a truthy top-level `error` whose text is absent from the rendered output, a final `error: {text}` line is appended. This guarantees no formatter can silently drop the `error` field — the gate fires for all code paths including `gate_error` and `tool_exception` early returns.
