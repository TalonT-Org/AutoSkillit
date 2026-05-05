# migration/

IL-2 migration engine — versioned config migration with adapter hierarchy and failure store.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Re-exports `check_and_migrate`, `MigrationEngine` |
| `engine.py` | `MigrationEngine`, adapter ABC hierarchy |
| `_api.py` | `check_and_migrate` — top-level migration entry point |
| `loader.py` | Migration note discovery + version chaining |
| `store.py` | `FailureStore` (JSON, atomic writes) |

## Architecture Notes

Migration notes live in `src/autoskillit/migrations/` as YAML files discovered by
`loader.py` at startup. `store.py` persists failures to `.autoskillit/temp/` using atomic
writes. `engine.py` defines the adapter ABC; concrete adapters are registered per migration
note version.
