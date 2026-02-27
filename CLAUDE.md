# **AutoSkillit: Development Guidelines**

Mandatory instructions for AI-assisted development in this repository.

## **1. Core Project Goal**

A Claude Code plugin that orchestrates automated skill-driven workflows using headless sessions. It provides 16 MCP tools (run_cmd, run_python, run_skill, run_skill_retry, test_check, merge_worktree, reset_test_dir, classify_fix, reset_workspace, read_db + ungated kitchen_status, list_recipes, load_recipe, validate_recipe, get_pipeline_report, get_token_summary) with 10 gated behind MCP prompts for user-only activation, and 13 bundled skills registered as `/autoskillit:*` slash commands.

## **2. General Principles**

  * **Follow the Task Description**: The issue or ticket is your primary source of truth.
  * **Adhere to Task Scope**: Do not work on unassigned features or unrelated refactoring.
  * **Implement Faithfully**: Produce functionally correct implementations. Do not add unrequested features.
  * **Adhere to Project Standards**: Write clean, maintainable Python following established conventions.

## **3. Critical Rules - DO NOT VIOLATE**

### **3.0. Skill Invocations Are Orders**

  * When a message includes a `/skill-name`, execute it via the Skill tool **BEFORE** any other action. No exceptions.
  * Never skip or substitute a skill invocation based on your own judgment.

### **3.1. Code and Implementation**

  * **Do Not Oversimplify**: Implement logic with required complexity. No shortcuts that compromise correctness.
  * **Respect the Existing Architecture**: Build on established patterns. Understand existing code before modifying.
  * **Address the Root Cause**: Debug to find and fix root causes. No hardcoded workarounds.
  * **No Backward Compatibility Hacks**: No comments about dead code. Remove dead code entirely.
  * **Avoid Redundancy**: Do not duplicate logic or utilities.
  * **Use Current Package Versions**: Web search for current stable versions when adding dependencies.

### **3.2. File System**

  * **Temporary Files:** All temp files must go in the project's `temp/` directory.
  * **Do Not Add Root Files**: Never create new root files unless explicitly required.
  * **Never commit unless told to do so**

### **3.3. Pipeline Execution**

  * **Orchestrator Discipline**: When executing a pipeline script (loaded via `load_recipe`), NEVER use native Claude Code tools directly. The following tools are prohibited for the orchestrator: Read, Grep, Glob, Edit, Write, Bash, Task, Explore, WebFetch, WebSearch, NotebookEdit.
  * **Delegate Through Headless Sessions**: All code reading, searching, editing, and investigation MUST go through `run_skill` or `run_skill_retry`, which launch headless sessions with full tool access.
  * **Route Failures, Do Not Investigate**: When a pipeline step fails, follow the step's `on_failure` route. Do NOT use native tools to diagnose failures — the downstream skill has diagnostic access that the orchestrator does not.
  * **Use `run_cmd` for Shell Access**: If shell commands are needed during a pipeline, use the `run_cmd` MCP tool, not the native Bash tool.

## **4. Testing Guidelines**

The project uses pytest with pytest-asyncio for async test support. Tests run in parallel via pytest-xdist (`-n 4`). All tests must be safe for parallel execution.

  * **Run tests**: `task test-all` from the project root. This is the **only** test command. Never use `pytest`, `python -m pytest`, or any other test runner directly.
  * **Always run tests at end of task**
  * **Fix failing tests immediately**
  * **Add tests for new features**
  * **Follow existing test patterns** in `tests/` — avoid test code redundancy
  * **Worktree setup**: Use `task install-worktree` in worktrees. Never hardcode `uv venv`/`pip install` in skills or plans.

## **5. Pre-commit Hooks**

Install hooks after cloning: `pre-commit install`

Hooks run automatically on commit. To run manually: `pre-commit run --all-files`

Configured hooks: ruff format (auto-fix), ruff check (auto-fix), mypy type checking.

## **6. Architecture**

```
src/autoskillit/
├── __init__.py              # Package version + NullHandler for stdlib compat
├── __main__.py              # python -m autoskillit entry point (delegates to cli)
├── .claude-plugin/          # Plugin metadata (plugin.json)
├── .mcp.json                # MCP server config for plugin loading
├── _audit.py                # FailureRecord, AuditLog, _audit_log singleton
├── _context.py              # ToolContext DI container (config, audit, token_log, gate, plugin_dir, runner)
├── _doctor.py               # Doctor command — 7 project setup checks
├── _gate.py                 # GateState, GATED_TOOLS, UNGATED_TOOLS, gate_error_result
├── _io.py                   # _atomic_write and _load_yaml (infrastructure primitives)
├── _llm_triage.py           # LLM-assisted contract staleness triage (Haiku subprocess)
├── _logging.py              # Centralized structlog configuration (get_logger, configure_logging)
├── _token_log.py            # TokenEntry, TokenLog, _token_log singleton
├── _yaml.py                 # load_yaml, dump_yaml (simple YAML helpers)
├── cli.py                   # CLI: serve, init, config show, skills, workflows, doctor
├── config.py                # Dataclass config + YAML loading (layered resolution)
├── db_tools.py              # Read-only SQLite execution with defence-in-depth
├── failure_store.py         # Migration failure persistence (JSON, atomic writes)
├── git_operations.py        # Git merge workflow for merge_worktree (L3 service)
├── headless_runner.py       # Headless Claude session orchestration (L3 service)
├── migration_engine.py      # Migration orchestration — engine + adapters (Layer B)
├── migration_loader.py      # Migration note discovery and version chaining
├── process_lifecycle.py     # Subprocess management (kill trees, temp I/O, timeouts)
├── recipe_io.py             # Recipe I/O: load_recipe, list_recipes, iter_steps_with_context, find_recipe_by_name
├── recipe_loader.py         # Path-based recipe metadata utilities for migration_engine
├── recipe_schema.py         # Recipe, RecipeStep, DataFlowWarning, AUTOSKILLIT_VERSION_KEY
├── recipe_validator.py      # validate_recipe, run_semantic_rules, generate_recipe_card, analyze_dataflow
├── server.py                # FastMCP server with 16 MCP tools + 2 prompts
├── session_result.py        # ClaudeSessionResult, SkillResult, _compute_success, _compute_retry, _truncate, extract_token_usage
├── skill_resolver.py        # Bundled skill listing
├── test_runner.py           # Pytest output parsing and pass/fail adjudication (L3 service)
├── types.py                 # Shared type contracts: StrEnums, constants, generics
├── version.py               # Version health utilities (Layer 0)
├── workspace.py             # Directory teardown utilities (CleanupResult, preserve list)
├── skills/                  # 13 bundled skills (SKILL.md per skill)
│   ├── assess-and-merge/    │   ├── audit-impl/
│   ├── dry-walkthrough/     │   ├── implement-worktree/
│   ├── implement-worktree-no-merge/ │   ├── investigate/
│   ├── make-groups/         │   ├── make-plan/
│   ├── write-recipe/        │   ├── mermaid/
│   ├── rectify/             │   ├── retry-worktree/
│   ├── review-approach/     │   └── setup-project/
└── recipes/                 # 5 bundled recipe YAML definitions
    ├── audit-and-fix.yaml
    ├── bugfix-loop.yaml
    ├── implementation-pipeline.yaml
    ├── investigate-first.yaml
    └── smoke-test.yaml

tests/
├── CLAUDE.md                            # xdist compatibility guidelines
├── __init__.py
├── conftest.py                          # Shared fixtures (tools enabled + default config)
├── test_architecture.py                 # AST enforcement (no print(), no sensitive logger kwargs)
├── test_audit.py                        # Audit log and FailureRecord tests
├── test_ci_dev_config.py
├── test_cli.py                          # CLI command tests
├── test_config.py                       # Config loading tests
├── test_conftest.py
├── test_context.py
├── test_db_tools.py
├── test_failure_store.py
├── test_gate.py
├── test_instruction_surface_contract.py
├── test_io.py
├── test_llm_triage.py
├── test_logging.py                      # Logging infrastructure tests
├── test_migration_engine.py
├── test_migration_loader.py
├── test_process_lifecycle.py            # Subprocess integration tests
├── test_recipe_io.py
├── test_recipe_loader.py                # Recipe loader tests
├── test_recipe_schema.py
├── test_recipe_validator.py
├── test_server.py                       # Server unit tests
├── test_session_result.py
├── test_skill_resolver.py               # Skill resolution tests
├── test_smoke_pipeline.py
├── test_smoke_utils.py
├── test_token_log.py                    # Token usage tracking tests
├── test_types.py
├── test_version_consistency.py
└── test_workspace.py

temp/                        # Temporary/working files (gitignored)
```

### **Key Components**

  * **config.py**: Dataclass hierarchy (`AutomationConfig`) with layered YAML resolution: defaults → user (`~/.autoskillit/config.yaml`) → project (`.autoskillit/config.yaml`). No config file = current hardcoded defaults.
  * **cli.py**: CLI entry point. `autoskillit` (no args) starts the MCP server. Also provides `init` (prints plugin-dir path), `config show`, `skills list`, `recipes list/show`, `workspace init`, and `doctor`.
  * **config.py**: Dataclass hierarchy (`AutomationConfig`) with layered YAML resolution: defaults → user (`~/.autoskillit/config.yaml`) → project (`.autoskillit/config.yaml`). No config file = current hardcoded defaults.
  * **cli.py**: CLI entry point. `autoskillit` (no args) starts the MCP server. Also provides `init` (prints plugin-dir path), `config show`, `skills list`, `recipes list/show`, `workspace init`, `install`, `upgrade`, `migrate`, `cook`, and `doctor`.
  * **recipe_loader.py**: Path-based recipe metadata utilities for `migration_engine`. Exports `parse_recipe_metadata(path: Path) -> RecipeInfo`, which handles both plain YAML and frontmatter-format files (`---` delimited). Reads the `name`, `description`, `summary`, `source`, and `version` fields from the YAML document and returns a `RecipeInfo` instance. Recipe discovery (`list_recipes`, `load_recipe`) lives in `recipe_io.py`, not here.
  * **server.py**: FastMCP server. 10 gated tools require user activation via MCP prompts. 6 ungated tools (`kitchen_status`, `list_recipes`, `load_recipe`, `validate_recipe`, `get_pipeline_report`, `get_token_summary`) are always available. Uses ToolContext DI (`_context.py`) — single module-level `_ctx: ToolContext | None`. `_initialize(ctx)` wires everything at startup. Gate policy in `_gate.py`. `version_info()` is public. Registers `recipe://` resource handler.
  * **skill_resolver.py**: Lists bundled skills from the package `skills/` directory. `SkillResolver` (no args) scans for `SKILL.md` files.
  * **recipe_io.py**: Recipe I/O layer. `load_recipe(name, project_dir)` and `list_recipes(project_dir)` discover recipes from project and bundled sources. `iter_steps_with_context(recipe)` yields `(name, step, available_context)` with accumulated captures. `find_recipe_by_name(name, project_dir)` returns first match or None. `RecipeStep` supports an optional `model` field for per-step model selection.
  * **recipe_schema.py**: Recipe data models. `Recipe`, `RecipeStep`, `DataFlowWarning`, `AUTOSKILLIT_VERSION_KEY`. Zero autoskillit I/O dependencies.
  * **recipe_validator.py**: Recipe validation layer. `validate_recipe(recipe)` structural checks. `run_semantic_rules(recipe)` semantic rule engine (decorator-based registry). `generate_recipe_card(pipeline_path, recipes_dir)` returns dict and writes YAML to disk. `analyze_dataflow(recipe)` traces data flow. Uses `iter_steps_with_context` from `recipe_io.py` for context-aware validation.
  * **process_lifecycle.py**: Subprocess utilities for process tree cleanup, temp file I/O to avoid pipe blocking, and configurable timeouts. Uses `get_logger()` from `_logging.py`.
  * **_logging.py**: Centralized structlog configuration. `get_logger(name)` is the single import point for all production modules. `configure_logging()` is called once by the CLI `serve` command — routes all output to stderr via `WriteLoggerFactory`, never stdout.
  * **_audit.py**: Pipeline failure tracking. `AuditLog` captures every non-success result from `_build_skill_result()` into an in-memory list. `_audit_log` is the module-level singleton used by `server.py`. `get_pipeline_report` retrieves the accumulated failures.
  * **_token_log.py**: Pipeline token usage tracking. `TokenLog` accumulates token counts keyed by YAML step name. `_token_log` is the module-level singleton used by `server.py`. `get_token_summary` retrieves the accumulated per-step totals.
  * **_context.py**: ToolContext DI container. Holds `config`, `audit`, `token_log`, `gate`, `plugin_dir`, `runner`. Passed to `server._initialize(ctx)` at startup. All gated tools access config and gate state through the context instead of module-level singletons.
  * **_gate.py**: Gate policy layer. `GateState` dataclass with `enabled` flag. `GATED_TOOLS` and `UNGATED_TOOLS` frozensets (the source of truth for the MCP tool registry). `gate_error_result()` builds standard disabled-gate error JSON.
  * **_yaml.py**: Minimal YAML helpers (`load_yaml`, `dump_yaml`). Thin wrappers used internally where `_io._load_yaml` would be over-scoped.
  * **session_result.py**: Data extraction layer for Claude CLI output. `ClaudeSessionResult` dataclass. `SkillResult` typed result. `_compute_success`, `_compute_retry` policy functions. `extract_token_usage(stdout)` prefers `type=result` record totals. Depends on `types.py`, `_logging.py`. Imported by server.py.
  * **types.py**: Cross-cutting type contracts layer. StrEnum discriminators (`RetryReason`, `MergeFailedStep`, `MergeState`, `RestartScope`, `SkillSource`, `RecipeSource`, `Severity`) and canonical constants (`CONTEXT_EXHAUSTION_MARKER`, `PIPELINE_FORBIDDEN_TOOLS`, `SKILL_TOOLS`, `RETRY_RESPONSE_FIELDS`). Generic result wrappers (`LoadReport`, `LoadResult`). Zero autoskillit dependencies.
  * **_io.py**: Infrastructure layer filesystem primitives. Two functions: `_atomic_write(path, content)` (crash-safe write via temp file + `os.replace`) and `_load_yaml(source)` (path-or-string YAML loader in binary mode for portable UTF-8 handling). Zero autoskillit dependencies. Imported by failure_store.py, migration_loader.py, recipe_io.py, recipe_loader.py, and migration_engine.py.
  * **failure_store.py**: Persistence layer for migration failure tracking. `FailureStore` persists `MigrationFailure` records to `.autoskillit/temp/migrations/failures.json` via atomic writes (`_io.py`). `record_from_skill()` is the `run_python` entry point invoked by the migrate-recipes skill when retries are exhausted. Depends on `_io.py`. Imported by migration_engine.py and `_doctor.py`.
  * **git_operations.py**: L3 service module for the git merge workflow. `perform_merge(worktree_path, base_branch, *, config, runner)` executes the full merge pipeline: path validation → worktree verification → branch detection → test gate → fetch → rebase → main-repo merge → worktree cleanup. Uses injected `SubprocessRunner` so existing test mocks apply unchanged. Depends on `test_runner`, `session_result._truncate`, `_logging`, `config`, `types`. Imported by server.py.
  * **headless_runner.py**: L3 service module for headless Claude Code session orchestration. `run_headless_core(skill_command, cwd, ctx, *, model, step_name, add_dir, timeout, stale_threshold)` is the single public entry point shared by `run_skill` and `run_skill_retry`. Contains `_build_skill_result`, `_resolve_model`, `_ensure_skill_prefix`, `_inject_completion_directive`, `_session_log_dir`, and `_capture_failure`. Depends on `session_result`, `_context`, `_audit`, `_logging`, `types`. Imported by server.py.
  * **test_runner.py**: L3 service module for pytest output parsing and pass/fail adjudication. `parse_pytest_summary(stdout)` extracts structured outcome counts from `=`-delimited summary lines. `check_test_passed(returncode, stdout)` cross-validates exit code against output for defense against PIPESTATUS bugs. Depends only on `_logging`. Used by server.py `test_check` tool and `git_operations.py` merge test gate.
  * **migration_engine.py**: Orchestration layer for recipe and contract migration. Layer B domain logic — no FastMCP dependency, imported by server.py at module level. `MigrationEngine` dispatches to registered adapters: `RecipeMigrationAdapter` (LLM-driven via headless Claude session) and `ContractMigrationAdapter` (deterministic contract regeneration). ABC hierarchy: `MigrationAdapter` → `HeadlessMigrationAdapter` / `DeterministicMigrationAdapter`. `default_migration_engine()` factory builds the standard adapter set.
  * **migration_loader.py**: Data access layer for the migration version graph. Discovers and parses versioned migration YAML files from the bundled `migrations/` package directory. `list_migrations()` enumerates all notes; `applicable_migrations(script_version, installed_version)` chains applicable notes from the script's current version to the installed version using semver ordering. Depends on `_io.py` and `packaging`. Imported by `migration_engine.py`.
  * **db_tools.py**: Data access layer: read-only SQLite execution with defence-in-depth. Regex pre-validation rejects non-SELECT queries; OS-level `file:...?mode=ro` connection; `set_authorizer` callback blocks any non-SELECT/READ/FUNCTION engine operation. `_execute_readonly_query` is the main entry point. Depends only on `_logging.py`. Imported by server.py.
  * **version.py**: Version health layer. `version_info(plugin_dir: Path | str | None = None)` reads `plugin.json` from the plugin directory and compares with `autoskillit.__version__` to return `{"package_version", "plugin_json_version", "match"}`. Layer 0: no autoskillit imports except `__init__` for `__version__`. Imported by `server.py` (delegates its own `version_info()`) and `_doctor.py` (version consistency health check).
  * **workspace.py**: Infrastructure layer for directory teardown. `_delete_directory_contents(directory, preserve)` removes all items in a directory except preserved names, recording failures in `CleanupResult` without raising. Depends only on `_logging.py`. Imported by server.py.
  * **_doctor.py**: CLI support layer: project health checks. `run_doctor()` runs 7 checks: stale MCP servers, duplicate autoskillit registrations, plugin metadata presence, PATH availability, project config existence, version consistency (package vs plugin.json), and recipe migration health (via failure_store.py). Depends on `version.py` (for `version_info`), `failure_store.py`, `recipe_io.py`, `types.py`. Imported by `cli.py`.
  * **_llm_triage.py**: AI orchestration layer for contract staleness semantic triage. `triage_staleness(stale_items)` spawns a `claude -p` subprocess via `process_lifecycle.run_managed_async` (Haiku model) to determine whether SKILL.md changes are semantically meaningful. Falls back to `meaningful=True` on timeout, JSON parse error, or OS error. Depends on `_logging.py`, `recipe_validator.py`, `process_lifecycle.py`, `skill_resolver.py`.
  * **smoke_utils.py**: Utility callables for smoke-test pipeline `run_python` steps. `check_bug_report_non_empty(workspace)` reads `bug_report.json` and returns `{"non_empty": "true"/"false"}`. Zero autoskillit imports.

### **Plugin Structure**

The Python package directory (`src/autoskillit/`) is the plugin root:
  * `.claude-plugin/plugin.json` — plugin manifest (name, version, description)
  * `.mcp.json` — MCP server config (command: `autoskillit`)
  * `skills/` — 13 bundled skills discovered by Claude Code as `/autoskillit:*` slash commands
  * `pyproject.toml` declares `artifacts` to include dotfiles in the wheel

### **Skills**

13 bundled skills, invoked as `/autoskillit:<name>`. These are the building blocks that project-specific pipeline recipes (generated by `setup-project`) compose together.

Skills are discovered by Claude Code via the plugin structure. Headless sessions receive `--plugin-dir` automatically via `run_skill` and `run_skill_retry`. Project-specific pipeline recipes go in `.autoskillit/recipes/` as YAML files, discovered via `list_recipes` and loaded via `load_recipe`.

### **MCP Tools**

| Tool | Purpose |
|------|---------|
| `run_cmd` | Execute shell commands with timeout |
| `run_python` | Call a Python function by dotted module path (in-process) |
| `run_skill` | Run Claude Code headless with a skill command (passes `--plugin-dir`, optional `model` param) |
| `run_skill_retry` | Run Claude Code headless with API call limit (passes `--plugin-dir`, optional `model` param) |
| `test_check` | Run test suite in a worktree, returns PASS/FAIL |
| `merge_worktree` | Merge worktree branch after test gate passes |
| `reset_test_dir` | Clear test directory (reset guard marker) |
| `classify_fix` | Analyze worktree diff to determine restart scope (full vs partial) |
| `reset_workspace` | Reset workspace, preserving configured directories |
| `read_db` | Run read-only SQL query against SQLite database |
| `kitchen_status` | Return version health and config status (ungated) |
| `list_recipes` | List pipeline recipes from .autoskillit/recipes/ (ungated) |
| `load_recipe` | Load a recipe by name as raw YAML (ungated) |
| `validate_recipe` | Validate a pipeline recipe against the recipe schema (ungated) |
| `get_pipeline_report` | Return accumulated run_skill/run_skill_retry failure report (ungated) |
| `get_token_summary` | Return accumulated token usage grouped by step name (ungated) |
| `open_kitchen` (prompt) | User-only activation — type the open_kitchen prompt from the MCP prompt list |
| `close_kitchen` (prompt) | User-only deactivation — type the close_kitchen prompt from the MCP prompt list |

### **Tool Activation**

10 tools are gated by default. At the start of a session, the user must type
the `open_kitchen` prompt to activate. The exact prompt name is prefixed by
Claude Code based on how the server was loaded (e.g. `plugin_autoskillit_autoskillit`
for plugin installs). This uses MCP prompts (user-only, model cannot invoke)
and survives `--dangerously-skip-permissions`.

`kitchen_status`, `list_recipes`, `load_recipe`, `validate_recipe`, `get_pipeline_report`, and `get_token_summary` are ungated — available without calling `open_kitchen`.

### **Configuration**

All tool behavior is configurable via `.autoskillit/config.yaml`. No config file = hardcoded defaults (backward compatible). Run `autoskillit init` to generate a template.

**Resolution order:** defaults → `~/.autoskillit/config.yaml` (user) → `.autoskillit/config.yaml` (project). Project overrides user, user overrides defaults. Partial configs are fine — unset fields keep their defaults.

**Available settings:**

| Section | Key | Default | Description |
|---------|-----|---------|-------------|
| `test_check` | `command` | `["task", "test-all"]` | Test command for `test_check` and `merge_worktree` |
| `test_check` | `timeout` | `600` | Test command timeout in seconds |
| `classify_fix` | `path_prefixes` | `[]` | File path prefixes that trigger `full_restart` |
| `reset_workspace` | `command` | `null` | Reset command (`null` = not configured) |
| `reset_workspace` | `preserve_dirs` | `[]` | Directories preserved during reset |
| `implement_gate` | `marker` | `"Dry-walkthrough verified = TRUE"` | Required first line in plan files |
| `implement_gate` | `skill_names` | `["/autoskillit:implement-worktree", "/autoskillit:implement-worktree-no-merge"]` | Skills subject to dry-walkthrough gate |
| `safety` | `reset_guard_marker` | `".autoskillit-workspace"` | Marker file required for destructive ops |
| `safety` | `require_dry_walkthrough` | `true` | Enforce plan verification before implementation |
| `safety` | `test_gate_on_merge` | `true` | Run tests before allowing merge |
| `read_db` | `timeout` | `30` | Query timeout in seconds |
| `read_db` | `max_rows` | `10000` | Maximum rows returned per query |
| `model` | `default` | `null` | Default model for run_skill/run_skill_retry when step has no model field |
| `model` | `override` | `null` | Force all run_skill/run_skill_retry to use this model (overrides step YAML) |
| `token_usage` | `verbosity` | `"summary"` | Token table behavior: `"summary"` = render once at pipeline end; `"none"` = suppress entirely |
| `worktree_setup` | `command` | `null` | Worktree env setup command (`null` = auto-detect) |
| `run_skill` | `completion_drain_timeout` | `5.0` | Seconds to wait for Channel A (stdout heartbeat) to confirm data after Channel B (session log) signals completion. Prevents false-negative failures from the Channel B / Channel A race. |
