# **AutoSkillit: Development Guidelines**

Mandatory instructions for AI-assisted development in this repository.

## **1. Core Project Goal**

A Claude Code plugin that orchestrates automated skill-driven workflows using headless sessions. It provides 17 MCP tools (run_cmd, run_python, run_skill, run_skill_retry, test_check, merge_worktree, reset_test_dir, classify_fix, reset_workspace, read_db, migrate_recipe + ungated kitchen_status, list_recipes, load_recipe, validate_recipe, get_pipeline_report, get_token_summary) with 11 gated behind MCP prompts for user-only activation, and 17 bundled skills registered as `/autoskillit:*` slash commands.

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
├── _llm_triage.py           # LLM-assisted contract staleness triage (Haiku subprocess)
├── smoke_utils.py           # Utility callables for smoke-test pipeline run_python steps
├── version.py               # Version health utilities (Layer 0)
├── .claude-plugin/          # Plugin metadata (plugin.json)
├── .mcp.json                # MCP server config for plugin loading
├── core/                    # L0 foundation sub-package (zero autoskillit imports)
│   ├── __init__.py          #   Re-exports full public surface
│   ├── io.py                #   _atomic_write, ensure_project_temp, load_yaml, dump_yaml, YAMLError
│   ├── logging.py           #   get_logger, configure_logging, PACKAGE_LOGGER_NAME
│   └── types.py             #   StrEnums, protocols, constants (SubprocessRunner, LoadResult, etc.)
├── config/                  # L1 configuration sub-package
│   ├── __init__.py          #   Re-exports AutomationConfig
│   └── settings.py          #   Dataclass config + YAML loading (layered resolution)
├── pipeline/                # L1 pipeline state sub-package
│   ├── __init__.py          #   Re-exports ToolContext, GateState, AuditLog, TokenLog
│   ├── audit.py             #   FailureRecord, AuditLog, _audit_log singleton
│   ├── context.py           #   ToolContext DI container (config, audit, token_log, gate, plugin_dir, runner)
│   ├── gate.py              #   GateState, GATED_TOOLS, UNGATED_TOOLS, gate_error_result
│   └── tokens.py            #   TokenEntry, TokenLog, _token_log singleton
├── execution/               # L1 execution sub-package
│   ├── __init__.py          #   Re-exports public surface
│   ├── db.py                #   Read-only SQLite execution with defence-in-depth
│   ├── headless.py          #   Headless Claude session orchestration (L3 service)
│   ├── process.py           #   Subprocess management (kill trees, temp I/O, timeouts)
│   ├── quota.py             #   Quota-aware check: QuotaStatus, cache, fetch, check_and_sleep_if_needed
│   ├── session.py           #   ClaudeSessionResult, SkillResult, extract_token_usage
│   └── testing.py           #   Pytest output parsing and pass/fail adjudication
├── workspace/               # L1 workspace sub-package
│   ├── __init__.py          #   Re-exports CleanupResult, SkillResolver, clone_repo, remove_clone, push_clone_to_origin
│   ├── cleanup.py           #   Directory teardown utilities (CleanupResult, preserve list)
│   ├── clone.py             #   Clone-based run isolation: clone_repo, remove_clone, push_clone_to_origin
│   └── skills.py            #   Bundled skill listing (SkillResolver)
├── recipe/                  # L2 recipe sub-package
│   ├── __init__.py          #   Re-exports Recipe, RecipeStep, validate_recipe, load_recipe, etc.
│   ├── contracts.py         #   Contract card generation and staleness triage utilities
│   ├── io.py                #   load_recipe, list_recipes, iter_steps_with_context, find_recipe_by_name
│   ├── loader.py            #   Path-based recipe metadata utilities (parse_recipe_metadata, RecipeInfo)
│   ├── schema.py            #   Recipe, RecipeStep, DataFlowWarning, AUTOSKILLIT_VERSION_KEY
│   └── validator.py         #   validate_recipe, run_semantic_rules, analyze_dataflow
├── migration/               # L2 migration sub-package
│   ├── __init__.py          #   Re-exports MigrationEngine, applicable_migrations, FailureStore
│   ├── engine.py            #   MigrationEngine, adapter ABC hierarchy, default_migration_engine()
│   ├── loader.py            #   Migration note discovery and version chaining
│   └── store.py             #   FailureStore: migration failure persistence (JSON, atomic writes)
├── server/                  # L3 FastMCP server sub-package
│   ├── __init__.py          #   FastMCP app, _initialize(ctx), version_info(), recipe:// resource handler
│   ├── git.py               #   Git merge workflow for merge_worktree (perform_merge)
│   ├── helpers.py           #   Shared server-layer helpers (worktree setup, path utilities)
│   ├── prompts.py           #   MCP prompt handlers (open_kitchen, close_kitchen)
│   ├── tools_execution.py   #   run_cmd, run_python, run_skill, run_skill_retry tool handlers
│   ├── tools_git.py         #   merge_worktree, classify_fix tool handlers
│   ├── tools_recipe.py      #   migrate_recipe, load_recipe, list_recipes, validate_recipe tool handlers
│   ├── tools_status.py      #   kitchen_status, get_pipeline_report, get_token_summary tool handlers
│   ├── tools_workspace.py   #   test_check, reset_test_dir, reset_workspace, read_db tool handlers
│   └── _factory.py              #   Composition Root: make_context() wires ToolContext
├── cli/                     # L3 CLI sub-package
│   ├── __init__.py          #   Re-exports main entry point
│   ├── _doctor.py           #   Doctor command — 7 project setup checks
│   └── app.py               #   CLI: serve, init, config show, skills, recipes, workspace, doctor
├── migrations/              # Data: versioned migration YAML notes
│   └── __init__.py
├── recipes/                 # Bundled recipe YAML definitions
│   ├── audit-and-fix.yaml
│   ├── bugfix-loop.yaml
│   ├── implementation-pipeline.yaml
│   ├── investigate-first.yaml
│   └── smoke-test.yaml
└── skills/                  # 17 bundled skills (SKILL.md per skill)
    ├── assess-and-merge/     ├── audit-friction/
    ├── audit-impl/           ├── dry-walkthrough/
    ├── implement-worktree/   ├── implement-worktree-no-merge/
    ├── investigate/          ├── make-groups/
    ├── make-plan/            ├── migrate-recipes/
    ├── mermaid/              ├── pipeline-summary/
    ├── rectify/              ├── retry-worktree/
    ├── review-approach/      └── setup-project/
    └── write-recipe/

tests/
├── CLAUDE.md                            # xdist compatibility guidelines
├── __init__.py
├── conftest.py                          # Shared fixtures (tools enabled + default config)
├── test_architecture.py                 # AST enforcement + sub-package layer contracts
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
├── test_service_wrappers.py             # REQ-ARCH-006/007: DefaultRecipeRepository and DefaultMigrationService behavior
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

  * **config/settings.py**: Dataclass hierarchy (`AutomationConfig`) with layered YAML resolution: defaults → user (`~/.autoskillit/config.yaml`) → project (`.autoskillit/config.yaml`). No config file = current hardcoded defaults.
  * **cli/app.py**: CLI entry point. `autoskillit` (no args) starts the MCP server. Also provides `init` (prints plugin-dir path), `config show`, `skills list`, `recipes list/show`, `workspace init`, `install`, `upgrade`, `migrate`, `cook`, and `doctor`.
  * **cli/_doctor.py**: CLI support layer: project health checks. `run_doctor()` runs 7 checks: stale MCP servers, duplicate autoskillit registrations, plugin metadata presence, PATH availability, project config existence, version consistency (package vs plugin.json), and recipe migration health (via migration/store.py). Depends on `version.py`, `migration/store.py`, `recipe/io.py`, `core/types.py`. Imported by `cli/app.py`.
  * **server/__init__.py**: FastMCP server. 11 gated tools require user activation via MCP prompts. 6 ungated tools (`kitchen_status`, `list_recipes`, `load_recipe`, `validate_recipe`, `get_pipeline_report`, `get_token_summary`) are always available. Uses ToolContext DI (`pipeline/context.py`) — single module-level `_ctx: ToolContext | None`. `_initialize(ctx)` wires everything at startup. Gate policy in `pipeline/gate.py`. `version_info()` is public. Registers `recipe://` resource handler. **Ungated vs gated notifications:** Ungated tools accept no `ctx: Context` parameter and emit no MCP progress notifications. This is intentional — they are fast, lightweight reads. MCP notifications are reserved for long-running gated operations. This asymmetry is documented in each ungated tool's docstring.
  * **server/git.py**: L3 service module for the git merge workflow. `perform_merge(worktree_path, base_branch, *, config, runner)` executes the full merge pipeline: path validation → worktree verification → branch detection → test gate → fetch → rebase → main-repo merge → worktree cleanup. Uses injected `SubprocessRunner` so existing test mocks apply unchanged.
  * **server/helpers.py**: Shared server-layer utilities — worktree environment setup, path normalization, and other helpers shared across `tools_*.py` modules.
  * **server/prompts.py**: MCP prompt handlers for `open_kitchen` and `close_kitchen` activation prompts (user-only, model cannot invoke).
  * **server/tools_execution.py**: MCP tool handlers for `run_cmd`, `run_python`, `run_skill`, and `run_skill_retry`.
  * **server/tools_git.py**: MCP tool handlers for `merge_worktree` and `classify_fix`.
  * **server/tools_recipe.py**: MCP tool handlers for `migrate_recipe`, `load_recipe`, `list_recipes`, and `validate_recipe`.
  * **server/tools_status.py**: MCP tool handlers for `kitchen_status`, `get_pipeline_report`, and `get_token_summary`.
  * **server/tools_workspace.py**: MCP tool handlers for `test_check`, `reset_test_dir`, `reset_workspace`, and `read_db`.
  * **server/_factory.py**: Composition Root. `make_context(config, *, runner, plugin_dir)` creates a fully-wired `ToolContext` — the only location that legally instantiates all service fields simultaneously. Imported by `cli/app.py serve()` and tests that need an isolated context without the full server import chain.
  * **pipeline/audit.py**: Pipeline failure tracking. `AuditLog` captures every non-success result from `_build_skill_result()` into an in-memory list. `_audit_log` is the module-level singleton used by `server/__init__.py`. `get_pipeline_report` retrieves the accumulated failures.
  * **pipeline/context.py**: ToolContext DI container. Holds `config`, `audit`, `token_log`, `gate`, `plugin_dir`, `runner`. Passed to `server._initialize(ctx)` at startup. All gated tools access config and gate state through the context instead of module-level singletons.
  * **pipeline/gate.py**: Gate policy layer. `GateState` dataclass with `enabled` flag. `GATED_TOOLS` and `UNGATED_TOOLS` frozensets (the source of truth for the MCP tool registry). `gate_error_result()` builds standard disabled-gate error JSON.
  * **pipeline/tokens.py**: Pipeline token usage tracking. `TokenLog` accumulates token counts keyed by YAML step name. `_token_log` is the module-level singleton used by `server/__init__.py`. `get_token_summary` retrieves the accumulated per-step totals.
  * **execution/headless.py**: L3 service module for headless Claude Code session orchestration. `run_headless_core(skill_command, cwd, ctx, *, model, step_name, add_dir, timeout, stale_threshold)` is the single public entry point shared by `run_skill` and `run_skill_retry`. Contains `_build_skill_result`, `_resolve_model`, `_ensure_skill_prefix`, `_inject_completion_directive`, `_session_log_dir`, and `_capture_failure`.
  * **execution/session.py**: Data extraction layer for Claude CLI output. `ClaudeSessionResult` dataclass. `SkillResult` typed result. `_compute_success`, `_compute_retry` policy functions. `extract_token_usage(stdout)` prefers `type=result` record totals. Depends on `core/types.py`, `core/logging.py`.
  * **execution/process.py**: Subprocess utilities for process tree cleanup, temp file I/O to avoid pipe blocking, and configurable timeouts. Uses `get_logger()` from `core/logging.py`.
  * **execution/testing.py**: L3 service module for pytest output parsing and pass/fail adjudication. `parse_pytest_summary(stdout)` extracts structured outcome counts from `=`-delimited summary lines. `check_test_passed(returncode, stdout)` cross-validates exit code against output for defense against PIPESTATUS bugs. Depends only on `core/logging`.
  * **execution/db.py**: Data access layer: read-only SQLite execution with defence-in-depth. Regex pre-validation rejects non-SELECT queries; OS-level `file:...?mode=ro` connection; `set_authorizer` callback blocks any non-SELECT/READ/FUNCTION engine operation. `_execute_readonly_query` is the main entry point. Depends only on `core/logging.py`.
  * **execution/quota.py**: Quota-aware check for long-running pipeline recipes. `QuotaStatus` dataclass. `_read_credentials(path)` reads Bearer token from `~/.claude/.credentials.json`. `_read_cache(path, max_age)` returns fresh status or None. `_write_cache(path, status)` persists to cache (silent on failure). `_fetch_quota(credentials_path)` fetches 5-hour utilization from Anthropic quota API via `httpx`. `check_and_sleep_if_needed(config)` is the main async entry point — returns metadata dict; does NOT sleep. L1 module: depends only on stdlib, httpx, and `core/logging`.
  * **workspace/cleanup.py**: Infrastructure layer for directory teardown. `_delete_directory_contents(directory, preserve)` removes all items in a directory except preserved names, recording failures in `CleanupResult` without raising. Depends only on `core/logging.py`.
  * **workspace/clone.py**: Clone-based run isolation for pipeline recipes. `clone_repo(source_dir, run_name)` clones source into `../autoskillit-runs/<run_name>-<timestamp>/` and returns `{"clone_path", "source_dir"}`. `remove_clone(clone_path, keep)` tears down the clone (never raises). `push_clone_to_origin(clone_path, source_dir, branch)` propagates the merged branch back to the source repo via pull inversion (`git fetch` from the source side). L1 module: depends only on stdlib and `core/logging`.
  * **workspace/skills.py**: Lists bundled skills from the package `skills/` directory. `SkillResolver` (no args) scans for `SKILL.md` files.
  * **recipe/schema.py**: Recipe data models. `Recipe`, `RecipeStep`, `DataFlowWarning`, `AUTOSKILLIT_VERSION_KEY`. Zero autoskillit I/O dependencies.
  * **recipe/io.py**: Recipe I/O layer. `load_recipe(name, project_dir)` and `list_recipes(project_dir)` discover recipes from project and bundled sources. `iter_steps_with_context(recipe)` yields `(name, step, available_context)` with accumulated captures. `find_recipe_by_name(name, project_dir)` returns first match or None. `RecipeStep` supports an optional `model` field for per-step model selection.
  * **recipe/loader.py**: Path-based recipe metadata utilities for `migration/engine.py`. Exports `parse_recipe_metadata(path: Path) -> RecipeInfo`, which handles both plain YAML and frontmatter-format files (`---` delimited). Reads the `name`, `description`, `summary`, `source`, and `version` fields from the YAML document and returns a `RecipeInfo` instance. Recipe discovery (`list_recipes`, `load_recipe`) lives in `recipe/io.py`, not here.
  * **recipe/validator.py**: Recipe validation layer. `validate_recipe(recipe)` structural checks. `run_semantic_rules(recipe)` semantic rule engine (decorator-based registry). `analyze_dataflow(recipe)` traces data flow. Uses `iter_steps_with_context` from `recipe/io.py` for context-aware validation.
  * **recipe/contracts.py**: Contract card generation and LLM staleness triage utilities. `generate_recipe_card(pipeline_path, recipes_dir)` returns dict and writes YAML to disk. Imported by `_llm_triage.py`.
  * **migration/engine.py**: Orchestration layer for recipe and contract migration. Layer B domain logic — no FastMCP dependency. `MigrationEngine` dispatches to registered adapters: `RecipeMigrationAdapter` (LLM-driven via headless Claude session) and `ContractMigrationAdapter` (deterministic contract regeneration). ABC hierarchy: `MigrationAdapter` → `HeadlessMigrationAdapter` / `DeterministicMigrationAdapter`. `default_migration_engine()` factory builds the standard adapter set.
  * **migration/loader.py**: Data access layer for the migration version graph. Discovers and parses versioned migration YAML files from the bundled `migrations/` package directory. `list_migrations()` enumerates all notes; `applicable_migrations(script_version, installed_version)` chains applicable notes from the script's current version to the installed version using semver ordering. Depends on `core/io.py` and `packaging`.
  * **migration/store.py**: Persistence layer for migration failure tracking. `FailureStore` persists `MigrationFailure` records to `.autoskillit/temp/migrations/failures.json` via atomic writes (`core/io.py`). `record_from_skill()` is the `run_python` entry point invoked by the migrate-recipes skill when retries are exhausted. Depends on `core/io.py`.
  * **core/types.py**: Cross-cutting type contracts layer. StrEnum discriminators (`RetryReason`, `MergeFailedStep`, `MergeState`, `RestartScope`, `SkillSource`, `RecipeSource`, `Severity`) and canonical constants (`CONTEXT_EXHAUSTION_MARKER`, `PIPELINE_FORBIDDEN_TOOLS`, `SKILL_TOOLS`, `RETRY_RESPONSE_FIELDS`). Generic result wrappers (`LoadReport`, `LoadResult`). Zero autoskillit imports.
  * **core/logging.py**: Centralized structlog configuration. `get_logger(name)` is the single import point for all production modules. `configure_logging()` is called once by the CLI `serve` command — routes all output to stderr via `WriteLoggerFactory`, never stdout.
  * **core/io.py**: Infrastructure and YAML I/O primitives. `_atomic_write(path, content)` (crash-safe write via temp file + `os.replace`); `ensure_project_temp(project_dir)` (creates `.autoskillit/temp/` with `.gitignore`, idempotent); `load_yaml(source)` (path-or-string YAML loader); `dump_yaml(data, path)` (write YAML to disk); `dump_yaml_str(data, **kwargs)` (serialize to string). Zero autoskillit imports.
  * **version.py**: Version health layer. `version_info(plugin_dir: Path | str | None = None)` reads `plugin.json` from the plugin directory and compares with `autoskillit.__version__` to return `{"package_version", "plugin_json_version", "match"}`. Layer 0: no autoskillit imports except `__init__` for `__version__`. Imported by `server/__init__.py` and `cli/_doctor.py`.
  * **_llm_triage.py**: AI orchestration layer for contract staleness semantic triage. `triage_staleness(stale_items)` spawns a `claude -p` subprocess via `execution/process.py` `run_managed_async` (Haiku model) to determine whether SKILL.md changes are semantically meaningful. Falls back to `meaningful=True` on timeout, JSON parse error, or OS error. Depends on `core/logging.py`, `recipe/contracts.py`, `execution/process.py`, `workspace/skills.py`.
  * **smoke_utils.py**: Utility callables for smoke-test pipeline `run_python` steps. `check_bug_report_non_empty(workspace)` reads `bug_report.json` and returns `{"non_empty": "true"/"false"}`. Zero autoskillit imports.

### **Plugin Structure**

The Python package directory (`src/autoskillit/`) is the plugin root:
  * `.claude-plugin/plugin.json` — plugin manifest (name, version, description)
  * `.mcp.json` — MCP server config (command: `autoskillit`)
  * `skills/` — 17 bundled skills discovered by Claude Code as `/autoskillit:*` slash commands
  * `pyproject.toml` declares `artifacts` to include dotfiles in the wheel

### **Skills**

17 bundled skills, invoked as `/autoskillit:<name>`. These are the building blocks that project-specific pipeline recipes (generated by `setup-project`) compose together.

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
| `migrate_recipe` | Apply pending migration notes to a recipe file (gated) |
| `kitchen_status` | Return version health and config status (ungated) |
| `list_recipes` | List pipeline recipes from .autoskillit/recipes/ (ungated) |
| `load_recipe` | Load a recipe by name as raw YAML — read-only, no migration (ungated) |
| `validate_recipe` | Validate a pipeline recipe against the recipe schema (ungated) |
| `get_pipeline_report` | Return accumulated run_skill/run_skill_retry failure report (ungated) |
| `get_token_summary` | Return accumulated token usage grouped by step name (ungated) |
| `open_kitchen` (prompt) | User-only activation — type the open_kitchen prompt from the MCP prompt list |
| `close_kitchen` (prompt) | User-only deactivation — type the close_kitchen prompt from the MCP prompt list |

### **Tool Activation**

11 tools are gated by default. At the start of a session, the user must type
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
| `run_skill` | `exit_after_stop_delay_ms` | `30000` | Milliseconds after session goes idle before Claude Code self-exits. Set to `0` to disable. |


**CRITICAL**: When using subagents, invoke with "CLAUDE_CODE_EXIT_AFTER_STOP_DELAY=30000" to ensure subagents exit when finished.