# Test Development Guidelines

## xdist Compatibility

All tests run under `-n 4 --dist worksteal`. Every test must be safe for parallel execution:
- Use `tmp_path` for filesystem isolation — never write to shared locations
- Session-scoped fixtures run once per worker process, not once globally
- Module-level globals are per-worker (separate processes) — no cross-worker state sharing
- Use `monkeypatch.setattr()` for all module-level state mutations — never bare assignment

## Fixture Discipline

- The conftest.py autouse fixtures use `monkeypatch` for both `_tools_enabled` and `_config`
- Class-level autouse fixtures that override these must also use `monkeypatch`
- Never use `try/finally` for state restoration — use fixtures with `monkeypatch`

## Performance

- `PYTHONDONTWRITEBYTECODE=1` is set via Taskfile — no `.pyc` disk writes
- Test temp I/O is routed to platform-resolved paths:
  - **Linux / WSL2**: `/dev/shm/pytest-tmp` (kernel tmpfs, RAM-backed)
  - **macOS**: `/tmp/pytest-tmp` (disk-backed system default)
- `TMPDIR` is set to the platform path via Taskfile — all `tempfile` calls are routed there
- `--basetemp` is passed to pytest — `tmp_path` fixtures resolve to the platform path
- `cache_dir` is redirected to the platform cache path — no stray pytest cache writes
- `test_tmp_path_is_ram_backed` in `test_architecture.py` enforces the `/dev/shm` prefix
  on Linux; on macOS it is a no-op (disk temp is acceptable there)
