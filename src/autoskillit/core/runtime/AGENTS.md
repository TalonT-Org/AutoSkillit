# runtime/

Process-state modules for session lifecycle tracking (stdlib-only, IL-0).

## Architecture Notes

All modules are stdlib-only (safe for import from hook subprocesses). `readiness.py` uses IL-0
`core.io.atomic_write`; `worktree_gate_lease.py` uses the `core.io` versioned-JSON helpers and
`core.paths.default_log_dir` so its lock cannot be deleted by worktree cleanup.

`_reclamation.py` classifies kernel-derived path evidence by `Revocability` (`REVOCABLE` —
cwd/fd/maps, a live kernel view that can genuinely become false — vs `MONOTONIC` —
environ/cmdline, an `execve()`-time snapshot that cannot) and exposes `veto_paths()`/
`snapshot_referenced()` as the only two ways to consume it, so a monotonic reference can never
reach a veto position. Also stdlib-only, importable from `scripts/pytest_tmp_lifecycle.py`
without the project venv. See its module docstring for the full evidence-classification
rationale.

It also owns `ReclamationBound`/`select_overflow`/`bound_unsatisfied` (a directory-oriented
capacity backstop, mirroring `execution/session_log.py`'s `_MAX_SESSIONS` co-retention model),
`append_and_trim_jsonl`/`trim_jsonl_lines` (the append-only-store analogue, applied to
`session_provenance.jsonl`, `reaper_events.jsonl`, and the `run_skill` cleanup-failure sink —
`hooks/_hook_settings.py` duplicates the trim logic inline instead of importing it, since that
module must stay free of all `autoskillit.*` imports), and `SESSION_STALE_SECONDS` (the one
TTL/stat-field shared by `workspace/session_skills.py`'s `cleanup_stale` and
`scripts/pytest_tmp_lifecycle.py`'s `sweep-sessions` subcommand for the same root).

`core/_capacity.py` (root-level, not nested under `runtime/`) owns `SpaceProbe`/
`default_space_probe`/`MIN_FREE_BYTES_THRESHOLD` instead of living here: `core.runtime`
already imports `core.types` (`artifact_lease.py`), and `core.types`'s `TestRunner` Protocol
needs `SpaceProbe` as a real default value, not just a type — landing it in `core.runtime`
would be a circular import.
