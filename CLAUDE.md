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
  * **Version Bumps**: When bumping the package version, update `pyproject.toml` and run `task sync-versions && uv lock`; then search tests for hardcoded version strings (e.g. `AUTOSKILLIT_INSTALLED_VERSION` monkeypatches) and update them.
  * **Run pre-commit before committing**: Always run `pre-commit run --all-files` before committing. Do not skip this step even when code appears clean — hooks auto-fix formatting and abort the commit, requiring re-stage and retry.
  * **Hook Renames**: Renaming a hook script under `src/autoskillit/hooks/` must update `HOOK_REGISTRY` in `hook_registry.py` AND add the old basename to `RETIRED_SCRIPT_BASENAMES` in the same commit. `test_no_retired_name_has_a_live_file` will fail otherwise.
  * **Skill Renames**: Renaming a skill under `src/autoskillit/skills_extended/` (or `src/autoskillit/skills/`) must update the skill's `SKILL.md` `name:` field AND add the old directory name to `RETIRED_SKILL_NAMES` in `src/autoskillit/core/types/_type_constants.py` in the SAME commit. `test_no_retired_skill_name_has_a_live_directory` will fail otherwise.
  * **Grep tool uses ripgrep (ERE) syntax**: Use `|` for OR-alternation in Grep tool `pattern`
    arguments. `\|` is Bash grep BRE syntax — ripgrep treats it as a literal backslash-pipe
    and returns 0 results. Example: `Grep(pattern="foo|bar")` not `Grep(pattern="foo\|bar")`.
    In production, `grep_pattern_lint_guard.py` (hooks/guards/) blocks `\|` calls with a correction hint. The silent zero-results failure described above only occurs in hook-inactive contexts (e.g., raw CLI usage without the plugin installed).
  * **Worktree Init Prohibition**: Never run `autoskillit init` from within a git worktree. `sync_hooks_to_settings()` will raise `RuntimeError` if `pkg_root()` resolves to a worktree. Use `task install-worktree` for worktree setup — it does NOT call `init`.
  * **Naming convention — `*Def` vs `*Spec` suffixes**:
    - `*Def` — static definition of a registered entity (e.g., `HookDef`, `PackDef`, `FeatureDef`). Typically a `NamedTuple` or `@dataclass(frozen=True)`, used as elements in a registry or lookup table. Typically lives in `core/`; stdlib-only types importable from hook scripts may live at the package root (e.g., `HookDef` in `hook_registry.py`).
    - `*Spec` — behavioral specification or validation rule (e.g., `RuleSpec`, `ExperimentTypeSpec`, `WriteBehaviorSpec`). Typically a `@dataclass` or `TypedDict` configuring a pipeline or validation stage. Typically lives in `recipe/` or domain layers; `*Spec` types used by IL-0 core protocols live in `core/` (e.g., `WriteBehaviorSpec` in `core/types/_type_results.py`).

### **3.2. File System**

  * **Temporary Files:** All temp files must go in the project's `.autoskillit/temp/` directory.
  * **Do Not Add Root Files**: Never create new root files unless explicitly required.
  * **Never commit unless told to do so**

### **3.3. CLAUDE.md Modifications**

  * **Correcting existing content is permitted**: If you discover that CLAUDE.md contains inaccurate information (wrong file paths, stale names, incorrect tool attributions), you may correct it without being asked.
  * **Adding new content requires explicit instruction**: Never add new sections, bullet points, entries, or any new information to CLAUDE.md unless the user has explicitly asked you to update or extend it. Corrections to existing facts ≠ permission to expand scope.

### **3.4. GitHub API Call Discipline**

  * **Batch inline review comments** via `POST /pulls/{N}/reviews` with `comments[]` array — never post comments individually unless the batch call fails.
  * **Batch GraphQL mutations** via aliases (N mutations in 1 request = 5 pts total, not N × 5 pts). Use for thread resolution, bulk PR queries, and any operation touching multiple entities.
  * **Delay 1s between POST/PATCH/PUT/DELETE calls** — add `sleep 1` (in shell) or `await asyncio.sleep(1)` (in Python) between consecutive mutating GitHub API calls.
  * **Pre-fetch entity lists** upfront in a single call; pass results via manifest files or variables rather than querying per-entity.
  * **Use `--json` field selection** to request only needed fields from `gh` CLI commands.
  * **Prefer GraphQL** for multi-entity reads — alias queries cost 1 point regardless of entity count.
  * **Never check response body for `comments` array length** after `POST /pulls/{N}/reviews` — GitHub does not echo back the comments array; HTTP 200 is the success signal.

### **3.5. GitHub Issue Body is the Source of Truth**

  * **Never use `gh issue comment`** to communicate issue status, triage feedback, tracking
    info, or occurrence data. Comments are not read by downstream consumers and fragment the
    record.
  * **All issue content updates must use `gh issue edit --body-file`**: fetch the current
    body, append the new section, write to `${{AUTOSKILLIT_TEMP}}`, then run
    `gh issue edit {number} --body-file "$FILE"`.
  * The `update_issue_body()` method on `GitHubFetcher` is the Python API equivalent.

### **3.6. Pyright LSP Usage**

The `LSP` tool provides type-aware code intelligence via Pyright. Use it for precise
navigation instead of grep when tracing symbols through imports, re-exports, or protocols.

**Available operations** (all take `filePath`, `line`, `character` — 1-based):

| Operation | Use case |
|-----------|----------|
| `goToDefinition` | Jump to where a symbol is defined (follows imports/re-exports) |
| `findReferences` | Find all usages of a symbol across the codebase |
| `documentSymbol` | List all classes, functions, and variables in a file |
| `goToImplementation` | Find concrete implementations of a Protocol or ABC |
| `prepareCallHierarchy` | Get the call hierarchy item at a position |
| `incomingCalls` | Find all callers of a function/method |
| `outgoingCalls` | Find all functions/methods called by a function |

**When to use LSP over grep:**
- Tracing a symbol through re-exports (e.g., `core/__init__.py` -> actual definition)
- Finding all implementations of a Protocol
- Mapping call hierarchies (who calls X, what does X call)
- Understanding a file's structure before editing

**When grep is still better:**
- Searching for string literals, comments, or non-symbol patterns
- Searching across non-Python files (YAML, JSON, markdown)

## **4. Testing Guidelines**

The project uses pytest with pytest-asyncio. Tests run in parallel via pytest-xdist (`-n 4`). All tests must be safe for parallel execution.

  * **Run tests**: `task test-all` from the project root (human-facing, runs lint + tests). For automation and MCP tools, `task test-check` is used (unambiguous PASS/FAIL, correct PIPESTATUS capture). Never use `pytest`, `python -m pytest`, or any other test runner directly.
  * **Always run tests at end of task**
  * **Add tests for new features**
  * **Follow existing test patterns** in `tests/` — avoid test code redundancy
  * **Worktree setup**: Use `task install-worktree` in worktrees. Never hardcode `uv venv`/`pip install` in skills or plans.
  * **Filtered tests**: `task test-filtered` runs path-filtered tests (defaults `AUTOSKILLIT_TEST_FILTER=conservative`). Set `AUTOSKILLIT_TEST_BASE_REF` to control the diff base. See `tests/CLAUDE.md` for filter modes and algorithm details.

## **5. Pre-commit Hooks**

Run manually with `pre-commit run --all-files`.

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

`src/autoskillit/` packages — each has its own CLAUDE.md with file-level detail (except `recipes/`, `skills/`, and `skills_extended/` — CLAUDE.md files for these are pending):

| Package | IL | Purpose |
|---|---|---|
| `./` | — | Package root: `__init__`, `__main__`, `hook_registry`, `version`, `_test_filter` |
| `core/` | IL-0 | Foundation — types/, runtime/, paths, IO, feature flags (zero autoskillit imports) |
| `config/` | IL-1 | `AutomationConfig` + Dynaconf loader + 24 leaf dataclasses |
| `pipeline/` | IL-1 | Pipeline state — `ToolContext` DI, gate, audit log, telemetry |
| `execution/` | IL-1 | Headless sessions (headless/, process/, merge_queue/, session/), CI/GitHub |
| `workspace/` | IL-1 | Clone management, worktrees, skill resolution |
| `planner/` | IL-1 | Progressive resolution planner — phases, assignments, WPs, validation |
| `recipe/` | IL-2 | Recipe schema, validation, semantic rules/ |
| `migration/` | IL-2 | Versioned migration engine + failure store |
| `fleet/` | IL-2 | Campaign dispatch, semaphore, sidecar, liveness, state persistence |
| `server/` | IL-3 | FastMCP server — tools/, kitchen gating, session-type dispatch |
| `cli/` | IL-3 | CLI — doctor/, update/, fleet/ subcommands, ui/, session/ management |
| `hooks/` | — | Claude Code hook scripts — guards/, formatters/ |
| `recipes/` | — | Bundled recipe YAML + contracts, diagrams, sub-recipes |
| `skills/` | — | Tier 1 skills: open-kitchen, close-kitchen, sous-chef |
| `skills_extended/` | — | Tier 2 (interactive) + Tier 3 (pipeline) skills, incl. arch-lens-* (13), exp-lens-* (18), vis-lens-* (12) |

**Session diagnostics logs** live at `~/.local/share/autoskillit/logs/` (Linux) or `~/Library/Application Support/autoskillit/logs/` (macOS). Override with `linux_tracing.log_dir`. Session directories are named by Claude Code session UUID when available (parsed from stdout, or discovered from JSONL filename via Channel B (the JSONL stream written by the Claude Code subprocess)). Fallback: `no_session_{timestamp}`. Query the index: `jq 'select(.success == false)' ~/.local/share/autoskillit/logs/sessions.jsonl`.

**CRITICAL**: When using subagents, invoke with `CLAUDE_CODE_EXIT_AFTER_STOP_DELAY=120000` to ensure subagents exit when finished.

**Import layer vs. orchestration level:** Module docstrings and import-linter
contracts use IL-N labels (IL-001–IL-009 in `pyproject.toml`) for the import
dependency hierarchy — these are separate from the L0–L3 orchestration levels
defined in `docs/orchestration-levels.md`.

## 7. Session Diagnostics

**Path components use hyphens, not underscores.** Log directory names and session folder names are hyphen-separated. Never assume underscores when constructing or searching for log paths — hyphen mismatch causes ENOENT (session f9170655 pattern).
