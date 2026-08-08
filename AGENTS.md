# **AutoSkillit: Development Guidelines**

Mandatory instructions for AI-assisted development in this repository.

## **1. Core Project Goal**

A coding-agent plugin that orchestrates automated skill-driven workflows using headless sessions. It provides MCP tools (gated behind `open_kitchen`/`close_kitchen` via FastMCP v3 tag-based visibility) and bundled skills registered as `/autoskillit:*` slash commands.

## **2. General Principles**

The assigned issue or ticket is the source of truth; do not work on unassigned features or unrelated refactoring.

## **3. Critical Rules - DO NOT VIOLATE**

### **3.1. Code and Implementation**

  * **Do Not Oversimplify**: Implement logic with required complexity. No shortcuts that compromise correctness.
  * **Do Not Over-Engineer**: Complexity must pay rent immediately. An abstraction, registry, wrapper, state machine, or new subsystem is justified only when the change at hand lands more simply on it — never for hypothetical future needs. Every added line is a maintenance liability someone must read, understand, and keep working. When the problem genuinely requires a complex solution, build it; prefer one deep module (simple interface, substantial implementation) over layers of shallow pass-throughs.
  * **Respect the Existing Architecture**: Build on established patterns. Understand existing code before modifying.
  * **Address the Root Cause**: Debug to find and fix root causes. No hardcoded workarounds. When a clean fix requires it, do preparatory refactoring — first make the change easy, then make the easy change; the structural work must immediately simplify the change at hand, not generalize for imagined ones.
  * **No Backward Compatibility Hacks**: No comments about dead code. Remove dead code entirely.
  * **Avoid Redundancy**: Do not duplicate logic or utilities.
  * **Use Current Package Versions**: Web search for current stable versions when adding dependencies.
  * **Version Bumps**: When bumping the package version, update `pyproject.toml` and run `task sync-versions && uv lock`; then search tests for hardcoded version strings (e.g. `AUTOSKILLIT_INSTALLED_VERSION` monkeypatches) and update them.
  * **Run pre-commit before committing**: Always run `pre-commit run --all-files` before committing. Do not skip this step even when code appears clean — hooks auto-fix formatting and abort the commit, requiring re-stage and retry. Configured hooks include ruff format, ruff check, mypy, uv lock validation, and gitleaks.
  * **Retirement Registries**: Renaming or retiring a registered entity must update its retirement registry in the SAME commit — hook scripts (`RETIRED_SCRIPT_BASENAMES`), skills (`RETIRED_SKILL_NAMES`), install artifact shapes (`RETIRED_INSTALL_ARTIFACT_SHAPES`), and skill contract validations (`SKILL_CONTRACT_REMEDIATIONS`). Contract tests enforce each; full procedures and rationale: `tests/AGENTS.md` § Retirement Registries.
  * **Search-tool ERE syntax**: ripgrep-backed search tools use POSIX ERE — `Grep(pattern="foo|bar")`, never `\|` (BRE backslash-pipe), which ripgrep treats literally and returns 0 results.
  * **Worktree Init Prohibition**: Never run `autoskillit init` from within a git worktree (`sync_hooks_to_settings()` raises `RuntimeError`). Use `task install-worktree` for worktree setup. Never hardcode `uv venv`/`pip install` in skills or plans.
  * **Naming convention — `*Def` vs `*Spec` suffixes**:
    - `*Def` — static definition of a registered entity (e.g., `HookDef`, `PackDef`, `FeatureDef`, `RuleDef`). Typically a `NamedTuple` or `@dataclass(frozen=True)`, used as elements in a registry or lookup table. Typically lives in `core/`; stdlib-only types importable from hook scripts may live at the package root (e.g., `HookDef` in `hook_registry.py`).
    - `*Spec` — behavioral specification or validation rule (e.g., `ExperimentTypeSpec`, `WriteBehaviorSpec`). Typically a `@dataclass` or `TypedDict` configuring a pipeline or validation stage. Typically lives in `recipe/` or domain layers; `*Spec` types used by IL-0 core protocols live in `core/` (e.g., `WriteBehaviorSpec` in `core/types/_type_results.py`).
  * **Commit discipline**: Always create NEW commits. Never use `git commit --amend`, `--fixup`, or `--squash` unless the active recipe or SKILL.md explicitly requires it. This applies to all session types including headless sessions.
  * **Multi-part plan green-gate invariant**: Every part of a multi-part plan must independently pass `task test-check`. A part that invalidates a pre-existing test must update, remove, or bridge it with `xfail(strict=True)` in the same part, with a `reason` citing the open tracking issue (`#NNNN`) — enforced by an architectural guard. A bridge whose exit condition is satisfied within the same PR must be removed in that PR.

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

  * **Add tests for new features**
  * **Follow existing test patterns** in `tests/` — avoid test code redundancy
  * **Run tests**: `task test-all` from the project root (human-facing, runs lint + tests). For automation and MCP tools, `task test-check` is used (unambiguous PASS/FAIL, correct PIPESTATUS capture). Never use `pytest`, `python -m pytest`, or any other test runner directly.
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

`src/autoskillit/` package layout is discoverable directly (`ls src/autoskillit/`); any package table in AGENTS files is an index, not required reading. Packages carry local `AGENTS.md` guides where local rules exist; packages without one inherit the nearest ancestor guide. Import-layer levels (IL-0 `core/` foundation → IL-1 `execution/`/`workspace/`/`pipeline/` → IL-2 `recipe/`/`fleet/` → IL-3 `server/`/`cli/`) are enforced by import-linter contracts in `pyproject.toml`.

**Import layer vs. orchestration level — disambiguation table:**

| Notation | System | Example | Meaning |
|----------|--------|---------|---------|
| IL-N (single digit) | Import layer level | IL-0, IL-2 | Module's position in the import DAG |
| IL-NNN (three-digit) | Import-linter contract ID | IL-001, IL-009 | Specific pyproject.toml contract |
| L-N | Orchestration level | L0, L3 | Runtime session spawning tier (see `docs/orchestration-levels.md`) |

## **6. Session Diagnostics**

**Path components use hyphens, not underscores.** Log directory names and session folder names are hyphen-separated. Never assume underscores when constructing or searching for log paths — hyphen mismatch causes ENOENT (session f9170655 pattern).

**Per-backend session identification:**

- **Claude Code**: logs at `~/.local/share/autoskillit/logs/` (Linux; macOS: `~/Library/Application Support/autoskillit/logs/`), directories named by agent session ID (resolved from stdout or Channel B, the JSONL side-channel stream). Query the index: `jq 'select(.success == false)' ~/.local/share/autoskillit/logs/sessions.jsonl`.
- **Codex**: sessions identified by `thread_id`, stored under `default_log_dir()/codex-sessions/`, registered in `sessions.jsonl` via the `codex_log` field. Deep mechanics: `docs/developer/diagnostics.md`.
