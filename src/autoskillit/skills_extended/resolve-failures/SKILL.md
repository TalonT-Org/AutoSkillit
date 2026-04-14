---
name: resolve-failures
description: Fix test failures in a worktree without merging. Leaves worktree green and unmerged for the orchestrator's merge gate. Use when tests fail after implementation. Takes worktree path, plan path, and base branch as positional arguments.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: resolve-failures] Resolving test failures...'"
          once: true
---

# Resolve Failures Skill

Fix test failures in a worktree implemented by `/autoskillit:implement-worktree-no-merge`, leaving the worktree green and unmerged for the orchestrator's merge gate.

## When to Use

- MCP orchestrator calls this via `run_skill` after `ci_watch` reports a CI failure
- MCP orchestrator calls this via `run_skill` after `test_check` returns FAIL
- MCP orchestrator calls this via `run_skill` when `merge_worktree` returns `dirty_tree`
- Takes three required positional arguments: `{worktree_path} {plan_path} {base_branch}`
- Optional trailing args (passed by recipe): `{ci_conclusion} {ci_failed_jobs} {diagnosis_path}`
- Remediates test failures only — the orchestrator is responsible for calling `merge_worktree` after verify passes.
- Emits a typed `verdict` output token so the recipe can route correctly (never silently re-pushes).

## Critical Constraints

**NEVER:**
- Merge if ANY test fails
- Merge via `merge_worktree` or any other mechanism
- Call `merge_worktree` MCP tool
- Make changes unrelated to fixing test failures
- Exceed 3 fix-and-retest iterations
- Delete the worktree if tests still fail after max attempts
- Modify the plan file
- Create files outside `{{AUTOSKILLIT_TEMP}}/resolve-failures/` directory

**ALWAYS:**
- Read the plan first to understand implementation intent
- Commit each fix iteration separately with descriptive messages
- Report iteration count and what was fixed
- Leave worktree intact on failure for manual inspection

## Workflow

Read the configured test command from `.autoskillit/config.yaml` (key: `test_check.command`). Use this command wherever `{test_command}` appears in these instructions. If no config exists, use the `test_check` MCP tool (which resolves the command from the project's config automatically).

### Step 0: Validate Arguments
1. Parse positional args using **path detection**: scan all tokens after the skill name.
   - First path-like token (starts with `/`, `./`, or `.autoskillit/`) → `worktree_path`
   - Second path-like token → `plan_path`
   - First non-path token → `base_branch`
   - Additional tokens may include `ci_conclusion`, `ci_failed_jobs`, and `diagnosis_path`
     (optional; use `-` or absence to indicate not provided)
   - The last path-like token after `plan_path` that ends with `.md` → `diagnosis_path`
   - If fewer than two path-like tokens are found, abort:
     `/autoskillit:resolve-failures <worktree_path> <plan_path> <base_branch> [diagnosis_path]`
2. Verify worktree exists and is a valid git worktree
3. Verify plan file exists and is readable
4. If `diagnosis_path` is provided and exists, note it for Step 2a below

   **Path-existence guard:** Before issuing a `Read` call on a path that is not guaranteed to
   exist (e.g., plan file arguments, `{{AUTOSKILLIT_TEMP}}/investigate/` reports, external file references), use
   `Glob` or `ls` to confirm the path exists first. This prevents ENOENT errors that cascade into
   sibling parallel-call cancellations.
4. Check for development environment in worktree, recreate if missing. Use the project's configured `worktree_setup.command`, or: `cd "${worktree_path}" && task install-worktree`

### Step 0.3 — Code-Index Initialization (required before any code-index tool call)

Call `set_project_path` with the repo root where this skill was invoked (not a worktree path):

```
mcp__code-index__set_project_path(path="{PROJECT_ROOT}")
```

Code-index tools require **project-relative paths**. Always use paths like:

    src/<your_package>/some_module.py

NOT absolute paths like:

    /absolute/path/to/src/<your_package>/some_module.py

> **Note:** Code-index tools (`find_files`, `search_code_advanced`, `get_file_summary`,
> `get_symbol_body`) are only available when the `code-index` MCP server is configured.
> If `set_project_path` returns an error, fall back to native `Glob` and `Grep` tools
> for the same searches — they provide equivalent results without the code-index server.

Agents launched via `run_skill` inherit no code-index state from the parent session — this
call is mandatory at the start of every headless session that uses code-index tools.

### Step 0.5: Commit Uncommitted Files
1. Run `git -C {worktree_path} status --porcelain`
2. If output is non-empty (dirty tree):
   - Run `git -C {worktree_path} add -A`
   - Run `git -C {worktree_path} commit -m "chore: commit auto-generated files"`
   - Log: "Committed {N} uncommitted file(s) before test run"
3. If output is empty: continue (worktree is clean)

### Step 0.7: Switch Code-Index to Worktree

Call `set_project_path` with the worktree path so all subsequent code-index
queries (`find_files`, `search_code_advanced`, `get_file_summary`, `get_symbol_body`)
return worktree-relative paths instead of source-repo paths:

```
mcp__code-index__set_project_path(path="{worktree_path}")
```

This prevents the model from being exposed to source-repo absolute paths during
investigation and fixing. Note: code-index tools are read-only; this switch does not
affect git operations, which always use `git -C {worktree_path}` explicitly.

### Step 1: Understand Context
1. Read the plan file to understand what was implemented and why
2. Run `git log --oneline $(git merge-base HEAD origin/{base_branch})..HEAD`
3. Run `git diff --stat $(git merge-base HEAD origin/{base_branch})..HEAD`

### Step 2a: Read CI Context

If `diagnosis_path` was provided and the file exists:
1. Open `diagnosis_path` and read its content
2. Find the "Structured Output" section or scan for the line matching `failure_subtype = {value}`
3. Extract the `failure_subtype` value (e.g., `flaky`, `deterministic`, `timing_race`, etc.)
4. Store as `{failure_subtype}` for use in Step 2d

If `diagnosis_path` is absent or the file does not exist:
- Set `{failure_subtype} = unknown`

### Step 2b: Reproduce CI Pre-Test Steps

Check if the project has artifact generation steps that CI runs before tests:
1. Read `.github/workflows/tests.yml` (or the CI workflow file) if it exists
2. Identify pre-test steps (e.g., `generate_hooks_json`, `recipes render`, code generation)
3. Run equivalent generation commands locally in the worktree before executing the test suite

This ensures local test results match CI behavior. Skip if no pre-test generation steps exist.

### Step 2c: Reproduce Failure Locally

1. Run `cd {worktree_path} && {test_command}`
2. Record exit code and output as `{local_result}`: PASS or FAIL

### Step 2d: Verdict Decision Tree

Using `{local_result}` from Step 2c and `{failure_subtype}` from Step 2a, determine `{verdict}`:

| Local result | `failure_subtype` | Verdict |
|---|---|---|
| FAIL → (fix applied in Step 3) → PASS | any | `real_fix` |
| FAIL → (no fix possible after 3 iterations) | any | proceed to Step 5 |
| PASS | `flaky` or `timing_race` | `flake_suspected` |
| PASS | `deterministic` | `ci_only_failure` |
| PASS | `fixture` or `import` | `ci_only_failure` |
| PASS | `env` or `unknown` | `ci_only_failure` (conservative) |

**Note on `already_green`:** This verdict is reserved for the `pre_resolve_rebase`
re-entry path — when a sibling pipeline's fix has already landed on integration and
the worktree was rebased before this skill ran. In that case, the orchestrator's
`pre_resolve_rebase` step has already pulled the fix; the re-run of diagnose-ci +
resolve-failures will now emit `real_fix` or another verdict. `already_green` is
not emitted by this skill's primary workflow.

If local tests PASS (no fix needed): skip Step 3 and go directly to Step 4 with
the verdict determined above (`flake_suspected` or `ci_only_failure`).

If local tests FAIL: enter Step 3.

### Step 3: Fix Loop (max 3 iterations)
1. Analyze test failures against the plan to understand root cause
2. Apply targeted fixes
3. If the project has pre-commit hooks, run `pre-commit run --all-files` and
   stage any auto-fixed files before committing. Commit each fix: `fix: {what was wrong and why}`
4. Write a fix log entry to `{{AUTOSKILLIT_TEMP}}/resolve-failures/` (relative to
   the current working directory) to satisfy the write_behavior contract
   (generates an Edit/Write call that proves work was done):
   - Path: `{{AUTOSKILLIT_TEMP}}/resolve-failures/fix_log_{iteration}_{ts}.md`
   - Content: iteration number, files changed, commit SHA, brief description
5. Re-run: `cd {worktree_path} && {test_command}`
6. Green → Step 4 (with `verdict = real_fix`); Red and < 3 iterations → repeat; Red and >= 3 → Step 5

### Step 4: Report

Tests are green. Report and exit — do NOT merge.

Output to terminal:
- Summary of what was fixed (or reason no fix was applied)
- Verdict: `{verdict}`
- Worktree path (left intact for orchestrator's gate)

Then emit the structured output tokens on their own lines so the pipeline's
`on_result:` verdict routing and `write_behavior: conditional` contract can evaluate them:

> **IMPORTANT:** Emit the tokens as **literal plain text with no markdown
> formatting**. The gate performs a regex match — decorators cause match failure.

```
verdict = {verdict}
fixes_applied = {N}
```

Where:
- `{verdict}` is one of: `real_fix`, `flake_suspected`, `ci_only_failure`
- `{N}` is the number of fix iterations performed (0 for non-real_fix verdicts, ≥1 for `real_fix`)

Return control to the orchestrator. The recipe's `on_result:` routing dispatches
on `verdict`:
- `real_fix` → `re_push` (fix landed, push to remote)
- `flake_suspected` → `release_issue_failure` (human escalation)
- `ci_only_failure` → `release_issue_failure` (human escalation)

### Step 5: Report Failure
- Total fix iterations attempted
- Remaining test failures (summary)
- Worktree path (left intact for manual inspection)
- Suggestion: review failures manually or run `/autoskillit:rectify`
