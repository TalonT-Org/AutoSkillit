# Test Development Guidelines

## xdist Compatibility

All tests run under `-n 4 --dist worksteal`. Every test must be safe for parallel execution:
- Use `tmp_path` for filesystem isolation — never write to shared locations
- Session-scoped fixtures run once per worker process, not once globally
- Module-level globals are per-worker (separate processes) — no cross-worker state sharing
- Use `monkeypatch.setattr()` for all module-level state mutations — never bare assignment
- Source directories passed to `clone_repo` must be **subdirectories** of `tmp_path`,
  not `tmp_path` itself. When `source_dir = tmp_path`, `clone_repo` places
  `autoskillit-runs/` at `tmp_path.parent` (worker-shared). Use `source_dir = tmp_path / "repo"`.

## Fixture Discipline

- The `tool_ctx` fixture (conftest.py) provides a fully isolated `ToolContext` with gate open
  by default (`DefaultGateState(enabled=True)`). It monkeypatches `server._ctx` so all server
  tool handler calls use the test context without global state leakage.
- To test with the kitchen closed, set `tool_ctx.gate = DefaultGateState(enabled=False)` at
  the start of the test or in a class-level autouse fixture (see `_close_kitchen` in
  `test_instruction_surface_contract.py` for an example).
- Never use bare assignment or `try/finally` to restore server state — use `monkeypatch` or
  rely on `tool_ctx`'s fixture teardown.

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

```
tests/
├── CLAUDE.md                            # xdist compatibility guidelines
├── __init__.py
├── conftest.py                          # Shared fixtures: MockSubprocessRunner, _make_result, _make_timeout_result
├── test_conftest.py                     # Tests for conftest fixtures
├── test_llm_triage.py                   # LLM triage tests
├── test_smoke_utils.py                  # Smoke utility tests
├── arch/                                # AST enforcement + sub-package layer contracts
│   ├── __init__.py
│   ├── test_ast_rules.py
│   ├── test_import_paths.py
│   ├── test_layer_enforcement.py
│   ├── test_registry.py
│   └── test_subpackage_isolation.py
├── cli/                                 # CLI command tests
│   ├── __init__.py
│   ├── test_cli_cook.py
│   ├── test_cli_doctor.py
│   ├── test_cli_init.py
│   └── test_cli_install.py
├── config/                              # Config loading tests
│   ├── __init__.py
│   └── test_config.py
├── contracts/                           # Protocol satisfaction + package gateway contracts
│   ├── __init__.py
│   ├── test_instruction_surface.py
│   ├── test_l1_packages.py
│   ├── test_package_gateways.py
│   ├── test_protocol_satisfaction.py
│   └── test_version_consistency.py
├── core/                                # Core layer tests
│   ├── __init__.py
│   ├── test_core.py
│   ├── test_io.py
│   ├── test_logging.py
│   ├── test_types.py
│   └── test_version.py
├── execution/                           # Subprocess integration + session tests
│   ├── __init__.py
│   ├── test_commands.py
│   ├── test_db.py
│   ├── test_github.py
│   ├── test_headless.py
│   ├── test_linux_tracing.py
│   ├── test_process_channel_b.py
│   ├── test_process_jsonl.py
│   ├── test_process_kill.py
│   ├── test_process_pty.py
│   ├── test_process_run.py
│   ├── test_quota.py
│   ├── test_session.py
│   └── test_testing.py
├── infra/                               # CI/CD and security configuration tests
│   ├── __init__.py
│   ├── test_ci_dev_config.py
│   ├── test_remove_clone_guard.py
│   ├── test_security_config.py
│   └── test_taskfile.py
├── migration/                           # Migration engine and store tests
│   ├── __init__.py
│   ├── test_engine.py
│   ├── test_loader.py
│   └── test_store.py
├── pipeline/                            # Audit log, gate, token log tests
│   ├── __init__.py
│   ├── test_audit.py
│   ├── test_context.py
│   ├── test_gate.py
│   └── test_tokens.py
├── recipe/                              # Recipe I/O, validation, schema tests
│   ├── __init__.py
│   ├── test_contracts.py
│   ├── test_io.py
│   ├── test_loader.py
│   ├── test_recipe_structures.py
│   ├── test_schema.py
│   ├── test_semantic_rules.py
│   ├── test_smoke_pipeline.py
│   └── test_validator.py
├── server/                              # Server unit tests (tool handlers)
│   ├── __init__.py
│   ├── conftest.py                      # tool_ctx fixture (imports MockSubprocessRunner from tests.conftest)
│   ├── test_factory.py
│   ├── test_git.py
│   ├── test_server_init.py
│   ├── test_service_wrappers.py         # REQ-ARCH-006/007: DefaultRecipeRepository and DefaultMigrationService
│   ├── test_tools_clone.py
│   ├── test_tools_execution.py
│   ├── test_tools_git.py
│   ├── test_tools_integrations.py
│   ├── test_tools_recipe.py
│   ├── test_tools_status.py
│   └── test_tools_workspace.py
└── workspace/                           # Workspace and clone tests
    ├── __init__.py
    ├── test_cleanup.py
    ├── test_clone.py
    └── test_skills.py

temp/                        # Temporary/working files (gitignored)
```
