---
name: resolve-claims-review
categories: [research]
description: >
  Fetch claim findings from audit-claims, run citation-aware intent validation
  (ACCEPT/REJECT/DISCUSS), apply targeted citation fixes, escalate findings
  requiring experiment reruns, and post inline replies.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: resolve-claims-review] Resolving claim review findings...'"
          once: true
---

# Resolve Claims Review Skill

Apply `changes_requested` claim findings from a research PR to the research
worktree. Reads open review threads from `audit-claims`, runs citation-aware
intent validation, applies targeted citation fixes by fix-strategy taxonomy,
escalates unrerunnable findings, resolves addressed threads, and posts inline
replies so a follow-up push closes the review cycle.

## Arguments

`/autoskillit:resolve-claims-review {worktree_path} {base_branch}`

- **worktree_path** — Absolute path to the research worktree
- **base_branch** — Target branch for the PR

## When to Use

Called by the research recipe when `audit_claims` routes `changes_requested` via
`route_claims_resolve`. Bounded by `retries: 2` — on exhaustion routes to
`merge_escalations`.

## Critical Constraints

**NEVER:**
- Merge or push the branch — the recipe's `re_push_research` step handles push
- Dismiss review threads without addressing the underlying comment
- Create files outside `{{AUTOSKILLIT_TEMP}}/resolve-claims-review/`
- Exceed 3 fix-and-retry iterations
- Delete or discard the working directory on failure
- Modify tests to suppress failures introduced by reviewer fixes
- Use file-path-segment grouping — claim comments are grouped by **dimension**, not by file path

**ALWAYS:**
- Find the PR by feature branch at invocation time (not a hardcoded number)
- Commit all fixes before returning control to the orchestrator
- Run intent validation BEFORE making any code changes
- Gracefully degrade (exit 0, report skip) if `gh` is unavailable or no PR is found
- Report a structured summary including escalation count

## Configuration

The skill reads the `claims_review` namespace from `.autoskillit/config.yaml`:

```yaml
claims_review:
  validation_command: null      # command to validate research artifacts after fixes; null = skip validation
  validation_timeout: 120       # seconds before treating validation as a failure
```

This namespace is not in `defaults.yaml` (it has no global default value); if absent, the
skill uses the in-code defaults shown above.

## Workflow

Read `claims_review.validation_command` (default: `null`) and
`claims_review.validation_timeout` (default: `120`) from `.autoskillit/config.yaml`.

### Step 0: Validate Arguments

Parse two positional arguments: `worktree_path` and `base_branch`.

Derive `feature_branch` via:
```bash
feature_branch=$(git -C "$worktree_path" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
if [ -z "$feature_branch" ]; then
  echo "Error: could not determine feature_branch from '$worktree_path'" >&2
  exit 1
fi
```

Read config:
```python
import yaml, pathlib
cfg = yaml.safe_load(pathlib.Path(".autoskillit/config.yaml").read_text()) if pathlib.Path(".autoskillit/config.yaml").exists() else {}
cr_cfg = cfg.get("claims_review", {})
validation_command = cr_cfg.get("validation_command", None)
validation_timeout = cr_cfg.get("validation_timeout", 120)
```

If either positional arg is missing, abort with:
`"Usage: /autoskillit:resolve-claims-review <worktree_path> <base_branch>"`

### Step 0.5 — Code-Index Initialization (required before any code-index tool call)

Call `set_project_path` with the repo root where this skill was invoked (not a worktree path):

```
mcp__code-index__set_project_path(path="{PROJECT_ROOT}")
```

Code-index tools require **project-relative paths**. Always use paths like:

    src/mypackage/core/module.py

NOT absolute paths like:

    /path/to/project/src/mypackage/core/module.py

Agents launched via `run_skill` inherit no code-index state from the parent session — this
call is mandatory at the start of every headless session that uses code-index tools.

### Step 1: Find the Open PR

```bash
PR_LIST_OUTPUT=$(gh pr list --head "$feature_branch" --base "$base_branch" \
  --json number,url -q '.[0] | "\(.number) \(.url)"' 2>/dev/null || echo "")
PR_NUMBER=$(echo "$PR_LIST_OUTPUT" | awk '{print $1}')
PR_URL=$(echo "$PR_LIST_OUTPUT" | awk '{print $2}')

# Graceful degradation: .[0] returns null when no PR matches; PR_NUMBER will be empty
if [ -z "$PR_NUMBER" ] || [ "$PR_NUMBER" = "null" ]; then
  echo "No PR found or gh unavailable — skipping claims review resolution"
  exit 0
fi
```

Get owner/repo:
```bash
gh repo view --json nameWithOwner -q .nameWithOwner
```

If `gh` is unavailable or not authenticated, or no PR is found:
- Log "No PR found or gh unavailable — skipping claims review resolution"
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

Fetch review thread node IDs using cursor-based pagination to handle PRs with more than
100 threads:

```bash
gh api graphql \
  -f query='query($owner:String!,$repo:String!,$number:Int!,$after:String){repository(owner:$owner,name:$repo){pullRequest(number:$number){reviewThreads(first:100,after:$after){pageInfo{hasNextPage endCursor}nodes{id isResolved comments(first:1){nodes{databaseId}}}}}}}' \
  -F owner="$owner" \
  -F repo="$repo" \
  -F number=$number \
  -F after=""
```

Build `comment_id_to_thread_id: dict[int, str]` map. Skip threads where `isResolved`
is already `true`.

If the GraphQL call fails, log a warning and set `comment_id_to_thread_id = {}`.
Thread resolution will be silently skipped in Step 6.

Save to:
- `{{AUTOSKILLIT_TEMP}}/resolve-claims-review/inline_comments_{pr}.json`
- `{{AUTOSKILLIT_TEMP}}/resolve-claims-review/reviews_{pr}.json`
- `{{AUTOSKILLIT_TEMP}}/resolve-claims-review/threads_{pr}.json`

### Step 3: Parse, Classify, and Dimension-Group

From **inline comments**, extract per comment:
- `path` — file path relative to repo root
- `line` — the line being commented on
- `body` — the reviewer's message
- `diff_hunk` — surrounding context
- `id` — the comment's REST database ID
- `thread_node_id` — look up `comment_id_to_thread_id.get(id)`

**Classify each finding by severity** (same as resolve-review):
- `critical` — body contains: "must", "critical", "security", "data loss", "wrong",
  "broken", "incorrect", "bug", "error", "never"
- `warning` — body contains: "should", "consider", "recommend", "prefer", "suggest",
  "missing", "lacks"
- `info` — body contains: "nit", "optional", "minor", "style", "cosmetic", "could"

Include `critical` and `warning` only. Skip `info` findings.

**Dimension extraction** — comments posted by `audit-claims` have format
`[severity] dimension: message`. Extract the dimension label using:

```python
import re
DIMENSION_PATTERN = re.compile(r'^\[(?:critical|warning|info)\]\s+(\S+):\s+')
```

Apply `DIMENSION_PATTERN` to each comment body to extract the dimension label.

**Dimension group mapping:**

| Comment dimension | Group key |
|---|---|
| `external` | `citations` |
| `methodological` | `methodology` |
| `comparative` | `comparisons` |
| (unparseable / no match) | `unknown` |

Note: `experimental` findings never appear — they are self-evidencing and generate no
findings in `audit-claims`.

Save `dimension_groups_{pr}.json` with findings keyed by group.

### Step 3.5: Intent Validation (Parallel Sub-Agents — BEFORE any code changes)

Before applying any fix, validate every critical and warning finding against the actual
codebase and git history. This analysis phase runs entirely before code changes are made.

**Dimension grouping:** Group findings by their extracted dimension group key
(`citations`, `methodology`, `comparisons`, `unknown`).
This is dimension-based grouping, NOT file-path grouping.

Launch one parallel subagent (Task tool, `model: "sonnet"`) per non-empty dimension
group. Each subagent receives:
- Its list of findings (path, line, body, diff_hunk, dimension)
- Instructions to read the actual content at each flagged line (±30 lines context)
- Instructions to classify each as `ACCEPT`, `REJECT`, or `DISCUSS` with:
  - `verdict` — the classification
  - `evidence` — specific references (line numbers, section names, citation markers)
  - `category` (REJECT only): one of `claim_is_supported`, `citation_not_required`,
    `experimental_claim_misclassified`, `out_of_scope`, `stale_comment`
  - `fix_strategy` (ACCEPT only): one of `add_citation`, `qualify_claim`, `remove_claim`,
    `rerun_required` (escalated), `design_flaw` (escalated)
  - `escalate`: `true` if `fix_strategy` is `rerun_required` or `design_flaw`
  - `dimension` — the extracted dimension label

**REJECT category guidance:**
- `claim_is_supported` — evidence for the claim exists in the diff but subagent missed it
- `citation_not_required` — claim is general knowledge not requiring citation
- `experimental_claim_misclassified` — claim is self-evidencing (derived from experiment data)
- `out_of_scope` — comment addresses content outside this PR
- `stale_comment` — comment refers to text no longer present

**fix_strategy guidance (ACCEPT only):**
- `add_citation` — add a missing reference in the report document
- `qualify_claim` — add hedging language ("in our experiments", "preliminary evidence suggests")
- `remove_claim` — delete an unsupported claim with no path to citation
- `rerun_required` — fix requires re-running experiment to generate supporting data
- `design_flaw` — fundamental scope/methodology issue; cannot fix with citation alone

**Protocol deviation rule (`rerun_required`):**
When a claim depends on experimental results, and the experiment plan specifies a
replication count, sample size, or other methodological parameter that the actual
execution did not follow, classify as `rerun_required` if the deviation materially
undermines the evidence supporting the claim. Qualifying the claim with hedging
language (`qualify_claim`) does not constitute remediation when the underlying data
is statistically invalid — the claim needs new supporting data, not softer language.

Exception — justified deviations: If the research report provides a substantive
rationale for why the conclusions remain valid despite the methodological difference,
and that rationale withstands scrutiny, then `qualify_claim` or `remove_claim` may
be appropriate. The key test is: **does this deviation materially undermine the
evidence cited in support of the claim?**

**Invalid statistics rule (`rerun_required`):**
When a finding identifies confidence intervals, p-values, or significance claims
cited as evidence for a claim, and those statistics are computed from the wrong unit
of analysis (e.g., within-run iterations treated as independent replicates), classify
as `rerun_required` if the invalid statistics remain in the report. Classification
as `remove_claim` applies if the claim and its invalid statistical evidence are both
fully removed. Classification as `qualify_claim` applies only if the statistical
artifacts are fully removed and the claim is restated without statistical backing.

**Fallback:** If a subagent fails or times out, discard any partial output from that
subagent (partial JSON must not be used — using partial output risks silently demoting
ACCEPT findings to DISCUSS without surfacing the data loss). Classify all comments in the
failed group as `DISCUSS` and log the failure with the error message, domain group name,
and affected comment IDs.

Merge results into `classification_map: dict[comment_id, verdict_entry]`.
Save `classification_map_{pr}.json`.

Write analysis report to `{{AUTOSKILLIT_TEMP}}/resolve-claims-review/analysis_{pr}_{ts}.md`
with banner (BEFORE any code changes):
```
Analysis complete (BEFORE any code changes)
ACCEPT: N | REJECT: N | DISCUSS: N
  (add_citation: N, qualify_claim: N, remove_claim: N, rerun_required: N ESCALATED, design_flaw: N ESCALATED)
```

Track `accept_count`, `reject_count`, `discuss_count`, and per-strategy counts.

### Step 4: Apply Fixes

Initialize before processing:
```python
addressed_thread_ids: list[str] = []
escalation_records: list = []
```

**Processing order** within ACCEPT findings (critical before warning within each tier):
1. `add_citation` — add reference entries or inline citations
2. `qualify_claim` — add qualifying language
3. `remove_claim` — delete unsupported assertions

For each ACCEPT finding, route by `fix_strategy`:

**`rerun_required` or `design_flaw` → ESCALATE:**
1. Append to `escalation_records` with full finding details and `strategy` field
2. Do NOT add to `addressed_thread_ids`
3. Continue processing — escalation does not change the exit code (exit code remains 0)

**`add_citation` / `qualify_claim` / `remove_claim` → apply edit → commit:**
```bash
git -C "$worktree_path" add {file}
# If pre-commit hooks exist:
pre-commit run --files {file} && git -C "$worktree_path" add {file}
git -C "$worktree_path" commit -m "fix(claims-review): {description} [{dimension}]"
```
Append `thread_node_id` to `addressed_thread_ids` (if not `None`).

**Classification gate — REJECT/DISCUSS bypass:**
- No code changes; record skip
- Do NOT add to `addressed_thread_ids`

### Step 5: Run Validation Command (max 3 iterations)

```python
if validation_command is None:
    # Skip validation step entirely — null validation_command means skip
    validation_status = "SKIPPED"
else:
    # Run with retry logic (max 3 iterations)
    # Timeout expiration is treated as failure and counts toward the iteration limit.
    for iteration in range(1, 4):
        result = run(validation_command, timeout=validation_timeout)
        # returncode non-zero OR timeout (TimeoutExpired) both count as failure.
        if result.returncode == 0:
            validation_status = "PASS"
            break
        if iteration >= 3:
            validation_status = "FAIL"
            # Report failure, leave working directory intact, exit non-zero
            exit(1)
        # Analyze failures, revert/adjust problematic commit, retry
```

When `validation_command` is `null`, skip validation — do not run any command.
When configured, enforce max 3 iteration retry loop before exiting non-zero.

### Step 6: Resolve Addressed Review Threads

For each `thread_id` in `addressed_thread_ids`:

```bash
gh api graphql \
  -f query='mutation($threadId:ID!){resolveReviewThread(input:{threadId:$threadId}){thread{isResolved}}}' \
  -f threadId="$thread_id"
```

- Success (`isResolved: true`): increment `resolved_count`
- Failure: log warning `"Warning: could not resolve thread {thread_id}: {error}"`,
  continue. Do not modify exit code.

Track `resolved_count` and `resolve_failed_count`. Thread resolution failure must never
cause exit non-zero.

### Step 6.5: Post Inline Replies

For every analyzed comment (critical + warning), post one reply using the reply API.

**Reply templates:**

```
ACCEPT (applied):    "Addressed in {sha}: {evidence}"
ACCEPT (skipped):    "Investigated but could not apply: {reason}"
REJECT:              "Investigated — {category}. {evidence}"
DISCUSS:             "Valid observation — flagged for human judgment. {evidence}"
ESCALATION (rerun):  "[ESCALATION] Requires re-running experiment. {message}"
ESCALATION (design): "[ESCALATION] Fundamental design issue. {message}"
```

API endpoint:
```bash
gh api repos/{owner}/{repo}/pulls/{pr_number}/comments/{comment_id}/replies \
  --method POST --field body="..."
```

Track `reply_posted_count` and `reply_failed_count`. Best-effort — failure to post
any reply must not affect exit code.

### Step 6.6: Persist Reject Patterns

Save all REJECT-classified comments to:
`{{AUTOSKILLIT_TEMP}}/resolve-claims-review/reject_patterns_{pr}_{ts}.json`

Schema:
```json
{
  "comment_id": 123,
  "path": "research/report.md",
  "line": 42,
  "body": "...",
  "evidence": "...",
  "category": "citation_not_required",
  "dimension": "external",
  "pr_number": 99,
  "feature_branch": "..."
}
```

Save escalation records to:
`{{AUTOSKILLIT_TEMP}}/resolve-claims-review/escalation_records_{pr}.json`

Each escalation record must include a `"strategy"` field set to the `fix_strategy` value
(either `"rerun_required"` or `"design_flaw"`). This field is read by the structured
output determination logic.

### Step 7: Report

Print structured summary:
```
resolve-claims-review complete
PR: #{pr_number} ({feature_branch} → {base_branch})
Findings fetched: {total}
  - critical: {n}, warning: {n}, info: {n} (skipped)
Intent validation:
  - ACCEPT: {n}  (add_citation: {n}, qualify_claim: {n}, remove_claim: {n},
                   rerun_required: {n} ESCALATED, design_flaw: {n} ESCALATED)
  - REJECT: {n}
  - DISCUSS: {n}
Fixes applied: {n}
Escalations: {n}
Validation: {SKIPPED | PASS | FAIL}
Threads resolved: {n}/{total}
Inline replies: {reply_posted_count} posted / {reply_failed_count} failed
Status: PASS
```

Save full report to `{{AUTOSKILLIT_TEMP}}/resolve-claims-review/report_{pr}_{ts}.md`.

Exit 0.

## Temp File Layout

```
{{AUTOSKILLIT_TEMP}}/resolve-claims-review/
├── inline_comments_{pr}.json
├── reviews_{pr}.json
├── threads_{pr}.json
├── dimension_groups_{pr}.json
├── classification_map_{pr}.json
├── escalation_records_{pr}.json
├── analysis_{pr}_{ts}.md          (written BEFORE code changes)
├── reject_patterns_{pr}_{ts}.json
└── report_{pr}_{ts}.md
```

## Structured Output

After completing all thread processing (addressed + escalated), emit structured
output tokens:

**Verdict decision:**
- If fixes were applied (`Fixes applied: N` where N >= 1): `verdict = real_fix`
- If no fixes were needed (all findings already addressed): `verdict = already_green`

```
needs_rerun = {true|false}
verdict = {real_fix|already_green}
fixes_applied = {N}
```

- **`needs_rerun = true`**: At least one finding was classified as `rerun_required` in
  the escalation records. This includes: (a) fixes requiring re-running the experiment
  to generate supporting data, (b) protocol deviations where the experiment execution
  diverged from the plan in ways that materially undermine the report's claims, or
  (c) invalid statistical analyses (e.g., CIs from the wrong unit of analysis) that
  remain cited as evidence for claims.
- **`needs_rerun = false`**: No `rerun_required` escalations exist. May still have
  `design_flaw` escalations (these are informational and do not require re-running).

**Determination logic:** After writing `escalation_records_{pr}.json`, check whether any
entry has `"strategy": "rerun_required"`. If yes → `true`. If no entries or all entries
are `design_flaw` → `false`.

`needs_rerun` is mandatory. The recipe captures it as `claims_needs_rerun` to route via
`merge_escalations`.

## Output

Emit the structured output tokens as the very last lines before `%%ORDER_UP%%`:

> **IMPORTANT:** Emit the tokens as **literal plain text with no code fences, no markdown formatting**. The recipe capture system reads raw stdout.

```
needs_rerun = {true|false}
verdict = {real_fix|already_green}
fixes_applied = {N}
%%ORDER_UP%%
```

Summary: `{{AUTOSKILLIT_TEMP}}/resolve-claims-review/report_{pr}_{ts}.md` (relative to the current working directory)
