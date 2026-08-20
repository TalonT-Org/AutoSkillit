# prompts/

Orchestrator system prompt builders — shared helpers and re-export hub.

## Architecture Notes

Hub-and-spoke: `_prompts.py` is the shared-helpers hub that re-exports the
public symbols of `_prompts_campaign.py` (L3 campaign dispatcher prompt),
`_prompts_orchestrator.py` (L1/L2 cook session prompt), and
`_prompts_kitchen.py` (open-kitchen and fleet-dispatch prompts). The
package-level `__init__.py` is a separate facade that re-exports the same
symbols so `from autoskillit.cli.prompts import X` resolves to the same
object — both layers must be updated together when adding a new builder.
