@AGENTS.md

# Claude Code — Project-Specific Rules

Mandatory instructions for AI-assisted development in this repository.

## Skill Invocations Are Orders

  * When a message includes a `/skill-name`, execute it via the Skill tool **BEFORE** any other action. No exceptions.
  * Never skip or substitute a skill invocation based on your own judgment.

## Code and Implementation (Claude-Specific Additions)

  * **Version Bumps**: When bumping the package version, update `pyproject.toml` and run `task sync-versions && uv lock`; then search tests for hardcoded version strings (e.g. `AUTOSKILLIT_INSTALLED_VERSION` monkeypatches) and update them.
  * **Run pre-commit before committing**: Always run `pre-commit run --all-files` before committing. Do not skip this step even when code appears clean — hooks auto-fix formatting and abort the commit, requiring re-stage and retry.
  * **Hook Renames**: Renaming a hook script under `src/autoskillit/hooks/` must update `HOOK_REGISTRY` in `hook_registry.py` AND add the old basename to `RETIRED_SCRIPT_BASENAMES` in the same commit. `test_no_retired_name_has_a_live_file` will fail otherwise.
  * **Skill Renames**: Renaming a skill under `src/autoskillit/skills_extended/` (or `src/autoskillit/skills/`) must update the skill's `SKILL.md` `name:` field AND add the old directory name to `RETIRED_SKILL_NAMES` in `src/autoskillit/core/types/_type_constants.py` in the SAME commit. `test_no_retired_skill_name_has_a_live_directory` will fail otherwise.
  * **Grep tool uses ripgrep (ERE) syntax**: Use `|` for OR-alternation in Grep tool `pattern`
    arguments. `\|` is Bash grep BRE syntax — ripgrep treats it as a literal backslash-pipe
    and returns 0 results. Example: `Grep(pattern="foo|bar")` not `Grep(pattern="foo\|bar")`.
  * **Worktree Init Prohibition**: Never run `autoskillit init` from within a git worktree. `sync_hooks_to_settings()` will raise `RuntimeError` if `pkg_root()` resolves to a worktree. Use `task install-worktree` for worktree setup — it does NOT call `init`.
  * **Naming convention — `*Def` vs `*Spec` suffixes**:
    - `*Def` — static definition of a registered entity (e.g., `HookDef`, `PackDef`, `FeatureDef`, `RuleDef`). Typically a `NamedTuple` or `@dataclass(frozen=True)`, used as elements in a registry or lookup table. Typically lives in `core/`; stdlib-only types importable from hook scripts may live at the package root (e.g., `HookDef` in `hook_registry.py`).
    - `*Spec` — behavioral specification or validation rule (e.g., `ExperimentTypeSpec`, `WriteBehaviorSpec`). Typically a `@dataclass` or `TypedDict` configuring a pipeline or validation stage. Typically lives in `recipe/` or domain layers; `*Spec` types used by IL-0 core protocols live in `core/` (e.g., `WriteBehaviorSpec` in `core/types/_type_results.py`).
  * **Commit discipline**: Always create NEW commits. Never use `git commit --amend`, `--fixup`, or `--squash` unless the active recipe or SKILL.md explicitly requires it. This applies to all session types including headless sessions.

## CLAUDE.md Modifications

  * **Correcting existing content is permitted**: If you discover that CLAUDE.md contains inaccurate information (wrong file paths, stale names, incorrect tool attributions), you may correct it without being asked.
  * **Adding new content requires explicit instruction**: Never add new sections, bullet points, entries, or any new information to CLAUDE.md unless the user has explicitly asked you to update or extend it. Corrections to existing facts ≠ permission to expand scope.

## Pyright LSP Usage

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

## Testing (Claude-Specific)

  * **Run tests**: `task test-all` from the project root (human-facing, runs lint + tests). For automation and MCP tools, `task test-check` is used (unambiguous PASS/FAIL, correct PIPESTATUS capture). Never use `pytest`, `python -m pytest`, or any other test runner directly.
  * **Worktree setup**: Use `task install-worktree` in worktrees. Never hardcode `uv venv`/`pip install` in skills or plans.
  * **Filtered tests**: `task test-filtered` runs path-filtered tests (defaults `AUTOSKILLIT_TEST_FILTER=conservative`). Set `AUTOSKILLIT_TEST_BASE_REF` to control the diff base. See `tests/CLAUDE.md` for filter modes and algorithm details.

## Pre-commit Hooks

Run manually with `pre-commit run --all-files`.

Configured hooks: ruff format (auto-fix), ruff check (auto-fix), mypy type checking, uv lock check, gitleaks secret scanning.

## Subagent Configuration

When using subagents, invoke with `CLAUDE_CODE_EXIT_AFTER_STOP_DELAY=120000` to ensure subagents exit when finished.