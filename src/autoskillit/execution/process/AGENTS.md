# process/

Subprocess lifecycle management — spawn, monitor, race, kill.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Main module: `run_managed_async()`, `run_managed_sync()`, `DefaultSubprocessRunner` |
| `_process_io.py` | `create_temp_io()` context manager for temp file stdin/stdout/stderr |
| `_process_jsonl.py` | JSONL parsing: `_jsonl_contains_marker`, `_jsonl_has_record_type` |
| `_process_kill.py` | `kill_process_tree()` (sync) and `async_kill_process_tree()` (async): SIGTERM -> wait -> SIGKILL |
| `_process_monitor.py` | Async monitor coroutines: `_heartbeat()` (Channel A), `_session_log_monitor()` (Channel B) |
| `_process_pty.py` | `pty_wrap_command()` — wraps command with `script(1)` for PTY allocation |
| `_process_race.py` | `RaceAccumulator`, `RaceSignals`, watcher coroutines, `resolve_termination()` |

## Architecture Notes

**Two-channel completion detection:**

- **Channel A** (stdout heartbeat): polls the subprocess stdout temp file for a `type=result` NDJSON record containing the completion marker. Guarantees stdout data is available.
- **Channel B** (session JSONL monitor): watches the Claude Code session JSONL log file (written by the Claude Code subprocess to its session log directory) for the completion marker in an `assistant`-type record. Provides an orthogonal confirmation signal and session ID discovery via the JSONL filename stem.

Both channels race concurrently in an `anyio` task group. `resolve_termination()` reads the frozen `RaceSignals` and returns `(TerminationReason, ChannelConfirmation)`. Channel A takes precedence if both fire in the same tick.

`execute_termination_action()` is the sole authorized caller of `async_kill_process_tree` (enforced by test).
