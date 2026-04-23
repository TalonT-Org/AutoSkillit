---
name: review-research-pr
categories: [research]
description: Automated diff-scoped research PR review using parallel audit subagents aligned to research quality dimensions. Posts inline GitHub review comments and submits a summary verdict. Use after a research PR is opened to gate on review approval.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: review-research-pr] Reviewing research pull request...'"
          once: true
---

# Review Research PR Skill

Perform an automated, diff-scoped code review on an open GitHub research PR using parallel
audit subagents tuned to research quality dimensions. Posts inline review comments and submits
a summary verdict. Called by the recipe pipeline after `open_research_pr` opens the PR.

## Arguments

`/autoskillit:review-research-pr <worktree-path-or-feature-branch> <base-branch>`

- **worktree-path-or-feature-branch** — Either an absolute path to the research worktree
  (preferred; skill derives the feature branch from `git rev-parse --abbrev-ref HEAD`)
  or the feature branch name directly
- **base-branch** — The base branch the PR targets (e.g., "main")

## When to Use

- Called by the recipe orchestrator via `run_skill` after `open_research_pr`
- Can be invoked standalone to review any open research PR

## Critical Constraints

**NEVER:**
- Create files outside `{{AUTOSKILLIT_TEMP}}/review-research-pr/`
- Approve a PR that has `changes_requested` findings
- Post review comments when `gh` is unavailable — output `verdict=approved` and exit 0
- Review files outside the PR diff — scope all audit to diff content only
- Modify any source code
- Flag the absence of a clear experimental conclusion as a deficiency — inconclusive
  results are valid outcomes for research PRs (do not flag them)

**ALWAYS:**
- Find the PR by feature branch at invocation time (not from a pre-captured URL)
- Output `verdict=` on the final line
- Exit 0 in all normal cases; verdict drives recipe routing via on_result, not exit code
- Exit non-zero only for unrecoverable errors (e.g., gh CLI truly unavailable after graceful degradation has already output verdict=approved)
- Tag the authenticated GitHub user (`gh api user -q .login`) in escalation comments (`needs_human` verdict) — omit the mention silently if username derivation fails
- Use `model: "sonnet"` when spawning all subagents via the Task tool
- Deduplicate findings by (file, line) pairs before posting

## Workflow

### Step 0: Validate Arguments

Parse two positional arguments: `worktree_or_branch` and `base_branch`.

Derive `feature_branch`:

```bash
if [ -d "$worktree_or_branch" ]; then
  feature_branch=$(git -C "$worktree_or_branch" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
else
  feature_branch="$worktree_or_branch"
fi
```

Derive the escalation username for `needs_human` verdicts:

```bash
escalation_user=$(gh api user -q .login 2>/dev/null || echo "")
```

If `escalation_user` is non-empty, set `escalation_user_mention="@${escalation_user}"`.
If empty (gh unavailable or not authenticated), set `escalation_user_mention=""`.

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
gh pr list --head "$feature_branch" --base "$base_branch" \
  --json number,url -q '.[0] | "\(.number) \(.url)"'
```

If `gh` is unavailable or not authenticated, or no PR is found:
- Log "No PR found or gh unavailable — skipping review"
- Output `verdict=approved`
- Exit 0 (graceful degradation)

### Step 2: Get PR Diff and Metadata

```bash
# Get the PR diff
gh pr diff {pr_number}

# Get owner/repo
gh repo view --json nameWithOwner -q .nameWithOwner
```

Save the diff to `{{AUTOSKILLIT_TEMP}}/review-research-pr/diff_{pr_number}.txt`. (relative to the current working directory)

### Step 2.7: Compute Valid Line Ranges

Parse hunk ranges from the diff saved in Step 2:

```bash
VALID_LINE_RANGES="{}"
# Parse @@ +start,count @@ headers from the diff to build a JSON map of
# {filepath: [[start, end], ...]} ranges. Same logic as review-pr Step 2.7.
# If hunk_ranges_path was provided as a contract input, load from there instead.
```

`VALID_LINE_RANGES` is used in Step 4 to partition findings into postable and unpostable.

### Step 3: Run Parallel Audit Subagents

Spawn parallel subagents (Task tool, model: sonnet) for each research audit dimension.
Each subagent receives only the PR diff content (not the full codebase) and returns
findings in JSON format:

```json
[
  {
    "file": "path/to/file.py",
    "line": 42,
    "dimension": "methodology|reproducibility|report-quality|statistical-rigor|isolation|data-integrity|slop|data-scope",
    "severity": "critical|warning|info",
    "message": "Description of the finding",
    "requires_decision": false
  }
]
```

**Research audit dimensions:**

1. **methodology** — Experimental design validity: appropriate controls, confounds, hypothesis
   alignment, scope creep.
   Check for: missing baselines, uncontrolled variables, methodology/hypothesis misalignment.

2. **reproducibility** — Ability to replicate the experiment independently.
   Check for: hardcoded paths, missing environment specs, undocumented random seeds,
   missing data provenance, non-deterministic procedures without documentation.

3. **report-quality** — Clarity, completeness, and honesty of research reporting.
   Check for: unexplained findings, missing limitations section, unsupported conclusions.
   **Constraint:** Do not flag absence of a clear experimental conclusion — inconclusive
   results are a valid outcome and must not be treated as a deficiency. Only flag
   reporting issues that obscure or misrepresent findings.

4. **statistical-rigor** — Correct use of statistical methods and honest interpretation.
   Check for: p-hacking indicators, missing confidence intervals, inappropriate aggregations,
   cherry-picked metrics, overstated effect sizes.

5. **isolation** — Experiment environment isolation and interference avoidance.
   Check for: shared mutable state across runs, missing teardown, environment contamination,
   test interference with production data.

6. **data-integrity** — Correctness and trustworthiness of data collection and handling.
   Check for: off-by-one errors in data slicing, incorrect aggregations, data leakage,
   mismatched units, silent NaN/None handling in metrics.

7. **slop** — AI-generated boilerplate that adds noise without research value.
   Check for: commented-out code, TODO without issue refs, over-verbose docstrings,
   dead code, backward-compat stubs left by the LLM.

8. **data-scope** — Data scope coverage and qualification.
   Checks whether the experiment's data coverage matches the research task directive:
   - **Scope coverage**: Did the experiment use the data types specified in the research
     task directive? If the directive said "use MERFISH data" but all benchmarks ran on
     synthetic data only, this is a finding.
   - **Qualification**: Are domain-specific claims (e.g., "Reduces MERFISH evaluation
     wall-clock by X%") qualified with actual data provenance? Claims derived from
     synthetic data must state this explicitly.
   - **Data Scope Statement**: Does the Executive Summary contain a Data Scope Statement?
     If not, this is a finding.
   - **Hypothesis gate alignment**: Do GO/NO-GO recommendations reference the correct
     pre-specified gate thresholds, or were thresholds silently substituted?

   **Severity guidance:**
   - `requires_decision: true` when all benchmarks used synthetic data for a domain-specific project
   - Standard finding when Data Scope Statement is missing or incomplete
   - Standard finding when claims are unqualified

Subagent prompt template (all 8 dimensions):

> You are reviewing a GitHub PR diff for [{dimension}] issues only.
> Scope: examine only the diff content provided. Do not fetch or read files outside the diff.
> Return a JSON array of findings. Each finding must have:
>   file, line, severity (critical/warning/info), dimension, message,
>   requires_decision (boolean).
>
> Set requires_decision=true ONLY for findings where the correct path forward is
> genuinely ambiguous and cannot be determined without the human's intent or
> preference — for example: design trade-offs, approach choices with valid
> alternatives, unclear intent after a merge conflict, plan/implementation
> divergences where both directions are valid.
>
> Set requires_decision=false for ALL bugs, style issues, or anything with a
> clear fix, regardless of severity. When in doubt, set requires_decision=false.
>
> Each line in the diff is prefixed with `[LNNN]` where NNN is the new-file line number.
> When reporting findings, use the `[LNNN]` number as the `line` value in your finding.
> Do not compute line numbers yourself — use the marker.
> If the finding cannot be anchored to a specific `[LNNN]` marker, use the nearest
> `+` or context line's marker in the same hunk.
>
> If no issues found, return an empty array [].
> Annotated diff content (each line prefixed with [LNNN] markers):
> {annotated_diff_content}

### Step 4: Aggregate and Deduplicate Findings

1. Collect all subagent JSON responses
2. Deduplicate by `(file, line)` pairs — keep highest severity for each pair
3. Partition findings against `VALID_LINE_RANGES` (built in Step 2.7):
   - `FILTERED_FINDINGS`: findings whose `(file, line)` falls within any hunk range.
   - `UNPOSTABLE_FINDINGS`: findings whose `line` is not in any hunk range.
     Log a warning for each. Critical-severity unpostable findings are posted as
     file-level comments in Step 6. All unpostable findings appear in the Step 7 body.
   - If `VALID_LINE_RANGES` is empty, all findings are `FILTERED_FINDINGS`.
4. Apply verdict logic (Step 5) to ALL findings (`FILTERED_FINDINGS` + `UNPOSTABLE_FINDINGS`
   combined), so unpostable findings still contribute to the verdict.
5. Bucket by actionability (applied to combined findings):
   - `actionable_findings` — requires_decision=false AND severity in ("critical", "warning")
   - `decision_findings` — requires_decision=true (any severity)
   - `info_findings` — severity == "info" AND requires_decision=false

### Step 4.5: Echo Primary Obligation

After aggregating all subagent findings, before proceeding to verdict or posting, you MUST state aloud:

> "I have N findings. My primary job is to post inline comments on specific code lines for each finding. I must use the GitHub Reviews API to leave comments anchored to the exact lines in the diff."

This is not optional. Do not proceed to Step 5 without stating this.

### Step 5: Determine Verdict

- Any `actionable_findings` present → `verdict = "changes_requested"` (clear fix exists, automated resolver handles it)
- No actionable findings, but `decision_findings` present → `verdict = "needs_human"` (`needs_human` fires only when one or more findings have `requires_decision=true` — meaning the correct path forward requires a human decision that the automated reviewer cannot make)
- No actionable or decision findings → `verdict = "approved"`

**Verdict logic:**
```python
decision_findings = [f for f in all_findings if f.get("requires_decision")]
actionable_findings = [
    f for f in all_findings
    if not f.get("requires_decision") and f["severity"] in ("critical", "warning")
]

if actionable_findings:
    verdict = "changes_requested"
elif decision_findings:
    verdict = "needs_human"
else:
    verdict = "approved"
```

### Step 6: Post Inline Review Comments

Build review comment bodies for each critical and warning finding. Use the `line` and `side`
fields (modern GitHub Reviews API — not the deprecated `position` field) so that file line
numbers from audit findings map directly without diff-position counting.

For each finding, `line` is the finding's `line` value (the line number in the new file) and
`side` is always `RIGHT` (referring to the right-hand side of the diff — additions and context
in the updated file).

Build a proper JSON payload where each comment is a complete object, then post via `--input -`.

```bash
# Build comments JSON array from findings
COMMENTS_JSON=$(jq -n --argjson findings "$FILTERED_FINDINGS" '
  $findings | map({
    path: .file,
    line: .line,
    side: "RIGHT",
    body: ("[" + .severity + "] " + .dimension + ": " + .message)
  })
')

# Build and post the full review payload via stdin
jq -n \
  --arg body "AutoSkillit Research PR Review — Verdict: {verdict}" \
  --arg event "{APPROVE|COMMENT|REQUEST_CHANGES}" \
  --argjson comments "$COMMENTS_JSON" \
  '{body: $body, event: $event, comments: $comments}' | \
gh api /repos/{owner}/{repo}/pulls/{pr_number}/reviews \
  --method POST --input -
```

Event mapping:
- `approved` → `APPROVE`
- `needs_human` → `COMMENT`
- `changes_requested` → `REQUEST_CHANGES`

**Fallback Tier 1 — Individual Comments (if batch POST fails):**

Attempt to post each finding individually via:

```bash
COMMIT_ID=$(gh pr view {pr_number} --json headRefOid -q .headRefOid)

# For each finding:
gh api /repos/{owner}/{repo}/pulls/{pr_number}/comments \
  --method POST \
  --field path="{finding.file}" \
  --field line={finding.line} \
  --field side="RIGHT" \
  --field commit_id="$COMMIT_ID" \
  --field body="[{finding.severity}] {finding.dimension}: {finding.message}"
sleep 1  # Rate-limit discipline: 1s between mutating calls
```

Individual POSTs are not atomic — one failure does not block others.
If at least one per-finding comment succeeds, proceed to Step 7.

**File-Level Comments for Critical Unpostable Findings:**

After the batch review POST succeeds (or after Tier 1 individual posting completes),
post file-level comments for each **critical-severity** finding in `UNPOSTABLE_FINDINGS`.
These use the individual comments endpoint with `subject_type: "file"` — this parameter
is NOT valid on the batch Reviews API `comments[]` array.

```bash
COMMIT_ID=$(gh pr view {pr_number} --json headRefOid -q .headRefOid)

# For each CRITICAL finding in UNPOSTABLE_FINDINGS:
# NOTE: Do NOT include a `line` field — `line` must be omitted (not set to null)
# for subject_type: "file". The `gh api --field` syntax naturally omits unspecified
# fields, so simply not including `--field line=...` is correct.
gh api /repos/{owner}/{repo}/pulls/{pr_number}/comments \
  --method POST \
  --field path="{finding.file}" \
  --field subject_type="file" \
  --field commit_id="$COMMIT_ID" \
  --field body="[{finding.severity}] {finding.dimension} (L{finding.line} — outside diff hunk): {finding.message}"
sleep 1  # Rate-limit discipline: 1s between mutating calls
```

Only critical-severity findings are posted as file-level comments to control API call volume.
Warning and info unpostable findings appear in the Step 7 review body only.

If a file-level POST fails, log the failure and continue — file-level comments are
best-effort supplementary visibility. Do not fall through to Tier 1/Tier 2 for these.

**Fallback Tier 2 — DEGRADED: Bullet-List Summary Dump (if all individual posts fail):**

WARNING: If you reach Tier 2 fallback, the review has FAILED its primary purpose.
Before posting the body dump, you MUST state:

> "FALLBACK: I was unable to post inline comments. Posting summary as review body instead. This is a DEGRADED review."

Post ALL findings via:

```bash
gh pr review {pr_number} --comment --body "{summary_markdown}"
```

Format each file's findings as a bullet list:

```
## AutoSkillit Research Review Findings

**Verdict:** {verdict}

### path/to/file.py
- **L{line}** [{severity}/{dimension}]: {message, truncated to 120 chars}
```

### Step 6.5: Post-Completion Confirmation

After completing Step 6, you MUST state:

> "I confirm that I posted N inline comments on the following files: [list files]. If I posted 0 inline comments and had findings, this review has FAILED its primary purpose."

### Step 7: Submit Summary Review

```bash
# approved
gh pr review {pr_number} --approve --body "AutoSkillit research review passed. No blocking issues found."

# changes_requested (with UNPOSTABLE_FINDINGS)
# needs_human (with UNPOSTABLE_FINDINGS)
#
# When UNPOSTABLE_FINDINGS is non-empty, append the "Outside Diff Range" section
# to the verdict body. Build the body string dynamically:

VERDICT_LINE="{verdict-specific one-liner from above}"

OUTSIDE_SECTION=""
if [ ${#UNPOSTABLE_FINDINGS[@]} -gt 0 ]; then
  # Group unpostable findings by file, format as bullet list
  # Reuse the Tier 2 bullet-list format (120-char message truncation)
  # TRUNCATION GUARD: Cap the Outside Diff Range section at ~40,000 characters.
  # The GitHub review body has a hard 65,536-char limit (HTTP 422 on overflow,
  # no graceful degradation). Reserve headroom for the verdict line and formatting.
  # If truncated, append: "...and N more findings. See file-level comments for
  # critical items."
  OUTSIDE_SECTION=$(cat <<'SECTION'

### ⚠️ Outside Diff Range

These findings target lines not in the diff and could not be posted as inline comments:

**{file_path_1}**
- **L{line}** [{severity}/{dimension}]: {message, truncated to 120 chars}

**{file_path_2}**
- **L{line}** [{severity}/{dimension}]: {message, truncated to 120 chars}
SECTION
)
fi

BODY="${VERDICT_LINE}${OUTSIDE_SECTION}"

# Then post with the appropriate event flag:
gh pr review {pr_number} {--comment|--request-changes} --body "$BODY"
```

### Step 8: Write Summary and Emit Verdict

Save findings summary to `{{AUTOSKILLIT_TEMP}}/review-research-pr/summary_{pr_number}_{timestamp}.md`. (relative to the current working directory)

Output the verdict as the final line:

> **IMPORTANT:** Emit the structured output tokens as **literal plain text with no
> markdown formatting on the token names**. Do not wrap token names in `**bold**`,
> `*italic*`, or any other markdown. The adjudicator performs a regex match on the
> exact token name — decorators cause match failure.

```
verdict = {approved|changes_requested|needs_human}
```

Exit 0 in all normal cases (approved, needs_human, changes_requested).
Exit 1 only for unrecoverable tool-level errors.

## Output

- `verdict=approved` — No blocking issues; research PR is clear for human review
- `verdict=changes_requested` — Blocking issues found; recipe routes to next step
- `verdict=needs_human` — Uncertain trade-offs; human review requested via the authenticated GitHub user mention (derived at runtime)

Summary written to: `{{AUTOSKILLIT_TEMP}}/review-research-pr/summary_{pr_number}_{timestamp}.md`
