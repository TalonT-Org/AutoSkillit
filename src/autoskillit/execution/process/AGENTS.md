# process/

Subprocess lifecycle management — spawn, monitor, race, kill.

## Architecture Notes

- A Channel B transcript marker is corroboration and session discovery only; it never releases a live race in any mode. Completion release requires Channel A native terminal evidence or a witnessed exit; timeout, cancellation and infrastructure-abort paths remain bounded.
- `NO_KILL` requires an observed exit.

**Two-channel completion detection:**

- **Channel A** (stdout heartbeat): polls the subprocess stdout temp file for a `type=result` NDJSON record containing the completion marker. Guarantees stdout data is available.
- **Channel B** (session JSONL monitor): watches the Claude Code session JSONL log file (written by the Claude Code subprocess to its session log directory) for the completion marker in an `assistant`-type record. Provides an orthogonal confirmation signal and session ID discovery via the JSONL filename stem.

Both channels race concurrently in an `anyio` task group. `resolve_termination()` reads the frozen `RaceSignals` and returns `(TerminationReason, ChannelConfirmation)`. Channel A takes precedence if both fire in the same tick.

managed abort settlement reaches `kill_process_tree` through `OwnedProcessGroup.cleanup()` using
identity-fenced descendant observations. Routine teardown remains group-scoped, while
`execute_termination_action()` decides whether managed termination escalates beyond that scope.
