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
- Report `fixes_applied=0` when CI has identified a specific failing test

**ALWAYS:**
- Read the plan first to understand implementation intent
- Commit each fix iteration separately with descriptive messages
- Report iteration count and what was fixed
- Leave worktree intact on failure for manual inspection
- Treat CI as the source of truth: "passes locally" is not a resolution

**Flaky tests must always be resolved.** A test that failed previously and now passes
is flaky by definition. Investigate timing dependencies, race conditions, insufficient
timeouts, resource contention under parallel execution (pytest-xdist), and
non-deterministic setup/teardown. Apply a stabilizing fix. Never classify a flaky test
as unfixable. Never emit `ci_only_failure` for a test that is merely non-deterministic.

## Context Limit Behavior

When context is exhausted mid-execution, edits may be on disk but not committed.
The recipe routes to `on_context_limit` (typically `test`), bypassing the normal
commit protocol in Step 3.

**Before every test run and before emitting structured output tokens:**
1. Run `git -C {worktree_path} status --porcelain`
2. If any files are dirty: `git -C {worktree_path} add -A && git -C {worktree_path} commit -m "fix: commit pending changes before context limit"`
3. Only then proceed with the test or structured output

This ensures that even if context exhaustion interrupts the fix loop, all applied
edits are committed and the downstream merge gate receives a clean worktree.

## Workflow

Read the configured test command from `.autoskillit/config.yaml` (key: `test_check.command`). Use this command wherever `{test_command}` appears in these instructions. If no config exists, use the `test_check` MCP tool (which resolves the command from the project's config automatically).

### Step 0: Validate Arguments
1. Parse positional args using **path detection**: scan all tokens after the
   skill name.

   ```
   Positional args (in order):
     1. worktree_path    — path-like token (starts with /, ./, or .autoskillit/)
     2. plan_path        — path-like token
     3. base_branch      — non-path token
     4. ci_conclusion    — optional: "failure", "success", or absent/"-"
     5. ci_failed_jobs   — optional: JSON array of job names or absent/"-"
     6. diagnosis_path   — optional: path-like token to the diagnose-ci report or absent/"-"
   ```

   Scanning rules: use path-detection (find path-like tokens for positions 1, 2,
   and 6); pick up remaining non-path non-`"-"` tokens for `base_branch`,
   `ci_conclusion`, and `ci_failed_jobs`. The last path-like token after
   `plan_path` that ends with `.md` → `diagnosis_path`. Ignore any non-path
   tokens that appear before the path arguments. If fewer than two path-like
   tokens are found, abort with a clear error and the correct format:
   `/autoskillit:resolve-failures <worktree_path> <plan_path> <base_branch>`
2. Verify worktree exists and is a valid git worktree
3. Verify plan file exists and is readable
4. If `diagnosis_path` is provided and exists, note it for Step 2a below

   **Path-existence guard:** Before issuing a `Read` call on a path that is not guaranteed to
   exist (e.g., plan file arguments, `{{AUTOSKILLIT_TEMP}}/investigate/` reports, external file references), use
   `Glob` or `ls` to confirm the path exists first. This prevents ENOENT errors that cascade into
   sibling parallel-call cancellations.
4. Check for development environment in worktree, recreate if missing. Use the project's configured `worktree_setup.command`, or: `cd "${worktree_path}" && task install-worktree`

### Step 0.5: Commit Uncommitted Files
1. Run `git -C {worktree_path} status --porcelain`
2. If output is non-empty (dirty tree):
   - Run `git -C {worktree_path} add -A`
   - Run `git -C {worktree_path} commit -m "chore: commit auto-generated files"`
   - Log: "Committed {N} uncommitted file(s) before test run"
3. If output is empty: continue (worktree is clean)

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

### Step 2: Run Tests
1. Run tests using the `test_check` MCP tool — not via Bash or `run_cmd`:
   ```
   test_check(worktree_path="{worktree_path}")
   ```
   When test suites take 5–7 minutes, the Bash tool auto-backgrounds the command
   and the LLM enters a polling cascade of 20+ API calls to detect completion.
   `test_check` blocks synchronously and returns `passed: true/false` in a single call.
2. Record the result as `{local_result}`: PASS (`passed: true`) or FAIL (`passed: false`)

### Step 2d: Verdict Decision Tree

Using `{local_result}` from Step 2 and `{failure_subtype}` from Step 2a, determine `{verdict}`:

| Local result | `failure_subtype` | Verdict |
|---|---|---|
| FAIL → (fix applied in Step 3) → PASS | any | `real_fix` |
| FAIL → (no fix possible after 3 iterations) | any | proceed to Step 5 |
| PASS | `flaky` or `timing_race` | `flake_suspected` |
| PASS | `deterministic` | `ci_only_failure` |
| PASS | `fixture` or `import` | `flake_suspected` |
| PASS | `env` or `unknown` | `flake_suspected` |

**Note on `already_green`:** This verdict is reserved for the `pre_resolve_rebase`
re-entry path — when a sibling pipeline's fix has already landed on integration and
the worktree was rebased before this skill ran. In that case, the orchestrator's
`pre_resolve_rebase` step has already pulled the fix; the re-run of diagnose-ci +
resolve-failures will now emit `real_fix` or another verdict. `already_green` is
not emitted by this skill's primary workflow.

If local tests PASS (no fix needed): go to Step 2.5 (Validate CI Resolution) before
proceeding to Step 4 — the CI-truth gate may redirect to Step 3 for flakiness
investigation even when local tests pass.

If local tests FAIL: enter Step 3.

### Step 2.5: Validate CI Resolution

Tests passed locally. Before reporting success, check whether the skill was invoked
in response to a CI failure.

**CI is the source of truth.** A local pass does not resolve a CI failure — it means
the failure could not be reproduced locally, which is a flaky-test signal.

1. If `diagnosis_path` is absent (or "-"), or `ci_conclusion` is absent (or is not
   "failure"): proceed to Step 4 (no active CI failure context to enforce).

2. If `diagnosis_path` is present AND `ci_conclusion == "failure"`:
   a. Read the diagnosis file at `diagnosis_path`
   b. Extract the failing test name(s) from the "## Log Excerpt" or the failure_type
      classification in the diagnosis
   c. If `failure_type == "test"` (one or more named test failures identified):
      - Do **NOT** proceed to Step 4
      - Log: "CI failure on [test name] — local pass is not a resolution (flaky test
        signal). Entering fix loop to investigate and stabilize."
      - Proceed to **Step 3 (Fix Loop)** to investigate the non-determinism, timing
        dependencies, or race conditions that caused the test to pass locally but fail
        in CI. Apply a stabilizing fix (e.g., increase timeouts, remove timing
        dependencies, add retry guards, fix resource cleanup).
   d. If `failure_type` is not "test" (e.g., "lint", "build") and tests pass locally:
      - Proceed to Step 4 — local pass resolves non-test CI failures (lint/build
        failures are deterministic; they don't pass locally while failing remotely).

### Step 3: Fix Loop (max 3 iterations)
1. Analyze test failures against the plan to understand root cause
2. Apply targeted fixes
3. Commit ALL modified files (not just intentionally changed ones):
   a. If the project has pre-commit hooks, run `pre-commit run --all-files` first
   b. Run `git -C {worktree_path} status --porcelain` to capture the full set of modified files, including any auto-fixed by hooks
   c. Stage and commit: `git -C {worktree_path} add -A && git -C {worktree_path} commit -m "fix: {what was wrong and why}"`
   d. Run `git -C {worktree_path} status --porcelain` again to verify the tree is clean; if any files remain dirty, stage and commit them too
4. Write a fix log entry to `{{AUTOSKILLIT_TEMP}}/resolve-failures/` (relative to
   the current working directory) to satisfy the write_behavior contract
   (generates an Edit/Write call that proves work was done):
   - Path: `{{AUTOSKILLIT_TEMP}}/resolve-failures/fix_log_{iteration}_{ts}.md`
   - Content: iteration number, files changed, commit SHA, brief description
5. Re-run tests using the `test_check` MCP tool:
   ```
   test_check(worktree_path="{worktree_path}")
   ```
   Do NOT re-run via Bash — see Step 2 rationale.
   After receiving the result, extract and retain ONLY:
   - Total pass/fail counts (e.g., "12 failed, 240 passed")
   - The names of all failing tests
   - The specific error message for each failure (first 10–15 lines)
   Discard the full pytest stdout — do not retain progress dots, install-worktree
   output, or timing lines. These accumulate across iterations and inflate context.
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
- `flake_suspected` → `re_push` (retry via CI, bounded by retries: 2 / on_exhausted: release_issue_failure)
- `ci_only_failure` → `release_issue_failure` (human escalation)

### Step 5: Report Failure
- Total fix iterations attempted
- Remaining test failures (summary)
- Worktree path (left intact for manual inspection)
- Suggestion: review failures manually or run `/autoskillit:rectify`
