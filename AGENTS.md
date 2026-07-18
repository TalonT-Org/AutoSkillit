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
  * **Version Bumps**: When bumping the package version, update `pyproject.toml` and run `task sync-versions && uv lock`; then search tests for hardcoded version strings (e.g. `AUTOSKILLIT_INSTALLED_VERSION` monkeypatches) and update them.
  * **Run pre-commit before committing**: Always run `pre-commit run --all-files` before committing. Do not skip this step even when code appears clean — hooks auto-fix formatting and abort the commit, requiring re-stage and retry.
  * **Hook Renames**: Renaming a hook script under `src/autoskillit/hooks/` must update `HOOK_REGISTRY` in `hook_registry.py` AND add the old basename to `RETIRED_SCRIPT_BASENAMES` in the same commit. `test_no_retired_name_has_a_live_file` will fail otherwise.
  * **Skill Renames**: Renaming a skill under `src/autoskillit/skills_extended/` (or `src/autoskillit/skills/`) must update the skill's `SKILL.md` `name:` field AND add the old directory name to `RETIRED_SKILL_NAMES` in `src/autoskillit/core/types/_type_constants.py` in the SAME commit. `test_no_retired_skill_name_has_a_live_directory` will fail otherwise.
  * **Search-tool ERE syntax**: ripgrep-backed search tools use POSIX ERE — use `|` for OR-alternation in `pattern` arguments. `\|` is Bash grep BRE syntax; ripgrep treats it as a literal backslash-pipe and returns 0 results. Example: `Grep(pattern="foo|bar")` not `Grep(pattern="foo\|bar")`.
  * **Worktree Init Prohibition**: Never run `autoskillit init` from within a git worktree. `sync_hooks_to_settings()` will raise `RuntimeError` if `pkg_root()` resolves to a worktree. Use `task install-worktree` for worktree setup — it does NOT call `init`.
  * **Naming convention — `*Def` vs `*Spec` suffixes**:
    - `*Def` — static definition of a registered entity (e.g., `HookDef`, `PackDef`, `FeatureDef`, `RuleDef`). Typically a `NamedTuple` or `@dataclass(frozen=True)`, used as elements in a registry or lookup table. Typically lives in `core/`; stdlib-only types importable from hook scripts may live at the package root (e.g., `HookDef` in `hook_registry.py`).
    - `*Spec` — behavioral specification or validation rule (e.g., `ExperimentTypeSpec`, `WriteBehaviorSpec`). Typically a `@dataclass` or `TypedDict` configuring a pipeline or validation stage. Typically lives in `recipe/` or domain layers; `*Spec` types used by IL-0 core protocols live in `core/` (e.g., `WriteBehaviorSpec` in `core/types/_type_results.py`).
  * **Commit discipline**: Always create NEW commits. Never use `git commit --amend`, `--fixup`, or `--squash` unless the active recipe or SKILL.md explicitly requires it. This applies to all session types including headless sessions.
  * **Multi-part plan green-gate invariant**: Every part of a multi-part plan must independently pass `task test-check`. If a part's changes invalidate a pre-existing test, that test must be updated, removed, or marked `xfail(strict=True)` in the same part. The `xfail(strict=True)` bridge is the canonical mechanism: `check_test_passed` ignores xfailed counts; `strict=True` forces cleanup when the xfail condition is resolved. A bridge's `reason` must cite the open tracking issue (`#NNNN`) for the deferred work — enforced by an architectural guard. A bridge whose stated exit condition is satisfied within the same PR must be removed in that same PR.

#### **3.1.a. Pre-commit Hooks**

Run manually with `pre-commit run --all-files`.

Configured hooks: ruff format (auto-fix), ruff check (auto-fix), mypy type checking, uv lock check, gitleaks secret scanning.

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
  * **Run tests**: `task test-all` from the project root (human-facing, runs lint + tests). For automation and MCP tools, `task test-check` is used (unambiguous PASS/FAIL, correct PIPESTATUS capture). Never use `pytest`, `python -m pytest`, or any other test runner directly.
  * **Worktree setup**: Use `task install-worktree` in worktrees. Never hardcode `uv venv`/`pip install` in skills or plans.
  * **Filtered tests**: `task test-filtered` runs path-filtered tests (defaults `AUTOSKILLIT_TEST_FILTER=conservative`). Set `AUTOSKILLIT_TEST_BASE_REF` to control the diff base. See `tests/AGENTS.md` for filter modes and algorithm details.

## **5. Architecture**

Top-level layout:

```
generic_automation_mcp/
├── assets/
├── docs/
├── scripts/
├── src/autoskillit/   # see below
├── tests/             # mirrors src/ layout; see tests/AGENTS.md
├── Taskfile.yml
├── install.sh
└── pyproject.toml
```

`src/autoskillit/` packages — each has its own `AGENTS.md` with file-level detail, plus a thin `CLAUDE.md` shim that imports the corresponding `AGENTS.md` (except `recipes/`, `skills/`, and `skills_extended/` — `AGENTS.md` files for these are pending):

| Package | IL | Purpose |
|---|---|---|
| `./` | — | Package root: `__init__`, `__main__`, `hook_registry`, `version`, `_test_filter`, `_llm_triage` |
| `smoke_utils/` | — | Callables for smoke-test pipeline `run_python` steps |
| `core/` | IL-0 | Foundation — types/, runtime/, paths, IO, feature flags (zero autoskillit imports) |
| `config/` | IL-1 | `AutomationConfig` + Dynaconf loader + 29 leaf dataclasses |
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
