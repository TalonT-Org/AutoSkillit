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

Save the diff to `temp/review-pr/diff_{pr_number}.txt`.

### Step 2.7: Parse Diff Hunk Ranges

Parse the saved diff to extract valid new-file line ranges per file. These ranges
define which `line` values are valid for the GitHub Reviews API batch POST.

For each `+++ b/{path}` header in the diff, collect all subsequent `@@ -a,b +c,d @@`
hunk headers. From each hunk header, `c` is the starting new-file line and `d` is
the hunk's new-file line count (if absent, count is 1). When `d=0` (pure deletion
hunk, e.g. `+0,0`), skip the hunk — it has no new-file lines to anchor to.
Otherwise the valid range for that hunk is `[c, c + d - 1]`.

Build `VALID_LINE_RANGES` — a map of file path → list of `(start, end)` tuples.
Store in memory for use in Steps 4 and 6.
If the diff is empty or parsing fails, leave `VALID_LINE_RANGES` empty (no filtering).

### Step 2.5: Deletion Context Pre-Computation

Before spawning audit subagents, compute the deletion context for the parallel
deletion regression audit. This step runs best-effort: if any command
fails (e.g., no local git checkout available), set `deletion_context = null` and
the deletion regression dimension is skipped in the parallel audit phase.

```bash
# 1. Get the PR's head and base refs
PR_HEAD=$(gh pr view {pr_number} --json headRefName -q .headRefName)
PR_BASE=$(gh pr view {pr_number} --json baseRefName -q .baseRefName)

# 2. Derive merge base via GitHub compare API (no local clone required)
MERGE_BASE=$(
  gh api repos/{owner}/{repo}/compare/${PR_BASE}...${PR_HEAD} \
    --jq '.merge_base_commit.sha' 2>/dev/null
)

# 3. Fetch the base branch locally to run git diff
git fetch origin ${PR_BASE} 2>/dev/null

# 4. Files deleted from base since branch point
DELETED_FILES=$(
  git diff --name-only --diff-filter=D ${MERGE_BASE} origin/${PR_BASE} 2>/dev/null
)

# 5. PR's changed files (from gh pr view, already available)
PR_FILES=$(gh pr view {pr_number} --json files -q '[.files[].path] | join(" ")' 2>/dev/null)

# 6. Symbols removed from files this PR modifies
if [ -n "$PR_FILES" ] && [ -n "$MERGE_BASE" ]; then
  DELETED_SYMBOLS=$(
    git diff --diff-filter=M ${MERGE_BASE} origin/${PR_BASE} -- ${PR_FILES} 2>/dev/null \
      | grep '^-' \
      | grep -E '^-(def |class |async def )' \
      | sed 's/^-//' \
      | sort -u
  )
else
  DELETED_SYMBOLS=""
fi
```

Store as `deletion_context`:
```python
deletion_context = {
    "merge_base": MERGE_BASE,
    "deleted_files": DELETED_FILES.splitlines(),        # list of paths
    "deleted_symbols": DELETED_SYMBOLS.splitlines(),    # list of "def foo", "class Bar"
    "pr_base": PR_BASE,
}
```

If `MERGE_BASE` is empty or any git command fails, set `deletion_context = null`.
The parallel deletion regression audit is skipped when `deletion_context` is null.

### Step 3: Run Parallel Audit Subagents

Spawn parallel subagents (Task tool, model: sonnet) for each audit dimension.
Each subagent receives only the PR diff content (not the full codebase) and returns
findings in JSON format:

```json
[
  {
    "file": "path/to/file.py",
    "line": 42,
    "dimension": "arch|tests|defense|bugs|cohesion|slop|deletion_regression",
    "severity": "critical|warning|info",
    "message": "Description of the finding",
    "requires_decision": false
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

7. **deletion_regression** — Deliberate deletion regression check: severity: "critical",
   requires_decision: false for every finding. Cross-references the PR diff against
   `deletion_context` (deleted files and symbols computed in Step 2.5) to detect code
   that was intentionally removed from the base branch but re-added by this PR.
   Only spawned when `deletion_context` is non-null.

Subagent prompt template (dimensions 1–6):

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
> The `line` value must be a line number visible in the diff hunks shown above.
> Report only lines from `+` (added) or ` ` (context) lines within a `@@` hunk block.
> Do not report absolute file line numbers that do not appear in this diff.
> If the finding cannot be anchored to a line visible in the diff, use the nearest
> `+` or context line in the same hunk.
>
> If no issues found, return an empty array [].
> Diff content:
> {diff_content}

Subagent prompt template (dimension 7 — deletion_regression, only when `deletion_context` is non-null):

> You are checking a GitHub PR diff for DELETION REGRESSIONS only.
> A deletion regression is when a PR reintroduces code (a file, function, or class)
> that was deliberately deleted from the base branch after the PR was branched.
>
> Deletion context (items deleted from {pr_base} since this PR branched at {merge_base}):
> Deleted files: {deletion_context.deleted_files}
> Deleted symbols: {deletion_context.deleted_symbols}
>
> PR diff:
> {diff_content}
>
> Instructions:
> - For each deleted file in the deletion context: check if the diff adds or recreates it
>   (look for `+++ b/{file}` or `diff --git a/{file}` with added lines).
> - For each deleted symbol (e.g., "def foo", "class Bar"): check if the diff adds it back
>   (look for `+def foo`, `+class Bar`, or `+async def foo` lines in the diff).
> - For each regression found, return a finding with:
>   - severity: "critical"
>   - dimension: "deletion_regression"
>   - requires_decision: false
>   - message: "Deletion regression: '{name}' was deliberately deleted from {pr_base}
>     but this PR reintroduces it. Remove it."
> - If no regressions found, return [].
>
> Return a JSON array of findings.

### Step 4: Aggregate and Deduplicate Findings

1. Collect all subagent JSON responses
2. Deduplicate by `(file, line)` pairs — keep highest severity for each pair
3. Partition findings against `VALID_LINE_RANGES` (built in Step 2.7):
   - `FILTERED_FINDINGS`: findings whose `(file, line)` falls within any hunk range for
     that file. These are in-hunk and safe to post as inline comments in Step 6.
   - `UNPOSTABLE_FINDINGS`: findings whose `line` is not in any hunk range for their file.
     Log a warning for each. These are included in the summary fallback body only.
   - If `VALID_LINE_RANGES` is empty, all findings are `FILTERED_FINDINGS`.
4. Apply verdict logic (Step 5) to ALL findings (`FILTERED_FINDINGS` + `UNPOSTABLE_FINDINGS`
   combined), so unpostable findings still contribute to the `changes_requested` verdict.
5. Bucket by actionability (applied to combined findings):
   - `actionable_findings` — requires_decision=false AND severity in ("critical", "warning")
   - `decision_findings` — requires_decision=true (any severity)
   - `info_findings` — severity == "info" AND requires_decision=false

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

Build `COMMENTS_JSON` from `FILTERED_FINDINGS` only (not `UNPOSTABLE_FINDINGS`). All findings
in `FILTERED_FINDINGS` have been validated against `VALID_LINE_RANGES` in Step 4, so they are
safe to post as inline comments.

Build a proper JSON payload where each comment is a complete object, then post via `--input -`.
The `--field` approach creates one array entry per flag (not one object per comment), so it must
not be used for the `comments` array:

```bash
# Build comments JSON array from FILTERED_FINDINGS only
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

**Fallback Tier 1 — Individual Comments (if batch POST fails):**

Attempt to post each finding from `FILTERED_FINDINGS` individually via:

```bash
COMMIT_ID=$(gh pr view {pr_number} --json headRefOid -q .headRefOid)

# For each finding in FILTERED_FINDINGS:
gh api /repos/{owner}/{repo}/pulls/{pr_number}/comments \
  --method POST \
  --field path="{finding.file}" \
  --field line={finding.line} \
  --field side="RIGHT" \
  --field commit_id="$COMMIT_ID" \
  --field body="[{finding.severity}] {finding.dimension}: {finding.message}"
```

Individual POSTs are not atomic — one failure does not block others.
If at least one per-finding comment succeeds, proceed to Step 7.

**Fallback Tier 2 — Bullet-List Summary Dump (if all individual posts fail):**

Post ALL findings (`FILTERED_FINDINGS` + `UNPOSTABLE_FINDINGS`) via:

```bash
gh pr review {pr_number} --comment --body "{summary_markdown}"
```

Format each file's findings as a bullet list (not a markdown table):

```
## AutoSkillit Review Findings

**Verdict:** {verdict}

### path/to/file.py
- **L{line}** [{severity}/{dimension}]: {message, truncated to 120 chars}

### path/to/other.py
- **L{line}** [{severity}/{dimension}]: {message, truncated to 120 chars}
```

This bullet-list format avoids horizontal overflow from long message content.

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
