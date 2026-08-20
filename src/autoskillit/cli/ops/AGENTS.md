# ops/

Operator-facing diagnostic and maintenance subcommand runners.

## Architecture Notes

Each module exposes a single `run_<command>` entry point consumed by
`app.py`: report by default, mutate only behind an explicit `--reap` (or
`--reclaim`) flag. `_sessions.py` is the only sibling that exposes a Cyclopts
sub-App (`sessions_app`) instead of a `run_*` function, mirroring the
prompts/ subpackage pattern.
