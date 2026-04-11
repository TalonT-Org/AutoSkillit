# **AutoSkillit: Development Guidelines**

Mandatory instructions for AI-assisted development in this repository.

## **1. Core Project Goal**

A Claude Code plugin that orchestrates automated skill-driven workflows using headless sessions. It provides MCP tools (gated behind `open_kitchen`/`close_kitchen` via FastMCP v3 tag-based visibility) and bundled skills registered as `/autoskillit:*` slash commands.

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
  * **Version Bumps**: When bumping the package version, update `pyproject.toml` and run `task sync-plugin-version && uv lock`; then search tests for hardcoded version strings (e.g. `AUTOSKILLIT_INSTALLED_VERSION` monkeypatches) and update them.
  * **Run pre-commit before committing**: Always run `pre-commit run --all-files` before committing. Do not skip this step even when code appears clean — hooks auto-fix formatting and abort the commit, requiring re-stage and retry.

### **3.2. File System**

  * **Temporary Files:** All temp files must go in the project's `.autoskillit/temp/` directory.
  * **Do Not Add Root Files**: Never create new root files unless explicitly required.
  * **Never commit unless told to do so**

### **3.3. Code Index MCP Usage**

  * **Initialize before use**: Always call `set_project_path` with the project root as the first action in any session that will use code-index tools. Without this call, all code-index tools fail with "Project path not set" and cascade-cancel sibling parallel calls.
  * **Index is locked to the main project root**: The `code-index` MCP server is indexed against the source repo and must never be redirected to a worktree or branch. Its value is for exploration before code changes — at that point any worktree is identical to main, so the index is accurate regardless of where you are working.
  * **Prefer code-index tools over native search** when exploring the codebase: `find_files`, `search_code_advanced`, `get_file_summary`, `get_symbol_body` (includes `called_by` call graph).
  * **Do not rely on code-index for code added or modified during a branch** — use Read/Grep directly for that.
  * **Fall back to native Grep/Glob** for multiline patterns or paths outside the project root.

### **3.4. CLAUDE.md Modifications**

  * **Correcting existing content is permitted**: If you discover that CLAUDE.md contains inaccurate information (wrong file paths, stale names, incorrect tool attributions), you may correct it without being asked.
  * **Adding new content requires explicit instruction**: Never add new sections, bullet points, entries, or any new information to CLAUDE.md unless the user has explicitly asked you to update or extend it. Corrections to existing facts ≠ permission to expand scope.

## **4. Testing Guidelines**

The project uses pytest with pytest-asyncio. Tests run in parallel via pytest-xdist (`-n 4`). All tests must be safe for parallel execution.

  * **Run tests**: `task test-all` from the project root (human-facing, runs lint + tests). For automation and MCP tools, `task test-check` is used (unambiguous PASS/FAIL, correct PIPESTATUS capture). Never use `pytest`, `python -m pytest`, or any other test runner directly.
  * **Always run tests at end of task**
  * **Add tests for new features**
  * **Follow existing test patterns** in `tests/` — avoid test code redundancy
  * **Worktree setup**: Use `task install-worktree` in worktrees. Never hardcode `uv venv`/`pip install` in skills or plans.

## **5. Pre-commit Hooks**

Install hooks after cloning: `pre-commit install`. Run manually with `pre-commit run --all-files`.

Configured hooks: ruff format (auto-fix), ruff check (auto-fix), mypy type checking, uv lock check, gitleaks secret scanning.

## **6. Architecture**

Top-level layout:

```
generic_automation_mcp/
├── assets/
├── docs/
├── scripts/
├── src/autoskillit/   # see below
├── tests/             # mirrors src/ layout; see tests/CLAUDE.md
├── Taskfile.yml
├── install.sh
└── pyproject.toml
```

`src/autoskillit/`:

```
├── __init__.py
├── __main__.py
├── _llm_triage.py           # Contract staleness triage (Haiku subprocess)
├── smoke_utils.py           # Callables for smoke-test pipeline run_python steps
├── hook_registry.py         # HookDef, HOOK_REGISTRY, generate_hooks_json
├── version.py               # Version health utilities (L0)
├── .claude-plugin/          # plugin.json
├── .mcp.json
│
├── core/                    # L0 foundation (zero autoskillit imports)
│   ├── __init__.py          #   Re-exports public surface
│   ├── io.py                #   atomic_write, ensure_project_temp, YAML helpers
│   ├── logging.py
│   ├── paths.py             #   pkg_root(), is_git_worktree()
│   ├── types.py             #   Re-export hub for _type_*.py
│   ├── _type_enums.py       #   StrEnums
│   ├── _type_subprocess.py
│   ├── _type_constants.py   #   GATED_TOOLS, FREE_RANGE_TOOLS, SKILL_TOOLS, etc.
│   ├── _type_results.py     #   LoadResult, SkillResult, FailureRecord, CleanupResult, etc.
│   ├── _type_protocols.py   #   Protocols: GatePolicy, HeadlessExecutor, CIWatcher, etc.
│   ├── _type_helpers.py
│   ├── _terminal_table.py   #   L0 color-agnostic terminal table primitive
│   ├── branch_guard.py
│   ├── claude_conventions.py #  Skill discovery directory layout constants
│   └── github_url.py        #   parse_github_repo
│
├── config/                  # L1
│   ├── __init__.py
│   ├── defaults.yaml
│   ├── ingredient_defaults.py
│   └── settings.py          #   Dataclass config + dynaconf layered resolution
│
├── pipeline/                # L1 pipeline state
│   ├── __init__.py
│   ├── audit.py             #   FailureRecord, DefaultAuditLog
│   ├── background.py        #   DefaultBackgroundSupervisor
│   ├── context.py           #   ToolContext DI container
│   ├── gate.py              #   DefaultGateState, gate_error_result
│   ├── mcp_response.py      #   Per-tool response size tracking
│   ├── telemetry_fmt.py     #   Canonical token/timing display
│   ├── timings.py
│   ├── tokens.py
│   └── pr_gates.py          #   is_ci_passing, is_review_passing, partition_prs
│
├── execution/               # L1
│   ├── __init__.py
│   ├── commands.py          #   Claude{Interactive,Headless}Cmd builders
│   ├── db.py                #   Read-only SQLite with defence-in-depth
│   ├── diff_annotator.py    #   Diff annotation + findings filter for review-pr
│   ├── headless.py          #   Headless Claude session orchestration
│   ├── linux_tracing.py     #   /proc + psutil process tracing (Linux)
│   ├── anomaly_detection.py #   Post-hoc anomaly detection over snapshots
│   ├── session_log.py       #   XDG-aware session diagnostics log writer
│   ├── recording.py         #   Record/replay subprocess runners via api-simulator
│   ├── process.py           #   Facade re-exporting from _process_*.py
│   ├── _process_io.py
│   ├── _process_jsonl.py
│   ├── _process_kill.py
│   ├── _process_monitor.py
│   ├── _process_pty.py
│   ├── _process_race.py     #   RaceAccumulator, resolve_termination
│   ├── quota.py             #   QuotaStatus, cache, check_and_sleep_if_needed
│   ├── ci.py                #   GitHub Actions CI watcher (httpx, never raises)
│   ├── merge_queue.py       #   GitHub merge queue watcher
│   ├── github.py            #   GitHub issue fetcher
│   ├── session.py           #   ClaudeSessionResult, extract_token_usage
│   ├── remote_resolver.py   #   upstream > origin, clone-aware
│   ├── testing.py           #   Pytest output parsing + pass/fail adjudication
│   └── pr_analysis.py       #   extract_linked_issues, DOMAIN_PATHS, partition_files_by_domain
│
├── workspace/               # L1
│   ├── __init__.py
│   ├── cleanup.py           #   CleanupResult, preserve list
│   ├── clone.py             #   Clone-based run isolation
│   ├── session_skills.py    #   Per-session ephemeral skill dirs; subset filtering
│   ├── clone_registry.py    #   Shared file-based coordination for deferred cleanup
│   ├── skills.py            #   SkillResolver — bundled skill listing
│   └── worktree.py
│
├── recipe/                  # L2
│   ├── __init__.py
│   ├── contracts.py         #   Contract card generation + staleness triage
│   ├── io.py                #   load_recipe, list_recipes, iter_steps_with_context
│   ├── loader.py            #   Path-based recipe metadata utilities
│   ├── _api.py              #   Orchestration API
│   ├── diagrams.py          #   Flow diagram generation + staleness detection
│   ├── registry.py          #   RuleFinding, RuleSpec, semantic_rule decorator
│   ├── repository.py
│   ├── _analysis.py         #   Step graph building + dataflow analysis
│   ├── rules_bypass.py
│   ├── rules_ci.py
│   ├── rules_clone.py
│   ├── rules_cmd.py
│   ├── rules_contracts.py
│   ├── rules_dataflow.py
│   ├── rules_graph.py
│   ├── rules_inputs.py
│   ├── rules_isolation.py
│   ├── rules_merge.py
│   ├── rules_packs.py
│   ├── rules_recipe.py
│   ├── rules_skill_content.py
│   ├── rules_skills.py
│   ├── rules_tools.py
│   ├── rules_verdict.py
│   ├── rules_worktree.py     #   Semantic validation rule modules
│   ├── _skill_placeholder_parser.py
│   ├── schema.py            #   Recipe, RecipeStep, DataFlowWarning
│   ├── staleness_cache.py
│   └── validator.py         #   validate_recipe, analyze_dataflow
│
├── migration/               # L2
│   ├── __init__.py
│   ├── engine.py            #   MigrationEngine, adapter ABC hierarchy
│   ├── _api.py              #   check_and_migrate
│   ├── loader.py            #   Migration note discovery + version chaining
│   └── store.py             #   FailureStore (JSON, atomic writes)
│
├── server/                  # L3 FastMCP server
│   ├── __init__.py          #   FastMCP app, kitchen gating, headless tool reveal
│   ├── git.py               #   Merge workflow for merge_worktree
│   ├── _editable_guard.py   #   Pre-deletion editable install guard (stdlib-only)
│   ├── helpers.py
│   ├── tools_kitchen.py     #   open_kitchen, close_kitchen + recipe:// resource
│   ├── tools_ci.py          #   CI/merge-queue tool handlers
│   ├── tools_clone.py
│   ├── tools_execution.py   #   run_cmd, run_python, run_skill
│   ├── tools_git.py         #   merge_worktree, classify_fix, etc.
│   ├── tools_recipe.py
│   ├── tools_status.py      #   kitchen_status, reports, summaries, quota events, read_db
│   ├── tools_github.py      #   fetch_github_issue, get_issue_title, report_bug
│   ├── tools_issue_lifecycle.py #  prepare/enrich/claim/release issue
│   ├── tools_pr_ops.py      #   get_pr_reviews, bulk_close_issues
│   ├── tools_workspace.py   #   test_check, reset_test_dir, reset_workspace
│   ├── _factory.py          #   Composition Root: make_context()
│   └── _state.py            #   Lazy init, plugin dir resolution
│
├── cli/                     # L3 CLI
│   ├── __init__.py
│   ├── _ansi.py             #   supports_color, NO_COLOR/TERM=dumb
│   ├── _terminal.py         #   terminal_guard() TTY restore
│   ├── _terminal_table.py   #   Re-export shim from core/_terminal_table
│   ├── _cook.py             #   cook: ephemeral skill session launcher
│   ├── _doctor.py           #   12 project setup checks
│   ├── _hooks.py            #   PreToolUse hook registration helpers
│   ├── _init_helpers.py
│   ├── _marketplace.py      #   Plugin install/upgrade
│   ├── _mcp_names.py        #   MCP prefix detection
│   ├── _onboarding.py       #   First-run detection + guided menu
│   ├── _prompts.py          #   Orchestrator prompt builder
│   ├── _stale_check.py      #   Version comparison, hook-drift prompt
│   ├── _workspace.py        #   Workspace clean helpers
│   └── app.py               #   CLI entry: serve, init, config, skills, recipes, doctor, etc.
│
├── hooks/                   # Claude Code PreToolUse/PostToolUse/SessionStart scripts
│   ├── __init__.py
│   ├── hooks.json           #   Plugin hook registration
│   ├── branch_protection_guard.py
│   ├── quota_check.py       #   Blocks run_skill when threshold exceeded
│   ├── quota_post_check.py  #   Appends quota warning to run_skill output
│   ├── remove_clone_guard.py
│   ├── skill_cmd_check.py
│   ├── skill_command_guard.py
│   ├── open_kitchen_guard.py
│   ├── unsafe_install_guard.py
│   ├── generated_file_write_guard.py
│   ├── headless_orchestration_guard.py
│   ├── pretty_output.py     #   Reformats MCP JSON responses as Markdown-KV
│   ├── token_summary_appender.py #  Appends Token Usage Summary to PR body
│   └── session_start_reminder.py #  Injects open-kitchen reminder on resume
│
├── migrations/              # Versioned migration YAML notes
├── recipes/                 # Bundled recipe YAML + contracts/, diagrams/, sub-recipes/
├── skills/                  # Tier 1: open-kitchen, close-kitchen, sous-chef
└── skills_extended/         # Tier 2 (interactive) + Tier 3 (pipeline/automation) skills
                             # incl. arch-lens-* (13) and exp-lens-* (18) diagram families
```

**Session diagnostics logs** live at `~/.local/share/autoskillit/logs/` (Linux) or `~/Library/Application Support/autoskillit/logs/` (macOS). Override with `linux_tracing.log_dir`. Session directories are named by Claude Code session UUID when available (parsed from stdout, or discovered from JSONL filename via Channel B). Fallback: `no_session_{timestamp}`. Query the index: `jq 'select(.success == false)' ~/.local/share/autoskillit/logs/sessions.jsonl`.

**CRITICAL**: When using subagents, invoke with `CLAUDE_CODE_EXIT_AFTER_STOP_DELAY=120000` to ensure subagents exit when finished.

## 7. Session Diagnostics

**Path components use hyphens, not underscores.** Log directory names and session folder names are hyphen-separated. Never assume underscores when constructing or searching for log paths — hyphen mismatch causes ENOENT (session f9170655 pattern).
