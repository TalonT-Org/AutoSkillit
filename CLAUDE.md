# **AutoSkillit: Development Guidelines**

Mandatory instructions for AI-assisted development in this repository.

## **1. Core Project Goal**

A Claude Code plugin that orchestrates automated skill-driven workflows using headless sessions. It provides 22 MCP tools (run_cmd, run_python, run_skill, run_skill_retry, test_check, merge_worktree, reset_test_dir, classify_fix, reset_workspace, read_db, migrate_recipe, clone_repo, remove_clone, push_to_remote, fetch_github_issue, report_bug + ungated kitchen_status, list_recipes, load_recipe, validate_recipe, get_pipeline_report, get_token_summary) with 16 gated behind MCP prompts for user-only activation, and 22 bundled skills registered as `/autoskillit:*` slash commands.

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

### **3.5. Code Index MCP Usage**

  * **Index is locked to the main project root**: The `code-index` MCP server is indexed against the source repo and must never be redirected to a worktree or branch. Its value is for exploration before code changes — at that point any worktree is identical to main, so the index is accurate regardless of where you are working.
  * **Prefer code-index tools over native search tools when exploring the codebase**:
    * `find_files` instead of Glob for in-project file discovery
    * `search_code_advanced` instead of Grep for in-project content search (auto-selects best backend, paginates results, supports fuzzy matching)
    * `get_file_summary` to understand a file's structure before reading it
    * `get_symbol_body` to retrieve a specific function or class by name, including a `called_by` call graph, without loading the whole file
  * **Do not rely on code-index tools for code added or modified during a branch** — use Read/Grep directly for that.
  * **Fall back to native Grep/Glob** for multiline patterns or paths outside the project root.

### **3.4. CLAUDE.md Modifications**

  * **Correcting existing content is permitted**: If you discover that CLAUDE.md contains inaccurate information (wrong file paths, stale names, incorrect tool attributions), you may correct it without being asked.
  * **Adding new content requires explicit instruction**: Never add new sections, bullet points, entries, or any new information to CLAUDE.md unless the user has explicitly asked you to update or extend it. Corrections to existing facts ≠ permission to expand scope.

## **4. Testing Guidelines**

The project uses pytest with pytest-asyncio for async test support. Tests run in parallel via pytest-xdist (`-n 4`). All tests must be safe for parallel execution.

  * **Run tests**: `task test-all` from the project root (human-facing, runs lint + tests). For automation and MCP tools, `task test-check` is used (unambiguous PASS/FAIL, correct PIPESTATUS capture). Never use `pytest`, `python -m pytest`, or any other test runner directly.
  * **Always run tests at end of task**
  * **Fix failing tests immediately**
  * **Add tests for new features**
  * **Follow existing test patterns** in `tests/` — avoid test code redundancy
  * **Worktree setup**: Use `task install-worktree` in worktrees. Never hardcode `uv venv`/`pip install` in skills or plans.

## **5. Pre-commit Hooks**

Install hooks after cloning: `pre-commit install`

Hooks run automatically on commit. To run manually: `pre-commit run --all-files`

Configured hooks: ruff format (auto-fix), ruff check (auto-fix), mypy type checking, uv lock check, gitleaks secret scanning.

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
│   ├── paths.py             #   pkg_root(), is_git_worktree() — canonical package root resolver
│   └── types.py             #   StrEnums, protocols, constants (SubprocessRunner, LoadResult, etc.)
├── config/                  # L1 configuration sub-package
│   ├── __init__.py          #   Re-exports AutomationConfig + GitHubConfig
│   ├── defaults.yaml        #   Bundled package defaults (always loaded as first layer)
│   └── settings.py          #   Dataclass config + dynaconf-backed layered resolution
├── pipeline/                # L1 pipeline state sub-package
│   ├── __init__.py          #   Re-exports ToolContext, GateState, AuditLog, TokenLog
│   ├── audit.py             #   FailureRecord, AuditLog, _audit_log singleton
│   ├── context.py           #   ToolContext DI container (config, audit, token_log, gate, plugin_dir, runner)
│   ├── gate.py              #   GateState, GATED_TOOLS, UNGATED_TOOLS, gate_error_result
│   └── tokens.py            #   TokenEntry, TokenLog, _token_log singleton
├── execution/               # L1 execution sub-package
│   ├── __init__.py          #   Re-exports public surface
│   ├── commands.py          #   ClaudeInteractiveCmd/ClaudeHeadlessCmd builders
│   ├── db.py                #   Read-only SQLite execution with defence-in-depth
│   ├── headless.py          #   Headless Claude session orchestration (L3 service)
│   ├── process.py           #   Subprocess management (kill trees, temp I/O, timeouts)
│   ├── quota.py             #   Quota-aware check: QuotaStatus, cache, fetch, check_and_sleep_if_needed
│   ├── session.py           #   ClaudeSessionResult, SkillResult, extract_token_usage
│   └── testing.py           #   Pytest output parsing and pass/fail adjudication
├── workspace/               # L1 workspace sub-package
│   ├── __init__.py          #   Re-exports CleanupResult, SkillResolver, clone_repo, remove_clone, push_to_remote
│   ├── cleanup.py           #   Directory teardown utilities (CleanupResult, preserve list)
│   ├── clone.py             #   Clone-based run isolation: clone_repo, remove_clone, push_to_remote
│   └── skills.py            #   Bundled skill listing (SkillResolver)
├── recipe/                  # L2 recipe sub-package
│   ├── __init__.py          #   Re-exports Recipe, RecipeStep, validate_recipe, load_recipe, etc.
│   ├── contracts.py         #   Contract card generation and staleness triage utilities
│   ├── io.py                #   load_recipe, list_recipes, iter_steps_with_context, find_recipe_by_name
│   ├── loader.py            #   Path-based recipe metadata utilities (parse_recipe_metadata, RecipeInfo)
│   ├── registry.py          #   RuleFinding, RuleSpec, _RULE_REGISTRY, semantic_rule, run_semantic_rules
│   ├── rules_bypass.py      #   Semantic rules for skip_when_false bypass routing contracts
│   ├── schema.py            #   Recipe, RecipeStep, DataFlowWarning, AUTOSKILLIT_VERSION_KEY
│   ├── staleness_cache.py   #   Disk-backed staleness check cache (StalenessEntry, load_cache, save_cache)
│   └── validator.py         #   validate_recipe, run_semantic_rules (re-exported), analyze_dataflow
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
│   ├── tools_clone.py       #   clone_repo, remove_clone, push_to_remote tool handlers
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
├── hooks/                   # Claude Code PreToolUse hook scripts
│   ├── __init__.py
│   ├── hooks.json           #   Plugin hook registration (auto-discovered by Claude Code)
│   ├── quota_check.py       #   Quota guard hook — blocks run_skill when threshold exceeded
│   ├── remove_clone_guard.py #  Remove-clone guard — denies remove_clone calls with keep != "true"
│   ├── skill_cmd_check.py   #   PreToolUse hook — validates skill_command path argument format
│   └── skill_command_guard.py #  PreToolUse hook — blocks run_skill with non-slash skill_command
├── migrations/              # Data: versioned migration YAML notes
│   └── __init__.py
├── recipes/                 # Bundled recipe YAML definitions
│   ├── audit-and-fix.yaml
│   ├── bugfix-loop.yaml
│   ├── implementation-pipeline.yaml
│   ├── investigate-first.yaml
│   └── smoke-test.yaml
└── skills/                  # 22 bundled skills (SKILL.md per skill)
    ├── analyze-prs/          ├── audit-friction/
    ├── audit-impl/           ├── dry-walkthrough/
    ├── implement-worktree/   ├── implement-worktree-no-merge/
    ├── investigate/          ├── make-groups/
    ├── make-plan/            ├── merge-pr/
    ├── mermaid/              ├── migrate-recipes/
    ├── open-pr/              ├── pipeline-summary/
    ├── rectify/              ├── report-bug/
    ├── resolve-failures/     ├── retry-worktree/
    ├── review-approach/      ├── setup-project/
    ├── smoke-task/           └── write-recipe/

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
│   ├── test_anyio_infra.py
│   ├── test_ci_dev_config.py
│   ├── test_remove_clone_guard.py
│   ├── test_security_config.py
│   ├── test_skill_cmd_check.py
│   ├── test_skill_command_guard.py
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
│   ├── test_staleness_cache.py
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

### **Key Components**

  * **config/settings.py**: Dataclass hierarchy (`AutomationConfig`) with dynaconf-backed layered resolution: package `defaults.yaml` → user → project → secrets → env vars. `_make_dynaconf(project_dir)` pre-merges YAML layers (dict deep-merge + list-replace) then uses Dynaconf for env var prefix support (`AUTOSKILLIT_SECTION__KEY`). `AutomationConfig.from_dynaconf(d)` maps the Dynaconf dict to typed dataclasses. No config file = package defaults from `config/defaults.yaml`.
  * **cli/app.py**: CLI entry point. `autoskillit` (no args) starts the MCP server. Also provides `init` (prints plugin-dir path), `config show`, `quota-status`, `skills list`, `recipes list/show`, `workspace init`, `install`, `upgrade`, `migrate`, `cook`, and `doctor`.
  * **cli/_doctor.py**: CLI support layer: project health checks. `run_doctor()` runs 7 checks: stale MCP servers, duplicate autoskillit registrations, plugin metadata presence, PATH availability, project config existence, version consistency (package vs plugin.json), and recipe migration health (via migration/store.py). Depends on `version.py`, `migration/store.py`, `recipe/io.py`, `core/types.py`. Imported by `cli/app.py`.
  * **server/__init__.py**: FastMCP server. 15 gated tools require user activation via MCP prompts. 6 ungated tools (`kitchen_status`, `list_recipes`, `load_recipe`, `validate_recipe`, `get_pipeline_report`, `get_token_summary`) are always available. Uses ToolContext DI (`pipeline/context.py`) — single module-level `_ctx: ToolContext | None`. `_initialize(ctx)` wires everything at startup. Gate policy in `pipeline/gate.py`. `version_info()` is public. Registers `recipe://` resource handler. **Ungated vs gated notifications:** Ungated tools accept no `ctx: Context` parameter and emit no MCP progress notifications. This is intentional — they are fast, lightweight reads. MCP notifications are reserved for long-running gated operations. This asymmetry is documented in each ungated tool's docstring.
  * **server/git.py**: L3 service module for the git merge workflow. `perform_merge(worktree_path, base_branch, *, config, runner)` executes the full merge pipeline: path validation → worktree verification → branch detection → test gate → fetch → rebase → main-repo merge → worktree cleanup. Uses injected `SubprocessRunner` so existing test mocks apply unchanged.
  * **server/helpers.py**: Shared server-layer utilities — worktree environment setup, path normalization, and other helpers shared across `tools_*.py` modules.
  * **server/prompts.py**: MCP prompt handlers for `open_kitchen` and `close_kitchen` activation prompts (user-only, model cannot invoke).
  * **server/tools_clone.py**: MCP tool handlers for `clone_repo`, `remove_clone`, and `push_to_remote`. Accesses clone functionality via `tool_ctx.clone_mgr` (DI pattern — no direct workspace imports).
  * **server/tools_execution.py**: MCP tool handlers for `run_cmd`, `run_python`, `run_skill`, and `run_skill_retry`.
  * **server/tools_git.py**: MCP tool handlers for `merge_worktree` and `classify_fix`.
  * **server/tools_integrations.py**: MCP tool handlers for `fetch_github_issue` and `report_bug`. `fetch_github_issue` resolves bare issue numbers using `config.github.default_repo` and delegates HTTP calls to `ctx.github_client`. `report_bug` runs a headless `/autoskillit:report-bug` session (blocking or fire-and-forget), writes the report to `.autoskillit/temp/bug-reports/`, parses a deduplication fingerprint from the skill output, and either creates a new GitHub issue or comments on the existing one via `ctx.github_client`.
  * **server/tools_recipe.py**: MCP tool handlers for `migrate_recipe`, `load_recipe`, `list_recipes`, and `validate_recipe`.
  * **server/tools_status.py**: MCP tool handlers for `kitchen_status`, `get_pipeline_report`, and `get_token_summary`.
  * **server/tools_workspace.py**: MCP tool handlers for `test_check`, `reset_test_dir`, `reset_workspace`, and `read_db`.
  * **server/_factory.py**: Composition Root. `make_context(config, *, runner, plugin_dir)` creates a fully-wired `ToolContext` — the only location that legally instantiates all service fields simultaneously. Imported by `cli/app.py serve()` and tests that need an isolated context without the full server import chain.
  * **pipeline/audit.py**: Pipeline failure tracking. `AuditLog` captures every non-success result from `_build_skill_result()` into an in-memory list. `_audit_log` is the module-level singleton used by `server/__init__.py`. `get_pipeline_report` retrieves the accumulated failures.
  * **pipeline/context.py**: ToolContext DI container. Holds `config`, `audit`, `token_log`, `gate`, `plugin_dir`, `runner`. Passed to `server._initialize(ctx)` at startup. All gated tools access config and gate state through the context instead of module-level singletons.
  * **pipeline/gate.py**: Gate policy layer. `GateState` dataclass with `enabled` flag. `GATED_TOOLS` and `UNGATED_TOOLS` frozensets (the source of truth for the MCP tool registry). `gate_error_result()` builds standard disabled-gate error JSON.
  * **pipeline/tokens.py**: Pipeline token usage tracking. `TokenLog` accumulates token counts keyed by YAML step name. `_token_log` is the module-level singleton used by `server/__init__.py`. `get_token_summary` retrieves the accumulated per-step totals.
  * **execution/commands.py**: Claude CLI command builders. `ClaudeInteractiveCmd` and `ClaudeHeadlessCmd` frozen dataclasses. `build_interactive_cmd(*, model)` builds an interactive session command with `--allow-dangerous-permissions` and `AUTOSKILLIT_KITCHEN_OPEN=1` env. `build_headless_cmd(prompt, *, model)` builds a headless session command with `-p` and `--dangerously-skip-permissions`. Zero autoskillit imports.
  * **execution/headless.py**: L3 service module for headless Claude Code session orchestration. `run_headless_core(skill_command, cwd, ctx, *, model, step_name, add_dir, timeout, stale_threshold)` is the single public entry point shared by `run_skill` and `run_skill_retry`. Contains `_build_skill_result`, `_resolve_model`, `_ensure_skill_prefix`, `_inject_completion_directive`, `_session_log_dir`, and `_capture_failure`.
  * **execution/session.py**: Data extraction layer for Claude CLI output. `ClaudeSessionResult` dataclass. `SkillResult` typed result. `_compute_success`, `_compute_retry` policy functions. `extract_token_usage(stdout)` prefers `type=result` record totals. Depends on `core/types.py`, `core/logging.py`.
  * **execution/process.py**: Subprocess utilities for process tree cleanup, temp file I/O to avoid pipe blocking, and configurable timeouts. Uses `get_logger()` from `core/logging.py`.
  * **execution/testing.py**: L3 service module for pytest output parsing and pass/fail adjudication. `parse_pytest_summary(stdout)` extracts structured outcome counts from `=`-delimited summary lines. `check_test_passed(returncode, stdout)` cross-validates exit code against output for defense against PIPESTATUS bugs. Depends only on `core/logging`.
  * **execution/db.py**: Data access layer: read-only SQLite execution with defence-in-depth. Regex pre-validation rejects non-SELECT queries; OS-level `file:...?mode=ro` connection; `set_authorizer` callback blocks any non-SELECT/READ/FUNCTION engine operation. `_execute_readonly_query` is the main entry point. Depends only on `core/logging.py`.
  * **execution/github.py**: GitHub issue fetcher. `DefaultGitHubFetcher` implements `GitHubFetcher` protocol via httpx. `_parse_issue_ref(ref)` parses full URLs and `owner/repo#N` shorthand. `_format_issue_markdown(...)` renders issue data as Markdown. Never raises — all errors returned as `{"success": False, "error": "..."}`. L1 module: depends only on stdlib, httpx, and `core/logging`.
  * **execution/quota.py**: Quota-aware check for long-running pipeline recipes. `QuotaStatus` dataclass. `_read_credentials(path)` reads Bearer token from `~/.claude/.credentials.json`. `_read_cache(path, max_age)` returns fresh status or None. `_write_cache(path, status)` persists to cache (silent on failure). `_fetch_quota(credentials_path)` fetches 5-hour utilization from Anthropic quota API via `httpx`. `check_and_sleep_if_needed(config)` is the main async entry point — returns metadata dict; does NOT sleep. L1 module: depends only on stdlib, httpx, and `core/logging`.
  * **hooks/quota_check.py**: PreToolUse hook that runs `autoskillit quota-status` before each `run_skill`/`run_skill_retry` call. Blocks with a recovery message if quota threshold is exceeded. Silently approves otherwise. Registered in `.claude/settings.json` by `autoskillit install` and auto-discovered as `hooks/hooks.json` for plugin installs.
  * **hooks/remove_clone_guard.py**: PreToolUse hook that prompts the user for permission on any `remove_clone` call where `keep != "true"`. Clones are never removed automatically — the user must approve each removal. Registered in `.claude/settings.json` by `autoskillit install` and auto-discovered via `hooks/hooks.json`.
  * **hooks/skill_cmd_check.py**: PreToolUse hook that validates `skill_command` path argument format. Denies `run_skill`/`run_skill_retry` calls where a path-argument skill is invoked with extra descriptive text before the actual file path. Auto-discovered via `hooks/hooks.json`.
  * **hooks/skill_command_guard.py**: PreToolUse hook that blocks `run_skill`/`run_skill_retry` calls where `skill_command` does not start with a `/` prefix. Fail-open: any error approves silently. Auto-discovered via `hooks/hooks.json`.
  * **workspace/cleanup.py**: Infrastructure layer for directory teardown. `_delete_directory_contents(directory, preserve)` removes all items in a directory except preserved names, recording failures in `CleanupResult` without raising. Depends only on `core/logging.py`.
  * **workspace/clone.py**: Clone-based run isolation for pipeline recipes. `clone_repo(source_dir, run_name)` clones source into `../autoskillit-runs/<run_name>-<timestamp>/` and returns `{"clone_path", "source_dir"}`. `remove_clone(clone_path, keep)` tears down the clone (never raises). `push_to_remote(clone_path, source_dir, branch)` reads the upstream remote URL from source_dir via `git remote get-url origin` (read-only) and pushes from clone_path directly to the remote, never touching source_dir. SOURCE ISOLATION: after clone_repo returns, source_dir must not be touched (no git checkout, fetch, reset, pull, or any command). All pipeline work runs in clone_path. source_dir is used only to read the remote URL. L1 module: depends only on stdlib and `core/logging`.
  * **workspace/skills.py**: Lists bundled skills from the package `skills/` directory. `SkillResolver` (no args) scans for `SKILL.md` files.
  * **recipe/schema.py**: Recipe data models. `Recipe`, `RecipeStep`, `DataFlowWarning`, `AUTOSKILLIT_VERSION_KEY`. Zero autoskillit I/O dependencies.
  * **recipe/io.py**: Recipe I/O layer. `load_recipe(name, project_dir)` and `list_recipes(project_dir)` discover recipes from project and bundled sources. `iter_steps_with_context(recipe)` yields `(name, step, available_context)` with accumulated captures. `find_recipe_by_name(name, project_dir)` returns first match or None. `RecipeStep` supports an optional `model` field for per-step model selection.
  * **recipe/loader.py**: Path-based recipe metadata utilities for `migration/engine.py`. Exports `parse_recipe_metadata(path: Path) -> RecipeInfo`, which handles both plain YAML and frontmatter-format files (`---` delimited). Reads the `name`, `description`, `summary`, `source`, and `version` fields from the YAML document and returns a `RecipeInfo` instance. Recipe discovery (`list_recipes`, `load_recipe`) lives in `recipe/io.py`, not here.
  * **recipe/registry.py**: Rule registry infrastructure for semantic validation. `RuleFinding`, `RuleSpec`, `_RULE_REGISTRY`, `semantic_rule` decorator. Also houses `run_semantic_rules`, `findings_to_dicts`, `filter_version_rule`, `build_quality_dict`, `compute_recipe_validity`. Extracted from validator.py to keep that file under 1000 lines. All symbols are re-exported from `recipe/validator.py` for backward compatibility.
  * **recipe/validator.py**: Recipe validation layer. `validate_recipe(recipe)` structural checks. `run_semantic_rules(recipe)` semantic rule engine (decorator-based registry — implementation in registry.py). `analyze_dataflow(recipe)` traces data flow. Uses `iter_steps_with_context` from `recipe/io.py` for context-aware validation.
  * **recipe/contracts.py**: Contract card generation and LLM staleness triage utilities. `generate_recipe_card(pipeline_path, recipes_dir)` returns dict and writes YAML to disk. Imported by `_llm_triage.py`.
  * **recipe/_api.py**: Recipe orchestration API — `load_and_validate`, `validate_from_path`, `list_all` convenience functions. Aggregates recipe I/O, validation, and contract-staleness checks into a single call surface for the server layer.
  * **recipe/repository.py**: Concrete `DefaultRecipeRepository` implementation backed by `recipe/io.py` and `recipe/_api.py`. Provides `find`, `list`, `load_and_validate`, `validate_from_path`, and `list_all` as a dependency-injected repository interface.
  * **recipe/rules.py**: Semantic validation rules registered with the `semantic_rule` decorator. Houses all rule implementations (forbidden-tool checks, ingredient reference validation, worktree safety, context-ref checks). Extracted from `recipe/registry.py` / `recipe/validator.py` to keep rule logic separate from infrastructure.
  * **recipe/rules_bypass.py**: Semantic validation rules for `skip_when_false` bypass routing contracts. `_check_optional_without_skip_when` fires when a step is marked `optional: true` but lacks a `skip_when_false` declaration. Registered via `semantic_rule` decorator.
  * **recipe/staleness_cache.py**: Disk-backed staleness check cache for recipe contract verification. `StalenessEntry` dataclass persists recipe hash, manifest version, staleness flag, and triage result. Provides `load_cache`, `save_cache`, and `get_or_check` for efficient staleness reuse across invocations.
  * **migration/_api.py**: Migration API convenience layer. `check_and_migrate(name, project_dir, installed_version)` checks applicable migrations and applies deterministic ones automatically; returns error dict when LLM-driven migration is required via MCP tool.
  * **migration/engine.py**: Orchestration layer for recipe and contract migration. Layer B domain logic — no FastMCP dependency. `MigrationEngine` dispatches to registered adapters: `RecipeMigrationAdapter` (LLM-driven via headless Claude session) and `ContractMigrationAdapter` (deterministic contract regeneration). ABC hierarchy: `MigrationAdapter` → `HeadlessMigrationAdapter` / `DeterministicMigrationAdapter`. `default_migration_engine()` factory builds the standard adapter set.
  * **migration/loader.py**: Data access layer for the migration version graph. Discovers and parses versioned migration YAML files from the bundled `migrations/` package directory. `list_migrations()` enumerates all notes; `applicable_migrations(script_version, installed_version)` chains applicable notes from the script's current version to the installed version using semver ordering. Depends on `core/io.py` and `packaging`.
  * **migration/store.py**: Persistence layer for migration failure tracking. `FailureStore` persists `MigrationFailure` records to `.autoskillit/temp/migrations/failures.json` via atomic writes (`core/io.py`). `record_from_skill()` is the `run_python` entry point invoked by the migrate-recipes skill when retries are exhausted. Depends on `core/io.py`.
  * **core/paths.py**: Canonical package root path resolution. `pkg_root()` returns the autoskillit package root directory via `importlib.resources.files('autoskillit')` — a named, depth-independent reference. `is_git_worktree(path)` returns True when the given path is inside a git linked worktree (`.git` FILE ancestor) vs a main checkout (`.git` DIRECTORY ancestor). All path-resolution sites must use `pkg_root()` instead of `Path(__file__).parent` depth-counting. Zero autoskillit imports.
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
  * `skills/` — 22 bundled skills discovered by Claude Code as `/autoskillit:*` slash commands
  * `pyproject.toml` declares `artifacts` to include dotfiles in the wheel

### **Skills**

22 bundled skills, invoked as `/autoskillit:<name>`. These are the building blocks that project-specific pipeline recipes (generated by `setup-project`) compose together.

Skills are discovered by Claude Code via the plugin structure. Headless sessions receive `--plugin-dir` automatically via `run_skill` and `run_skill_retry`. Project-specific pipeline recipes go in `.autoskillit/recipes/` as YAML files, discovered via `list_recipes` and loaded via `load_recipe`.

**CRITICAL**: When using subagents, invoke with "CLAUDE_CODE_EXIT_AFTER_STOP_DELAY=120000" to ensure subagents exit when finished.

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
| `clone_repo` | Clone a source repository into an isolated run directory. After cloning, source_dir must not be touched — all work runs in clone_path. |
| `remove_clone` | Remove a pipeline clone directory (best-effort). Auto-removal (keep="false") requires user approval via a PreToolUse guard hook — clones are never removed without explicit permission. |
| `push_to_remote` | Push merged branch from clone to upstream remote |
| `fetch_github_issue` | Retrieve a GitHub issue as formatted Markdown (auto-call on any GitHub issue reference) |
| `report_bug` | Run a headless bug investigation, write a report, and file or comment on a GitHub issue (deduplicates by fingerprint) |
| `kitchen_status` | Return version health and config status (ungated) |
| `list_recipes` | List pipeline recipes from .autoskillit/recipes/ (ungated) |
| `load_recipe` | Load a recipe by name as raw YAML — read-only, no migration (ungated) |
| `validate_recipe` | Validate a pipeline recipe against the recipe schema (ungated) |
| `get_pipeline_report` | Return accumulated run_skill/run_skill_retry failure report (ungated) |
| `get_token_summary` | Return accumulated token usage grouped by step name (ungated) |
| `open_kitchen` (prompt) | User-only activation — type the open_kitchen prompt from the MCP prompt list |
| `close_kitchen` (prompt) | User-only deactivation — type the close_kitchen prompt from the MCP prompt list |

### **Configuration**

All tool behavior is configurable via `.autoskillit/config.yaml`. No config file = package defaults.

**Available settings:**

| Section | Key | Default | Description |
|---------|-----|---------|-------------|
| `test_check` | `command` | `["task", "test-check"]` | Test command for `test_check` and `merge_worktree` |
| `test_check` | `timeout` | `600` | Test command timeout in seconds |
| `model` | `default` | `null` | Default model for run_skill/run_skill_retry when step has no model field |
| `model` | `override` | `null` | Force all run_skill/run_skill_retry to use this model (overrides step YAML) |
| `token_usage` | `verbosity` | `"summary"` | Token table behavior: `"summary"` = render once at pipeline end; `"none"` = suppress entirely |