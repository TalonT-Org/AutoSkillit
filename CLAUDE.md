# **AutoSkillit: Development Guidelines**

Mandatory instructions for AI-assisted development in this repository.

## **1. Core Project Goal**

A Claude Code plugin that orchestrates automated skill-driven workflows using headless sessions. It provides 37 MCP tools (run_cmd, run_python, run_skill, test_check, merge_worktree, reset_test_dir, classify_fix, reset_workspace, read_db, migrate_recipe, clone_repo, remove_clone, push_to_remote, report_bug, prepare_issue, enrich_issues, claim_issue, release_issue, wait_for_ci, create_unique_branch, check_pr_mergeable, write_telemetry_files, get_pr_reviews, bulk_close_issues, set_commit_status + ungated kitchen_status, list_recipes, load_recipe, validate_recipe, get_pipeline_report, get_token_summary, get_timing_summary, fetch_github_issue, get_issue_title, get_ci_status, open_kitchen, close_kitchen) with 25 tools tagged `kitchen` and hidden at startup via FastMCP v3 `mcp.disable(tags={'kitchen'})`, revealed per-session via `open_kitchen` tool, and 24 bundled skills registered as `/autoskillit:*` slash commands.

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
  * **Version Bumps**: When bumping the package version, update `pyproject.toml`, `.claude-plugin/plugin.json`, and run `uv lock`; then search tests for hardcoded version strings (e.g. `AUTOSKILLIT_INSTALLED_VERSION` monkeypatches) and update them.
  * **Run pre-commit before committing**: Always run `pre-commit run --all-files` before
    committing. Do not skip this step even when code appears clean — hooks auto-fix
    formatting and abort the commit, requiring re-stage and retry.

### **3.2. File System**

  * **Temporary Files:** All temp files must go in the project's `temp/` directory.
  * **Do Not Add Root Files**: Never create new root files unless explicitly required.
  * **Never commit unless told to do so**

### **3.3. Pipeline Execution**

  * **Orchestrator Discipline**: When executing a pipeline script (loaded via `load_recipe`), NEVER use native Claude Code tools directly. The following tools are prohibited for the orchestrator: Read, Grep, Glob, Edit, Write, Bash, Task, Explore, WebFetch, WebSearch, NotebookEdit.
  * **Delegate Through Headless Sessions**: All code reading, searching, editing, and investigation MUST go through `run_skill`, which launches headless sessions with full tool access.
  * **Route Failures, Do Not Investigate**: When a pipeline step fails, follow the step's `on_failure` route. Do NOT use native tools to diagnose failures — the downstream skill has diagnostic access that the orchestrator does not.
  * **Use `run_cmd` for Shell Access**: If shell commands are needed during a pipeline, use the `run_cmd` MCP tool, not the native Bash tool.

### **3.5. Code Index MCP Usage**

  * **Initialize before use**: Always call `set_project_path` with the project root
    as the first action in any session that will use code-index tools. Without this
    call, all code-index tools (`find_files`, `search_code_advanced`, `get_file_summary`,
    `get_symbol_body`) fail with "Project path not set" and cascade-cancel sibling
    parallel calls.
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
├── hook_registry.py         # Canonical hook definitions (HookDef, HOOK_REGISTRY, generate_hooks_json)
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
│   ├── __init__.py          #   Re-exports ToolContext, DefaultGateState, AuditLog, TokenLog
│   ├── audit.py             #   FailureRecord, AuditLog, _audit_log singleton
│   ├── context.py           #   ToolContext DI container (config, audit, token_log, gate, plugin_dir, runner)
│   ├── gate.py              #   DefaultGateState, GATED_TOOLS, UNGATED_TOOLS, gate_error_result
│   ├── mcp_response.py      #   McpResponseEntry, DefaultMcpResponseLog — per-tool response size tracking
│   ├── timings.py           #   TimingEntry, DefaultTimingLog — per-step wall-clock accumulation
│   └── tokens.py            #   TokenEntry, TokenLog, _token_log singleton
├── execution/               # L1 execution sub-package
│   ├── __init__.py          #   Re-exports public surface
│   ├── commands.py          #   ClaudeInteractiveCmd/ClaudeHeadlessCmd builders
│   ├── db.py                #   Read-only SQLite execution with defence-in-depth
│   ├── headless.py          #   Headless Claude session orchestration (L1 service)
│   ├── linux_tracing.py     #   Linux-only /proc + psutil process tracing (accumulate snapshots)
│   ├── anomaly_detection.py #   Post-hoc anomaly detection over ProcSnapshot series
│   ├── session_log.py       #   File-based session diagnostics log writer (XDG-aware)
│   ├── process.py           #   Subprocess management facade (re-exports from _process_*.py)
│   ├── _process_io.py       #   create_temp_io, read_temp_output
│   ├── _process_jsonl.py    #   _jsonl_contains_marker, _jsonl_has_record_type, _marker_is_standalone
│   ├── _process_kill.py     #   kill_process_tree, async_kill_process_tree
│   ├── _process_monitor.py  #   _heartbeat, _session_log_monitor, _has_active_api_connection
│   ├── _process_pty.py      #   pty_wrap_command
│   ├── _process_race.py     #   RaceAccumulator, RaceSignals, resolve_termination, _watch_*
│   ├── quota.py             #   Quota-aware check: QuotaStatus, cache, fetch, check_and_sleep_if_needed
│   ├── ci.py                #   GitHub Actions CI watcher service (L1, httpx-based, never raises)
│   ├── github.py            #   GitHub issue fetcher (L1, httpx-based, never raises)
│   ├── session.py           #   ClaudeSessionResult, SkillResult, extract_token_usage
│   └── testing.py           #   Pytest output parsing and pass/fail adjudication
├── workspace/               # L1 workspace sub-package
│   ├── __init__.py          #   Re-exports CleanupResult, SkillResolver, SessionSkillManager, clone_repo, remove_clone, push_to_remote
│   ├── cleanup.py           #   Directory teardown utilities (CleanupResult, preserve list)
│   ├── clone.py             #   Clone-based run isolation: clone_repo, remove_clone, push_to_remote
│   ├── session_skills.py    #   Per-session ephemeral skill dirs (SkillsDirectoryProvider, SessionSkillManager, TIER2_SKILLS, resolve_ephemeral_root)
│   └── skills.py            #   Bundled skill listing (SkillResolver)
├── recipe/                  # L2 recipe sub-package
│   ├── __init__.py          #   Re-exports Recipe, RecipeStep, validate_recipe, load_recipe, etc.
│   ├── contracts.py         #   Contract card generation and staleness triage utilities
│   ├── io.py                #   load_recipe, list_recipes, iter_steps_with_context, find_recipe_by_name
│   ├── loader.py            #   Path-based recipe metadata utilities (parse_recipe_metadata, RecipeInfo)
│   ├── _api.py              #   Recipe orchestration API: load/validate pipelines, format responses
│   ├── diagrams.py          #   Recipe flow diagram generation, loading, and staleness detection
│   ├── registry.py          #   RuleFinding, RuleSpec, _RULE_REGISTRY, semantic_rule, run_semantic_rules
│   ├── repository.py        #   Concrete RecipeRepository implementation
│   ├── _analysis.py         #   Step graph building and dataflow analysis
│   ├── rules_bypass.py      #   Semantic rules for skip_when_false bypass routing contracts
│   ├── rules_ci.py          #   Semantic rules for CI polling patterns (ci-polling-inline-shell)
│   ├── rules_clone.py       #   Semantic rules for clone/push workflow validation
│   ├── rules_dataflow.py    #   Semantic rules for capture/output dataflow analysis
│   ├── rules_graph.py       #   Semantic rules for step graph reachability and cycles
│   ├── rules_inputs.py      #   Semantic rules for ingredient/version validation
│   ├── rules_merge.py       #   Semantic rules for merge_worktree routing completeness
│   ├── rules_tools.py       #   Semantic rules for MCP tool name validity (unknown-tool rule)
│   ├── rules_verdict.py     #   Semantic rules for skill verdict routing completeness (unrouted-verdict-value)
│   ├── rules_worktree.py    #   Semantic rules for worktree retry lifecycle
│   ├── schema.py            #   Recipe, RecipeStep, DataFlowWarning, AUTOSKILLIT_VERSION_KEY
│   ├── staleness_cache.py   #   Disk-backed staleness check cache (StalenessEntry, load_cache, save_cache)
│   └── validator.py         #   validate_recipe, run_semantic_rules (re-exported), analyze_dataflow
├── migration/               # L2 migration sub-package
│   ├── __init__.py          #   Re-exports MigrationEngine, applicable_migrations, FailureStore
│   ├── engine.py            #   MigrationEngine, adapter ABC hierarchy, default_migration_engine()
│   ├── _api.py              #   Top-level check_and_migrate convenience function
│   ├── loader.py            #   Migration note discovery and version chaining
│   └── store.py             #   FailureStore: migration failure persistence (JSON, atomic writes)
├── server/                  # L3 FastMCP server sub-package
│   ├── __init__.py          #   FastMCP app, _initialize(ctx), version_info(), recipe:// resource handler
│   ├── git.py               #   Git merge workflow for merge_worktree (perform_merge)
│   ├── helpers.py           #   Shared server-layer helpers (worktree setup, path utilities)
│   ├── tools_kitchen.py     #   open_kitchen, close_kitchen tool handlers + recipe:// resource
│   ├── tools_ci.py          #   wait_for_ci, get_ci_status tool handlers
│   ├── tools_clone.py       #   clone_repo, remove_clone, push_to_remote tool handlers
│   ├── tools_execution.py   #   run_cmd, run_python, run_skill tool handlers
│   ├── tools_git.py         #   merge_worktree, classify_fix, create_unique_branch, check_pr_mergeable tool handlers
│   ├── tools_recipe.py      #   migrate_recipe, load_recipe, list_recipes, validate_recipe tool handlers
│   ├── tools_status.py      #   kitchen_status, get_pipeline_report, get_token_summary, write_telemetry_files tool handlers
│   ├── tools_integrations.py #  fetch_github_issue, report_bug, get_pr_reviews, bulk_close_issues tool handlers
│   ├── tools_workspace.py   #   test_check, reset_test_dir, reset_workspace, read_db tool handlers
│   ├── _factory.py          #   Composition Root: make_context() wires ToolContext
│   └── _state.py            #   Server state extraction (lazy init, plugin dir resolution)
├── cli/                     # L3 CLI sub-package
│   ├── __init__.py          #   Re-exports main entry point
│   ├── _chefs_hat.py        #   chefs-hat command: ephemeral skill session launcher (claude --add-dir)
│   ├── _doctor.py           #   Doctor command -- 7 project setup checks
│   ├── _hooks.py            #   Unified PreToolUse hook registration helpers
│   ├── _init_helpers.py     #   Init command helpers: interactive prompts and workspace marker
│   ├── _marketplace.py      #   Plugin install/upgrade marketplace operations
│   ├── _prompts.py          #   Orchestrator prompt builder for recipe execution
│   └── app.py               #   CLI: serve, init, config show, skills, recipes, workspace, doctor
├── hooks/                   # Claude Code PreToolUse and PostToolUse hook scripts
│   ├── __init__.py
│   ├── hooks.json           #   Plugin hook registration (auto-discovered by Claude Code)
│   ├── quota_check.py       #   Quota guard hook — blocks run_skill when threshold exceeded
│   ├── remove_clone_guard.py #  Remove-clone guard — denies remove_clone calls with keep != "true"
│   ├── skill_cmd_check.py   #   PreToolUse hook — validates skill_command path argument format
│   ├── skill_command_guard.py #  PreToolUse hook — blocks run_skill with non-slash skill_command
│   ├── open_kitchen_guard.py #  PreToolUse hook — blocks open_kitchen from headless sessions
│   └── pretty_output.py     #   PostToolUse hook — reformats MCP JSON responses as Markdown-KV
├── migrations/              # Data: versioned migration YAML notes
│   └── __init__.py
├── recipes/                 # Bundled recipe YAML definitions
│   ├── audit-and-fix.yaml
│   ├── bugfix-loop.yaml
│   ├── implementation-groups.yaml
│   ├── implementation.yaml
│   ├── remediation.yaml
│   └── smoke-test.yaml
└── skills/                  # 33 bundled skills (SKILL.md per skill)
    ├── analyze-prs/              ├── audit-friction/
    ├── audit-impl/               ├── collapse-issues/
    ├── create-review-pr/         ├── diagnose-ci/
    ├── dry-walkthrough/          ├── enrich-issues/
    ├── implement-worktree/       ├── implement-worktree-no-merge/
    ├── investigate/              ├── issue-splitter/
    ├── make-groups/              ├── make-plan/
    ├── merge-pr/                 ├── mermaid/
    ├── migrate-recipes/          ├── open-pr/
    ├── pipeline-summary/         ├── prepare-issue/
    ├── process-issues/           ├── rectify/
    ├── report-bug/               ├── resolve-failures/
    ├── resolve-merge-conflicts/  ├── resolve-review/
    ├── retry-worktree/           ├── review-approach/
    ├── review-pr/                ├── setup-project/
    ├── smoke-task/               ├── sous-chef/
    ├── triage-issues/            └── write-recipe/
```

**Session diagnostics logs** are stored globally at `~/.local/share/autoskillit/logs/` (Linux) or `~/Library/Application Support/autoskillit/logs/` (macOS). Override with `linux_tracing.log_dir` in config. Session directories are named by Claude Code session UUID when available (preferred: parsed from stdout, fallback: discovered from JSONL filename via Channel B). When no session ID is available from either source, directories use `no_session_{timestamp}` naming. Query the index: `jq 'select(.success == false)' ~/.local/share/autoskillit/logs/sessions.jsonl`.

**CRITICAL**: When using subagents, invoke with "CLAUDE_CODE_EXIT_AFTER_STOP_DELAY=120000" to ensure subagents exit when finished.

## 7. Session Diagnostics

**Path components use hyphens, not underscores.** Log directory names and session folder
names are hyphen-separated. Never assume underscores when constructing or searching for
log paths — hyphen mismatch causes ENOENT (session f9170655 pattern).