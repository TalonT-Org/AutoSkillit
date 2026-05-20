# fleet/

IL-2 fleet campaign layer — parallel issue dispatch, semaphore, sidecar, liveness, state.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Re-exports `CampaignSummary`, `parse_campaign_summary`, and dispatch callables |
| `_api.py` | Fleet campaign execution engine — dispatches L2 sessions, resolves campaign/result variable references; `evaluate_skip_when` for conditional dispatch skipping |
| `_prompts.py` | Prompt builder for L2 fleet dispatch sessions — assembles admiral dispatch instruction block from SKILL.md sections |
| `result_parser.py` | L2 result block parser with Channel B JSONL fallback |
| `sidecar.py` | Per-issue JSONL sidecar — `IssueSidecarEntry`, append/read/`compute_remaining` helpers |
| `_label_cleanup.py` | Infrastructure-level label cleanup — `cleanup_orphaned_labels`, `sweep_stale_dispatch_labels`, `discover_campaign_state_files` |
| `_liveness.py` | `is_dispatch_session_alive()` — boot_id + starttime_ticks liveness gate |
| `_semaphore.py` | `FleetSemaphore` — configurable `asyncio.BoundedSemaphore` implementing `FleetLock` |
| `_sidecar_rpc.py` | `run_python`-callable entry points: `write_sidecar_entry`, `get_remaining_issues` |
| `_findings_rpc.py` | `run_python`-callable entry points: `parse_and_resume`, `load_execution_map` |
| `_checkpoint_bridge.py` | `checkpoint_from_sidecar` — converts `IssueSidecarEntry` list to `SessionCheckpoint` |
| `state.py` | Campaign state I/O and mutations — `CampaignStateMutator`, `read_state`, `mark_dispatch_*`, re-exports from `state_types`, `state_gates`, `state_recovery` |
| `state_types.py` | Campaign state types — `DispatchStatus`, `DispatchRecord`, `CampaignState`, `ResumeDecision`, `GateRecordResult`, constants |
| `state_gates.py` | Gate dispatch recording — `record_gate_outcome` |
| `state_recovery.py` | Crash recovery + resume — `classify_stale_dispatch`, `has_failed_dispatch`, `resume_campaign_from_state`, `derive_orchestrator_resume_spec`, `find_dispatch_for_issue` |
| `summary.py` | Campaign summary schema v1: frozen dataclasses, sentinel parser, validator |

## Test Files

| File | Purpose |
|------|---------|
| `tests/fleet/test_state_lock_contract.py` | Locking contract tests — AST scan for flock targets, flock acquisition per mutation, cross-caller concurrency |
| `tests/fleet/test_state_recovery.py` | Tests for `derive_orchestrator_resume_spec` |
| `tests/fleet/test_campaign_capture.py` | Campaign capture extraction and ingredient interpolation tests |
| `tests/fleet/test_capture_roundtrip.py` | Prompt-extractor field name alignment tests — verifies sentinel example uses bare names matching `_extract_captures` expectations |
| `tests/fleet/test_skip_when.py` | `evaluate_skip_when` unit tests — campaign/inputs ref resolution, expression validation, quote stripping |
| `tests/fleet/test_label_cleanup.py` | Tests for `cleanup_orphaned_labels` — finally block cleanup on CancelledError, RuntimeError, no-sidecar, no-client, multiple issues |
| `tests/fleet/test_startup_label_recovery.py` | Tests for `sweep_stale_dispatch_labels` — dead dispatch cleanup, alive dispatch skip, missing sidecar, multi-campaign |
| `tests/fleet/test_find_dispatch_for_issue.py` | Tests for `find_dispatch_for_issue` — running dispatch lookup, non-running skip, missing sidecar, empty state |

## Architecture Notes

`_api.py` is the primary entry point called by `server/tools/tools_execution.py:dispatch_food_truck`.
Sidecars are per-issue JSONL files appended atomically; `_sidecar_rpc.py` and
`_findings_rpc.py` expose sidecar operations to in-recipe `run_python` steps without
requiring a full server import. `_liveness.py` gates dispatch to prevent zombie sessions
from blocking campaign progress.
