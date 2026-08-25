# runtime/

Process-state modules for session lifecycle tracking (stdlib-only, IL-0).

## Architecture Notes

All modules are stdlib-only (safe for import from hook subprocesses). `readiness.py` is the sole exception — it uses `core.io.atomic_write`.

`_reclamation.py` classifies kernel-derived path evidence by `Revocability` (`REVOCABLE` —
cwd/fd/maps, a live kernel view that can genuinely become false — vs `MONOTONIC` —
environ/cmdline, an `execve()`-time snapshot that cannot) and exposes `veto_paths()`/
`snapshot_referenced()` as the only two ways to consume it, so a monotonic reference can never
reach a veto position. Also stdlib-only, importable from `scripts/pytest_tmp_lifecycle.py`
without the project venv. See its module docstring for the full evidence-classification
rationale.
