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
- pytest uses its platform-aware default temp directory (`/tmp` on Linux/macOS)
