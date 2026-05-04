---
name: review-pr
categories: [github]
description: Automated diff-scoped PR code review using parallel audit subagents. Posts inline GitHub review comments and submits a summary verdict. Use after a PR is opened to gate CI on review approval.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: review-pr] Reviewing pull request...'"
          once: true
---

# Review PR Skill

Perform an automated, diff-scoped code review on an open GitHub PR using parallel
audit subagents. Posts inline review comments and submits a summary verdict. Called
by the recipe pipeline after `open_pr_step` opens the PR.

## Arguments

`/autoskillit:review-pr <feature-branch> <base-branch> [annotated_diff_path=<path>] [hunk_ranges_path=<path>] [diff_metrics_path=<path>]`

- **feature-branch** — The feature branch containing the changes to review
- **base-branch** — The base branch the PR targets (e.g., "main")
- **annotated_diff_path** (optional) — absolute path to a pre-computed annotated diff file (produced by `annotate_pr_diff` run_python step). When provided and present, read from file instead of running python3.
- **hunk_ranges_path** (optional) — absolute path to a pre-computed hunk ranges JSON file (produced by `annotate_pr_diff` run_python step). When provided and present, read from file instead of running python3.
- **diff_metrics_path** (optional) — absolute path to a pre-computed diff metrics JSON file (produced by `annotate_pr_diff` run_python step). Contains `dispatch_agents` list that determines which audit dimensions to spawn. When absent, all 6 standard agents are dispatched.

## When to Use

- Called by the recipe orchestrator via `run_skill` after `open_pr_step`
- Can be invoked standalone to review any open PR

## Critical Constraints

**NEVER:**
- Create files outside `{{AUTOSKILLIT_TEMP}}/review-pr/`
- Approve a PR that has `changes_requested` findings
- Post review comments when `gh` is unavailable — output `verdict=approved` and exit 0
- Review files outside the PR diff — scope all audit to diff content only
- Modify any source code
- Run subagents in the background (`run_in_background: true` is prohibited)

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

### Step 1.5: Fetch Prior Review Thread Context

This step is always executed when a PR is found. It builds prior-thread context for
suppressing already-resolved findings on re-reviews and for focusing subagents on
known-unresolved items.

Fetch all review threads using cursor-based pagination (same GraphQL query as
resolve-review Step 2, but also fetching `comments(first:5)` to see the original
finding and up to 4 replies):

```graphql
query($owner:String!, $repo:String!, $number:Int!, $after:String) {
  repository(owner:$owner, name:$repo) {
    pullRequest(number:$number) {
      reviewThreads(first:100, after:$after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          isResolved
          path
          line
          originalLine
          comments(first:5) {
            nodes { databaseId body author { login } }
          }
        }
      }
    }
  }
}
```

Build two lists from the thread nodes. For each thread, resolve line via:
`line = thread.get("line") or thread.get("originalLine")` — `line` is nullable for
outdated threads where new commits have shifted the diff anchor; `originalLine` is
the stable fallback.

If both `line` and `originalLine` are null (file-level comment thread from a prior review),
skip this thread — do not add it to `prior_resolved_findings` or `prior_unresolved_findings`.
File-level threads have no line anchor and must not suppress line-anchored findings via the
±5 proximity match.

**`prior_resolved_findings`** — threads meeting EITHER condition, AND where the first comment body
contains `[critical]` or `[warning]` (autoskillit-posted finding):
- `isResolved=true` (ACCEPT/REJECT findings resolved by resolve-review), OR
- Any reply comment (`comments[1:]`) contains `<!-- autoskillit:resolved` (DISCUSS/INFO findings
  acknowledged by resolve-review but intentionally left unresolved)

Check for the marker using:
```python
RESOLVED_MARKER_RE = re.compile(r"<!--\s*autoskillit:resolved\b")

has_marker_reply = any(
    RESOLVED_MARKER_RE.search(c.get("body", ""))
    for c in thread_comments[1:]
)

if thread.get("isResolved") or has_marker_reply:
    prior_resolved_findings.append({"file": path, "line": line, "body": first_body})
else:
    prior_unresolved_findings.append({"file": path, "line": line, "body": first_body})
```

```json
[{"file": "src/foo.py", "line": 42, "body": "[critical] arch: ..."}]
```

**`prior_unresolved_findings`** — threads where `isResolved=false` AND no reply contains the
`<!-- autoskillit:resolved` marker AND the first comment contains `[critical]` or `[warning]`:
```json
[{"file": "src/bar.py", "line": 17, "body": "[warning] tests: ..."}]
```

Save to: `{{AUTOSKILLIT_TEMP}}/review-pr/prior_threads_{pr_number}.json`

If the GraphQL call fails (token scope, network): set both lists to `[]` and log a warning.
Prior-thread context is best-effort — failure must not abort the review.

### Step 2: Get PR Diff and Metadata

```bash
# Get the PR diff
gh pr diff {pr_number}

# Get owner/repo
gh repo view --json nameWithOwner -q .nameWithOwner
```

Save the diff to `{{AUTOSKILLIT_TEMP}}/review-pr/diff_{pr_number}.txt`. (relative to the current working directory)

### Step 2.7: Deterministic Diff Annotation

Read pre-computed annotated diff and hunk ranges from disk when available:

```bash
ANNOTATED_DIFF=""
VALID_LINE_RANGES="{}"
if [ -n "${annotated_diff_path:-}" ] && [ -f "$annotated_diff_path" ]; then
    ANNOTATED_DIFF="$(cat "$annotated_diff_path")"
fi
if [ -n "${hunk_ranges_path:-}" ] && [ -f "$hunk_ranges_path" ]; then
    VALID_LINE_RANGES="$(cat "$hunk_ranges_path")"
fi
```

`VALID_LINE_RANGES` is a JSON mapping file paths to valid hunk line ranges. Load it in Step 4
for filtering. If `annotated_diff_path` or `hunk_ranges_path` are absent, leave
`ANNOTATED_DIFF` and `VALID_LINE_RANGES` empty (no filtering).

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
REMOTE=$(git remote get-url upstream >/dev/null 2>&1 && echo upstream || echo origin)
git fetch "$REMOTE" ${PR_BASE} 2>/dev/null

# 4. Files deleted from base since branch point
DELETED_FILES=$(
  git diff --name-only --diff-filter=D ${MERGE_BASE} "$REMOTE"/${PR_BASE} 2>/dev/null
)

# 5. PR's changed files (from gh pr view, already available)
PR_FILES=$(gh pr view {pr_number} --json files -q '[.files[].path] | join(" ")' 2>/dev/null)

# 6. Symbols removed from files this PR modifies
if [ -n "$PR_FILES" ] && [ -n "$MERGE_BASE" ]; then
  DELETED_SYMBOLS=$(
    git diff --diff-filter=M ${MERGE_BASE} "$REMOTE"/${PR_BASE} -- ${PR_FILES} 2>/dev/null \
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

### Step 2.9: Diff-Size Adaptive Agent Selection

Read the pre-computed diff metrics when available:

```bash
DISPATCH_AGENTS=""
if [ -n "${diff_metrics_path:-}" ] && [ -f "$diff_metrics_path" ]; then
    DISPATCH_AGENTS=$(cat "$diff_metrics_path" | python3 -c "import sys,json; print(','.join(json.load(sys.stdin).get('dispatch_agents',[])))" 2>/dev/null || echo "")
fi
```

`DISPATCH_AGENTS` is a comma-separated list of audit dimension names to spawn in Step 3
(e.g., `"tests,cohesion"` for a small diff, or `"arch,tests,defense,bugs,cohesion,slop"`
for a medium/large diff).

When `DISPATCH_AGENTS` is empty (metrics file absent or unparseable), dispatch all 6
standard agents — this is the graceful-degradation fallback that preserves current behavior.

**Agent selection tiers:**
- **Small diff** (<200 added LoC and <5 changed files): `tests`, `cohesion`, and optionally `arch` if structural files changed (e.g., `__init__.py`, `pyproject.toml`).
- **Medium/large diff** (>= 200 added LoC or >= 5 changed files): All 6 standard agents (`arch`, `tests`, `defense`, `bugs`, `cohesion`, `slop`).

Note: Dimension 7 (the deletion regression audit) is NOT part of the dispatch plan — it remains
unconditionally gated on `deletion_context` being non-null (unchanged from current behavior).

### Step 3: Run Parallel Audit Subagents

Spawn parallel subagents (Task tool, model: sonnet) for each audit dimension listed in
`DISPATCH_AGENTS`. If `DISPATCH_AGENTS` is non-empty, spawn ONLY the dimensions it lists.
If `DISPATCH_AGENTS` is empty, spawn all 6 standard dimensions (1-6).

Parse `DISPATCH_AGENTS` (comma-separated string) into a list. For each dimension name in
the list, spawn the corresponding subagent using the prompt template below.

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
> Each line in the diff is prefixed with `[LNNN]` where NNN is the new-file line number.
> When reporting findings, use the `[LNNN]` number as the `line` value in your finding.
> Do not compute line numbers yourself — use the marker.
> If the finding cannot be anchored to a specific `[LNNN]` marker, use the nearest
> `+` or context line's marker in the same hunk.
>
> If no issues found, return an empty array [].
> Annotated diff content (each line prefixed with [LNNN] markers):
> {annotated_diff_content}
>
> Prior resolved findings (DO NOT RE-RAISE — these have been addressed by resolve-review):
> {json_list_of_prior_resolved_findings or "[]"}
>
> Prior unresolved findings (FOCUS ON these persistent issues if they appear in the diff you are reviewing):
> {json_list_of_prior_unresolved_findings or "[]"}
>
> When a finding matches a prior resolved entry by file and approximate line (within ±5 lines):
> SKIP it entirely — do not include it in your findings array.

Pass `prior_resolved_findings` and `prior_unresolved_findings` (both as JSON arrays) into each
subagent prompt via template substitution, same as `annotated_diff_content`.

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
2. Suppression pass — filter out prior_resolved_findings matches: after collecting raw
   findings and before deduplication, remove any finding that matches a
   `prior_resolved_findings` entry by same `file` path AND `line` within ±5 of the
   resolved finding's `line`. This handles line drift caused by fix commits shifting
   context. Log each suppressed finding:
   `"Suppressing finding at {file}:{line} — matches prior resolved thread"`.
   The remaining findings proceed through deduplication and verdict logic unchanged.
3. Deduplicate by `(file, line)` pairs — keep highest severity for each pair
4. Partition findings against `VALID_LINE_RANGES` (built in Step 2.7):
   - `FILTERED_FINDINGS`: findings whose `(file, line)` falls within any hunk range for
     that file. These are in-hunk and safe to post as inline comments in Step 6.
   - `UNPOSTABLE_FINDINGS`: findings whose `line` is not in any hunk range for their file.
     Log a warning for each. These findings are surfaced via:
     - Step 6: Critical-severity unpostable findings are posted as file-level comments
       (subject_type: "file") on the individual comments endpoint.
     - Step 7: All unpostable findings appear in the "Outside Diff Range" section of the
       review body.
   - If `VALID_LINE_RANGES` is empty, all findings are `FILTERED_FINDINGS`.
5. Apply verdict logic (Step 5) to ALL findings (`FILTERED_FINDINGS` + `UNPOSTABLE_FINDINGS`
   combined), so unpostable findings still contribute to the `changes_requested` verdict.
6. Bucket by actionability (applied to combined findings):
   - `actionable_findings` — requires_decision=false AND severity in ("critical", "warning")
   - `decision_findings` — requires_decision=true (any severity)
   - `info_findings` — severity == "info" AND requires_decision=false

### Step 4.5: Echo Primary Obligation

After aggregating all subagent findings, before proceeding to verdict or posting, you MUST state aloud:

> "I have N findings. My primary job is to post inline comments on specific code lines for each finding. I must use the GitHub Reviews API to leave comments anchored to the exact lines in the diff."

This is not optional. Do not proceed to Step 5 without stating this.

### Step 5: Determine Verdict

- Any `blocking_findings` (critical severity, non-decision) present → `verdict = "changes_requested"` (clear fix exists, automated resolver handles it)
- No blocking findings, but `warning_findings` (warning severity, non-decision) present → `verdict = "approved_with_comments"` (recipe routes to `resolve_review` but does not require a re-review cycle)
- No blocking or warning findings, but `decision_findings` present → `verdict = "needs_human"` (`needs_human` fires only when one or more findings have `requires_decision=true` — meaning the correct path forward requires a human decision that the automated reviewer cannot make)
- No findings of any kind → `verdict = "approved"`

**Verdict logic:**
```python
decision_findings = [f for f in all_findings if f.get("requires_decision")]
blocking_findings = [
    f for f in all_findings
    if not f.get("requires_decision") and f["severity"] == "critical"
]
warning_findings = [
    f for f in all_findings
    if not f.get("requires_decision") and f["severity"] == "warning"
]

if blocking_findings:
    verdict = "changes_requested"
elif warning_findings:
    verdict = "approved_with_comments"
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
- `approved_with_comments` → `COMMENT`
- `needs_human` → `COMMENT`
- `changes_requested` → `REQUEST_CHANGES`

**Success signal:** If the batch POST returns HTTP 200, treat the review as successfully
posted regardless of response body content. Do NOT inspect the response body for a
`comments` array — GitHub's review API does not echo back the submitted comments, so any
length check would always read 0 and falsely trigger Tier 1 fallback.

**Own-PR guard:** If the batch POST returns HTTP 422 and the error message mentions
"review" or "author", the PR is self-authored. Retry the same request with event
`COMMENT` instead of `REQUEST_CHANGES`. GitHub does not allow a PR author to submit a
`REQUEST_CHANGES` review on their own PR.

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
sleep 1  # Rate-limit discipline: 1s between mutating calls
```

Individual POSTs are not atomic — one failure does not block others.
If at least one per-finding comment succeeds, proceed to Step 7.

**Fallback Tier 2 — DEGRADED: Bullet-List Summary Dump (if all individual posts fail):**

WARNING: If you reach Tier 2 fallback, the review has FAILED its primary purpose.
Before posting the body dump, you MUST state:

> "FALLBACK: I was unable to post inline comments. Posting summary as review body instead. This is a DEGRADED review."

Tier 2 is a failure mode with a workaround, not an acceptable alternative to inline comments.

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

### Step 6.5: Post-Completion Confirmation

After completing Step 6, you MUST state:

> "I confirm that I posted N inline comments on the following files: [list files]. If I posted 0 inline comments and had findings, this review has FAILED its primary purpose."

If you find yourself writing "I posted 0 inline comments and had N findings" — STOP.
Do not proceed to Step 7. Instead, investigate why zero comments were posted. Check
whether the line numbers in your findings match `VALID_LINE_RANGES`. If they do not,
attempt to map each finding to the nearest valid hunk line before falling back.

**CRITICAL — No Local File Paths in GitHub Output:**
Never reference local file paths (e.g., `{{AUTOSKILLIT_TEMP}}/...`, `summary_*.md`, absolute paths) in the review body, inline comments, or any content posted to GitHub. The summary file is a local audit artifact only — GitHub readers cannot access local filesystem paths. Reference findings by file path and line number within the repository, not by local temp file locations.

### Step 7: Submit Summary Review

```bash
# approved
gh pr review {pr_number} --approve --body "AutoSkillit review passed. No blocking issues found."

# approved_with_comments (no UNPOSTABLE_FINDINGS)
gh pr review {pr_number} --comment --body "AutoSkillit review: warning-only findings detected. See inline comments — no blocking changes required."

# approved_with_comments / changes_requested / needs_human (with UNPOSTABLE_FINDINGS)
# When UNPOSTABLE_FINDINGS is non-empty, append the "Outside Diff Range" section
# to the verdict body. Build the body string dynamically.
# Then post with the appropriate event flag:
gh pr review {pr_number} --approve|--comment|--request-changes --body "$BODY"
```

**Building the Outside Diff Range body section:**

When `UNPOSTABLE_FINDINGS` is non-empty, construct the body by appending the following
section after the verdict one-liner. Group unpostable findings by file, format as a
bullet list reusing the Tier 2 format (120-char message truncation).

**TRUNCATION GUARD:** Cap the Outside Diff Range section at ~40,000 characters.
The GitHub review body has a hard 65,536-char limit (HTTP 422 on overflow,
no graceful degradation). Reserve headroom for the verdict line and formatting.
If truncated, append: "...and N more findings. See file-level comments for
critical items."

Template for the appended section:

```
### ⚠️ Outside Diff Range

These findings target lines not in the diff and could not be posted as inline comments:

**path/to/file.py**
- **L42** [critical/arch]: Finding message truncated to 120 chars

**path/to/other.py**
- **L99** [warning/security]: Finding message truncated to 120 chars
```

### Step 8: Write Summary and Emit Verdict

**CRITICAL — Ordering:** Step 8 must execute after Steps 6 and 7. Do not write the summary file before posting inline comments and submitting the review verdict to GitHub. Writing the file first anchors you to treating it as the primary output rather than a local audit artifact.

Save findings summary to `{{AUTOSKILLIT_TEMP}}/review-pr/summary_{pr_number}_{timestamp}.md`. (relative to the current working directory)

**Write Diff-Scoped Context Handoff (before emitting verdict):**

After writing the summary file and before emitting the verdict token, write the handoff
file for resolve-review's pre-built context. This costs zero additional API calls or file
reads — all data is already in the session's context.

For each finding in `FILTERED_FINDINGS` + `UNPOSTABLE_FINDINGS` where severity is
`"critical"` or `"warning"`, build a context entry:
- `path` — the finding's `file` field (the finding schema uses `file`, not `path`;
  map `finding.file` → `path` in the context entry for resolve-review compatibility)
- `line` — the finding's line number
- `severity` — `"critical"` or `"warning"`
- `dimension` — the audit dimension (arch, tests, bugs, etc.)
- `message` — the finding's message text
- `code_region` — extract from `ANNOTATED_DIFF`: find the file's section in the
  annotated diff (between its `diff --git` header and the next), then collect all
  lines whose `[LX]` marker has X within ±50 of the finding's `line`. Include those
  raw annotated-diff lines as-is. If ANNOTATED_DIFF is empty or the file section is
  not found, set `code_region` to `""`.

Write to `{{AUTOSKILLIT_TEMP}}/review-pr/diff_context_{pr_number}.json`:

```json
{
  "pr_number": 1234,
  "schema_version": 1,
  "written_at": "{ISO-8601 timestamp}",
  "context_entries": [
    {
      "path": "src/autoskillit/execution/headless.py",
      "line": 42,
      "severity": "critical",
      "dimension": "arch",
      "message": "...",
      "code_region": "[L40] ...\n[L41] ...\n[L42] ..."
    }
  ]
}
```

Log: `"Wrote diff-scoped context handoff: N entries → {path}"`. If the write fails
(e.g., temp dir unavailable), log a warning and continue — the handoff file is
best-effort and its absence is handled gracefully by resolve-review.

**Write Raw Findings JSON (after diff-context handoff):**

After writing the diff-context handoff file, also write the raw findings list for
downstream enrichment. This is a separate file from the handoff — it captures only
the finding dicts as produced by the subagents, without code_region extraction.

Write to `{{AUTOSKILLIT_TEMP}}/review-pr/raw_findings_{pr_number}.json`:

```json
{
  "pr_number": 1234,
  "findings": [
    {
      "file": "src/autoskillit/execution/headless.py",
      "line": 42,
      "severity": "critical",
      "dimension": "arch",
      "message": "..."
    }
  ]
}
```

Include all findings from `FILTERED_FINDINGS` + `UNPOSTABLE_FINDINGS` where severity
is `"critical"` or `"warning"`. Log: `"Wrote raw findings: N entries → {path}"`.

Output the verdict as the final line:

> **IMPORTANT:** Emit the structured output tokens as **literal plain text with no
> markdown formatting on the token names**. Do not wrap token names in `**bold**`,
> `*italic*`, or any other markdown. The adjudicator performs a regex match on the
> exact token name — decorators cause match failure.

```
verdict = {approved|approved_with_comments|changes_requested|needs_human}
```

Immediately after the verdict line, emit the review gate tag on a new line:

- If `verdict = changes_requested`: emit `%%REVIEW_GATE::LOOP_REQUIRED%%`
- If `verdict = approved` or `verdict = needs_human`: emit `%%REVIEW_GATE::CLEAR%%`
- If `verdict = approved_with_comments`: do NOT emit a gate tag

Exit 0 in all normal cases (approved, needs_human, changes_requested).
Exit 1 only for unrecoverable tool-level errors.

## Output

- `verdict=approved` → `%%REVIEW_GATE::CLEAR%%` — No blocking issues; CI can proceed
- `verdict=approved_with_comments` — no gate tag — Warning-only findings; recipe routes to `resolve_review` but does not require a re-review cycle
- `verdict=changes_requested` → `%%REVIEW_GATE::LOOP_REQUIRED%%` — Blocking issues found; recipe routes to `resolve_review`
- `verdict=needs_human` → `%%REVIEW_GATE::CLEAR%%` — Uncertain trade-offs; human review requested via the authenticated GitHub user mention (derived at runtime)

Summary written to: `{{AUTOSKILLIT_TEMP}}/review-pr/summary_{pr_number}_{timestamp}.md`