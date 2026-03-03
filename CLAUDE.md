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
│   ├── linux_tracing.py     #   Linux-only /proc + psutil process tracing (Tier 2 debug)
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

**CRITICAL**: When using subagents, invoke with "CLAUDE_CODE_EXIT_AFTER_STOP_DELAY=120000" to ensure subagents exit when finished.
