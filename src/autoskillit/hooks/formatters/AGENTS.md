# formatters/

PostToolUse output formatters — MCP JSON to Markdown-KV reformatter (30-77% token reduction).

The package initializer remains import-free.

## Architecture Notes

All `_fmt_*` modules use bare relative imports (`from _fmt_primitives import ...`) because hook scripts run as standalone executables with this directory as CWD — not via the Python package system. `pretty_output_hook.py` is the only entry point.

**Error-fidelity invariant:** `_format_response` in `pretty_output_hook.py` enforces a single-exit-point gate: if a payload dict has a truthy top-level `error` whose text is absent from the rendered output, a final `error: {text}` line is appended. This guarantees no formatter can silently drop the `error` field — the gate fires for all code paths including `gate_error` and `tool_exception` early returns.
