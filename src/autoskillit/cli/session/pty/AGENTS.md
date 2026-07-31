# session/pty/

Private POSIX PTY support for interactive Codex cook attempts.

## Architecture Notes

The parent process owner exclusively manages process groups and foreground
terminal ownership. The observer manages PTY I/O, window propagation, raw-mode
entry, signal-handler restoration, and master-descriptor closure. The exec-side
launcher only attaches its inherited slave descriptor, duplicates standard
streams, and replaces itself with the requested command.
