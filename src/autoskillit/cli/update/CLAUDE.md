# update/

Update and upgrade machinery for the autoskillit package.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Re-exports `run_update_command` and `run_update_checks` |
| `_update.py` | `run_update_command()` for the explicit `autoskillit update` subcommand |
| `_update_checks.py` | `run_update_checks()` — unified startup update check with dismissable prompt |
| `_update_checks_fetch.py` | HTTP cache + fetch: `_fetch_with_cache()`, disk cache with TTL |
| `_update_checks_source.py` | Source-repo discovery and SHA resolution for drift detection |

## Architecture Notes

`_update_checks.py` is the facade for the startup path; `_update.py` is the facade for the explicit `autoskillit update` command. Both reuse the same `upgrade_command()` policy from `cli/_install_info.py`.
