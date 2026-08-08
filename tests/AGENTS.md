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
  without global state leakage. Gate starts closed (matching production) — use
  `tool_ctx_kitchen_open` when a test needs the gate open.
- The `minimal_ctx` fixture (conftest.py) provides a lightweight `ToolContext` using only
  L0+L1 imports (core, pipeline, config). Use for tests that only need gate, audit,
  token_log, timing_log, or config — no server factory, no L2/L3 service wiring. Does NOT
  monkeypatch `server._state._ctx`. Gate starts closed (matching production). Guard tests
  in `test_conftest.py` enforce the import boundary via AST analysis.
- Both `tool_ctx` and `minimal_ctx` start with gate closed to match production behavior.
  Use `tool_ctx_kitchen_open` or `build_ctx_open` for tests that need an open gate.
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

**In-scope directories:** All test directories are in scope. Enforced by `tests/arch/test_size_markers.py` via AST scan. Root-level `test_*.py` files are covered by `test_root_test_files_have_size_marker`.

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

## Environment Parity

The test harness sets env vars (via Taskfile `env:` blocks) that diverge from
production. Every override is registered with a justification in
`TEST_HARNESS_ENV_OVERRIDES` (`tests/_test_env_parity.py`), enforced in both
directions by `tests/contracts/test_test_env_parity.py` — its failure messages
walk you through registration. What no contract test can catch: an override
silently masking the production behavior your test needs to observe. When that
happens (e.g. observing real bytecode writes under `PYTHONDONTWRITEBYTECODE=1`),
build the child-process env with the override's parity helper — a plain function
in `tests/conftest.py` named by the registry's `parity_fixture` field, e.g.
`production_interpreter_env()` — instead of popping env vars ad hoc.

## Performance

- `PYTHONDONTWRITEBYTECODE=1` is set via Taskfile — no `.pyc` disk writes (registered
  in `TEST_HARNESS_ENV_OVERRIDES` with `production_interpreter_env` as parity fixture)
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
   - **Aggressive mode override**: Uses `git diff HEAD --name-only` (working-tree-only) instead of merge-base diff. This prevents committed-but-old files from inflating the changed set.
3. **Bucket A**: If any "global impact" file changed (conftest.py, pyproject.toml, etc.) -> full run
4. **Large changeset**: >30 files -> full run (conservative only; disabled in aggressive mode)
5. **Classification**: src Python -> layer cascade, test Python -> direct, other Python -> manifest lookup, non-Python -> manifest lookup
6. **Always-run**: `arch/` + `contracts/` always included (+ `infra/` + `docs/` in conservative mode)
7. **Deselection**: `pytest_collection_modifyitems` deselects items outside scope paths

**Modes**:

| Mode | Cascade | Always-run | Use case |
|------|---------|-----------|----------|
| `conservative` | Wide (L0 core -> all layers) | arch, contracts, infra, docs | CI, merge gates |
| `aggressive` | Narrow (each package -> itself) | arch, contracts | Local dev |
| `none` | N/A | N/A | Full run (default) |

### Aggressive Mode Behavioral Notes

**Commit-then-test escape window:** After committing a file, `git diff HEAD` no longer
shows it, so tests for that file won't run locally in aggressive mode until it's modified
again. This is by design — committed changes are validated by CI on the PR. To test
committed-but-unpushed changes locally, use `task test-filtered` (conservative mode with
merge-base diff) or push to trigger CI.

**Stash behavior:** After `git stash`, the working tree is clean — only always-run tests
(arch/, contracts/) execute. Pop the stash before testing.

**Size filter exemption:** In aggressive mode, tests under `arch/` and `contracts/` are
exempt from the size-based deselection filter. All other directories follow the standard
rule: unannotated tests default to `large` and are deselected.

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
`load_coverage_map()` (`tests/_test_filter.py:1328`) returns `None` if `test-source-map.json`
is older than 30 days. When this happens, Step 7 silently falls back
to directory-level cascade — no error is raised. Refresh cadence:
- Run `task coverage-audit` after any architectural change that adds or moves source files.
- Run at least once per calendar month if using the coverage oracle in CI (conservative or aggressive mode).

```
tests/
├── arch/                                # AST enforcement + sub-package layer contracts (see arch/AGENTS.md)
├── assets/                              # Vendored asset integrity tests
├── cli/                                 # CLI command tests (see cli/AGENTS.md)
├── config/                              # Config loading tests
├── contracts/                           # Protocol satisfaction + package gateway contracts (see contracts/AGENTS.md)
├── core/                                # Core layer tests (see core/AGENTS.md)
├── docs/                                # Documentation integrity tests
├── execution/                           # Subprocess integration + session tests (see execution/AGENTS.md)
├── fleet/                               # Fleet campaign + dispatch tests (see fleet/AGENTS.md)
├── hooks/                               # Hook script tests (see hooks/AGENTS.md)
├── infra/                               # CI/CD and security configuration tests (see infra/AGENTS.md)
├── integration/                         # Cross-layer integration tests
├── fixtures/
│   └── context_admission_journals/      # Versioned content-free golden journal vectors
├── migration/                           # Migration engine and store tests
├── pipeline/                            # Audit log, gate, fidelity, and PR-gate tests
├── planner/                             # Planner manifest, validation, and compilation tests (see planner/AGENTS.md)
├── recipe/                              # Recipe I/O, validation, schema tests (see recipe/AGENTS.md)
│   └── fixtures/                        # YAML test data: sample recipes, expected diagram output
├── server/                              # Server unit tests — tool handlers (see server/AGENTS.md)
├── skills/                              # Skill contract and compliance tests (see skills/AGENTS.md)
├── skills_extended/                     # Extended skill tests
└── workspace/                           # Workspace and clone tests (see workspace/AGENTS.md)

temp/                        # Temporary/working files (gitignored)
```

## Retirement Registries

Root `AGENTS.md` § 3.1 states the invariant once; this section is the authoritative
detail, kept beside the contract tests that enforce it. Renaming or retiring a
registered entity must update its retirement registry in the SAME commit:

- **Hook scripts** (`src/autoskillit/hooks/`): update `HOOK_REGISTRY` in
  `hook_registry.py` AND add the old basename to `RETIRED_SCRIPT_BASENAMES`.
  `test_no_retired_name_has_a_live_file` fails otherwise.
- **Skills** (`src/autoskillit/skills_extended/` or `skills/`): update the skill's
  `SKILL.md` `name:` field AND add the old directory name to `RETIRED_SKILL_NAMES`
  in `src/autoskillit/core/types/_type_constants.py`.
  `test_no_retired_skill_name_has_a_live_directory` fails otherwise.
- **Install artifact shapes** (`~/.autoskillit/`, `~/.claude/plugins/`): changing an
  artifact's *shape* (symlink → real directory, file → directory, …) must add an entry
  to `RETIRED_INSTALL_ARTIFACT_SHAPES` in `_type_constants.py`. `~/.autoskillit/`
  persists across years of releases while every contract test builds it fresh in
  `tmp_path` — a shape change with no registry entry strands every pre-existing
  install and no test notices. `test_no_retired_artifact_shape_is_unhandled` and
  `test_reconciler_handles_every_retired_artifact_shape` fail otherwise.
- **Skill contract validations**: adding or tightening an `invalid_reason`-producing
  skill validation must mint a `SkillInvalidityKind` member AND register a
  `SkillContractRemediationDef` in `SKILL_CONTRACT_REMEDIATIONS`
  (`core/types/_type_constants.py`), extending
  `tests/contracts/fixtures/skill_contract_corpus/` when the new validation would
  strand a previously-valid shape. Project-local skill copies persist in external
  repos with no way to see a tightened contract coming; the registry forces every new
  validation to declare a `DETERMINISTIC` migration or `ADVISORY` hint before it can
  ship. `tests/contracts/test_skill_contract_remediations.py` fails otherwise.

## run_skill Parameter-Role Ledgers (#4402)

Two frozen ledgers guard the junctions where the run_skill parameter-role
authority mechanism can silently drift. Same discipline as the config-key
ledger (#4303, `test_config_key_ledger.py`), applied to `ToolParamRole` and
`RecipeStep` field classification — inline Python dict literals rather than
external `.txt` files, since these are name→classification mappings whose
values carry structure (`inert-tracked:#NNNN` is itself validated), not flat
name lists:

- **`tests/contracts/test_run_skill_kwarg_ledger.py`** — a frozen
  `(param_name, role)` table diffed bidirectionally against the live
  `get_tool_def("run_skill").params` registry. A parameter added, removed, or
  re-roled without a matching ledger edit fails CI, naming the drifted entry.
  Ledger edits ship in the SAME commit as the registry change they witness —
  a role change alters gate admission behavior, so the diff must be visible
  at review time, not discovered later.
- **`tests/contracts/test_recipe_step_field_ledger.py`** — a frozen
  `RecipeStep` field-name → classification table
  (`execution` / `composition` / `validation-only` / `inert-tracked:#NNNN`),
  diffed bidirectionally against `dataclasses.fields(RecipeStep)`. A new field
  without a conscious classification fails; an `inert-tracked` entry without a
  live issue reference fails. Same same-commit rule: classify a new field
  when you add it, and file a tracking issue immediately if it has no runtime
  consumer yet rather than leaving a silently-inert field for someone else to
  rediscover.

**`tool_ctx_ready_recipe`** (`tests/server/conftest.py`) is the required
fixture entry point for attested-path server tests. `tool_ctx_kitchen_open`
alone leaves `recipe_initialization_state = NoActiveRecipe()` and cannot
reach the attested `run_skill` branch — the fixture drives the real
production `open_kitchen` → credit sections → pull step →
`complete_recipe_initialization` flow so tests exercise a genuinely attested
context (proven by `test_tool_ctx_ready_recipe_fixture_yields_genuine_attestation`
round-tripping the installed snapshot's template digest), never the
low-level `bind_recipe`/`build_recipe_execution_snapshot`/
`install_recipe_execution` chain directly — those functions precondition on
`InitializingRecipe` → `ReadyRecipe` staging that only the production flow
performs, so bypassing it would test a context no real session ever has.
