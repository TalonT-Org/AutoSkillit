# execution/

IL-1 execution layer — headless Claude sessions, process lifecycle, CI/GitHub integration.
Sub-packages: headless/ (see headless/AGENTS.md), process/ (see process/AGENTS.md),
merge_queue/ (see merge_queue/AGENTS.md), session/ (see session/AGENTS.md),
backends/ (see backends/AGENTS.md).

SQLite access is read-only with defense in depth. The GitHub Actions CI watcher never
raises.

## Architecture Notes

`session_log.py` uses XDG base dir spec; log directory names use hyphens (never
underscores). `recording.py` and `_recording_skills.py` only activate when
`AUTOSKILLIT_RECORD_SESSION` is set; production paths never touch them.
