# fleet/

IL-2 fleet campaign layer — parallel issue dispatch, semaphore, sidecar, liveness, state.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Re-exports `CampaignSummary`, `parse_campaign_summary`, and dispatch callables |
| `_api.py` | Fleet campaign execution engine — dispatches L2 sessions, resolves campaign/result variable references |
| `_prompts.py` | Prompt builder for L2 fleet dispatch sessions — assembles sous-chef instruction block from SKILL.md sections |
| `result_parser.py` | L2 result block parser with Channel B JSONL fallback |
| `sidecar.py` | Per-issue JSONL sidecar — `IssueSidecarEntry`, append/read/`compute_remaining` helpers |
| `_liveness.py` | `is_dispatch_session_alive()` — boot_id + starttime_ticks liveness gate |
| `_semaphore.py` | `FleetSemaphore` — configurable `asyncio.BoundedSemaphore` implementing `FleetLock` |
| `_sidecar_rpc.py` | `run_python`-callable entry points: `write_sidecar_entry`, `get_remaining_issues` |
| `_findings_rpc.py` | `run_python`-callable entry points: `parse_and_resume`, `load_execution_map` |
| `_checkpoint_bridge.py` | `checkpoint_from_sidecar` — converts `IssueSidecarEntry` list to `SessionCheckpoint` |
| `state.py` | Campaign state persistence — `DispatchRecord`, `DispatchStatus`, atomic writes, resume algorithm |
| `summary.py` | Campaign summary schema v1: frozen dataclasses, sentinel parser, validator |

## Architecture Notes

`_api.py` is the primary entry point called by `server/tools/tools_execution.py:dispatch_food_truck`.
Sidecars are per-issue JSONL files appended atomically; `_sidecar_rpc.py` and
`_findings_rpc.py` expose sidecar operations to in-recipe `run_python` steps without
requiring a full server import. `_liveness.py` gates dispatch to prevent zombie sessions
from blocking campaign progress.
