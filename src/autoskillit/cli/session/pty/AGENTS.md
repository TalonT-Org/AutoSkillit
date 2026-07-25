# session/pty/

Private POSIX PTY support for interactive Codex cook attempts.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Private package marker with no gateway exports |
| `_observer.py` | Transparent PTY relay, bounded semantic observation, and guarded Codex state-readiness probing |
| `_exec.py` | Minimal exec-side controlling-terminal attachment and immediate target exec |

## Architecture Notes

The parent process owner exclusively manages process groups and foreground
terminal ownership. The observer manages PTY I/O, window propagation, raw-mode
entry, signal-handler restoration, and master-descriptor closure. The exec-side
launcher only attaches its inherited slave descriptor, duplicates standard
streams, and replaces itself with the requested command.
