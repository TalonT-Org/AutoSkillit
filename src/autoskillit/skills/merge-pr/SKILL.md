---
name: merge-pr
description: Merge a single PR into the current integration branch. For simple PRs, attempts direct git merge. For needs_check PRs, re-assesses complexity against the current integration branch state before deciding whether to merge directly or return needs_plan=true with a conflict report. Use inside the pr-merge-pipeline loop.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo 'Processing PR merge...'"
          once: true
---

# PR Merge Skill

Merge one PR into the current working branch (the integration branch). Handles both
simple direct merges and complexity re-assessment for PRs that may have accumulated
conflicts from earlier merges in the queue.

## When to Use

- Inside the `pr-merge-pipeline` loop, once per PR
- Called with the PR number and its complexity tag from `analyze-prs` output

## Arguments

```
{pr_number} {complexity}
```

- `pr_number` — GitHub PR number (integer)
- `complexity` — `simple` or `needs_check` (from `analyze-prs` pr_order JSON)

## Critical Constraints

**NEVER:**
- Modify any source code files directly (conflict resolution is handled by `make-plan` + `implement-worktree-no-merge`)
- Push to any remote branch
- Close or comment on the PR
- Leave the git working tree in a dirty state — always abort failed merges before exiting
- Create files outside `temp/pr-merge-pipeline/` directory

**ALWAYS:**
- Run `git status` before and after any merge attempt to verify clean state
- Run `git merge --abort` if a merge leaves conflicts, before returning `needs_plan=true`
- Fetch the PR branch from remote before attempting merge
- Use the current working directory's HEAD branch as the merge target (the integration branch)

## Workflow

### Step 0: Validate Inputs

- Parse `pr_number` (must be a positive integer)
- Parse `complexity` (must be `simple` or `needs_check`)
- Verify git working tree is clean: `git status --porcelain` must be empty. If dirty: abort with error.
- Identify current branch: `git branch --show-current` (this is the integration branch)

### Step 1: Fetch PR Information

```bash
gh pr view {pr_number} --json number,title,headRefName,baseRefName,body,additions,deletions,files
gh pr diff {pr_number}
```

Extract:
- `pr_branch`: the headRefName
- `pr_title`: the title
- `pr_files`: list of changed file paths

Fetch the PR branch locally:
```bash
git fetch origin {pr_branch}:{pr_branch} 2>/dev/null || git fetch origin pull/{pr_number}/head:{pr_branch}
```

### Step 2: Complexity Path — `simple`

Attempt direct merge:

```bash
git merge --no-ff origin/{pr_branch} -m "Merge PR #{pr_number}: {pr_title}"
```

**If merge succeeds (exit code 0, no conflicts):**
- Verify working tree is clean: `git status --porcelain`
- Report success → return `merged=true, needs_plan=false`

**If merge produces conflicts:**
- This is unexpected for a `simple` PR but must be handled
- Record which files conflicted: `git diff --name-only --diff-filter=U`
- Abort the merge: `git merge --abort`
- Fall through to conflict report (same as `needs_check` complex path below)

### Step 3: Complexity Path — `needs_check` (Re-assessment)

Before attempting any merge, re-assess complexity against the current integration branch state.

Launch an Explore subagent to:

1. Determine `base_branch` from the PR: extract `baseRefName` from the `gh pr view` result in Step 1 (this is the branch the PR targets, e.g. `main`).

2. Get the current integration branch's changes since base_branch:
   ```bash
   git log {base_branch}..HEAD --oneline
   git diff {base_branch}...HEAD --stat
   ```
2. Get this PR's full diff: the diff fetched in Step 1
3. Identify file-level overlap: files in the PR diff that also appear in the integration branch diff
4. For each overlapping file, inspect both diffs to assess:
   - Do they modify the same functions or classes?
   - Would the PR's changes be semantically incompatible with what's already been merged?
   - Would resolving the conflicts require understanding the intent of both changes (not just picking lines)?
   - Do the combined test changes risk test suite conflicts?

**Re-assessment verdict:**

**Still simple** (all of these are true):
- File overlap is only in non-logic files (e.g., imports, constants, docstrings)
- No shared functions/classes are modified by both
- No test conflicts that require semantic reconciliation
→ Proceed to direct merge (same as Step 2 above)

**Complex** (any of these):
- Shared functions or classes modified by both
- The PR assumes a code structure that earlier merges have changed
- Resolving the conflict requires understanding the intent of both PRs
- Test changes from both sides modify the same test file in ways that conflict semantically
→ Proceed to write conflict report below

### Step 4: Write Conflict Report (complex path only)

Before composing the report, extract `## Requirements` from the PR body:
```bash
gh pr view {pr_number} --json body -q .body
```
Extract the `## Requirements` section if present — set `requirements_section = ""` if not found. Gracefully skip if `gh` is unavailable.

Compute timestamp: `YYYY-MM-DD_HHMMSS`.

Write `temp/pr-merge-pipeline/conflict_pr{pr_number}_plan_{ts}.md`:

```markdown
# Conflict Resolution Plan: PR #{pr_number} — "{pr_title}"

## Context

This PR could not be merged directly into the integration branch because it has
semantic conflicts with previously merged PRs.

**PR:** #{pr_number}
**Branch:** {pr_branch}
**Integration Branch:** {integration_branch (current HEAD)}

{If requirements_section is non-empty, include this block to give make-plan the full requirement context:}
## Requirements

{requirements_section from PR body}

## PR Summary

{pr_body or a 2–3 sentence summary of what the PR does}

## Conflict Analysis

### Overlapping Files

| File | What Earlier Merges Did | What This PR Does | Conflict Type |
|------|------------------------|-------------------|---------------|
| {path} | {summary} | {summary} | {logic/signature/test/structural} |

### Root Conflicts

For each conflict:

**Conflict {N}: {short title}**
- **File:** {path}
- **Function/Class:** {name if applicable}
- **Earlier merge changed:** {description}
- **This PR assumes:** {description}
- **Reconciliation needed:** {what needs to happen to satisfy both}

## Implementation Task

Apply the changes from PR #{pr_number} (branch: `{pr_branch}`) to the current integration
branch, resolving the conflicts identified above. The goal is to preserve the intent of
both this PR and the PRs already merged.

### Required Changes

For each conflict, specify what the reconciled implementation must do:

1. **{file path}**: {concrete description of the reconciled change needed}
2. ...

### Verification

After implementation:
- All tests from this PR's test files must pass
- All previously passing tests must continue to pass
- The functionality described in PR #{pr_number} must be present in the integration branch
```

### Step 5: Return Result

Print a JSON result block to stdout for recipe capture:

**On successful direct merge:**
```json
{
    "merged": true,
    "needs_plan": false,
    "pr_number": 42,
    "pr_branch": "feature/auth",
    "pr_title": "Add user authentication",
    "conflict_report_path": null
}
```

**On complex / conflict detected:**
```json
{
    "merged": false,
    "needs_plan": true,
    "pr_number": 47,
    "pr_branch": "feature/db-refactor",
    "pr_title": "Refactor database layer",
    "conflict_report_path": "temp/pr-merge-pipeline/conflict_pr47_plan_YYYY-MM-DD_HHMMSS.md"
}
```

Exit 0 in both cases — `needs_plan=true` is not a failure, it is a routing signal.

After printing the result block, emit the following structured output tokens as the
very last lines of your text output:

**On successful direct merge:**
```
merged=true
needs_plan=false
pr_number={pr_number}
pr_branch={pr_branch_name}
pr_title={pr_title}
```

**On complex / conflict detected:**
```
merged=false
needs_plan=true
pr_number={pr_number}
pr_branch={pr_branch_name}
pr_title={pr_title}
conflict_report_path={absolute_path_to_conflict_plan_file}
```

Emit `conflict_report_path=` only when `needs_plan=true` and a conflict plan file was
written. Omit the line entirely on a successful direct merge.

## Output Location

```
temp/pr-merge-pipeline/
└── conflict_pr{N}_plan_{ts}.md    (written only when needs_plan=true)
```

## Related Skills

- **`/autoskillit:analyze-prs`** — Produces the pr_order JSON that feeds this skill
- **`/autoskillit:make-plan`** — Receives `conflict_report_path` as its task when needs_plan=true
- **`/autoskillit:dry-walkthrough`** — Verifies the plan before implementation
- **`/autoskillit:implement-worktree-no-merge`** — Implements conflict resolution in a worktree
