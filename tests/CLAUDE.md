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

- `tmp_path` resolves to `/dev/shm/pytest-tmp` (RAM) via `--basetemp`
- `TMPDIR=/dev/shm/pytest-tmp` is set via Taskfile — all `tempfile` calls go to tmpfs
- `cache_dir = /dev/shm/pytest-cache` redirects pytest's internal cache to tmpfs
- `PYTHONDONTWRITEBYTECODE=1` is set — no `.pyc` disk writes
