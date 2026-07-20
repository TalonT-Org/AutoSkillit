# migration/

Migration engine, store, and loader tests.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | empty |
| `test_api.py` | Tests for migration/_api.py |
| `test_api_integration.py` | Integration tests for migration/_api.py — no mocking of recipe lookup or engine |
| `test_engine.py` | Tests for migration_engine.py — ME1 through ME21 |
| `test_fleet_migration_note.py` | Fleet migration note tests |
| `test_loader.py` | Tests for migration note discovery and version chaining |
| `test_pipeline_health_migration_note.py` | Diagnostics config-key rename migration note tests |
| `test_store.py` | Tests for failure_store.py — FS1 through FS11 and FS-IM1 through FS-IM4 |
| `test_vis_lens_rename_migration_note.py` | Vis-lens skill rename migration note tests |
