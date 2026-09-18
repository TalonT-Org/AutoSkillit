---
name: resolve-research-review
categories:
- research
uses_capabilities:
- commit_files
- test_check
- github_api_write
description: 'Fetch PR review comments from review-research-pr, run research-aware intent validation (ACCEPT/REJECT/DISCUSS),
  apply targeted fixes, escalate unrerunnable findings, and post inline replies. Exit 0 drives recipe re_push_research; exit
  non-zero halts the cycle.

  '
hooks:
  PreToolUse:
  - matcher: '*'
    hooks:
    - type: command
      command: 'echo ''[SKILL: resolve-research-review] Resolving research review comments...'''
      once: true
semantic_version: 1
semantic_requirements:
  logical_roles:
  - name: delegated-worker
    purpose: perform the named independent responsibility and return bounded evidence
  child_spawns:
  - role: delegated-worker
    for_each: intent_validation_groups
  concurrency:
    required: true
  join:
    required: true
  evidence:
    required: true
    independent: true
  child_model_policies:
  - role: delegated-worker
    model_class: sonnet
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
- Fabricate, invent, or embellish information not supported by the available evidence or code.

- Merge or push the branch — the recipe's `re_push_research` step handles push
- Dismiss review threads without addressing the underlying comment
- Create files outside `{{AUTOSKILLIT_TEMP}}/resolve-research-review/`
- Exceed 3 fix-and-retry iterations
- Delete or discard the working directory on failure
- Modify tests to suppress failures introduced by reviewer fixes
- Treat dimension grouping as intent-classification-only; Step 4 groups concrete
  edits by file
- Detach child delegations instead of joining them (joining every child is required)
- Start independent child delegations sequentially

**ALWAYS:**
- Find the PR by feature branch at invocation time (not a hardcoded number)
- Commit all fixes before returning control to the orchestrator
- Run intent validation BEFORE making any code changes
- Gracefully degrade (exit 0, report skip) if `gh` is unavailable or no PR is found
- Report a structured summary including escalation count
- **Read before editing**: Before issuing an `Edit` call on any file, ensure you have issued a `Read` on that file earlier in this session. Claude Code rejects `Edit` on unread files — the retry wastes a full API turn at current context size. If you are uncertain whether a file was read, issue a targeted `Read` (offset + limit to the region you plan to edit) rather than risk an error. **Note:** Reads performed by subagents (Task/Agent) do NOT satisfy this requirement — they run in a child session whose reads are invisible to the parent. If a file was only read inside a subagent, you must Read it again in this main session before calling Edit.
- Start all independent child delegations before awaiting any result to maximize concurrency

## Workflow

Read the optional `research_review.validation_command` (default: `null`) and
`research_review.validation_timeout` (default: `120`) from `.autoskillit/config.yaml`.
This custom check never replaces the recorded `test_check` gate.

### Step 0: Validate Arguments

Parse two positional arguments: `worktree_path` and `base_branch`.

Derive `feature_branch` via:
```bash
feature_branch=$(git -C "$worktree_path" rev-parse --abbrev-ref HEAD)
```

Read config:
```python
import yaml, pathlib

cfg = (
    yaml.safe_load(pathlib.Path(".autoskillit/config.yaml").read_text())
    if pathlib.Path(".autoskillit/config.yaml").exists()
    else {}
)
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
- Write a bounded no-PR report under `{{AUTOSKILLIT_TEMP}}/resolve-research-review/`.
- Emit exactly `review_status = no_pr`; do not emit `needs_rerun`, a
  `finding_disposition` row, or `verdict`.
- Exit 0 (graceful degradation — do not fail the pipeline).

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
100 threads. Before each page, use a file-write tool in a separate completed tool call to
write the bounded parameterized query and its `owner`, `repo`, `number`, and `after`
variables object to `{{AUTOSKILLIT_TEMP}}/resolve-research-review/thread_query.json`.
Invoke the exact literal path in a later call:

```bash
gh api graphql --input "{{AUTOSKILLIT_TEMP}}/resolve-research-review/thread_query.json"
```

Build `comment_id_to_thread_id: dict[int, str]` map. Skip threads where `isResolved`
is already `true`.

If the GraphQL call fails, log a warning and set `comment_id_to_thread_id = {}`.
Thread resolution will be silently skipped in Step 6.

Save to:
- `{{AUTOSKILLIT_TEMP}}/resolve-research-review/inline_comments_{pr}.json`
- `{{AUTOSKILLIT_TEMP}}/resolve-research-review/reviews_{pr}.json`
- `{{AUTOSKILLIT_TEMP}}/resolve-research-review/threads_{pr}.json`

Use `jq -n` or the Write tool to create these files. If a file already exists from a prior retry, either read it first (to satisfy the Write tool guard) or use a Bash redirect (`jq -n ... > path`). Do not use inline Python one-liners or heredoc scripts with `open()` — these are blocked by the sandbox.

### Step 3: Parse, Classify, and Dimension-Group

From **inline comments**, extract per comment:
- `path` — file path relative to repo root
- `line` — the line being commented on
- `body` — the reviewer's message
- `diff_hunk` — surrounding context
- `id` — the comment's REST database ID
- `thread_node_id` — look up `comment_id_to_thread_id.get(id)`

**File-level comment guard:** If `line` is null (file-level comment posted by
review-research-pr), skip this finding entirely — file-level comments have no code
anchor and cannot be resolved by code changes. Record: `(path, null, reason="file-level
comment — no line anchor")`. Do not add its `thread_node_id` to `addressed_thread_ids`.

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

DIMENSION_PATTERN = re.compile(r"^\[(?:critical|warning|info)\]\s+(\S+):\s+")
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

Save `dimension_groups_{pr}.json` with findings keyed by group. Use `jq -n` or the Write tool. If the file already exists from a prior retry, either read it first (to satisfy the Write tool guard) or use a Bash redirect (`jq -n ... > path`).

### Step 3.5: Intent Validation (Parallel Sub-Agents — BEFORE any code changes) (SINGLE MESSAGE)

Set `intent_validation_groups` to the non-empty dimension groups produced in Step 3.
Use the same keys for prompts, child IDs, verdict association, and joins.

**Start ALL independent child delegations before awaiting any result — one per item — and join every child before synthesis.**

Do not output any prose between subagent dispatches. Immediately proceed to the next tool call.

Before applying any fix, validate every critical and warning finding against the actual
codebase and git history. This analysis phase runs entirely before code changes are made.

**Dimension grouping:** Group findings by their extracted dimension group key
(`statistical`, `methodology`, `reproducibility`, `reporting`, `hygiene`, `unknown`).
This is dimension-based grouping for intent classification only. It does not prohibit
the file grouping required for concrete edits in Step 4.

Launch one parallel subagent via `child delegation under the declared `sonnet` model-class policy` per non-empty dimension
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
Save `classification_map_{pr}.json`. Use `jq -n` or the Write tool. If the file already exists from a prior retry, either read it first (to satisfy the Write tool guard) or use a Bash redirect (`jq -n ... > path`).

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

**Edit order for ACCEPT findings:** Treat `rerun_required` and `design_flaw` as
escalations, not edit candidates. Group all remaining concrete edit strategies by
path. Define severity rank as `critical=3`, `warning=2`, `info=1`; process contiguous
file groups by `(-file_max_severity, path, -line, comment_id)`. Within each file group,
edit in descending-line order. `config_fix`, `script_fix`, and `report_edit` choose
only the edit route; they must not split a file group.

For each concrete candidate in that order, re-read the live source immediately before
editing. Use its existing path, line, and diff-hunk context to re-derive the edit from
the live text; do not edit from a previously read line range. Then route by
`fix_strategy`:

**`rerun_required` or `design_flaw` → ESCALATE:**
1. Append to `escalation_records` with full finding details
2. Do NOT add to `addressed_thread_ids`
3. Continue processing — escalation does not change the exit code (exit code remains 0)

**`config_fix` / `script_fix` / `report_edit` → apply edit → commit:**
```
commit_files(paths=["{file}"], message="fix(research-review): {description} [{dimension}]", cwd="{work_dir}")
```
The tool runs pre-commit hooks, handles auto-fix re-staging, and returns
`{"success": true, "commit_sha": "..."}` or `{"success": false, "error": "..."}`.
Record the finding as `applied` only when `commit_files` returns `success: true`, and attach
the returned commit SHA. Failed attempts do not create a terminal disposition; after retries
are exhausted, record the finding once as `failed`. Do NOT use `--amend` — always create new commits.
Append `thread_node_id` to `addressed_thread_ids` (if not `None`).

**Classification gate — REJECT/DISCUSS bypass:**
- No code changes; record skip
- Do NOT add to `addressed_thread_ids`

### Step 5: Run Custom Validation, Then the Recorded Test Gate (max 3 iterations)

```python
if validation_command is None:
    # Skip only the optional custom command.
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
            # Write a bounded diagnostic report, emit supported diagnostic output,
            # leave the working directory intact, and exit non-zero.
            exit(1)
        # Analyze failures, revert/adjust problematic commit, retry

# Required for every processed result, including when validation_command is null.
test_result = test_check(worktree_path=worktree_path)
if test_result["passed"] is not True:
    # Write bounded diagnostics, preserve the worktree, and exit non-zero.
    exit(1)
```

When `validation_command` is `null`, skip only that custom command. Always call the MCP
`test_check` tool before a processed success so the server records the final test outcome.
When configured, enforce the custom command's max 3 iteration retry loop before the final gate.

### Step 6: Resolve Addressed Review Threads

Batch all thread resolutions into a single GraphQL request using aliased mutations.
This reduces N requests (5 pts each = 5N pts) to 1 request (5 pts total).
If `addressed_thread_ids` has more than 50 threads, chunk into batches of 50.
Do not output prose between payload-write and GitHub calls or between chunks; immediately
proceed to the next required call. For each chunk, build aliased `resolveReviewThread`
mutations and a `variables` object containing the actual thread node IDs. Use a file-write
tool in a separate completed tool call to write the
bounded JSON payload to
`{{AUTOSKILLIT_TEMP}}/resolve-research-review/resolve_threads_chunk_0.json`, changing only
the numeric suffix for later chunks. Invoke each literal path in a later tool call:

```bash
gh api graphql --input "{{AUTOSKILLIT_TEMP}}/resolve-research-review/resolve_threads_chunk_0.json"
```

Parse the response: for each `resolve${i}` alias key, check `thread.isResolved`.
- **Success** (`isResolved: true`): increment `resolved_count`.
- **Failure** (non-zero exit code, parse error, or `isResolved: false` for any alias): log a warning
  `"Warning: could not resolve thread ${tid}: {error}"`. Continue to the next thread.
  Do not modify exit code.

Track `resolved_count` and `resolve_failed_count`. This step is best-effort — failure
to resolve any thread never affects the exit code.

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
sleep 1  # Rate-limit discipline: 1s between mutating calls
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

Use `jq -n` or the Write tool to create this file. If the file already exists from a prior retry, either read it first (to satisfy the Write tool guard) or use a Bash redirect (`jq -n ... > path`).

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
Fixes applied: {number of applied finding_disposition rows}
Escalations: {n}
Validation: {SKIPPED | PASS | FAIL}
Threads resolved: {n}/{total}
Inline replies: {reply_posted_count} posted / {reply_failed_count} failed
Status: {PASS|FAIL}
```

Save full report to `{{AUTOSKILLIT_TEMP}}/resolve-research-review/report_{pr}_{ts}.md`.

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

`needs_rerun = true` covers protocol deviations or execution that diverged from the
approved research plan and therefore requires a fresh experiment run.

<!-- gated-field-semantics:begin -->
### Finding disposition and qualifier semantics

For every processed finding, emit one `finding_disposition` row. `applied` requires a
successful commit and includes that commit SHA. Use `skipped` for a finding intentionally
left unchanged and `failed` for an attempted apply or commit that did not succeed. No PR
means no findings, so no disposition rows are emitted.

`accepted_without_changes` is a success qualifier, never a disposition. It applies when
`accept_count > 0 and fixes_applied == 0 and fix_failures == 0`; report it in prose only.
Server derivation: `accept_count` equals the number of `finding_disposition` rows.
Server derivation: `fixes_applied` equals the number of `applied` rows.
Server derivation: `fix_failures` equals the number of `failed` rows.
Server derivation: `skipped_in_fix_phase` equals the number of `skipped` rows.
Do not emit those derived counters or arithmetic expressions for them in skill output.

### Verdict Decision

| Verdict | Decision | Route |
| --- | --- | --- |
| `real_fix` | At least one finding is `applied`, none is `failed`, and the final `test_check` passes. | Continue normal success routing. |
| `already_green` | No finding is `applied` or `failed`, and the final `test_check` passes. | Continue normal success routing; the success qualifier may apply. |
| `flake_suspected` | No finding is `applied` or `failed`; a local failure is followed by a passing unchanged-tree rerun. | Continue the existing flake route with the recorded evidence. |
| `ci_only_failure` | No finding is `applied` or `failed`; local tests pass and evidence identifies a CI-only environment or configuration failure. | Continue the existing CI-only route with the recorded evidence. |

Any terminal `failed` disposition is a failure and emits no success verdict. Every processed
success requires a final passing `test_check`. The validation cap remains a failure: emit
`review_status = processed`, terminal disposition rows, and diagnostics without adding another
`review_status` value or claiming success.

`needs_rerun = true` when an escalation record has `strategy = rerun_required`; otherwise
it is `false`. It remains independent of the finding disposition and verdict.

### Operative output template

<!-- resolver-operative-output:begin -->
needs_rerun = {true|false}
review_status = processed
finding_disposition = {finding-id} | {applied|skipped|failed} [| {commit-sha}]
verdict = {real_fix|already_green|flake_suspected|ci_only_failure}
<!-- resolver-operative-output:end -->

For no PR or unavailable `gh`, emit exactly `review_status = no_pr`; omit every other
token. At a validation cap, write the diagnostic report, emit only supported processed
diagnostic fields, and retain the non-zero exit.

## Output

Emit the applicable final tokens as the very last plain-text lines:

<!-- resolver-final-output:begin -->
needs_rerun = {true|false}
review_status = processed
finding_disposition = {finding-id} | {applied|skipped|failed} [| {commit-sha}]
verdict = {real_fix|already_green|flake_suspected|ci_only_failure}
<!-- resolver-final-output:end -->

For no PR, emit only `review_status = no_pr`.

Summary: `{{AUTOSKILLIT_TEMP}}/resolve-research-review/report_{pr}_{ts}.md` (relative to the current working directory)
<!-- gated-field-semantics:end -->
