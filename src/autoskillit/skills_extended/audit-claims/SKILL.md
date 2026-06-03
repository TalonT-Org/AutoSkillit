---
name: audit-claims
categories: [research]
backend_requirements: [claude-code]
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
- Post review comments when `gh` is unavailable — output `verdict=approved` and exit 0
- Review files outside the PR diff — scope all audit to diff content only
- Modify any source code
- Run deterministic diff annotation (claim positions are report-level, not line-level)
- Generate findings for `experimental` claims — they are self-evidencing by definition
- Run subagents in the background (`run_in_background: true` is prohibited)
- Issue subagent Task calls sequentially — ALL must be in a single parallel message

**ALWAYS:**
- Use the explicit `pr_url` argument instead of re-discovering via `gh pr list`
- Output `verdict=` on the final line
- Exit 0 in all normal cases; verdict drives recipe routing via on_result, not exit code
- Exit non-zero only for unrecoverable errors (e.g., gh CLI truly unavailable after graceful degradation)
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
- Output `verdict=approved`
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
> Diff content for section [{section_name}]:
> {section_diff_content}

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
> {evidence_rules_for_type}
>
> If all claims have adequate evidence, return an empty array [].
> Claims to check:
> {claims_json}
> Full PR diff:
> {diff_content}

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

### Step 6: Post Inline Review Comments

Build review comment bodies for each critical and warning finding. Use the `line` and
`side` fields (modern GitHub Reviews API — not the deprecated `position` field).

```bash
COMMENTS_JSON=$(jq -n --argjson findings "$FINDINGS" '
  $findings
  | map(select(.line != null and (.line | type) == "number" and .line > 0))
  | map({
    path: .file,
    line: .line,
    side: "RIGHT",
    body: ("[" + .severity + "] " + .dimension + ": " + .message)
  })
')

jq -n \
  --arg body "AutoSkillit Citation Audit — Verdict: {verdict}" \
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

If the batch POST returns HTTP 200, treat the review as successfully posted regardless
of response body content. Do NOT inspect the response body for a `comments` array —
GitHub's review API does not echo back the submitted comments, so any length check
would always read 0 and falsely trigger Tier 1 fallback.

If the batch POST returns HTTP 422 and the error message mentions "review" or "author",
the PR is self-authored. Retry the same request with event `COMMENT` instead of
`REQUEST_CHANGES` or `APPROVE`.

**Fallback Tier 1 — Individual Comments (if batch POST fails):**

Track success and failure counts across all individual post attempts:

```bash
COMMIT_ID=$(gh pr view {pr_number} --json headRefOid -q .headRefOid)
tier1_success=0
tier1_failed=0

# For each finding:
if gh api /repos/{owner}/{repo}/pulls/{pr_number}/comments \
  --method POST \
  --field path="{finding.file}" \
  --field line={finding.line} \
  --field side="RIGHT" \
  --field commit_id="$COMMIT_ID" \
  --field body="[{finding.severity}] {finding.dimension}: {finding.message}"; then
  tier1_success=$((tier1_success + 1))
  sleep 1  # Rate-limit discipline: 1s between mutating calls
else
  # On HTTP 422 (invalid line number), retry as file-level comment
  if gh api /repos/{owner}/{repo}/pulls/{pr_number}/comments \
    --method POST \
    --field path="{finding.file}" \
    --field subject_type="file" \
    --field commit_id="$COMMIT_ID" \
    --field body="[{finding.severity}] {finding.dimension}: {finding.message} (line {finding.line})"; then
    tier1_success=$((tier1_success + 1))
  else
    tier1_failed=$((tier1_failed + 1))
    echo "Warning: failed to post individual comment for {finding.file}:{finding.line}" >&2
  fi
fi

echo "Fallback Tier 1: $tier1_success succeeded, $tier1_failed failed"
```

Proceed to Fallback Tier 2 only if `tier1_success == 0` (all individual posts failed).
If at least one post succeeded, skip Tier 2.

**Fallback Tier 2 — DEGRADED: Bullet-List Summary Dump (if all individual posts fail):**

Before posting, state:

> "FALLBACK: I was unable to post inline comments. Posting summary as review body instead. This is a DEGRADED review."

```bash
gh pr review {pr_number} --comment --body "{summary_markdown}"
```

**File-Level Comments for Critical Unpostable Findings:**

After the batch review POST succeeds (or after Tier 1 individual posting completes),
post file-level comments for each **critical-severity** finding in `UNPOSTABLE_FINDINGS`.
These use the individual comments endpoint with `subject_type: "file"` — this parameter
is NOT valid on the batch Reviews API `comments[]` array.

```bash
COMMIT_ID=$(gh pr view {pr_number} --json headRefOid -q .headRefOid)

# For each CRITICAL finding in UNPOSTABLE_FINDINGS:
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

### Step 6.5: Post-Completion Confirmation

After completing Step 6, you MUST state:

> "I confirm that I posted N inline comments on the following files: [list files]. If I posted 0 inline comments and had findings, this review has FAILED its primary purpose."

### Step 7: Submit Summary Review

```bash
# approved
gh pr review {pr_number} --approve --body "AutoSkillit citation audit passed. No unsupported claims found."

# changes_requested
gh pr review {pr_number} --request-changes --body "AutoSkillit citation audit found {N} claims lacking evidence. See inline comments."

# needs_human
gh pr review {pr_number} --comment --body "AutoSkillit citation audit: uncertain citation requirements detected. Please review. See inline comments."
```

### Step 8: Write Summary and Emit Verdict

Save findings summary to `{{AUTOSKILLIT_TEMP}}/audit-claims/summary_{pr_number}_{timestamp}.md`.

Output the verdict as the final line:

> **IMPORTANT:** Emit the structured output tokens as **literal plain text with no
> markdown formatting on the token names**. Do not wrap token names in `**bold**`,
> `*italic*`, or any other markdown. Do not wrap the output block in a code fence.
> The adjudicator performs a regex match on the exact token name — decorators and
> code fences cause match failure.

```
verdict = {approved|changes_requested|needs_human}
```

Exit 0 in all normal cases (approved, needs_human, changes_requested).
Exit 1 only for unrecoverable tool-level errors.

## Temp File Layout

```
{{AUTOSKILLIT_TEMP}}/audit-claims/
├── diff_{pr_number}.txt
├── claims_{pr_number}.json          (Phase 1 output)
├── findings_{pr_number}.json        (Phase 2 output)
└── summary_{pr_number}_{ts}.md
```

## Output

- `verdict=approved` — No unsupported claims; citation integrity is clear
- `verdict=changes_requested` — Missing citations or unsupported claims found; recipe routes to resolve step
- `verdict=needs_human` — Ambiguous citation requirements; human review requested

Summary written to: `{{AUTOSKILLIT_TEMP}}/audit-claims/summary_{pr_number}_{timestamp}.md`
