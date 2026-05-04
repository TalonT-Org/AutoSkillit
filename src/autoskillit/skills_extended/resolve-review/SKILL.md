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
- Run subagents in the background (`run_in_background: true` is prohibited)

**ALWAYS:**
- Find the PR by feature branch at invocation time (not a hardcoded number)
- Fetch both inline comments (`pulls/{number}/comments`) and top-level review
  bodies (`pulls/{number}/reviews`) via the GitHub API
- Commit each distinct fix separately with a message describing what was addressed
- Run `{test_command}` (from config, default: `task test-check`) after applying all fixes to catch regressions
- Gracefully degrade (exit 0, report skip) if `gh` is unavailable or no PR is found
- Report a structured summary: findings fetched, fixes applied, fixes skipped (with reasons)
- **Read before editing**: Before issuing an `Edit` call on any file, ensure you have issued a `Read` on that file earlier in this session. Claude Code rejects `Edit` on unread files — the retry wastes a full API turn at current context size. If you are uncertain whether a file was read, issue a targeted `Read` (offset + limit to the region you plan to edit) rather than risk an error.
- **CWD awareness**: Before running `python3` or other interpreters, verify your current working directory is the worktree root (not the orchestrator's project root). Use absolute paths for imports or `cd` to the worktree first. A wrong-CWD import error wastes a full API turn.

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

Read test configuration from `.autoskillit/config.yaml`: check `test_check.commands` (ordered list, if set) or `test_check.command` (single command, default: `task test-check`).
The `test_check` MCP tool runs all configured commands automatically.

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
  -f query='query($owner:String!,$repo:String!,$number:Int!,$after:String){repository(owner:$owner,name:$repo){pullRequest(number:$number){reviewThreads(first:100,after:$after){pageInfo{hasNextPage endCursor}nodes{id isResolved comments(first:5){nodes{databaseId body}}}}}}}' \
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

**Build `already_replied_ids` (idempotency guard):**

```python
RESOLVED_MARKER_RE = re.compile(r"<!--\s*autoskillit:resolved\b")

already_replied_ids: set[int] = set()
for thread in all_thread_nodes:
    if thread.get("isResolved"):
        continue  # Already resolved — Step 3 will not see these comments anyway
    comments_in_thread = thread.get("comments", {}).get("nodes", [])
    if len(comments_in_thread) < 2:
        continue  # No replies yet
    first_comment_id = comments_in_thread[0].get("databaseId")
    if first_comment_id is None:
        continue
    for reply in comments_in_thread[1:]:
        if RESOLVED_MARKER_RE.search(reply.get("body", "")):
            already_replied_ids.add(first_comment_id)
            log(f"Skipping comment {first_comment_id} — already resolved by prior resolve-review run")
            break
```

`already_replied_ids` is a set of original-comment `databaseId` integers for which a prior
resolve-review invocation already posted a reply. Comments in this set are skipped in Step 3
before classification.

If the GraphQL call failed and `all_thread_nodes` is empty, `already_replied_ids` defaults to
`set()` — no skipping occurs (safe degradation: worst case is a duplicate reply on the next
run, same as the current behavior).

**Load Pre-Built Context (if available):**

After saving the raw review responses, check for the handoff file from review-pr:

```bash
DIFF_CONTEXT_PATH="{{AUTOSKILLIT_TEMP}}/review-pr/diff_context_${PR_NUMBER}.json"
```

If the file exists:
- Parse it as JSON
- Build `diff_context_map: dict[tuple[str, int], str]` where key is `(entry.path, entry.line)`
  and value is `entry.code_region`
- Log: `"Loaded pre-built context for N findings from review-pr handoff (schema_version: {v})"`

If the file is absent or cannot be parsed:
- Set `diff_context_map = {}`
- Log: `"No pre-built context file found — will read files in Step 3.5 (fallback)"`

This lookup is used in Steps 3.5 and 4 to avoid redundant file reads.

### Step 3: Parse and Classify Findings

From **inline comments**, extract per comment:
- `path` — file path relative to repo root
- `line` — the line being commented on
- `body` — the reviewer's message
- `diff_hunk` — surrounding context
- `id` — the comment's REST database ID (integer `id` field in the JSON)
- `thread_node_id` — look up `comment_id_to_thread_id.get(id)` (may be `None` if lookup
  failed or thread was already resolved)

**File-level comment guard:** If `line` is null (file-level comment posted by
review-pr), skip this finding entirely — file-level comments have no code anchor and
cannot be resolved by code changes. Record: `(path, null, reason="file-level comment — no
line anchor")`. See the thread_node_id tracking table in Step 4 for the no-add disposition.

**Idempotency guard — already-replied comments:**
If `comment["id"]` (the REST `id` integer) is in `already_replied_ids`, skip this
comment entirely. Do not classify it, do not apply fixes, do not post a reply.
Record: `(path, line, reason="already replied in prior round — skipped")`.
These skipped comments do not count toward `accept_count`, `reject_count`, or
`discuss_count`, and must not appear in the Step 7 report's "Findings fetched" total
(they were fetched but filtered before classification).

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

Critical and warning findings proceed to intent validation (Step 3.5). Info findings are auto-classified as `DISCUSS` — they do not enter Step 3.5.

### Step 3.5: Intent Validation (Parallel Sub-Agents — BEFORE any code changes)

Before applying any fix, validate every critical and warning finding against the actual
codebase and git history. This analysis phase runs entirely before code changes are made.

**Domain grouping:** Group all critical and warning findings by the top-level path segment of
their `path` field:
- `src/autoskillit/execution/headless.py` → group `execution`
- `tests/skills/test_foo.py` → group `tests`
- `src/autoskillit/server/tools_ci.py` → group `server`

**Inline classification shortcut:** If there are 3 or fewer findings AND they all
fall in a single domain group, classify them inline — use each finding's
`diff_hunk` as the primary code context, run `git log` once per unique path, then
emit a verdict for each finding — without spawning a Task sub-agent. Only read
source files if a comment explicitly references code outside the hunk or the
`diff_hunk` is missing. The classification criteria and output format are
identical to the sub-agent path.

This produces 3–6 groups on a typical PR. Launch one parallel sub-agent per group using
the Task tool (`model: "sonnet"`).

**Context resolution hierarchy** (applied per finding):
1. **`diff_context_map` code_region** — richest context (±50 annotated diff lines); used when review-pr ran in the same pipeline and wrote the handoff file.
2. **`diff_hunk` from the review comment** — the unified-diff snippet surrounding the commented line; always available from the GitHub API. Sufficient for most classification tasks (naming, patterns, style).
3. **Source file read** — last resort (±30 lines); used only when the comment references code outside the hunk or the hunk is truncated/missing.

**Sub-agent prompt template** — each sub-agent receives:
- The list of comments in its domain group (with `path`, `line`, `body`, `diff_hunk`)
- Instructions for reading code context: if a pre-built code_region for this finding's
  `(path, line)` is available in `diff_context_map`, include it directly in the prompt
  under "Pre-built code region (from review-pr, ±50 diff lines):" and instruct the
  sub-agent to use it — do **not** instruct it to read the file for context. If
  `diff_context_map` has no entry for this finding, use the comment's `diff_hunk`
  as the primary code context — include it directly in the prompt under
  "Code context (diff_hunk from review comment):" and instruct the sub-agent to
  classify the finding using this hunk. Only instruct the sub-agent to read the
  source file if: (a) the review comment body explicitly references code outside
  the hunk (e.g., "see the function above", "this conflicts with the import at
  line N", "look at the caller in X.py"), or (b) the `diff_hunk` is truncated
  or missing (empty string). When a file read IS needed, read each unique file
  once, spanning all flagged lines with ±30 lines margin — do not re-read per
  finding.
- Instructions to run `git log --follow -p --max-count=5 -- {path}` once per unique path (not once per finding) to trace original intent
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

**Output from each sub-agent** — a JSON array of objects with fields: `comment_id`, `path`, `line`, `verdict`, `evidence`, `category` (REJECT only), `commit_sha_hint`.

**Building sub-agent prompts with pre-built context:**

When `diff_context_map.get((comment.path, comment.line))` returns a value:
```
Pre-built code region (from review-pr, ±50 diff lines):
{diff_context_map.get((comment.path, comment.line), "")}

Use the above region for context. Do NOT read the file — the region is already provided.
Run `git log --follow -p --max-count=5 -- {path}` for history context as usual.
```

When `diff_context_map` has no entry but `diff_hunk` is present (non-empty):
```
Code context (diff_hunk from review comment):
{comment.diff_hunk}

Use the above hunk for classification context. Only read the source file if:
(a) the comment body references code outside this hunk, or (b) you need
additional context not visible in the hunk. Run `git log` for history as usual.
```

When `diff_context_map` has no entry AND `diff_hunk` is empty or missing:
fall back to reading the file at `±30 lines` from the flagged line.

**Fallback:** If a sub-agent fails or times out, classify all comments in that group as
`DISCUSS` (safe fallback — no code is changed, human reviews). Log the failure including
the error message, domain group name, and affected comment IDs.

**Merge results** into a `classification_map: dict[comment_id, verdict_entry]`.

Each entry must also carry two additional fields populated at merge time (not delegated to sub-agents):
- `severity` — `diff_context_map.get((path, line), {}).get("severity", locally_classified_severity)` where `locally_classified_severity` is the severity computed in Step 3 (`critical`/`warning`/`info` from keyword matching). This ensures a meaningful value even when no review-pr handoff entry exists for this `(path, line)`.
- `dimension` — `diff_context_map.get((path, line), {}).get("dimension", "unknown")` (`arch|tests|bugs|defense|cohesion|slop|deletion_regression|unknown`). `"unknown"` is the correct sentinel when `diff_context_map` has no entry.

For auto-classified INFO findings (those classified as DISCUSS in Step 3 without entering Step 3.5): add them to `classification_map` with `severity="info"` and `dimension=diff_context_map.get((path, line), {}).get("dimension", "unknown")`.

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

1. **Context for understanding:** If `diff_context_map.get((path, line))` returns a value,
   use the pre-built code_region for initial understanding — skip the ±20 line read.
   The pre-built region is already available from the review-pr handoff.
   If `diff_context_map` has no entry, read the referenced file and ±20 lines of
   context as before. In both cases, still read the file when actually applying
   the edit — the pre-built context covers understanding only, not the write.
2. Understand what the reviewer is requesting
3. Apply the fix
4. Stage and commit:
   ```bash
   git add {file}
   # If pre-commit hooks are configured:
   pre-commit run --files {file} && git add {file}
   git commit -m "fix(review): {brief description of reviewer's request}"
   ```

**Classification gate — REJECT/DISCUSS bypass:**
For findings where the classification map shows `verdict = REJECT` or `verdict = DISCUSS`:
- For REJECT: no code changes are applied; record `(file, line, reason="classifier: REJECT — {evidence}")`.
- For DISCUSS: record `(file, line, reason="classifier: DISCUSS — {context}")`.

**`thread_node_id` Tracking:**

| Outcome | Append to `addressed_thread_ids`? |
|---------|-----------------------------------|
| ACCEPT — fix committed | Yes (if `thread_node_id` is not `None`) |
| REJECT — no code change | Yes (if `thread_node_id` is not `None`) |
| DISCUSS — awaiting human decision | No — do not add DISCUSS findings to `addressed_thread_ids` |
| Skipped finding (stale, missing file, unclear) | No |
| File-level comment (`line` is null) | No |

**Skip a finding if:**
- The comment is a file-level comment (`line` is null) — these have no code anchor
- The referenced file does not exist in the current branch
- The finding references a line number that no longer exists (stale comment)
- The fix would require a design decision beyond the reviewer's explicit guidance
- The reviewer's request is contradicted by another reviewer's comment on the same location

Record each skip with: `(file, line, reason)`.

**Skip a finding flow:** When skipping a finding (stale comment, missing file, unclear guidance, contradiction):
- Record `(file, line, reason)` as before.

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

This step is best-effort — failure to resolve any thread never affects the exit code.
The same applies to Step 6.5 (inline replies).

### Step 6.5: Post Inline Replies

For every finding (those classified via intent validation in Step 3.5 and info findings
auto-classified as DISCUSS in Step 3), post an inline reply using the GitHub comment reply
API. Each finding receives exactly one reply based on its classification.

```bash
# Build reply body based on classification:
# ACCEPT:
BODY="Agreed — fixed in ${commit_sha}. ${evidence}
<!-- autoskillit:resolved comment_id=${comment_id} verdict=ACCEPT -->"
# REJECT:
BODY="Investigated — this is intentional. ${evidence}
<!-- autoskillit:resolved comment_id=${comment_id} verdict=REJECT -->"
# DISCUSS:
BODY="Valid observation — flagged for design decision. ${evidence}
<!-- REVIEW-FLAG: severity=${severity} dimension=${dimension} -->
<!-- autoskillit:resolved comment_id=${comment_id} verdict=DISCUSS -->"
# INFO (auto-classified DISCUSS):
BODY="Acknowledged — minor suggestion noted.
<!-- REVIEW-FLAG: severity=info dimension=${dimension} -->
<!-- autoskillit:resolved comment_id=${comment_id} verdict=INFO -->"

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

Summary written to: `{{AUTOSKILLIT_TEMP}}/resolve-review/report_{pr_number}_{ts}.md` (relative to the current working directory)
