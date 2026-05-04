# Test Development Guidelines

## xdist Compatibility

All tests run under `-n 4` (xdist default: `--dist load`). Every test must be safe for parallel execution:
- Use `tmp_path` for filesystem isolation — never write to shared locations
- Session-scoped fixtures run once per worker process, not once globally
- Module-level globals are per-worker (separate processes) — no cross-worker state sharing
- Use `monkeypatch.setattr()` for all module-level state mutations — never bare assignment
- Source directories passed to `clone_repo` must be **subdirectories** of `tmp_path`,
  not `tmp_path` itself. When `source_dir = tmp_path`, `clone_repo` places
  `autoskillit-runs/` at `tmp_path.parent` (worker-shared). Use `source_dir = tmp_path / "repo"`.

**FastMCP singleton visibility state:** `mcp.enable(tags=...)` and `mcp.disable(tags=...)`
append entries to `mcp._transforms` — a list that never shrinks. Calling `mcp.disable()`
does NOT undo a previous `mcp.enable()`; it adds another entry. Tests that call either
method must use the directory-level conftest autouse fixture which calls
`mcp._transforms.clear()` and re-applies the baseline state (e.g.,
`mcp.disable(tags={"kitchen"})`). New test classes that need their own enable/disable
calls must add a class-level autouse fixture following the same clear+restore pattern.
Never rely on inverse method calls for cleanup.

## Fixture Discipline

- The `tool_ctx` fixture (conftest.py) provides a fully isolated `ToolContext` via
  `make_context()` — a full-stack L3 fixture that imports all production layers. Use for
  server integration tests that need executor, tester, recipes, or other service fields.
  It monkeypatches `server._ctx` so all server tool handler calls use the test context
  without global state leakage.
- The `minimal_ctx` fixture (conftest.py) provides a lightweight `ToolContext` using only
  L0+L1 imports (core, pipeline, config). Use for tests that only need gate, audit,
  token_log, timing_log, or config — no server factory, no L2/L3 service wiring. Does NOT
  monkeypatch `server._state._ctx`. Guard tests in `test_conftest.py` enforce the import
  boundary via AST analysis.
- To test with the kitchen closed, set `ctx.gate = DefaultGateState(enabled=False)` at
  the start of the test or in a class-level autouse fixture (see `_close_kitchen` in
  `test_instruction_surface.py` for an example).
- Never use bare assignment or `try/finally` to restore server state — use `monkeypatch` or
  rely on the fixture's teardown.

## Layer Markers

Every `test_*.py` file in a source-layer-mirroring directory carries a module-level
`pytestmark` with a `layer` marker matching the directory name:

```python
pytestmark = [pytest.mark.layer("execution")]
```

**In-scope directories:** core, config, pipeline, execution, workspace, recipe,
migration, server, cli.

**Out of scope:** arch/, contracts/, infra/, docs/, skills/, hooks/, skills_extended/.

When a file already defines `pytestmark` for other markers (e.g., `skipif`, `anyio`),
use list form and place the `layer` marker first.

The `layer` marker is registered in `pyproject.toml`. Conftest validates at collection
time that marker values match directories (warnings on mismatch).
`tests/arch/test_layer_markers.py` enforces completeness and correctness via AST scan.

**Usage:** `pytest -m 'layer("core")'` runs only L0 core tests.

## Size Markers

Test files in annotated directories carry a size marker indicating resource constraints:

```python
pytestmark = [pytest.mark.layer("core"), pytest.mark.small]
```

**Size definitions (Google-style):**

| Marker | Constraints | Examples |
|--------|------------|---------|
| `small` | No persistent I/O, no network, no subprocess. RAM-backed tmpfs via `tmp_path` IS allowed. | Pure logic, string parsing, in-memory dataclass tests |
| `medium` | Filesystem and subprocess allowed. No network, no external services. | Tests spawning child processes, real file system operations |
| `large` | Everything allowed. Full integration. Default for unannotated tests. | End-to-end tests, network calls, Claude API access |

**In-scope directories:** core, pipeline (initial rollout). Other directories follow incrementally.

**Aggressive filter behavior:** When `AUTOSKILLIT_TEST_FILTER=aggressive`, only `small` and `medium` tests run. Unannotated tests default to `large` and are deselected.

**Rules:**
- Each file has exactly one size marker — no conflicts (enforced by `tests/arch/test_size_markers.py`)
- Place size marker after the `layer` marker in the `pytestmark` list
- When in doubt, use `medium` — it's safer to over-classify than under-classify
- `tests/arch/test_size_markers.py` enforces completeness via AST scan

**Usage:** `pytest -m small` runs only small tests. `pytest -m 'small or medium'` excludes large tests.

## Placement Convention: tests/skills/ vs tests/contracts/

- `tests/skills/` — tests that exercise the skill loader, skill discovery, or skill
  resolution infrastructure (SkillResolver, SessionSkillManager, etc.)
- `tests/contracts/` — tests that verify SKILL.md contract content: required sections,
  output patterns, schema validity

## Performance

- `PYTHONDONTWRITEBYTECODE=1` is set via Taskfile — no `.pyc` disk writes
- Test temp I/O is routed to platform-resolved paths:
  - **Linux / WSL2**: `/dev/shm/pytest-tmp` (kernel tmpfs, RAM-backed)
  - **macOS**: `/tmp/pytest-tmp` (disk-backed system default)
- `TMPDIR` is set to the platform path via Taskfile — all `tempfile` calls are routed there
- `--basetemp` is passed to pytest — `tmp_path` fixtures resolve to the platform path
- `cache_dir` is redirected to the platform cache path — no stray pytest cache writes
- `test_tmp_path_is_ram_backed` in `tests/arch/test_ast_rules.py` enforces the `/dev/shm` prefix
  on Linux; on macOS it is a no-op (disk temp is acceptable there)

## Path Filtering

Tests support opt-in path-based filtering to run only the test directories affected by
changed files. Controlled by env var + CLI flags:

- **Opt-in**: Set `AUTOSKILLIT_TEST_FILTER=1` (or `=conservative` / `=aggressive`)
- **CLI override**: `--filter-mode=conservative|aggressive|none`
- **Base ref override**: `--filter-base-ref=<branch>` (default: reads `AUTOSKILLIT_TEST_BASE_REF` then `GITHUB_BASE_REF`)

**Filter algorithm** (`tests/_test_filter.py`):

1. **Fail-open gate**: If env var is unset/falsy, all tests run. On any error, all tests run.
2. **Changed files**: `git merge-base HEAD base_ref` → SHA, then `git diff --name-only <sha>` (working tree vs merge-base: committed + staged + unstaged tracked) + `git ls-files --others --exclude-standard` (new untracked files). Union of all three — a strict superset of the old three-dot form. **Known limitation**: `git rm --cached` (stage-only deletions) are not captured — the file still exists on disk so the working-tree diff misses the deletion. This is acceptable given the fail-open design.
3. **Bucket A**: If any "global impact" file changed (conftest.py, pyproject.toml, etc.) -> full run
4. **Large changeset**: >30 files -> full run
5. **Classification**: src Python -> layer cascade, test Python -> direct, non-Python -> manifest lookup
6. **Always-run**: `arch/` + `contracts/` always included (+ `infra/` + `docs/` in conservative mode)
7. **Deselection**: `pytest_collection_modifyitems` deselects items outside scope paths

**Modes**:

| Mode | Cascade | Always-run | Use case |
|------|---------|-----------|----------|
| `conservative` | Wide (L0 core -> all layers) | arch, contracts, infra, docs | CI, merge gates |
| `aggressive` | Narrow (each package -> itself) | arch, contracts | Local dev |
| `none` | N/A | N/A | Full run (default) |

## Coverage Audit

A quarterly coverage audit validates that the test suite covers all production functions
and that the test filter cascade maps are not hiding blind spots.

**Schedule:** Run `task coverage-audit` quarterly (January, April, July, October) or
after significant architectural changes (new subpackages, major refactors).

**Workflow:**
1. `task coverage-audit` runs the full test suite with `--cov-context=test --cov-branch`
2. `scripts/compare-coverage-ast.py` queries the `.coverage` SQLite database
3. AST-derived function map is compared against actual coverage
4. Report identifies uncovered and partially covered functions
5. Results saved to `temp/coverage-audit-{timestamp}.json`

**Interpreting results:**
- **Uncovered functions**: Production code with zero test coverage — potential blind spots
  in the test filter cascade maps
- **Partially covered functions**: Functions where some branches are untested
- Exit code is always 0 (audit tool, not a gate)

**Coverage oracle staleness guard:**
`load_coverage_map()` (`tests/_test_filter.py:530`) returns `None` if `test-source-map.json`
is older than 30 days. When this happens, Step 7 of the aggressive filter silently falls back
to directory-level cascade — no error is raised. Refresh cadence:
- Run `task coverage-audit` after any architectural change that adds or moves source files.
- Run at least once per calendar month if using `AUTOSKILLIT_TEST_FILTER=aggressive` in CI.

```
tests/
├── CLAUDE.md                            # Universal test guidelines (this file)
├── __init__.py
├── _helpers.py
├── _subprocess_ready.py
├── _test_filter.py                      # Test filter manifest: glob-to-test-directory mapping
├── conftest.py                          # Shared fixtures: minimal_ctx, tool_ctx, _make_result, _make_timeout_result
├── fakes.py                             # Protocol-based test fakes: InMemory*, MockSubprocessRunner
├── test_conftest.py                     # Tests for conftest fixtures
├── test_fakes_conformance.py
├── test_llm_triage.py
├── test_no_orchestration_tier_language.py
├── test_smoke_utils.py
├── test_test_filter.py
├── test_test_filter_cascade.py
├── test_test_filter_content_aware.py
├── test_test_filter_core_cascade.py
├── test_test_filter_execution_cascade.py
├── test_test_filter_plugin.py
├── test_test_filter_scope_extras.py
├── test_test_filter_step7.py
├── test_test_filter_tiered_always_run.py
├── test_version.py                      # Version health tests
├── arch/                                # AST enforcement + sub-package layer contracts (see arch/CLAUDE.md)
├── assets/                              # Vendored asset integrity tests (see assets/CLAUDE.md)
├── cli/                                 # CLI command tests (see cli/CLAUDE.md)
├── config/                              # Config loading tests (see config/CLAUDE.md)
├── contracts/                           # Protocol satisfaction + package gateway contracts (see contracts/CLAUDE.md)
├── core/                                # Core layer tests (see core/CLAUDE.md)
├── docs/                                # Documentation integrity tests (see docs/CLAUDE.md)
├── execution/                           # Subprocess integration + session tests (see execution/CLAUDE.md)
├── fleet/                               # Fleet campaign + dispatch tests (see fleet/CLAUDE.md)
├── hooks/                               # Hook script tests (see hooks/CLAUDE.md)
├── infra/                               # CI/CD and security configuration tests (see infra/CLAUDE.md)
├── migration/                           # Migration engine and store tests (see migration/CLAUDE.md)
├── pipeline/                            # Audit log, gate, fidelity, and PR-gate tests (see pipeline/CLAUDE.md)
├── planner/                             # Planner manifest, validation, and compilation tests (see planner/CLAUDE.md)
├── recipe/                              # Recipe I/O, validation, schema tests (see recipe/CLAUDE.md)
├── server/                              # Server unit tests — tool handlers (see server/CLAUDE.md)
├── skills/                              # Skill contract and compliance tests (see skills/CLAUDE.md)
├── skills_extended/                     # Extended skill tests (see skills_extended/CLAUDE.md)
└── workspace/                           # Workspace and clone tests (see workspace/CLAUDE.md)

temp/                        # Temporary/working files (gitignored)
```
