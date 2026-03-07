---
name: prepare-issue
description: >
  Create a single GitHub issue and immediately triage it — dedup check,
  classification (recipe:implementation or recipe:remediation), mixed-concern
  detection, and label application. The user-facing counterpart to report_bug.
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

Search open issues for potential duplicates using keywords from the description:

```bash
gh issue list --state open --search "{keywords}" \
    --json number,title,url,body --limit 10
```

If a candidate with high title overlap is found:
- Display the candidate (number, title, URL) to the user
- Ask interactively: **"Comment on #N or create a new issue?"**
- If comment: `gh issue comment N --body "..."` → emit result block → exit
- If create new: continue to Step 5

### Step 5: Create Issue or Adopt Existing

**Creating new:**
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

Analyze the issue title + body using in-context reasoning:

| Signal | Route | Issue Type |
|--------|-------|------------|
| Existing behavior broken / error traceback present | `remediation` | `bug` |
| New feature / enhancement with clear acceptance criteria | `implementation` | `enhancement` |
| "X doesn't support Y" / clearly absent feature | `implementation` | `enhancement` |
| Large/ambiguous scope / unclear root cause | `remediation` | `enhancement` |

Record: `route` (implementation|remediation), `issue_type` (bug|enhancement),
`confidence` (high|low), `rationale` (one sentence).

### Step 7: Confidence Gate

If `confidence == "low"`:
- Present classification + rationale to user
- Ask: **"Classify as recipe:{route} ({issue_type})? [Y/n]"**
- If user overrides: record their chosen route/type

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
gh label create "recipe:implementation" \
    --description "Route: proceed directly to implementation" \
    --color "0E8A16" --force
gh label create "recipe:remediation" \
    --description "Route: investigate/decompose before implementation" \
    --color "D93F0B" --force
gh label create "bug" --description "Existing behavior is broken" \
    --color "d73a4a" --force
gh label create "enhancement" --description "New feature or request" \
    --color "a2eeef" --force

# Apply triage labels
gh issue edit {issue_number} --add-label "recipe:{route}"
gh issue edit {issue_number} --add-label "{issue_type}"
```

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
  "sub_issues": []
}
---/prepare-issue-result---
```
