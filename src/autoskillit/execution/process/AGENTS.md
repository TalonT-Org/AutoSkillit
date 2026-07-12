# process/

Subprocess lifecycle management — spawn, monitor, race, kill.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Main module: `run_managed_async()`, `run_managed_sync()`, `DefaultSubprocessRunner` |
| `_process_io.py` | `create_temp_io()` context manager for temp file stdin/stdout/stderr |
| `_process_jsonl.py` | JSONL parsing: `_jsonl_contains_marker`, `_jsonl_has_record_type` |
| `_process_kill.py` | Process-tree kill primitives plus `_OwnedProcessFinalizer`, the single-flight, deadline-bounded async cleanup authority |
| `_process_monitor.py` | Async monitor coroutines: `_heartbeat()` (Channel A), `_session_log_monitor()` (Channel B with `activity_tracker` parameter), and `ProcessActivityTracker` (per-invocation cache of `psutil.Process` handles for CPU baselines; no module-global singleton) |
| `_process_pty.py` | `pty_wrap_command()` — wraps command with `script(1)` for PTY allocation |
| `_process_race.py` | `RaceAccumulator`, `RaceSignals`, watcher coroutines, `resolve_termination()` |
| `_child_lifecycle.py` | `ChildLifecycleCoordinator`, `ChildLifecycleCoordinatorHandle`, `make_coordinator_handle` — single-owner reducer of immutable child-lifecycle observations; tracks `attempt_generation` (replaces/replaced_by) and `parent_turn_generation` (marker-bearing parent-assistant UUIDs) consumed by the completion gate; pending blocking-evidence store keyed by canonical fingerprint; unmatched evidence retention for later correlation (issue #4233) |
| `_channel_a_pump.py` | One persistent binary Channel A pump per invocation: owns the bound `StreamParser`, byte cursor, split-UTF-8 carry, and emits ordered `ChannelABatch` facts with exclusive-end watermarks (issue #4233) |
| `_lifecycle_actor.py` | One actor per invocation: sole mutable reducer and completion authority; consumes Channel A/B/process-exit facts, dispatches watermark catch-up commands, emits `LifecycleDecision` (CONTINUE / ELIGIBLE / CHILD_WORK_FAILED / CATCH_UP_FAILED) (issue #4233) |
| `_process_ownership.py` | Per-invocation owned process identity tracker: canonical root + retained descendant PID/create-time identities, post-reap process-group enumeration, PID-reuse protection (issue #4233) |

## Architecture Notes

**Lifecycle-aware completion detection:**

- **Channel A** is a persistent binary pump that owns the backend parser, byte cursor, and partial-line carry. It emits typed child observations and parent-marker candidates to the lifecycle actor.
- **Channel B** watches the backend session log. Completion is a proposal that must pass Channel A catch-up and actor adjudication; it is not independent completion authority.

The lifecycle actor is the sole completion authority for parser-enabled runs. It defers candidates while child obligations are active, fails closed on catch-up or child-work failure, and authorizes only an eligible fresh parent candidate. `resolve_termination()` remains the legacy race resolver for parser-less callers and supplies non-completion wakeups to the actor-aware boundary.

`_OwnedProcessFinalizer` owns cancellation-shielded, budget-bounded async cleanup and caches one typed `CleanupOutcome` per invocation. Architecture tests authorize direct process-tree kill calls by function, covering the finalizer, explicit termination boundary, post-spawn exceptional fallback, sync runner, and the two fleet fail-closed/reaper boundaries.
