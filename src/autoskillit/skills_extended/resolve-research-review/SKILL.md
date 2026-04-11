---
name: resolve-research-review
categories: [research]
description: >
  Fetch PR review comments from review-research-pr, run research-aware intent
  validation (ACCEPT/REJECT/DISCUSS), apply targeted fixes, escalate unrerunnable
  findings, and post inline replies. Exit 0 drives recipe re_push_research; exit
  non-zero halts the cycle.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: resolve-research-review] Resolving research review comments...'"
          once: true
---

# Resolve Research Review Skill

Apply `changes_requested` review comments from a research PR to the research
worktree. Reads open review threads, runs research-aware intent validation, applies
targeted fixes by fix-strategy taxonomy, escalates unrerunnable findings, resolves
addressed threads, and posts inline replies so a follow-up push closes the review cycle.

## Arguments

`/autoskillit:resolve-research-review {worktree_path} {base_branch}`

- **worktree_path** — Absolute path to the research worktree
- **base_branch** — Target branch for the PR

## When to Use

Called by the research recipe when `review_research_pr` routes `changes_requested`.
Bounded by `retries: 2` — on exhaustion routes to `research_complete`.

## Critical Constraints

**NEVER:**
- Merge or push the branch — the recipe's `re_push_research` step handles push
- Dismiss review threads without addressing the underlying comment
- Create files outside `{{AUTOSKILLIT_TEMP}}/resolve-research-review/`
- Exceed 3 fix-and-retry iterations
- Delete or discard the working directory on failure
- Modify tests to suppress failures introduced by reviewer fixes
- Use file-path-segment grouping — research comments are grouped by **dimension**, not by file path

**ALWAYS:**
- Find the PR by feature branch at invocation time (not a hardcoded number)
- Commit all fixes before returning control to the orchestrator
- Run intent validation BEFORE making any code changes
- Gracefully degrade (exit 0, report skip) if `gh` is unavailable or no PR is found
- Report a structured summary including escalation count

## Workflow

Read `research_review.validation_command` (default: `null`) and
`research_review.validation_timeout` (default: `120`) from `.autoskillit/config.yaml`.

### Step 0: Validate Arguments

Parse two positional arguments: `worktree_path` and `base_branch`.

Derive `feature_branch` via:
```bash
feature_branch=$(git -C "$worktree_path" rev-parse --abbrev-ref HEAD)
```

Read config:
```python
import yaml, pathlib
cfg = yaml.safe_load(pathlib.Path(".autoskillit/config.yaml").read_text()) if pathlib.Path(".autoskillit/config.yaml").exists() else {}
rr_cfg = cfg.get("research_review", {})
validation_command = rr_cfg.get("validation_command", None)
validation_timeout = rr_cfg.get("validation_timeout", 120)
```

If either positional arg is missing, abort with:
`"Usage: /autoskillit:resolve-research-review <worktree_path> <base_branch>"`

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

Fetch review thread node IDs using cursor-based pagination to handle PRs with more than
100 threads:

```bash
# Fetch all pages; repeat with after=$endCursor while hasNextPage is true
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
- `{{AUTOSKILLIT_TEMP}}/resolve-research-review/inline_comments_{pr}.json`
- `{{AUTOSKILLIT_TEMP}}/resolve-research-review/reviews_{pr}.json`
- `{{AUTOSKILLIT_TEMP}}/resolve-research-review/threads_{pr}.json`

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

**Dimension extraction** — comments posted by `review-research-pr` have format
`[severity] dimension: message`. Extract the dimension label using:

```python
import re
DIMENSION_PATTERN = re.compile(r'^\[(?:critical|warning|info)\]\s+(\S+):\s+')
```

Apply `DIMENSION_PATTERN` to each comment body to extract the dimension label.

**Dimension group mapping:**

| Comment dimension             | Group key       |
|-------------------------------|-----------------|
| statistical-rigor, data-integrity | statistical |
| methodology                   | methodology     |
| reproducibility, isolation    | reproducibility |
| report-quality                | reporting       |
| slop                          | hygiene         |
| (unparseable / no match)      | unknown         |

Save `dimension_groups_{pr}.json` with findings keyed by group.

### Step 3.5: Intent Validation (Parallel Sub-Agents — BEFORE any code changes)

Before applying any fix, validate every critical and warning finding against the actual
codebase and git history. This analysis phase runs entirely before code changes are made.

**Dimension grouping:** Group findings by their extracted dimension group key
(`statistical`, `methodology`, `reproducibility`, `reporting`, `hygiene`, `unknown`).
This is dimension-based grouping, NOT file-path grouping.

Launch one parallel subagent (Task tool, `model: "sonnet"`) per non-empty dimension
group. Each subagent receives:
- Its list of findings (path, line, body, diff_hunk, dimension)
- Instructions to read the actual code at each flagged line (±30 lines context)
- Instructions to run `git log --follow -p --max-count=5 -- {path}` for git history
- Instructions to classify each as `ACCEPT`, `REJECT`, or `DISCUSS` with:
  - `verdict` — the classification
  - `evidence` — specific references (line numbers, function names, design rationale)
  - `category` (REJECT only): one of `methodology_misunderstanding`,
    `false_positive_intentional`, `inconclusive_not_deficiency`, `out_of_scope`,
    `stale_comment`
  - `fix_strategy` (ACCEPT only): one of `report_edit` (research/*.md edits),
    `script_fix` (scripts/*.py), `config_fix` (config YAML/seed/env spec),
    `rerun_required` (requires experiment re-run), `design_flaw` (fundamental redesign)
  - `escalate`: `true` if `fix_strategy` is `rerun_required` or `design_flaw`
  - `dimension` — the extracted dimension label
  - `commit_sha_hint` — from `git log`

**REJECT category guidance:**
- `methodology_misunderstanding` — reviewer misread the experimental design
- `false_positive_intentional` — flagged pattern is intentional research design
- `inconclusive_not_deficiency` — reviewer treated inconclusive results as a deficiency;
  inconclusive results are valid research outcomes and must never be rejected as deficiencies
- `out_of_scope` — comment addresses something outside this PR's scope
- `stale_comment` — comment refers to code that no longer exists

**fix_strategy guidance (ACCEPT only):**
- `config_fix` — fix is in config YAML, seed values, or environment spec
- `script_fix` — fix is in scripts/*.py or other experiment code
- `report_edit` — fix is in research/*.md report documents
- `rerun_required` — fix requires re-running the experiment; cannot be applied in-place
- `design_flaw` — fundamental design issue that cannot be fixed without redesign

**Protocol deviation rule (`rerun_required`):**
When the experiment plan specifies a replication count, sample size, or other
methodological parameter, and the actual execution deviated from that specification
in a way that materially undermines the evidence supporting the report's claims,
classify as `rerun_required` — not `report_edit`. Adding a caveat about inadequate
replication does not constitute remediation when the deviation invalidates the
statistical basis for the claims (e.g., running R=1 instead of planned R=3 means
confidence intervals are computed from within-run iterations rather than between-run
replicates — a different unit of analysis entirely).

Exception — justified deviations: If the research report provides a substantive
rationale for why the conclusions remain valid despite the methodological difference,
and that rationale withstands scrutiny (not just acknowledging the limitation), then
the classification may remain `report_edit`. Minor deviations that would not
reasonably change the experiment's conclusions (e.g., R=4 instead of R=5 with large
effect size) can also remain `report_edit` with an appropriate caveat. The key test
is: **does this deviation materially undermine the evidence supporting the claims?**

**Invalid statistics rule (`rerun_required`):**
When a finding identifies confidence intervals, p-values, or significance claims
computed from the wrong unit of analysis (e.g., within-run iterations treated as
independent replicates, or pseudoreplication), and those statistical artifacts are
retained in the report in any form (tables, figures, inline references), classify
as `rerun_required`. Classification as `report_edit` applies only if the invalid
statistical artifacts are **fully removed** from the report and replaced with
appropriately qualified point estimates or narrative descriptions that make no
statistical claims.

**Fallback:** If a subagent fails, classify all comments in that group as `DISCUSS`.

Merge results into `classification_map: dict[comment_id, verdict_entry]`.
Save `classification_map_{pr}.json`.

Write analysis report to `{{AUTOSKILLIT_TEMP}}/resolve-research-review/analysis_{pr}_{ts}.md`
with banner (BEFORE any code changes):
```
Analysis complete (BEFORE any code changes)
ACCEPT: N | REJECT: N | DISCUSS: N
  (report_edit: N, script_fix: N, config_fix: N, rerun_required: N ESCALATED, design_flaw: N ESCALATED)
```

Track `accept_count`, `reject_count`, `discuss_count`, and per-strategy counts.

### Step 4: Apply Fixes

Initialize before processing:
```python
addressed_thread_ids: list[str] = []
escalation_records: list = []
```

**Processing order** within ACCEPT findings (critical before warning within each tier):
1. `config_fix` — config YAML, seed, env spec changes
2. `script_fix` — scripts/*.py, experiment code changes
3. `report_edit` — research/*.md document changes

For each ACCEPT finding, route by `fix_strategy`:

**`rerun_required` or `design_flaw` → ESCALATE:**
1. Append to `escalation_records` with full finding details
2. Do NOT add to `addressed_thread_ids`
3. Continue processing — escalation does not change the exit code (exit code remains 0)

**`config_fix` / `script_fix` / `report_edit` → apply edit → commit:**
```bash
git -C "$worktree_path" add {file}
# If pre-commit hooks exist:
pre-commit run --files {file} && git -C "$worktree_path" add {file}
git -C "$worktree_path" commit -m "fix(research-review): {description} [{dimension}]"
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
    for iteration in range(1, 4):
        result = run(validation_command, timeout=validation_timeout)
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
REJECT:              "Investigated — intentional research design. {evidence}"
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
`{{AUTOSKILLIT_TEMP}}/resolve-research-review/reject_patterns_{pr}_{ts}.json`

Schema extends resolve-review's schema with a `dimension` field:
```json
{
  "comment_id": 123,
  "path": "research/experiment.md",
  "line": 42,
  "body": "...",
  "evidence": "...",
  "category": "inconclusive_not_deficiency",
  "dimension": "reporting",
  "pr_number": 99,
  "feature_branch": "..."
}
```

Save escalation records to:
`{{AUTOSKILLIT_TEMP}}/resolve-research-review/escalation_records_{pr}.json`

### Step 7: Report

Print structured summary:
```
resolve-research-review complete
PR: #{pr_number} ({feature_branch} → {base_branch})
Findings fetched: {total}
  - critical: {n}, warning: {n}, info: {n} (skipped)
Intent validation:
  - ACCEPT: {n}  (report_edit: {n}, script_fix: {n}, config_fix: {n},
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

Save full report to `{{AUTOSKILLIT_TEMP}}/resolve-research-review/report_{pr}_{ts}.md`.

Exit 0.

## Temp File Layout

```
{{AUTOSKILLIT_TEMP}}/resolve-research-review/
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

After completing all thread processing (addressed + escalated), emit a single structured
output token:

```
needs_rerun = {true|false}
```

- **`true`**: At least one finding was classified as `rerun_required` in the escalation
  records. This includes: (a) fixes that change code affecting measurement (benchmark
  code, analysis scripts, normalization logic) requiring result re-validation,
  (b) protocol deviations where the experiment execution diverged from the plan in ways
  that materially undermine the report's claims, or (c) invalid statistical analyses
  (e.g., CIs from the wrong unit of analysis) that remain in the report.
- **`false`**: No `rerun_required` escalations exist. May still have `design_flaw`
  escalations (these are informational and do not require re-running benchmarks).

**Determination logic:** After writing `escalation_records_{pr}.json`, check whether any
entry has `"strategy": "rerun_required"`. If yes → `true`. If no entries or all entries
are `design_flaw` → `false`.

This token is mandatory. The recipe captures it to route between re-validation
(re-run affected benchmarks) and direct push.

## Output

Emit the structured output token as the very last line before `%%ORDER_UP%%`:

> **IMPORTANT:** Emit the token as **literal plain text with no code fences, no markdown formatting**. The recipe capture system reads raw stdout.

```
needs_rerun = {true|false}
%%ORDER_UP%%
```

Summary: `{{AUTOSKILLIT_TEMP}}/resolve-research-review/report_{pr}_{ts}.md` (relative to the current working directory)
