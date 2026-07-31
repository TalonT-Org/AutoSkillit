# fleet/

IL-2 fleet campaign layer — parallel issue dispatch, semaphore, sidecar, liveness, state.

## Architecture Notes

`_api.py` is the primary entry point called by `server/tools/tools_execution.py:dispatch_food_truck`.
Sidecars are per-issue JSONL files appended atomically; `_sidecar_rpc.py` and
`_findings_rpc.py` expose sidecar operations to in-recipe `run_python` steps without
requiring a full server import. `_liveness.py` gates dispatch to prevent zombie sessions
from blocking campaign progress.
