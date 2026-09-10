# fleet/

IL-2 fleet campaign layer — parallel issue dispatch, semaphore, sidecar, liveness, state.

## Architecture Notes

`_api.py` is a thin public-API facade that re-exports the canonical symbol
list (per REQ-IMP-001). The actual dispatch engine lives in the
`dispatch/` subpackage — see [dispatch/AGENTS.md](dispatch/AGENTS.md)
for the per-phase shard layout, threading contract, and isolation
discipline. Campaign-state storage, transitions, records, effects, and
recovery live in the `campaign_state/` subpackage — see
[campaign_state/AGENTS.md](campaign_state/AGENTS.md). Sidecars are
per-issue JSONL files appended atomically; `_sidecar_rpc.py` and
`_findings_rpc.py` expose sidecar operations to in-recipe `run_python`
steps without requiring a full server import. `_liveness.py` gates
dispatch to prevent zombie sessions from blocking campaign progress.

## Dispatch shards (issue #4851)

The dispatch engine was decomposed from `fleet/_api.py` (1592 lines) into
a per-phase shard package `fleet/dispatch/`. The destination layout is
finalized as a hard prerequisite for issue #4673's broader fleet folder
reorg — see `dispatch/AGENTS.md` for the canonical shard mapping and
threading contract.

## Folder decomposition (issue #4673)

The nine campaign-state modules (`state.py`, `state_effects.py`,
`state_error_codes.py`, `state_gates.py`, `state_outcomes.py`,
`state_records.py`, `state_recovery.py`, `state_transitions.py`,
`_state_lock.py`) moved into `fleet/campaign_state/` with their basenames
preserved. `fleet/__init__.py` retains its exact existing `__all__` and
now reaches them through `campaign_state`; no old-path alias or wrapper
module was created.
