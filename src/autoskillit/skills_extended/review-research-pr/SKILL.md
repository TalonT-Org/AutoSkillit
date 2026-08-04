---
name: review-research-pr
categories:
- research
description: Automated diff-scoped research PR review using parallel audit subagents aligned to research quality dimensions. Posts inline GitHub review comments and submits a summary verdict. Use after a research PR is opened to gate on review approval.
hooks:
  PreToolUse:
  - matcher: '*'
    hooks:
    - type: command
      command: 'echo ''[SKILL: review-research-pr] Reviewing research pull request...'''
      once: true
semantic_version: 1
semantic_requirements:
  logical_roles:
  - name: delegated-worker
    purpose: perform the named independent responsibility and return bounded evidence
  child_spawns:
  - role: delegated-worker
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

# Review Research PR Skill

Perform an automated, diff-scoped code review on an open GitHub research PR using parallel
audit subagents tuned to research quality dimensions. Posts inline review comments and submits
a summary verdict. Called by the recipe pipeline after `open_research_pr` opens the PR.

## Arguments

`/autoskillit:review-research-pr <worktree-path-or-feature-branch> <base-branch> [annotated_diff_path=<path>] [hunk_ranges_path=<path>] [valid_lines_path=<path>]`

- **worktree-path-or-feature-branch** — Either an absolute path to the research worktree
  (preferred; skill derives the feature branch from `git rev-parse --abbrev-ref HEAD`)
  or the feature branch name directly
- **base-branch** — The base branch the PR targets (e.g., "main")
- **annotated_diff_path** (optional) — absolute path to a pre-computed annotated diff file (produced by `annotate_pr_diff` run_python step). When provided and present, read from file instead of running python3.
- **hunk_ranges_path** (optional) — absolute path to a pre-computed hunk ranges JSON file (produced by `annotate_pr_diff` run_python step). When provided, loaded in Step 2.7 instead of parsing from the diff inline.
- **valid_lines_path** (optional) — absolute path to a pre-computed valid lines JSON file (produced by `annotate_pr_diff` run_python step). Contains exact `{filepath: [line_numbers]}` set. When provided, enables exact set-membership validation in Step 4.

## When to Use

- Called by the recipe orchestrator via `run_skill` after `open_research_pr`
- Can be invoked standalone to review any open research PR

## Critical Constraints

**NEVER:**
- Fabricate, invent, or embellish information not supported by the available evidence or code.

- Create files outside `{{AUTOSKILLIT_TEMP}}/review-research-pr/`
- Approve a PR that has `changes_requested` findings
- Post review comments when `gh` is unavailable — output `verdict=needs_human` and exit 0
- Review files outside the PR diff — scope all audit to diff content only
- Modify any source code
- Flag the absence of a clear experimental conclusion as a deficiency — inconclusive
  results are valid outcomes for research PRs (do not flag them)
- Detach child delegations instead of joining them (joining every child is required)
- Start independent child delegations sequentially
- Embed diff content inline in subagent prompts — always pass by path and instruct subagents to Read

**ALWAYS:**
- Find the PR by feature branch at invocation time (not from a pre-captured URL)
- Start all independent child delegations before awaiting any result to maximize concurrency
- Output `verdict=` on the final line
- Exit 0 in all normal cases; verdict drives recipe routing via on_result, not exit code
- Exit non-zero only for unrecoverable errors (e.g., gh CLI truly unavailable after graceful degradation has already output verdict=needs_human)
- Tag the authenticated GitHub user (`gh api user -q .login`) in escalation comments (`needs_human` verdict) — omit the mention silently if username derivation fails
- Spawn all subagents via `child delegation under the declared `sonnet` model-class policy`
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

### Step 1: Find the Open PR

```bash
gh pr list --head "$feature_branch" --base "$base_branch" \
  --json number,url -q '.[0] | "\(.number) \(.url)"'
```

If `gh` is unavailable or not authenticated, or no PR is found:
- Log "No PR found or gh unavailable — skipping review"
- Output `verdict=needs_human`
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
VALID_DIFF_LINES=""
# Parse @@ +start,count @@ headers from the diff to build a JSON map of
# {filepath: [[start, end], ...]} ranges.
if [ -n "${hunk_ranges_path:-}" ] && [ -f "$hunk_ranges_path" ]; then
    VALID_LINE_RANGES="$(cat "$hunk_ranges_path")"
fi
if [ -n "${valid_lines_path:-}" ] && [ -f "$valid_lines_path" ]; then
    VALID_DIFF_LINES="$(cat "$valid_lines_path")"
fi
```

`VALID_DIFF_LINES` is a JSON mapping `{filepath: [line_numbers]}` containing the exact set of
new-file line numbers present in the diff. When available, Step 4 uses set-membership for
validation. `VALID_LINE_RANGES` is used as fallback for interval checking.

### Step 3: Run Parallel Audit Subagents (SINGLE MESSAGE)

**Start ALL independent child delegations before awaiting any result — one per item — and join every child before synthesis.**

Do not output any prose between subagent dispatches. Immediately proceed to the next tool call.

Spawn parallel subagents via `child delegation under the declared `sonnet` model-class policy` for each research audit dimension.
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
> Read the annotated diff file at path: {annotated_diff_path}
> Each line is prefixed with [LNNN] markers. Use the [LNNN] number as the `line` value.

If `annotated_diff_path` is unset or the file does not exist, state "No annotated diff is
available for this run — evaluate the diff without [LNNN] anchors and approximate `line` from
context" instead of emitting a reference to a nonexistent path.

### Step 4: Aggregate and Deduplicate Findings

1. Collect all subagent JSON responses
2. Deduplicate by `(file, line)` pairs — keep highest severity for each pair
3. Partition findings using exact line validation when available:
   - When `VALID_DIFF_LINES` is non-empty (loaded in Step 2.7), use **set-membership**:
     a finding is postable if its `line` exists in `VALID_DIFF_LINES[file]`.
   - When `VALID_DIFF_LINES` is empty but `VALID_LINE_RANGES` is non-empty, fall back to
     **hunk-span interval checking**.
   - `FILTERED_FINDINGS`: findings that pass validation.
   - `UNPOSTABLE_FINDINGS`: findings that fail validation.
     Log a warning for each. Critical-severity unpostable findings are posted as
     file-level comments in Step 6. All unpostable findings appear in the Step 7 body.
   - If both `VALID_DIFF_LINES` and `VALID_LINE_RANGES` are empty, all findings are `FILTERED_FINDINGS`.
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

### Step 6: Publish the Complete Research Review

Resolve `repository` from the canonical caller-supplied `nameWithOwner`, require a positive
caller-supplied `pr_number`, and validate the caller-supplied `pr_head_sha` against
`^[0-9a-f]{40}$`. Require caller-supplied namespaced `logical_iteration` and the contained
caller-supplied `receipt_path`. The logical iteration must start with
`review-research-pr:`. The receipt must be under `${AUTOSKILLIT_TEMP}` and use the exact
`batch_review_response_${pr_number}.json` basename.

Prepare one complete `comments` array from `FILTERED_FINDINGS`, filtering at the publication
boundary to `severity == "critical"` or `severity == "warning"`. Preserve every validated
`[LNNN]` anchor from `VALID_DIFF_LINES` as repository-relative `path`, positive `line`, and
`side: "RIGHT"`. Put findings outside the diff in the complete review `body`, not in
`comments`.

Map `approved` to `APPROVE`, `needs_human` to `COMMENT`, and `changes_requested` to
`REQUEST_CHANGES`. Then call the structured publication tool once:

```text
post_pr_review(
  cwd: "$PWD",
  receipt_path: "$receipt_path",
  repository: "$repository",
  pr_number: "$pr_number",
  head_sha: "$pr_head_sha",
  logical_iteration: "$logical_iteration",
  event: "$REVIEW_EVENT",
  body: "$REVIEW_BODY",
  comments: "$COMMENTS_JSON",
  dry_run: false
)
```

Capture `review_operation_key`, `review_head_sha`, `review_post_state`, and
`review_receipt_path`. Continue only for a confirmed or reconciled final-success state.
Stop on ambiguous, throttled, terminal, prepared, posting, or verification-pending results.
The server owns identity, response classification, reconciliation, receipts, safe validation
handling, and cross-session pacing.

### Step 6.5: Post-Completion Confirmation

Confirm the authoritative receipt matches the requested repository, PR, head SHA, and
logical iteration, contains a positive review ID, and accounts for every original finding.

### Step 7: Submit Summary Review

The complete summary was included in the Step 6 body. Preserve the structured publication
fields for the recipe effect gate and make no additional GitHub review write.

### Step 8: Write Summary and Emit Verdict

Save findings summary to `{{AUTOSKILLIT_TEMP}}/review-research-pr/summary_{pr_number}_{timestamp}.md`. (relative to the current working directory)

Output the verdict as the final line:

> **IMPORTANT:** Emit the structured output tokens as **literal plain text with no
> markdown formatting on the token names**. Do not wrap token names in `**bold**`,
> `*italic*`, or any other markdown. Do not wrap the output block in a code fence.
> The adjudicator performs a regex match on the exact token name — decorators and
> code fences cause match failure.

```
review_operation_key = {authoritative operation key}
review_head_sha = {authoritative requested head}
review_post_state = {SUCCEEDED|RECONCILED}
review_receipt_path = {authoritative receipt path}
verdict = {approved|changes_requested|needs_human}
```

Exit 0 in all normal cases (approved, needs_human, changes_requested).
Exit 1 only for unrecoverable tool-level errors.

## Output

- `review_operation_key`, `review_head_sha`, `review_post_state`, and
  `review_receipt_path` identify the authoritative publication receipt
- `verdict=approved` — No blocking issues; research PR is clear for human review
- `verdict=changes_requested` — Blocking issues found; recipe routes to next step
- `verdict=needs_human` — Uncertain trade-offs; human review requested via the authenticated GitHub user mention (derived at runtime)

Summary written to: `{{AUTOSKILLIT_TEMP}}/review-research-pr/summary_{pr_number}_{timestamp}.md`
