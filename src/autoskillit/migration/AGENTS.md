# migration/

IL-2 migration engine — versioned config migration with adapter hierarchy and failure store.

## Architecture Notes

Migration notes live in `src/autoskillit/migrations/` as YAML files discovered by
`loader.py` at startup. `store.py` persists failures to `.autoskillit/temp/` using atomic
writes. `engine.py` defines the adapter ABC; concrete adapters are registered per migration
note version.
