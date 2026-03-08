---
name: review-pr
description: Automated diff-scoped PR code review using parallel audit subagents. Posts inline GitHub review comments and submits a summary verdict. Use after a PR is opened to gate CI on review approval.
---

# Review PR Skill

Perform an automated, diff-scoped code review on an open GitHub PR using parallel
audit subagents. Posts inline review comments and submits a summary verdict. Called
by the recipe pipeline after `open_pr_step` opens the PR.

## Arguments

`/autoskillit:review-pr <feature-branch> <base-branch>`

- **feature-branch** — The feature branch containing the changes to review
- **base-branch** — The base branch the PR targets (e.g., "main")

## When to Use

- Called by the recipe orchestrator via `run_skill` after `open_pr_step`
- Can be invoked standalone to review any open PR

## Critical Constraints

**NEVER:**
- Create files outside `temp/review-pr/`
- Approve a PR that has `changes_requested` findings
- Post review comments when `gh` is unavailable — output `verdict=approved` and exit 0
- Review files outside the PR diff — scope all audit to diff content only
- Modify any source code

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

Parse two positional arguments: `feature_branch` and `base_branch`.

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
- Output `verdict=approved`
- Exit 0 (graceful degradation)

### Step 2: Get PR Diff and Metadata

```bash
# Get the PR diff
gh pr diff {pr_number}

# Get owner/repo
gh repo view --json nameWithOwner -q .nameWithOwner
```

Save the diff to `temp/review-pr/diff_{pr_number}.txt`.

### Step 3: Run Parallel Audit Subagents

Spawn parallel subagents (Task tool, model: sonnet) for each audit dimension.
Each subagent receives only the PR diff content (not the full codebase) and returns
findings in JSON format:

```json
[
  {
    "file": "path/to/file.py",
    "line": 42,
    "severity": "critical|warning|info",
    "dimension": "arch|tests|defense|bugs|cohesion|slop",
    "message": "Description of the finding"
  }
]
```

**Audit dimensions:**

1. **arch** — Architectural layering, import rule violations, domain separation.
   Check for: cross-layer imports, business logic in server layer, L0 importing L1+.

2. **tests** — Test quality: over-mocking, weak assertions, xdist safety, redundant tests.
   Check for: tests that assert nothing meaningful, broad mock patches, non-isolated state.

3. **defense** — Typed boundaries, error context preservation, validation at construction.
   Check for: missing type annotations at public boundaries, swallowed exceptions, late validation.

4. **bugs** — Diff checked against known recurring root causes.
   Check for: off-by-one errors, missing await, unhandled None, incorrect dict access.

5. **cohesion** — Structural symmetry, naming consistency, feature locality.
   Check for: inconsistent naming, scattered feature code, asymmetric patterns.

6. **slop** — Useless comments, dead code, backward-compat hacks left by AI.
   Check for: commented-out code, TODO without issue refs, over-verbose docstrings.

Subagent prompt template:

> You are reviewing a GitHub PR diff for [{dimension}] issues only.
> Scope: examine only the diff content provided. Do not fetch or read files outside the diff.
> Return a JSON array of findings. Each finding must have: file, line, severity (critical/warning/info), dimension, message.
> If no issues found, return an empty array [].
> Diff content:
> {diff_content}

### Step 4: Aggregate and Deduplicate Findings

1. Collect all subagent JSON responses
2. Deduplicate by `(file, line)` pairs — keep highest severity for each pair
3. Separate into:
   - `critical_findings` — severity == "critical"
   - `warning_findings` — severity == "warning"
   - `info_findings` — severity == "info"

### Step 5: Determine Verdict

- Any entry in `critical_findings` → `verdict = "changes_requested"`
- `critical_findings` empty, but uncertain trade-offs present → `verdict = "needs_human"`
- No actionable findings → `verdict = "approved"`

**Verdict logic:**
```
if len(critical_findings) > 0:
    verdict = "changes_requested"
elif len(warning_findings) > 3:
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
The `--field` approach creates one array entry per flag (not one object per comment), so it must
not be used for the `comments` array:

```bash
# Build comments JSON array — each element is a complete object
COMMENTS_JSON=$(jq -n --argjson findings "$FINDINGS_JSON" '
  $findings | map({
    path: .file,
    line: .line,
    side: "RIGHT",
    body: ("[" + .severity + "] " + .dimension + ": " + .message)
  })
')

# Build and post the full review payload via stdin
jq -n \
  --arg body "AutoSkillit PR Review — Verdict: {verdict}" \
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

**Fallback (if the `gh api` call fails):**

Post a per-file structured summary using `gh pr review --comment`. Group all findings by file and
format each group as a markdown section:

```
## AutoSkillit Review Findings

**Verdict:** {verdict}

### {file_path_1}
| Line | Severity | Dimension | Message |
|------|----------|-----------|---------|
| {line} | {severity} | {dimension} | {message} |
...

### {file_path_2}
| Line | Severity | Dimension | Message |
...
```

Post this structured comment with:
```bash
gh pr review {pr_number} --comment --body "{structured_findings_markdown}"
```

This preserves per-file grouping even when inline anchoring is unavailable.

### Step 7: Submit Summary Review

```bash
# approved
gh pr review {pr_number} --approve --body "AutoSkillit review passed. No blocking issues found."

# changes_requested
gh pr review {pr_number} --request-changes --body "AutoSkillit review found {N} blocking issues. See inline comments."

# needs_human
gh pr review {pr_number} --comment --body "AutoSkillit review: uncertain trade-offs detected. {escalation_user_mention} Please review. See inline comments."
```

### Step 8: Write Summary and Emit Verdict

Save findings summary to `temp/review-pr/summary_{pr_number}_{timestamp}.md`.

Output the verdict as the final line:

```
verdict={approved|changes_requested|needs_human}
```

Exit 0 in all normal cases (approved, needs_human, changes_requested).
Exit 1 only for unrecoverable tool-level errors.

## Output

- `verdict=approved` — No blocking issues; CI can proceed
- `verdict=changes_requested` — Blocking issues found; recipe routes to `resolve_review`
- `verdict=needs_human` — Uncertain trade-offs; human review requested via the authenticated GitHub user mention (derived at runtime)

Summary written to: `temp/review-pr/summary_{pr_number}_{timestamp}.md`
