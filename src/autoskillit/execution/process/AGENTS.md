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
| `_channel_a_pump.py` | One persistent binary Channel A pump per invocation: owns the bound `StreamParser`, byte cursor, split-UTF-8 carry, and an ID-keyed set of admitted catch-up commands; emits ordered `ChannelABatch` facts with exclusive-end watermarks (issue #4233) |
| `_lifecycle_actor.py` | One actor per invocation: owns `ActorIngress`, request state, and full `LifecycleActorReply` delivery; persistently consumes Channel A/B/process-exit facts, dispatches watermark catch-up commands, and emits `LifecycleDecision` (CONTINUE / ELIGIBLE / CHILD_WORK_FAILED / CATCH_UP_FAILED) (issue #4233) |
| `_process_ownership.py` | Per-invocation owned process identity tracker: canonical root + retained descendant PID/create-time identities, post-reap process-group enumeration, PID-reuse protection (issue #4233) |

## Architecture Notes

**Lifecycle-aware completion detection:**

- **Channel A** is a persistent binary pump that owns the backend parser, byte cursor, partial-line carry, and ID-keyed catch-up commands. It emits typed child observations and parent-marker candidates to the lifecycle actor.
- **Channel B** is a persistent binary session-log tail for the invocation. Each parent candidate is submitted through `ActorIngress` and receives a full actor reply; completion is a proposal that must pass Channel A catch-up and actor adjudication, not independent completion authority.

The lifecycle actor is the sole completion authority for parser-enabled runs. It owns request mutation and replies containing the processed watermark, snapshot, issues, decision, eligible candidate/source, sightings, and disposition. It defers candidates while child obligations are active, fails closed on catch-up or child-work failure, and authorizes only an eligible fresh parent candidate. `resolve_termination()` remains the legacy race resolver for parser-less callers and supplies non-completion wakeups to the actor-aware boundary.

Lifecycle shutdown is cooperative: `producer_stop` asks producers to finish their final drains, producer-owned ingress endpoints close naturally, and the actor consumes `ActorIngress` to EOF before setting `actor_done`. Producer-group cancellation is only a bounded fallback after the cooperative drain window; normal shutdown does not cancel the actor.

`_OwnedProcessFinalizer` is installed immediately after spawn with the exact raw process handle and no identity I/O. It owns cancellation-shielded, single-flight async cleanup under one absolute deadline and caches one typed `CleanupOutcome` per invocation. Root identity enrichment is guarded; unknown roots use only the raw handle, while retained descendants are signaled only after an immediate identity match. The sync runner uses the corresponding private identity-validating cleanup helper. The process facade has no raw kill fallback; architecture tests reserve direct process-tree primitives to the cleanup module and the two fleet fail-closed/reaper boundaries.
