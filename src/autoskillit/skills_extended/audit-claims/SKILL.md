---
name: audit-claims
categories: [research]
uses_capabilities: [agent_model]
description: >
  Parallel subagent-driven claim extraction and citation integrity audit for
  research PRs. Extracts claims by section, matches against available evidence,
  classifies unsupported claims as findings, and emits a verdict for recipe routing.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: audit-claims] Auditing research claims for citation integrity...'"
          once: true
---

# Audit Claims Skill

Perform a two-phase citation integrity audit on an open GitHub research PR using
parallel subagents. Phase 1 extracts claims by report section; Phase 2 matches
each claim against available evidence and generates findings. Posts inline review
comments and emits a verdict for recipe routing.

## Arguments

`/autoskillit:audit-claims <worktree_path> <base_branch> <pr_url>`

- **worktree_path** — Absolute path to the research worktree (skill derives
  `feature_branch` from `git rev-parse --abbrev-ref HEAD` inside it)
- **base_branch** — The base branch the PR targets (e.g., "main")
- **pr_url** — Explicit PR URL passed by the recipe (avoids re-discovering the PR)

## When to Use

- Called by the research recipe orchestrator after `review_research_pr`
- Both read-only gates complete before any resolution step begins
- Can be invoked standalone to audit citation integrity for any open research PR

## Critical Constraints

**NEVER:**
- Fabricate, invent, or embellish information not supported by the available evidence or code.

- Create files outside `{{AUTOSKILLIT_TEMP}}/audit-claims/`
- Approve a PR that has `changes_requested` findings
- Post review comments when `gh` is unavailable — output `verdict=needs_human` and exit 0
- Review files outside the PR diff — scope all audit to diff content only
- Modify any source code
- Run deterministic diff annotation (claim positions are report-level, not line-level)
- Generate findings for `experimental` claims — they are self-evidencing by definition
- Run subagents in the background (`run_in_background: true` is prohibited)
- Issue subagent Task calls sequentially — ALL must be in a single parallel message
- Embed diff content inline in subagent prompts — always pass by path and instruct subagents to Read

**ALWAYS:**
- Use the explicit `pr_url` argument instead of re-discovering via `gh pr list`
- Output `verdict=` on the final line
- Exit 0 in all normal cases; verdict drives recipe routing via on_result, not exit code
- Exit non-zero only for unrecoverable errors (e.g., gh CLI truly unavailable after graceful degradation has already output verdict=needs_human)
- Spawn all subagents via `Agent(model="sonnet")`
- Deduplicate findings by (file, line) pairs before posting
- Issue all Task calls in a single message to maximize parallelism

## Workflow

### Step 0: Validate Arguments

Parse three positional arguments: `worktree_path`, `base_branch`, `pr_url`.

Derive `feature_branch`:

```bash
if [ -d "$worktree_path" ]; then
  feature_branch=$(git -C "$worktree_path" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
else
  echo "Error: worktree_path '$worktree_path' does not exist or is not a directory" >&2
  exit 1
fi
```

If `pr_url` is missing or positional args are insufficient, abort with:
`"Usage: /autoskillit:audit-claims <worktree_path> <base_branch> <pr_url>"`

### Step 1: Use the Explicit PR URL

Parse `pr_number` from `pr_url` (last path segment).

Get owner/repo (must run inside the worktree to resolve the correct repository):
```bash
gh repo view --json nameWithOwner -q .nameWithOwner -C "$worktree_path"
```

If `gh` is unavailable or not authenticated:
- Log "gh unavailable — skipping citation audit"
- Output `verdict=needs_human`
- Exit 0 (graceful degradation)

### Step 2: Get PR Diff

```bash
mkdir -p {{AUTOSKILLIT_TEMP}}/audit-claims
gh pr diff {pr_number} > {{AUTOSKILLIT_TEMP}}/audit-claims/diff_{pr_number}.txt
```

Save the diff to `{{AUTOSKILLIT_TEMP}}/audit-claims/diff_{pr_number}.txt` (relative to the
current working directory). If this file already exists from a prior retry, overwrite it using a Bash redirect (`gh pr diff {pr_number} > path`) — the redirect clobbers the file safely.

Do NOT run deterministic diff annotation — claim positions are report-level, not
line-level. Subagents use section structure, not line markers.

### Step 3: Two-Phase Claim Analysis

#### Phase 1 — Claim Extraction (parallel subagents by report section) (SINGLE MESSAGE)

**Issue ALL Task tool calls in a single message — one per item — so they execute in parallel. Do NOT iterate across multiple turns.**

Do not output any prose between subagent dispatches. Immediately proceed to the next tool call.

Divide the diff by top-level markdown section: `## Executive Summary`, `## Results`,
`## Methodology`, `## Discussion`, `## Limitations`, and any other top-level `##` section.

After dividing the diff by section, write each section's diff chunk to its own file at
`{{AUTOSKILLIT_TEMP}}/audit-claims/section_diff_{section_slug}_{pr_number}.txt` where
`{section_slug}` is the section name lowercased with spaces replaced by underscores (e.g.
`executive_summary`, `results`, `methodology`). Use `jq -n` or the Write tool — do not use
inline Python one-liners or heredoc scripts with `open()` (these are blocked by the sandbox,
per the existing convention at Step 1 of this file). Then bind `{section_diff_path}` to that
section's file path when building each subagent's prompt.

Launch one subagent via `Agent(model="sonnet")` per section containing `+` diff lines.
Each subagent returns a JSON array of extracted claims:

```json
[{
  "file": "research/report.md",
  "line": 42,
  "claim_text": "Our method reduces latency by 40% compared to baseline X",
  "claim_type": "experimental|external|methodological|comparative",
  "section": "Results"
}]
```

**Claim type guidance:**
- `experimental` — derived from the experiment's own measured data; self-evidencing
- `external` — references domain knowledge, papers, web data, or datasets not in this PR
- `methodological` — asserts that a methodology choice is appropriate or valid
- `comparative` — compares to prior work, published results, or other baselines

Subagent prompt template:

> You are extracting factual claims from a section of a GitHub PR diff for a research report.
> Section: [{section_name}]
> Scope: examine only the `+` lines in the diff content provided.
> Return a JSON array of claims. Each claim must have:
>   file, line (new-file line number from the diff), claim_text, claim_type
>   (one of: experimental, external, methodological, comparative), section.
>
> Claim type guidance:
> - experimental: derived from experiment data in this PR (self-evidencing)
> - external: references external knowledge, papers, or datasets not in this PR
> - methodological: asserts a methodology choice is appropriate or valid
> - comparative: compares to prior work, published results, or baselines
>
> If no claims found in this section, return an empty array [].
> Read the section diff from: {section_diff_path}

Aggregate all extracted claims from all subagents. Save to
`{{AUTOSKILLIT_TEMP}}/audit-claims/claims_{pr_number}.json`. Use `jq -n` or the Write tool. If the file already exists from a prior retry, either read it first (to satisfy the Write tool guard) or use a Bash redirect (`jq -n ... > path`).

#### Phase 2 — Evidence Matching (parallel subagents by claim type) (SINGLE MESSAGE)

**Issue ALL Task tool calls in a single message — one per item — so they execute in parallel. Do NOT iterate across multiple turns.**

Do not output any prose between subagent dispatches. Immediately proceed to the next tool call.

Group extracted claims by `claim_type`. Launch one subagent via `Agent(model="sonnet")`
per non-empty group. Each subagent receives the claim list and the full PR diff, and
returns findings:

```json
[{
  "file": "research/report.md",
  "line": 42,
  "dimension": "external|methodological|comparative",
  "severity": "critical|warning|info",
  "message": "Claim references [Paper X] but no citation appears in the diff",
  "requires_decision": false
}]
```

**Evidence rules per claim type:**
- `experimental` — always self-evidencing; no finding generated (skip this group)
- `external` — requires a citation `[N]` or inline reference within the report; absence
  of citation is `warning`; absence for a specific numeric comparison is `critical`
- `methodological` — requires a rationale or supporting reference; absence is `warning`
- `comparative` — requires attribution; "comparable to state-of-the-art" without citation
  is `critical`

When building each subagent's prompt, bind:
- `{claims_json_path}` to `{{AUTOSKILLIT_TEMP}}/audit-claims/claims_{pr_number}.json`
  (the file written in Phase 1 above)
- `{diff_file_path}` to `{{AUTOSKILLIT_TEMP}}/audit-claims/diff_{pr_number}.txt`
  (the file written in Step 2 above)

Subagent prompt template:

> You are checking citation evidence for [{claim_type}] claims in a GitHub PR diff.
> For each claim, determine whether adequate supporting evidence exists in the diff.
> Return a JSON array of findings for claims that lack evidence. Each finding must have:
>   file, line, severity (critical/warning/info), dimension (the claim_type value),
>   message, requires_decision (boolean).
>
> Set requires_decision=true ONLY when the correct path forward is genuinely ambiguous
> and cannot be determined without human judgment.
> Set requires_decision=false for all cases with a clear fix (add citation, qualify claim,
> remove claim).
>
> Evidence rules for [{claim_type}]:
> Apply the rule for your claim type as enumerated under "Evidence rules per claim type"
> earlier in this skill (skipped entirely for `experimental` claims; warning for missing
> `external` citations or for specific numeric comparisons in `external` claims being
> critical; warning for missing `methodological` rationale; critical for unattributed
> `comparative` claims such as "comparable to state-of-the-art" without citation).
>
> If all claims have adequate evidence, return an empty array [].
> Claims to check:
> Read the claims file at: {claims_json_path}
> Full PR diff:
> Read the diff file at: {diff_file_path}

Save findings to `{{AUTOSKILLIT_TEMP}}/audit-claims/findings_{pr_number}.json`. Use `jq -n` or the Write tool. If the file already exists from a prior retry, either read it first (to satisfy the Write tool guard) or use a Bash redirect (`jq -n ... > path`).

### Step 4: Aggregate and Deduplicate Findings

1. Collect all Phase 2 subagent JSON responses
2. Deduplicate by `(file, line)` pairs — keep highest severity for each pair
3. Partition by postability: validate each finding's `line` against the PR diff hunks.
   Findings whose line number falls outside a diff hunk are `UNPOSTABLE_FINDINGS` —
   they cannot be posted as inline comments via the batch Reviews API.
   Findings with valid diff-hunk lines are `FILTERED_FINDINGS`.
4. Bucket `FILTERED_FINDINGS` by actionability:
   - `actionable_findings` — requires_decision=false AND severity in ("critical", "warning")
   - `decision_findings` — requires_decision=true (any severity)
   - `info_findings` — severity == "info" AND requires_decision=false

### Step 4.5: Echo Primary Obligation

After aggregating all subagent findings, before proceeding to verdict or posting, you MUST state aloud:

> "I have N findings. My primary job is to post inline comments on specific code lines for each finding. I must use the GitHub Reviews API to leave comments anchored to the exact lines in the diff."

This is not optional. Do not proceed to Step 5 without stating this.

### Step 5: Determine Verdict

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

### Step 6: Publish the Complete Citation Audit

Resolve `repository` from the canonical caller-supplied `nameWithOwner`, require a positive
caller-supplied `pr_number`, and validate the caller-supplied `pr_head_sha` against
`^[0-9a-f]{40}$`. Require caller-supplied namespaced `logical_iteration` and the contained
caller-supplied `receipt_path`. The logical iteration must start with `audit-claims:`.
The receipt must be under `${AUTOSKILLIT_TEMP}` and use the exact
`batch_review_response_${pr_number}.json` basename.

Prepare one complete `comments` array from `FINDINGS`, filtering to critical and warning
entries with a repository-relative path and positive numeric line. Preserve `side: "RIGHT"`.
Put findings without a valid line in the complete review `body`, not in `comments`.

Map `approved` to `APPROVE`, `needs_human` to `COMMENT`, and `changes_requested` to
`REQUEST_CHANGES`. Call the structured publication tool once:

```text
post_pr_review(
  cwd: "$worktree_path",
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
The complete summary is already in the publication body; make no additional review write.

### Step 6.5: Confirm the Authoritative Receipt

Confirm the receipt matches repository, PR, head SHA, and logical iteration; contains a
positive review ID; and accounts for every original finding.

### Step 7: Preserve Publication Identity

Carry the four structured publication fields unchanged into the final output block for the
recipe effect gate.

### Step 8: Write Summary and Emit Verdict

Save findings summary to `{{AUTOSKILLIT_TEMP}}/audit-claims/summary_{pr_number}_{timestamp}.md`.

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

## Temp File Layout

```
{{AUTOSKILLIT_TEMP}}/audit-claims/
├── diff_{pr_number}.txt
├── section_diff_{section_slug}_{pr_number}.txt  (Phase 1 intermediate, one per section)
├── claims_{pr_number}.json          (Phase 1 output)
├── findings_{pr_number}.json        (Phase 2 output)
└── summary_{pr_number}_{ts}.md
```

## Output

- `review_operation_key`, `review_head_sha`, `review_post_state`, and
  `review_receipt_path` identify the authoritative publication receipt
- `verdict=approved` — No unsupported claims; citation integrity is clear
- `verdict=changes_requested` — Missing citations or unsupported claims found; recipe routes to resolve step
- `verdict=needs_human` — Ambiguous citation requirements; human review requested

Summary written to: `{{AUTOSKILLIT_TEMP}}/audit-claims/summary_{pr_number}_{timestamp}.md`
