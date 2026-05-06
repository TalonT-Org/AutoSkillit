# runtime/

Process-state modules for session lifecycle tracking (stdlib-only, IL-0).

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Named explicit re-exports (not wildcard) for all public symbols |
| `_linux_proc.py` | Reads `/proc` boot ID and process start-time ticks; returns `None` on non-Linux |
| `kitchen_state.py` | `KitchenMarker` disk-based session marker written by `open_kitchen`, read by hook subprocesses; also `find_caller_session_id()` |
| `readiness.py` | Filesystem sentinel for MCP server startup synchronization in integration tests |
| `session_provenance.py` | Provenance record store and reader for L2 food truck session ownership tuples |
| `session_registry.py` | Maps autoskillit launch IDs to Claude Code session UUIDs for scoped resume |

## Architecture Notes

All modules are stdlib-only (safe for import from hook subprocesses). `readiness.py` is the sole exception — it uses `core.io.atomic_write`.
