# runtime/

Process-state modules for session lifecycle tracking (stdlib-only, IL-0).

## Architecture Notes

All modules are stdlib-only (safe for import from hook subprocesses). `readiness.py` is the sole exception — it uses `core.io.atomic_write`.
