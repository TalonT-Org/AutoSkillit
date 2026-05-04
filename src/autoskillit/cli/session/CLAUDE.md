# session/

Interactive session management — cook (ephemeral) and order (orchestrator) entry points.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Re-exports `cook` and `order` commands |
| `_cook.py` | `cook` command: ephemeral skill session launcher |
| `_order.py` | `order` command: orchestrator prompt builder with recipe selection |
| `_reload.py` | `consume_reload_sentinel()` — detects reload sentinel written by MCP reload tool |
| `_session_launch.py` | Shared prelude: `_launch_cook_session()`, `_run_interactive_session()` |
| `_session_picker.py` | Scoped resume picker: filters session history by greeting prefix |
