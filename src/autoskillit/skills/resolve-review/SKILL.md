---
name: resolve-review
description: Fetch PR review comments and apply fixes for each actionable finding. MCP-only — used exclusively by recipe orchestration via run_skill after review_pr reports changes_requested or needs_human verdict.
---

# Resolve Review Skill

Read all review comments (inline + summary) on an open GitHub PR, apply targeted fixes
for actionable findings, commit each fix, and verify tests still pass.

## Arguments

`/autoskillit:resolve-review <feature_branch> <base_branch>`

- **feature_branch** — The PR's head branch (used to find the open PR)
- **base_branch** — The PR's base branch (e.g., "main")

The `cwd` is provided by the recipe step's `cwd:` field — the clone with the feature
branch already checked out.

## When to Use

- Called by the recipe orchestrator via `run_skill` after `review_pr` reports
  `changes_requested` or `needs_human` verdict
- MCP-only: not user-invocable directly

## Critical Constraints

**NEVER:**
- Create files outside `temp/resolve-review/`
- Merge, push, or call `merge_worktree`
- Fix issues beyond the explicit scope of the reviewer's comments
- Exceed 3 fix-and-retest iterations
- Delete or discard the working directory on failure
- Modify tests to suppress failures introduced by reviewer fixes

**ALWAYS:**
- Find the PR by feature branch at invocation time (not a hardcoded number)
- Fetch both inline comments (`pulls/{number}/comments`) and top-level review
  bodies (`pulls/{number}/reviews`) via the GitHub API
- Commit each distinct fix separately with a message describing what was addressed
- Run `task test-check` after applying all fixes to catch regressions
- Gracefully degrade (exit 0, report skip) if `gh` is unavailable or no PR is found
- Report a structured summary: findings fetched, fixes applied, fixes skipped (with reasons)

## Workflow

### Step 0: Validate Arguments

Parse two positional arguments: `feature_branch` and `base_branch`.

If either is missing, abort with:
`"Usage: /autoskillit:resolve-review <feature_branch> <base_branch>"`

### Step 1: Find the Open PR

```bash
gh pr list --head "$feature_branch" --base "$base_branch" \
  --json number,url -q '.[0] | "\(.number) \(.url)"'
```

Get owner/repo:
```bash
gh repo view --json nameWithOwner -q .nameWithOwner
```

If `gh` is unavailable or not authenticated, or no PR is found:
- Log "No PR found or gh unavailable — skipping review resolution"
- Exit 0 (graceful degradation — do not fail the pipeline)

### Step 2: Fetch Review Comments

Fetch inline comments (anchored to specific file lines):
```bash
gh api repos/{owner}/{repo}/pulls/{number}/comments --paginate
```

Fetch top-level review bodies (summary reviews):
```bash
gh api repos/{owner}/{repo}/pulls/{number}/reviews --paginate
```

Fetch review thread node IDs (needed for thread resolution in Step 6.5):
```bash
gh api graphql \
  -f query='query($owner:String!,$repo:String!,$number:Int!){repository(owner:$owner,name:$repo){pullRequest(number:$number){reviewThreads(first:100){nodes{id isResolved comments(first:1){nodes{databaseId}}}}}}}' \
  -F owner="$owner" \
  -F repo="$repo" \
  -F number=$number
```

Save raw responses to:
- `temp/resolve-review/inline_comments_{pr_number}.json`
- `temp/resolve-review/reviews_{pr_number}.json`
- `temp/resolve-review/threads_{pr_number}.json`

Build a lookup map from the threads response:
- `comment_id_to_thread_id: dict[int, str]` — key: comment `databaseId` (integer), value: thread GraphQL `id` (string node ID)
- Skip threads where `isResolved` is already `true` (no need to resolve again)

If the GraphQL call fails (e.g., token lacks `read:discussion` scope), log a warning and
set `comment_id_to_thread_id = {}`. Thread resolution will be silently skipped in Step 6.5.

### Step 3: Parse and Classify Findings

From **inline comments**, extract per comment:
- `path` — file path relative to repo root
- `line` — the line being commented on
- `body` — the reviewer's message
- `diff_hunk` — surrounding context
- `id` — the comment's REST database ID (integer `id` field in the JSON)
- `thread_node_id` — look up `comment_id_to_thread_id.get(id)` (may be `None` if lookup
  failed or thread was already resolved)

From **top-level reviews**, extract:
- `state` — APPROVED, CHANGES_REQUESTED, COMMENTED
- `body` — the review summary text (skip empty bodies and APPROVED state)

**Classify each finding by severity:**
- `critical` — body contains: "must", "critical", "security", "data loss", "wrong",
  "broken", "incorrect", "bug", "error", "never"
- `warning` — body contains: "should", "consider", "recommend", "prefer", "suggest",
  "missing", "lacks"
- `info` — body contains: "nit", "optional", "minor", "style", "cosmetic", "could"

When a finding matches multiple tiers, use the highest severity.

**Filter:** Include `critical` and `warning` only. Skip `info` findings entirely.

### Step 4: Apply Fixes (max 3 iterations)

Initialize `addressed_thread_ids: list[str] = []` before processing findings.

For each actionable finding (process critical findings first, then warnings):

1. Read the referenced file and ±20 lines of context around the comment line
2. Understand what the reviewer is requesting
3. Apply the fix
4. Stage and commit:
   ```bash
   git add {file}
   # If pre-commit hooks are configured:
   pre-commit run --files {file} && git add {file}
   git commit -m "fix(review): {brief description of reviewer's request}"
   ```

**Apply the fix flow:** After committing the fix:
- Append the finding's `thread_node_id` to `addressed_thread_ids` (if not `None`).

**Skip a finding if:**
- The referenced file does not exist in the current branch
- The finding references a line number that no longer exists (stale comment)
- The fix would require a design decision beyond the reviewer's explicit guidance
- The reviewer's request is contradicted by another reviewer's comment on the same location

Record each skip with: `(file, line, reason)`.

**Skip a finding flow:** When skipping a finding (stale comment, missing file, unclear guidance, contradiction):
- Record `(file, line, reason)` as before.
- Do NOT add the finding's `thread_node_id` to `addressed_thread_ids`.

### Step 5: Run Tests

```bash
task test-check
```

- Pass → proceed to Step 6
- Fail (iteration < 3): analyze failures against the fixes applied, revert/adjust the
  problematic commit, re-commit and retry (increment iteration counter)
- Fail (iteration >= 3): report failure, leave working directory intact, exit non-zero

### Step 6.5: Resolve Addressed Review Threads

For each `thread_id` in `addressed_thread_ids`:

```bash
gh api graphql \
  -f query='mutation($threadId:ID!){resolveReviewThread(input:{threadId:$threadId}){thread{isResolved}}}' \
  -f threadId="$thread_id"
```

- **Success** (`isResolved: true` in response): increment `resolved_count`.
- **Failure** (non-zero exit code, parse error, or `isResolved: false`): log a warning
  `"Warning: could not resolve thread {thread_id}: {error}"`. Continue to the next thread.
  Do not modify exit code.

Track:
- `resolved_count: int` — successfully resolved threads
- `resolve_failed_count: int` — threads that could not be resolved (permissions, network)

This step is a best-effort operation. Failure to resolve any thread must never cause the
overall skill to exit non-zero. Thread resolution failure does not affect the exit code of
the overall skill.

### Step 6: Report

Print a structured summary to terminal:

```
resolve-review complete
PR: #{pr_number} ({feature_branch} → {base_branch})
Findings fetched: {total}
  - critical: {n}
  - warning: {n}
  - info: {n} (skipped — below threshold)
Fixes applied: {n}
Fixes skipped: {n}
  - {file}:{line} — {reason}
Threads resolved: {resolved_count}/{len(addressed_thread_ids)}
  - {resolve_failed_count} failed (warnings logged above)
Test iterations: {n}
Status: PASS
```

Save full report to `temp/resolve-review/report_{pr_number}_{timestamp}.md`.

Exit 0.

## Output

No structured output tokens are emitted. The recipe's `resolve_review` step has no
`capture:` block — success/failure drives routing, not captured values.

Summary written to: `temp/resolve-review/report_{pr_number}_{timestamp}.md`
