# config/

IL-1 configuration layer — `AutomationConfig`, Dynaconf loader, schema validation.

## Architecture Notes

`_config_dataclasses.py` defines the 29 leaf config dataclasses that form the schema tree
rooted at `AutomationConfig`, plus the `ProviderProfileDef` frozen definition type. `defaults.yaml` (non-Python) is the Dynaconf default values
file read at startup. `ingredient_defaults.py` bridges recipe-level ingredient declarations
to config-layer defaults without importing from `recipe/`.
