---
name: prepare-issue
categories: [github]
description: >
  Create a single GitHub issue and immediately triage it — dedup check,
  classification (recipe:implementation or recipe:remediation), mixed-concern
  detection, and label application. Use when user says "open an issue",
  "create an issue", "file an issue", or "file a bug". The user-facing
  counterpart to report_bug.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: prepare-issue] Preparing issue...'"
          once: true
---

# prepare-issue Skill

Create a GitHub issue and immediately triage it with LLM classification.

## When to Use

- User says "open an issue", "create an issue", "file an issue", or "file a bug"
- User says "make a new issue", "open a GitHub issue", or "create a GitHub issue"
- User says "I want to open up a GitHub issue" or any similar natural phrasing
- User describes a bug or feature and wants it recorded as a GitHub issue
- User provides `/autoskillit:prepare-issue` directly

## Interface

```
/autoskillit:prepare-issue [--issue N] [--split] [--dry-run] [--repo owner/repo] [description...]
```

- `description...` — free-form text describing the problem or feature (becomes issue title + body)
- `--issue N` — adopt and triage an existing unlabeled issue instead of creating new
- `--split` — when mixed concerns detected, create sub-issues automatically
- `--dry-run` — show classification and labels without creating or editing anything
- `--repo owner/repo` — target repository (falls back to gh default repo context)

## Workflow

### Step 1: Parse Arguments

Parse ARGUMENTS for:
- `--issue N` → set `issue_number = N`, skip dedup
- `--split` → set `split = true`
- `--dry-run` → set `dry_run = true`
- `--repo owner/repo` → set `repo = "owner/repo"`
- Remaining tokens → `description`

### Step 1b: Detect Validated Audit Report

After parsing arguments, check whether the description is a validated audit report:

1. If `description` is a file path (relative or absolute) pointing to an existing `.md` file,
   read the first non-blank line of that file. If the file cannot be read (e.g., permission
   error), skip to case 4 and set `is_validated_report = false`.
2. If that first non-blank line, after stripping trailing whitespace (including `\r`), is
   exactly `validated: true`, set `is_validated_report = true` and record
   `report_path = description`.
3. If `description` itself (not a file path) begins with `validated: true` (after stripping
   trailing whitespace) as its first non-blank line, set `is_validated_report = true` and
   treat `description` as the report content directly.
4. Otherwise set `is_validated_report = false`.

When `is_validated_report = true`:
- In **Step 5**, use the validated report body construction procedure below instead of
  the standard summarization.
- In **Step 7a** (`is_validated_report = true`): skip requirement generation entirely.
- Set `requirements_generated: false` in the final result block.

### Step 2: Authenticate

```bash
gh auth status
```

Fail fast with a clear error if authentication is not available.

### Step 3: Resolve Repo

If `--repo owner/repo` was provided, use it. Otherwise rely on `gh`'s default repo context.
Confirm access:

```bash
gh repo view --json owner,name
```

### Step 4: Dedup Check (skip if `--issue N` provided)

Extract multiple keyword sets from the description — individual key terms and 2–3 phrase
combinations that capture the core topic. For each keyword set, search all issues:

```bash
gh issue list --state all --search "{keyword-set}" \
    --json number,title,url,body,labels,state --limit 15
```

> `labels` is returned as an array of objects `[{"name": "..."}]` by `gh`.
> Extract label names as a flat list when classifying eligibility.

Run searches for each keyword set and deduplicate results by issue number, tracking the
`match_count` (number of keyword sets that matched) per candidate.

After deduplication, apply a relevance gate to closed issues: include a closed issue as
a candidate only if it was matched by **two or more** keyword sets. Open issues are
included regardless of match count (same threshold as before). Discard single-match
closed results — they are likely false positives.

Classify each surviving candidate as **extend-eligible** or **informational-only**:

| Condition | Classification |
|-----------|---------------|
| `state == open` AND no `in-progress` label AND no `staged` label | extend-eligible |
| `state == open` AND has `in-progress` label | informational-only |
| `state == open` AND has `staged` label | informational-only |
| `state == closed` | informational-only |

Assign sequential numbers `[1]`, `[2]`, … only to extend-eligible candidates.
Informational-only candidates receive a `[—]` marker.

If candidates are found, display them with state annotations per candidate:

```
━━━ Possible Duplicates Found ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  [1] #{number} — {title}           (open)
      {url}

  [—] #{number} — {title}           (in-progress)
      {url}

  [—] #{number} — {title}           (closed)
      {url}

Options:
  [1]–[{N}]  Add to / extend an existing issue   (only shown if extend-eligible candidates exist)
  C          Create a new issue anyway

Your choice [C]:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

If no extend-eligible candidates exist (all are informational-only), omit the `[N]`
option entirely — only `C` appears.

**If the user enters C (or presses Enter):** Continue to Step 4a.

**If the user enters a number corresponding to an extend-eligible candidate (extend existing):**

1. Fetch current body and append new context using a temp file to avoid shell injection:
   ```bash
   ts=$(date +%Y-%m-%d_%H%M%S)
   EDIT_BODY_FILE="{{AUTOSKILLIT_TEMP}}/prepare-issue/edit_body_${ts}.md"
   mkdir -p "{{AUTOSKILLIT_TEMP}}/prepare-issue"
   gh issue view {selected_number} --json body -q .body > "${EDIT_BODY_FILE}"
   printf '\n## Additional Context\n\n%s' "{description}" >> "${EDIT_BODY_FILE}"
   gh issue edit {selected_number} --body-file "${EDIT_BODY_FILE}"
   ```
2. Set `issue_number = selected_number` (no new issue will be created).
3. Fetch the updated issue for triage:
   ```bash
   gh issue view {selected_number} --json number,title,body,labels,url
   ```
4. **Continue to Step 6 (LLM Classification)** on this existing issue, then proceed through
   Steps 7, 7a, 8, and 9 to apply labels and requirements. Emit the result block with the
   existing issue's number and URL, then exit.

**If no candidates found:** Continue directly to Step 4a.

### Step 4a: Show Draft and Confirm

Before creating any new issue, display the proposed title and body to the user and wait
for explicit approval:

```
━━━ Draft Issue ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Title: {title}

Body:
{body}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Create this issue? [Y/n/edit]
```

- **Y (or Enter):** Proceed to Step 5.
- **n:** Abort. Output a `prepare-issue-result` block with `"aborted": true` and exit.
- **edit:** Accept edited title and/or body from the user, redisplay, and re-prompt.

This gate fires for every new-issue creation path. Skip this step when `--issue N` is
provided (adopting an existing issue) or when `--dry-run` is active.

### Step 5: Create Issue or Adopt Existing

**Creating new:**

**Validated audit report input (`is_validated_report = true`):**

1. Derive the issue title from the validated report's H1 heading: strip the leading `# `
   and use the result verbatim.
2. Apply all strip transforms in a single deterministic shell pipeline:

   ```bash
   ts=$(date +%Y-%m-%d_%H%M%S)
   ISSUE_BODY_FILE="{{AUTOSKILLIT_TEMP}}/prepare-issue/issue_body_${ts}.md"
   mkdir -p "{{AUTOSKILLIT_TEMP}}/prepare-issue"

   grep -v 'validated: true' "$report_path" \
     | grep -v '\.autoskillit/' \
     | grep -v 'contested_findings_' \
     | grep -v '| CONTESTED |' \
     | grep -v '| VALID BUT EXCEPTION WARRANTED |' \
     | sed 's/ | \*\*Contested:\*\* [0-9][0-9]*//' \
     | sed 's/ | \*\*Exception warranted:\*\* [0-9][0-9]*//' \
   > "${ISSUE_BODY_FILE}" || { echo 'ERROR: failed to process report_path'; exit 1; }

   # Strip the ## Findings with Exceptions section entirely:
   awk '/^## Findings with Exceptions/{skip=1} skip && /^---/{skip=0; next} !skip' \
     "${ISSUE_BODY_FILE}" > "${ISSUE_BODY_FILE}.tmp" \
     && mv "${ISSUE_BODY_FILE}.tmp" "${ISSUE_BODY_FILE}"

   # Defensive strip: remove any finding detail section that contains an exception note
   # (guards against exception-warranted findings leaking inline into ## Validated Findings)
   awk '
     /^\*\*Exception note:\*\*/ { in_exception=1; next }
     in_exception && /^---$/ { in_exception=0; next }
     in_exception && /^## / { in_exception=0 }
     !in_exception
     END { in_exception=0 }
   ' "${ISSUE_BODY_FILE}" > "${ISSUE_BODY_FILE}.tmp" \
     && mv "${ISSUE_BODY_FILE}.tmp" "${ISSUE_BODY_FILE}"
   ```

   **What each transform removes:**
   - `validated: true` — YAML front-matter sentinel (not content)
   - `.autoskillit/` lines — all artifact paths (`**Original report:**` and any future variants)
   - `contested_findings_` lines — footer reference to the excluded-findings file
   - `| CONTESTED |` rows — CONTESTED verdict rows from `## Validation Status` table
   - `| VALID BUT EXCEPTION WARRANTED |` rows — exception verdict rows from `## Validation Status`
   - `| **Contested:** N` and `| **Exception warranted:** N` — count segments from the
     `**Findings processed:**` summary line; leaves `**Findings processed:** {total} | **Valid:** {N}`
   - `## Findings with Exceptions` through next `---` — the entire exception-findings section
   - `**Exception note:**` blocks — exception-warranted finding detail sections

3. Call `gh issue create` using the temp file:

   ```bash
   gh issue create \
     --title "{title}" \
     --body-file "${ISSUE_BODY_FILE}"
   ```

   Capture the returned issue URL and extract the issue number from it.

The resulting body contains **only** actionable, VALID findings: the H1 title, the
`## Validation Status` table (VALID rows only), and the `## Validated Findings` section.
No artifact paths, no contested content, no exception-warranted content.

**Standard input (`is_validated_report = false`):**

Derive a concise title (first sentence of description, max 80 chars) and a structured
body from the full description:

```bash
gh issue create \
    --title "{title}" \
    --body "{body}"
```

Capture the returned issue URL and extract the issue number from it.

**Adopting existing (`--issue N`):**

```bash
gh issue view N --json number,title,body,labels,url
```

Use the fetched data as the issue context.

### Step 6: LLM Classification

**Shortcut — Validated audit report:** When `is_validated_report = true` (set in Step 1b),
immediately assign:
- `route = implementation`
- `issue_type = enhancement`
- `confidence = high`
- `rationale = "Validated audit report — structural/quality improvement work, not broken behavior"`

Skip the heuristic table below and proceed directly to Step 7.

Analyze the issue title + body using in-context reasoning for all other inputs:

| Signal | Route | Issue Type |
|--------|-------|------------|
| Existing behavior broken / error traceback present | `remediation` | `bug` |
| New feature / enhancement with clear acceptance criteria | `implementation` | `enhancement` |
| "X doesn't support Y" / clearly absent feature | `implementation` | `enhancement` |
| Validated audit report with structural/quality findings (finding IDs present, no tracebacks, no crashes) | `implementation` | `enhancement` |
| Large/ambiguous scope / unclear root cause | `remediation` | `enhancement` |

Record: `route` (implementation|remediation), `issue_type` (bug|enhancement),
`confidence` (high|low), `rationale` (one sentence).

Note: The fallback row for large/ambiguous scope remains, but the audit-signal row above it now
prevents validated reports from falling through to it. The `is_validated_report` shortcut is the
primary guard; the table row is a secondary defense for cases where `is_validated_report` may
not have been set (e.g., `--issue N` adoption of a pre-existing validated audit issue).

### Step 7: Confidence Gate

If `confidence == "low"`:
- Present classification + rationale to user
- Ask: **"Classify as recipe:{route} ({issue_type})? [Y/n]"**
- If user overrides: record their chosen route/type

### Step 7a: Requirement Generation (recipe:implementation only)

**Skip entirely when `is_validated_report = true`** — the validated report IS the
specification. No requirements section will be generated or appended.
Set `requirements_generated: false` in the result block and proceed directly to Step 8.

Skip if route is `recipe:remediation` — proceed directly to Step 8.

If route is `recipe:implementation`:

1. Trace backward from the goal in the issue title and body:
   - Ask: "What must be true for this functionality to exist?"
   - Each answer is a requirement. Stop when you reach implementation choices.
2. Group requirements by co-implementation concern. Name each group with a short
   uppercase abbreviation (2–5 letters). Example groups: AUTH, API, DATA, UI, CLI.
3. Format each requirement as: `**REQ-{GRP}-NNN:** {single-sentence condition}.`
   - NNN is zero-padded, resets per group (001, 002, ...).
   - Requirements are conditions, not instructions: "The system must X" not "Do X".
4. Fetch the current issue body:
   ```bash
   gh issue view {N} --json body -q .body
   ```
5. If `## Requirements` section already exists in the body: skip (idempotent).
6. If `--dry-run` is set: print the generated requirements to stdout but do NOT call
   `gh issue edit`. Set `requirements_generated: true`, `requirements_appended: false`.
7. Otherwise, append the Requirements section:
   ```bash
   ts=$(date +%Y-%m-%d_%H%M%S)
   EDIT_BODY_FILE="{{AUTOSKILLIT_TEMP}}/prepare-issue/edit_body_${ts}.md"
   REQUIREMENTS_FILE="{{AUTOSKILLIT_TEMP}}/prepare-issue/requirements_${ts}.md"
   mkdir -p "{{AUTOSKILLIT_TEMP}}/prepare-issue"

   # Fetch current issue body to temp file (avoids shell interpolation):
   gh issue view {N} --json body -q .body > "${EDIT_BODY_FILE}"

   # Populate ${REQUIREMENTS_FILE} with the generated requirements content, then:
   printf '\n\n## Requirements\n\n' >> "${EDIT_BODY_FILE}"
   cat "${REQUIREMENTS_FILE}" >> "${EDIT_BODY_FILE}"

   gh issue edit {N} --body-file "${EDIT_BODY_FILE}"
   ```
8. If the issue is too vague for clean requirement extraction (no clear goal,
   contradictory claims, or entirely implementation-prescriptive): do not force it.
   Instead: post a comment flagging the issue as needs more detail, suggest
   remediation routing if the goal is unclear. Set `requirements_generated: false`.
9. On success: set `requirements_generated: true`, `requirements_appended: true`.

### Step 8: Mixed-Concern Detection

Examine whether the issue blends distinct concern categories (e.g., bug fix + new feature,
investigation + implementation work). Criteria: the issue describes two separate,
independently-completable outcomes.

If mixed concerns detected:
- Notify user: *"This issue mixes {concern_a} and {concern_b}. Consider splitting."*
- If `--split` is set: create a sub-issue for each concern via `gh issue create`,
  link them back to the parent with a comment, and track all sub-issue numbers.

### Step 9: Label Application

If `--dry-run`: skip this step, print a preview of what would be applied, emit result
block, exit.

Otherwise:

```bash
# Ensure labels exist (idempotent)
gh label create "recipe:implementation" --force \
    --description "Route: proceed directly to implementation" \
    --color "0E8A16"
sleep 1  # Rate-limit discipline: 1s between mutating calls
gh label create "recipe:remediation" --force \
    --description "Route: investigate/decompose before implementation" \
    --color "D93F0B"
sleep 1  # Rate-limit discipline: 1s between mutating calls
gh label create "bug" --force \
    --description "Existing behavior is broken" \
    --color "d73a4a"
sleep 1  # Rate-limit discipline: 1s between mutating calls
gh label create "enhancement" --force \
    --description "New feature or request" \
    --color "a2eeef"
sleep 1  # Rate-limit discipline: 1s between mutating calls

# Apply triage labels (use the route determined in Step 6)
gh issue edit {issue_number} --add-label "recipe:implementation"
sleep 1  # Rate-limit discipline: 1s between mutating calls
# or, for remediation route:
gh issue edit {issue_number} --add-label "recipe:remediation"
sleep 1  # Rate-limit discipline: 1s between mutating calls
gh issue edit {issue_number} --add-label "{issue_type}"
```

## Critical Constraints

**NEVER:**
- Create or modify GitHub issues without explicit user intent
- Apply labels not in the defined set (`recipe:implementation`, `recipe:remediation`, `bug`, `enhancement`)
- Skip the dedup check when creating a new issue (unless `--issue N` is provided)
- Proceed past Step 2 (Auth) if `gh auth status` fails
- Create a GitHub issue without displaying the draft and receiving explicit Y confirmation
  (unless `--issue N` or `--dry-run` is active)
- Use `--body` inline for the validated-report `gh issue create` — always write the
  stripped body to `{{AUTOSKILLIT_TEMP}}/prepare-issue/issue_body_{timestamp}.md` and
  pass `--body-file` (prevents LLM paraphrase, shell truncation, and special-character injection)
- Use `--body` shell substitution (`--body "$(...)`) for `gh issue edit` — always write to
  `{{AUTOSKILLIT_TEMP}}/prepare-issue/req_body_{timestamp}.md` and use `--body-file`

**ALWAYS:**
- Confirm repo access with `gh repo view` before any issue operations
- Use `--force` on all `gh label create` calls for idempotency
- Emit the result block (`---prepare-issue-result---`) even on dry-run
- Respect `--dry-run`: never create or edit anything when this flag is set

## Output

Emit to stdout for recipe capture:

```
---prepare-issue-result---
{
  "issue_url": "https://github.com/owner/repo/issues/N",
  "issue_number": N,
  "route": "recipe:implementation",
  "issue_type": "enhancement",
  "confidence": "high",
  "rationale": "...",
  "labels_applied": ["recipe:implementation", "enhancement"],
  "dry_run": false,
  "sub_issues": [],
  "requirements_generated": true,
  "requirements_appended": true
}
---/prepare-issue-result---
```

Also emit the issue URL as a standalone structured token for recipe capture:

```
issue_url = https://github.com/owner/repo/issues/N
```
