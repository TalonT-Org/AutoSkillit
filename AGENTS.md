# **AutoSkillit: Development Guidelines**

Mandatory instructions for AI-assisted development in this repository.

## **1. Core Project Goal**

A coding-agent plugin that orchestrates automated skill-driven workflows using headless sessions. It provides MCP tools (gated behind `open_kitchen`/`close_kitchen` via FastMCP v3 tag-based visibility) and bundled skills registered as `/autoskillit:*` slash commands.

## **2. General Principles**

  * **Follow the Task Description**: The issue or ticket is your primary source of truth.
  * **Adhere to Task Scope**: Do not work on unassigned features or unrelated refactoring.
  * **Implement Faithfully**: Produce functionally correct implementations. Do not add unrequested features.
  * **Adhere to Project Standards**: Write clean, maintainable Python following established conventions.

## **3. Critical Rules - DO NOT VIOLATE**

### **3.1. Code and Implementation**

  * **Do Not Oversimplify**: Implement logic with required complexity. No shortcuts that compromise correctness.
  * **Respect the Existing Architecture**: Build on established patterns. Understand existing code before modifying.
  * **Address the Root Cause**: Debug to find and fix root causes. No hardcoded workarounds.
  * **No Backward Compatibility Hacks**: No comments about dead code. Remove dead code entirely.
  * **Avoid Redundancy**: Do not duplicate logic or utilities.
  * **Use Current Package Versions**: Web search for current stable versions when adding dependencies.

### **3.2. File System**

  * **Temporary Files:** All temp files must go in the project's `.autoskillit/temp/` directory.
  * **Do Not Add Root Files**: Never create new root files unless explicitly required (except agent instruction files like AGENTS.md and CLAUDE.md).
  * **Never commit unless told to do so**

### **3.3. GitHub API Call Discipline**

  * **Batch inline review comments** via `POST /pulls/{N}/reviews` with `comments[]` array — never post comments individually unless the batch call fails.
  * **Batch GraphQL mutations** via aliases (N mutations in 1 request = 5 pts total, not N × 5 pts). Use for thread resolution, bulk PR queries, and any operation touching multiple entities.
  * **Delay 1s between POST/PATCH/PUT/DELETE calls** — add `sleep 1` (in shell) or `await asyncio.sleep(1)` (in Python) between consecutive mutating GitHub API calls.
  * **Pre-fetch entity lists** upfront in a single call; pass results via manifest files or variables rather than querying per-entity.
  * **Use `--json` field selection** to request only needed fields from `gh` CLI commands.
  * **Prefer GraphQL** for multi-entity reads — alias queries cost 1 point regardless of entity count.
  * **Never check response body for `comments` array length** after `POST /pulls/{N}/reviews` — GitHub does not echo back the comments array; HTTP 200 is the success signal.

### **3.4. GitHub Issue Body is the Source of Truth**

  * **Never use `gh issue comment`** to communicate issue status, triage feedback, tracking
    info, or occurrence data. Comments are not read by downstream consumers and fragment the
    record.
  * **All issue content updates must use `gh issue edit --body-file`**: fetch the current
    body, append the new section, write to `${{AUTOSKILLIT_TEMP}}`, then run
    `gh issue edit {number} --body-file "$FILE"`.
  * The `update_issue_body()` method on `GitHubFetcher` is the Python API equivalent.

## **4. Testing Guidelines**

The project uses pytest with pytest-asyncio. Tests run in parallel via pytest-xdist (`-n 4`). All tests must be safe for parallel execution.

  * **Always run tests at end of task**
  * **Add tests for new features**
  * **Follow existing test patterns** in `tests/` — avoid test code redundancy

## **5. Architecture**

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
| `./` | — | Package root: `__init__`, `__main__`, `hook_registry`, `version`, `_test_filter`, `_llm_triage` |
| `smoke_utils/` | — | Callables for smoke-test pipeline `run_python` steps |
| `core/` | IL-0 | Foundation — types/, runtime/, paths, IO, feature flags (zero autoskillit imports) |
| `config/` | IL-1 | `AutomationConfig` + Dynaconf loader + 28 leaf dataclasses |
| `pipeline/` | IL-1 | Pipeline state — `ToolContext` DI, gate, audit log, telemetry |
| `execution/` | IL-1 | Headless sessions (headless/, process/, merge_queue/, session/), backends/, CI/GitHub |
| `workspace/` | IL-1 | Clone management, worktrees, skill resolution |
| `planner/` | IL-1 | Progressive resolution planner — phases, assignments, WPs, validation |
| `report/` | IL-1 | HTML report renderer — `renderer.py` uses `pkg_root()` for asset resolution |
| `recipe/` | IL-2 | Recipe schema, validation, semantic rules/ (campaign/, ci/, dataflow/, graph/) |
| `migration/` | IL-2 | Versioned migration engine + failure store |
| `fleet/` | IL-2 | Campaign dispatch, semaphore, sidecar, liveness, state persistence |
| `server/` | IL-3 | FastMCP server — tools/, kitchen gating, session-type dispatch |
| `cli/` | IL-3 | CLI — doctor/, update/, fleet/ subcommands, ui/, session/ management |
| `hooks/` | — | coding-agent hook scripts — guards/, formatters/ |
| `agents/` | — | Bundled agent definition markdown files served as MCP resources |
| `recipes/` | — | Bundled recipe YAML + contracts, diagrams, sub-recipes |
| `skills/` | — | Tier 1 skills: open-kitchen, close-kitchen, sous-chef |
| `skills_extended/` | — | Tier 2 (interactive) + Tier 3 (pipeline) skills, incl. arch-lens-* (13), exp-lens-* (18), vis-lens-* (12) |

**Session diagnostics logs** — per-backend log paths and session identification:

- **Claude Code**: Logs live at `~/.local/share/autoskillit/logs/` (Linux) or `~/Library/Application Support/autoskillit/logs/` (macOS). Override with `linux_tracing.log_dir`. Session directories are named by the agent session ID when available (resolved from stdout or, for Claude Code backends, from the session JSONL filename via Channel B (the JSONL side-channel stream)). Fallback: `no_session_{timestamp}`. Query the index: `jq 'select(.success == false)' ~/.local/share/autoskillit/logs/sessions.jsonl`.
- **Codex**: Session log discovery uses `CodexSessionLocator` which searches `rollout-*.jsonl` files in `default_log_dir()/codex-sessions/` (permanent storage) and `$CODEX_HOME/sessions/` (ephemeral), matching by `thread_id` from the `thread.started` NDJSON event. Session logs are preserved via a symlink from the ephemeral `$CODEX_HOME/sessions/` to permanent storage during `init_session()`.

**Import layer vs. orchestration level — disambiguation table:**

| Notation | System | Example | Meaning |
|----------|--------|---------|---------|
| IL-N (single digit) | Import layer level | IL-0, IL-2 | Module's position in the import DAG |
| IL-NNN (three-digit) | Import-linter contract ID | IL-001, IL-009 | Specific pyproject.toml contract |
| L-N | Orchestration level | L0, L3 | Runtime session spawning tier (see `docs/orchestration-levels.md`) |

## **6. Session Diagnostics**

**Path components use hyphens, not underscores.** Log directory names and session folder names are hyphen-separated. Never assume underscores when constructing or searching for log paths — hyphen mismatch causes ENOENT (session f9170655 pattern).

**Per-backend session identification:**

- **Claude Code**: Session directories are named by the agent session ID (resolved from stdout or from the session JSONL filename via Channel B). Fallback: `no_session_{timestamp}`.
- **Codex**: The canonical session identifier is the `thread_id` from the `thread.started` NDJSON event, extracted by `CodexStreamParser`. `CodexSessionLocator` searches `rollout-*.jsonl` files in permanent storage (`codex-sessions/`) and ephemeral `$CODEX_HOME/sessions/`. Logs are registered in `sessions.jsonl` via the `codex_log` field.
