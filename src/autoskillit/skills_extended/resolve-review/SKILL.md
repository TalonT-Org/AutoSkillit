---
name: resolve-review
categories: [github]
description: Fetch PR review comments, run intent validation (ACCEPT/REJECT/DISCUSS) before applying fixes, and post inline replies. MCP-only — used exclusively by recipe orchestration via run_skill after review_pr reports changes_requested or needs_human verdict.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: resolve-review] Resolving PR review comments...'"
          once: true
---

# Resolve Review Skill

Read all review comments (inline + summary) on an open GitHub PR, apply targeted fixes
for actionable findings, commit each fix, and verify tests still pass.

## Arguments

`/autoskillit:resolve-review <feature_branch> <base_branch>`

- `feature_branch` — The PR's head branch (used to find the open PR)
- `base_branch` — The PR's base branch (e.g., "main")

The `cwd` is provided by the recipe step's `cwd:` field — the clone with the feature
branch already checked out.

## When to Use

- Called by the recipe orchestrator via `run_skill` after `review_pr` reports
  `changes_requested` or `needs_human` verdict
- MCP-only: not user-invocable directly

## Critical Constraints

**NEVER:**
- Create files outside `{{AUTOSKILLIT_TEMP}}/resolve-review/`
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
- Run `{test_command}` (from config, default: `task test-check`) after applying all fixes to catch regressions
- Gracefully degrade (exit 0, report skip) if `gh` is unavailable or no PR is found
- Report a structured summary: findings fetched, fixes applied, fixes skipped (with reasons)

## Context Limit Behavior

When context is exhausted mid-execution, edits may be on disk but not committed.
The recipe routes to `on_context_limit` (typically a re-push step), bypassing the
normal commit protocol.

**Before every test run and before emitting structured output tokens:**
1. Run `git -C {work_dir} status --porcelain`
2. If any files are dirty: `git -C {work_dir} add -A && git -C {work_dir} commit -m "fix: commit pending review changes"`
3. Only then proceed with the test or structured output

This ensures that even if context exhaustion interrupts the fix loop, all applied
review fixes are committed and the downstream push step receives a clean branch.

## Workflow

Read `test_check.command` from `.autoskillit/config.yaml` (default: `task test-check`).
Store the resolved command as `{test_command}` for use in all test-running steps.

### Step 0: Validate Arguments

Parse two positional arguments: `feature_branch` and `base_branch`.

If either is missing, abort with:
`"Usage: /autoskillit:resolve-review <feature_branch> <base_branch>"`

### Step 1: Find the Open PR

```bash
PR_LIST_OUTPUT=$(gh pr list --head "$feature_branch" --base "$base_branch" \
  --json number,url -q '.[0] | "\(.number) \(.url)"')
PR_NUMBER=$(echo "$PR_LIST_OUTPUT" | awk '{print $1}')
PR_URL=$(echo "$PR_LIST_OUTPUT" | awk '{print $2}')
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

Fetch review thread node IDs (needed for thread resolution in Step 6) using
cursor-based pagination to handle PRs with more than 100 threads:

```bash
# Fetch all pages; repeat with after=$endCursor while hasNextPage is true
gh api graphql \
  -f query='query($owner:String!,$repo:String!,$number:Int!,$after:String){repository(owner:$owner,name:$repo){pullRequest(number:$number){reviewThreads(first:100,after:$after){pageInfo{hasNextPage endCursor}nodes{id isResolved comments(first:1){nodes{databaseId}}}}}}}' \
  -F owner="$owner" \
  -F repo="$repo" \
  -F number=$number \
  -F after=""
```

Collect all `nodes` across pages into a single list. Continue fetching while
`pageInfo.hasNextPage` is `true`, passing `pageInfo.endCursor` as `$after`.

Save raw responses to:
- `{{AUTOSKILLIT_TEMP}}/resolve-review/inline_comments_{pr_number}.json`
- `{{AUTOSKILLIT_TEMP}}/resolve-review/reviews_{pr_number}.json`
- `{{AUTOSKILLIT_TEMP}}/resolve-review/threads_{pr_number}.json` (first page; subsequent pages merged in memory)

Build a lookup map from the threads response:
- `comment_id_to_thread_id: dict[int, str]` — key: comment `databaseId` (integer), value: thread GraphQL `id` (string node ID)
- Skip threads where `isResolved` is already `true` (no need to resolve again)

If the GraphQL call fails (e.g., token lacks `read:discussion` scope), log a warning and
set `comment_id_to_thread_id = {}`. Thread resolution will be silently skipped in Step 6.
Flag this in the Step 7 report for human review.

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

All three severity levels proceed to intent validation.

### Step 3.5: Intent Validation (Parallel Sub-Agents — BEFORE any code changes)

Before applying any fix, validate every finding (critical, warning, and info) against the actual
codebase and git history. This analysis phase runs entirely before code changes are made.

**Domain grouping:** Group all findings by the top-level path segment of
their `path` field:
- `src/autoskillit/execution/headless.py` → group `execution`
- `tests/skills/test_foo.py` → group `tests`
- `src/autoskillit/server/tools_ci.py` → group `server`

This produces 3–6 groups on a typical PR. Launch one parallel sub-agent per group using
the Task tool (`model: "sonnet"`).

**Sub-agent prompt template** — each sub-agent receives:
- The list of comments in its domain group (with `path`, `line`, `body`, `diff_hunk`)
- Instructions to read the actual code at each flagged line (±30 lines context)
- Instructions to run `git log --follow -p --max-count=5 -- {path}` to trace original intent via git history
- Instructions to classify each comment as `ACCEPT`, `REJECT`, or `DISCUSS` with:
  - `verdict`: the classification (`ACCEPT` / `REJECT` / `DISCUSS`)
  - `evidence`: specific references (line numbers, function names, API docs, contracts)
  - `category` (for `REJECT` only): one of `api_direction_misunderstanding`,
    `false_positive_intentional_pattern`, `design_intent_misread`, `stale_comment`, `other`
  - `commit_sha_hint`: the most recent commit touching the flagged line (from `git log`)

**Classification criteria:**
- `ACCEPT` — the reviewer identified a real issue; a code fix is warranted
- `REJECT` — the reviewer is factually wrong (misread a guard, misunderstood an API,
  failed to recognize an intentional design pattern); do NOT change the code
- `DISCUSS` — the comment raises a valid design question that requires a human decision;
  flag for human review, do NOT change the code automatically

**Output from each sub-agent** — a JSON array:
```json
[
  {
    "comment_id": 123,
    "path": "src/autoskillit/execution/headless.py",
    "line": 42,
    "verdict": "REJECT",
    "evidence": "The method never raises — this is contractual (see docstring line 12 and callers in tools_execution.py:88)",
    "category": "false_positive_intentional_pattern",
    "commit_sha_hint": "abc1234"
  }
]
```

**Fallback:** If a sub-agent fails or times out, classify all comments in that group as
`DISCUSS` (safe fallback — no code is changed, human reviews). Log the failure including
the error message, domain group name, and affected comment IDs.

**Merge results** into a `classification_map: dict[comment_id, verdict_entry]`.

**Write analysis report** to `{{AUTOSKILLIT_TEMP}}/resolve-review/analysis_{pr_number}_{ts}.md` before
any code changes are made. The report must include a summary banner:
```
Analysis complete (BEFORE any code changes)
ACCEPT: N | REJECT: N | DISCUSS: N
```

Track: `accept_count`, `reject_count`, `discuss_count`.

---

### Step 4: Apply Fixes (max 3 iterations)

Initialize `addressed_thread_ids: list[str] = []` before processing findings.

For each finding where the classification map shows `verdict = ACCEPT`
(process critical findings first, then warnings):

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

**Classification gate — REJECT/DISCUSS bypass:**
For findings where the classification map shows `verdict = REJECT` or `verdict = DISCUSS`:
- For REJECT: no code changes are applied; record `(file, line, reason="classifier: REJECT — {evidence}")`.
  Append the finding's `thread_node_id` to `addressed_thread_ids` (if not `None`) — a resolved
  thread with an "Investigated — this is intentional" reply is the correct end state.
- For DISCUSS: record `(file, line, reason="classifier: DISCUSS — {context}")`.
  Do NOT add DISCUSS findings' `thread_node_id` to `addressed_thread_ids` — these threads
  remain open for human decision.

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
{test_command}
```

- Pass → proceed to Step 6 (Resolve Addressed Review Threads)
- Fail (iteration < 3): analyze failures against the fixes applied, revert/adjust the
  problematic commit, re-commit and retry (increment iteration counter)
- Fail (iteration >= 3): report failure, leave working directory intact, exit non-zero

### Step 6: Resolve Addressed Review Threads

Batch all thread resolutions into a single GraphQL request using aliased mutations.
This reduces N requests (5 pts each = 5N pts) to 1 request (5 pts total).
If `addressed_thread_ids` has more than 50 threads, chunk into batches of 50.

```bash
# Build aliased mutation query for all addressed threads
MUTATION_QUERY="mutation {"
for i in $(seq 0 $((${#ADDRESSED_THREAD_IDS[@]} - 1))); do
    tid="${ADDRESSED_THREAD_IDS[$i]}"
    MUTATION_QUERY="${MUTATION_QUERY} resolve${i}: resolveReviewThread(input: {threadId: \"${tid}\"}) { thread { isResolved } }"
done
MUTATION_QUERY="${MUTATION_QUERY} }"

gh api graphql -f query="${MUTATION_QUERY}"
```

Parse the response: for each `resolve${i}` alias key, check `thread.isResolved`.
- **Success** (`isResolved: true`): increment `resolved_count`.
- **Failure** (non-zero exit code, parse error, or `isResolved: false` for any alias): log a warning
  `"Warning: could not resolve thread ${tid}: {error}"`. Continue to the next thread.
  Do not modify exit code.

Track:
- `resolved_count: int` — successfully resolved threads
- `resolve_failed_count: int` — threads that could not be resolved (permissions, network)

This step is a best-effort operation. Failure to resolve any thread must never cause the
overall skill to exit non-zero. Thread resolution failure does not affect the exit code of
the overall skill.

### Step 6.5: Post Inline Replies

For every comment that was analyzed (i.e., every finding that reached intent validation in
Step 3.5), post an inline reply using the GitHub comment reply API. Each analyzed
comment receives exactly one reply based on its classification.

```bash
# Build reply body based on classification:
# ACCEPT:
BODY="Agreed — fixed in ${commit_sha}. ${evidence}"
# REJECT:
BODY="Investigated — this is intentional. ${evidence}"
# DISCUSS:
BODY="Valid observation — flagged for design decision. ${evidence}"

gh api repos/{owner}/{repo}/pulls/{pr_number}/comments/{comment_id}/replies \
  --method POST \
  --field body="${BODY}"
sleep 1  # Rate-limit discipline: 1s between mutating calls
```

For ACCEPT replies, use the `commit_sha` from the most recent commit made in Step 4
(i.e., `git log --format="%H" -1` after committing the fix). If the comment was
classified as ACCEPT but skipped in Step 4 (stale comment, etc.), omit the commit sha
reference.

For REJECT replies, include specific evidence (line numbers, design contracts, API
references) from the sub-agent's `evidence` field so the reply is self-contained and
suitable for future automated mining.

Track:
- `reply_posted_count: int` — successfully posted replies
- `reply_failed_count: int` — replies that failed (log warning, continue)

This step is best-effort: failure to post any reply must not affect the exit code.

### Step 6.6: Persist Reject Patterns

After Step 6.5, save all REJECT-classified comments to a JSON file for future analysis:

```bash
ts=$(date +%Y%m%d-%H%M%S)
python3 -c "
import json, pathlib
reject_entries = [
    {
        'comment_id': c['comment_id'],
        'path': c['path'],
        'line': c['line'],
        'body': c['body'],
        'evidence': c['evidence'],
        'category': c['category'],
        'pr_number': ${PR_NUMBER},
        'feature_branch': '${feature_branch}',
    }
    for c in classification_map.values()
    if c['verdict'] == 'REJECT'
]
pathlib.Path('{{AUTOSKILLIT_TEMP}}/resolve-review/reject_patterns_${PR_NUMBER}_${ts}.json').write_text(
    json.dumps(reject_entries, indent=2)
)
print(f'Saved {len(reject_entries)} reject patterns')
"
```

### Step 7: Report

Print a structured summary to terminal:

```
resolve-review complete
PR: #{pr_number} ({feature_branch} → {base_branch})
Findings fetched: {total}
  - critical: {n}
  - warning: {n}
  - info: {n}
Intent validation (before code changes):
  - ACCEPT: {accept_count}
  - REJECT: {reject_count}
  - DISCUSS: {discuss_count}
Fixes applied: {accept_count - skipped_in_fix_phase}
Fixes skipped: {n}
  - {file}:{line} — {reason}
Threads resolved: {resolved_count}/{len(addressed_thread_ids)}
  - {resolve_failed_count} failed (warnings logged above)
Inline replies: {reply_posted_count} posted / {reply_failed_count} failed
Reject patterns saved: {{AUTOSKILLIT_TEMP}}/resolve-review/reject_patterns_{pr_number}_{ts}.json
Test iterations: {n}
Status: PASS
```

Save full report to:
- Analysis report: `{{AUTOSKILLIT_TEMP}}/resolve-review/analysis_{pr_number}_{ts}.md` (written before code changes)
- Final report: `{{AUTOSKILLIT_TEMP}}/resolve-review/report_{pr_number}_{ts}.md`

Then determine and emit the structured output tokens (required for the
`write_behavior: conditional` contract gate and `on_result:` routing):

**Verdict Decision:**
- If `{accept_count - skipped_in_fix_phase} >= 1` (fixes were applied): `verdict = real_fix`
- If all ACCEPT findings were skipped (no code changes): `verdict = already_green`

> **IMPORTANT:** Emit the tokens as **literal plain text with no markdown
> formatting**. Do not wrap in bold or italic.

```
verdict = {verdict}
fixes_applied = {accept_count - skipped_in_fix_phase}
```

Where:
- `{verdict}` is `real_fix` if fixes were applied, `already_green` otherwise
- `{accept_count - skipped_in_fix_phase}` is the number of ACCEPT findings
  where code changes were actually committed

The Step 1 graceful degradation exit must NOT emit these tokens — no tokens
when skipping due to no PR found.

Exit 0.

## Output

When a PR is processed, the following structured output tokens are emitted:

```
verdict = real_fix|already_green
fixes_applied = {N}
```

Where `{N}` is the count of ACCEPT findings where code changes were committed.
`verdict = real_fix` means fixes were applied; `verdict = already_green` means
all review findings were already addressed and no code changes were needed.

When no PR is found (graceful degradation), no structured tokens are emitted.

Summary written to: `{{AUTOSKILLIT_TEMP}}/resolve-review/report_{pr_number}_{ts}.md` (relative to the current working directory)
