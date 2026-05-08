# config/

IL-1 configuration layer — `AutomationConfig`, Dynaconf loader, schema validation.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Re-exports `AutomationConfig`, `load_config`, `ConfigSchemaError` |
| `ingredient_defaults.py` | Per-recipe ingredient default resolution |
| `settings.py` | `AutomationConfig` + schema validate/write API |
| `_config_dataclasses.py` | 26 leaf dataclasses + `ConfigSchemaError` |
| `_config_loader.py` | `_make_dynaconf` + `load_config` layer helpers |

## Architecture Notes

`_config_dataclasses.py` defines the 24 leaf config dataclasses that form the schema tree
rooted at `AutomationConfig`. `defaults.yaml` (non-Python) is the Dynaconf default values
file read at startup. `ingredient_defaults.py` bridges recipe-level ingredient declarations
to config-layer defaults without importing from `recipe/`.
