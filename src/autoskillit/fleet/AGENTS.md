# fleet/

IL-2 fleet campaign layer — parallel issue dispatch, semaphore, sidecar, liveness, state.

## Architecture Notes

`_api.py` is a thin public-API facade that re-exports the canonical symbol
list (per REQ-IMP-001). The actual dispatch engine lives in the
`dispatch/` subpackage — see [dispatch/AGENTS.md](dispatch/AGENTS.md)
for the per-phase shard layout, threading contract, and isolation
discipline. Sidecars are per-issue JSONL files appended atomically;
`_sidecar_rpc.py` and `_findings_rpc.py` expose sidecar operations to
in-recipe `run_python` steps without requiring a full server import.
`_liveness.py` gates dispatch to prevent zombie sessions from blocking
campaign progress.

## Dispatch shards (issue #4851)

The dispatch engine was decomposed from `fleet/_api.py` (1592 lines) into
a per-phase shard package `fleet/dispatch/`. The destination layout is
finalized as a hard prerequisite for issue #4673's broader fleet folder
reorg — see `dispatch/AGENTS.md` for the canonical shard mapping and
threading contract.
