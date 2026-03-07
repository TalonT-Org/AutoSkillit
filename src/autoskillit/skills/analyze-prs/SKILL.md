---
name: analyze-prs
description: Analyze all open PRs targeting a base branch — determine merge order, identify file overlaps, and tag each PR as simple or needs_check for complexity. Use at the start of a PR consolidation workflow.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo 'Analyzing open PRs...'"
          once: true
---

# PR Analysis Skill

Analyze all open PRs targeting a base branch, determine a safe merge order, assess
complexity, and produce machine-readable output for the `pr-merge-pipeline` recipe.

## When to Use

- At the start of a `pr-merge-pipeline` run
- User wants to understand which PRs can be merged safely and in what order
- User says "analyze PRs", "order PRs", or "assess PR complexity"

## Critical Constraints

**NEVER:**
- Merge, close, or modify any PR
- Modify any source code files
- Create files outside `temp/pr-merge-pipeline/` directory

**ALWAYS:**
- Use subagents to fetch PR data in parallel
- Use `model: "sonnet"` when spawning all subagents via the Task tool
- Abort clearly if `gh` CLI is not authenticated
- Include every open PR targeting base_branch in the output — no PR is silently dropped

## Workflow

### Step 0: Authenticate and List PRs

Run:
```bash
gh pr list --base {base_branch} --state open --json number,title,headRefName,author,body,additions,deletions,changedFiles --limit 100
```

If zero PRs are returned: write a summary to terminal and exit cleanly with an empty
`pr_order_{ts}.json` (zero PRs, no integration branch needed).

If `gh` returns an auth error: abort with a clear message.

### Step 1: Fetch PR Diffs in Parallel

Launch one Explore subagent per PR (up to 8 in parallel; batch if more):

Each subagent fetches:
- `gh pr diff {number}` — full unified diff
- `gh pr view {number} --json files` — structured file list with additions/deletions per file
- `gh pr view {number} --json body -q .body` — PR body to extract `## Requirements` section if present

Each subagent returns:
- `pr_number`: int
- `title`: str
- `branch`: str (headRefName)
- `files_changed`: list of file paths
- `additions`: int
- `deletions`: int
- `test_files_changed`: list of test file paths (files matching `test_*.py`, `*_test.py`, `*.test.*`, `tests/**`)
- `requirements_section`: str — the `## Requirements` section extracted from the PR body, or `""` if not present

### Step 2: Build File Overlap Matrix

For each pair of PRs, compute:
- `shared_files`: files modified by both PRs
- `shared_test_files`: test files modified by both PRs

A PR pair is **conflicting** if `shared_files` is non-empty.

### Step 3: Determine Merge Order

Order PRs to minimize cascading conflict risk:

1. **PRs with no overlapping files** with any other PR → place first (order by additions ASC)
2. **PRs with overlap** → order so the PR that others depend on (touches foundational files) comes first; use topological sort on the overlap graph
3. **Large PRs** (additions > 200) → place after small PRs that touch the same files, unless they have no overlap

Produce a final ordered list. Document the rationale for each ordering decision.

### Step 4: Tag Complexity

For each PR in the ordered list, assign a complexity tag:

**`simple`** — all of the following are true:
- No shared files with any PR ahead of it in the merge order
- Total additions < 100
- No shared test files with PRs ahead of it
- No substantial logic changes in files also touched by earlier PRs (based on diff inspection)

**`needs_check`** — any of the following:
- Shares files with one or more PRs ahead of it in merge order
- Additions ≥ 100 and touches files also present in earlier PRs
- Modifies shared test files
- The diff suggests it depends on function signatures or class structures that earlier PRs may change

### Step 5: Write Outputs

Compute a timestamp: `YYYY-MM-DD_HHMMSS`.

Compute integration branch name: `integration/pr-merge-{YYYYMMDD-HHMMSS}`.

Ensure `temp/pr-merge-pipeline/` exists.

**5a. Machine-readable order file:** `temp/pr-merge-pipeline/pr_order_{ts}.json`

```json
{
    "integration_branch": "integration/pr-merge-YYYYMMDD-HHMMSS",
    "base_branch": "{base_branch}",
    "generated_at": "{ISO timestamp}",
    "pr_count": 5,
    "prs": [
        {
            "number": 42,
            "title": "Add user authentication",
            "branch": "feature/auth",
            "complexity": "simple",
            "files_changed": ["src/auth.py", "tests/test_auth.py"],
            "test_files_changed": ["tests/test_auth.py"],
            "additions": 87,
            "deletions": 12,
            "overlap_with_pr_numbers": []
        },
        {
            "number": 47,
            "title": "Refactor database layer",
            "branch": "feature/db-refactor",
            "complexity": "needs_check",
            "files_changed": ["src/db.py", "src/auth.py", "tests/test_db.py"],
            "test_files_changed": ["tests/test_db.py"],
            "additions": 165,
            "deletions": 45,
            "overlap_with_pr_numbers": [42]
        }
    ]
}
```

**5b. Human-readable analysis plan:** `temp/pr-merge-pipeline/pr_analysis_plan_{ts}.md`

This file is named `*_plan_*.md` so `audit-impl` can discover it as the baseline specification.

```markdown
# PR Analysis: Integration into {base_branch}

**Date:** {YYYY-MM-DD}
**Base Branch:** {base_branch}
**Integration Branch:** integration/pr-merge-YYYYMMDD-HHMMSS
**PRs Analyzed:** {count}

## Merge Order

1. PR #{number} — "{title}" (complexity: simple)
2. PR #{number} — "{title}" (complexity: needs_check)
...

## File Overlap Matrix

| PR | Files | Overlaps With |
|----|-------|---------------|
| #{number} | {file list} | None |
| #{number} | {file list} | PR #{number} (src/auth.py) |

## Per-PR Assessment

### PR #{number}: "{title}"
- **Branch:** {branch}
- **Complexity:** simple / needs_check
- **Rationale:** {why this complexity tag was assigned}
- **Key files:** {list}
- **Risk notes:** {any concerns}

{If requirements_section is non-empty, include this block so reviewers can trace intent:}
#### Requirements

{requirements_section from PR body}

{repeat for each PR}

## Integration Strategy

{2–3 sentences describing the overall merge strategy and key risk areas}
```

### Step 6: Verify and Report

Verify:
- `pr_order_{ts}.json` is valid JSON and parseable
- Every listed PR number appears exactly once
- `integration_branch` field is set

Report to terminal:
- Order file path
- Analysis file path
- Number of PRs: {simple_count} simple, {needs_check_count} needs_check
- Proposed integration branch name
- Any PRs flagged as high risk

## Output Location

```
temp/pr-merge-pipeline/
├── pr_order_{ts}.json              # Machine-readable manifest (captured by recipe)
└── pr_analysis_plan_{ts}.md        # Human-readable analysis (discovered by audit-impl)
```

## Output Fields (for recipe capture)

After writing all output files and printing the terminal report, emit the following
structured output tokens as the very last lines of your text output:

```
pr_order_file={absolute_path_to_pr_order_json}
analysis_file={absolute_path_to_pr_analysis_plan_md}
integration_branch={integration_branch_name}
pr_count={total_pr_count}
simple_count={simple_pr_count}
needs_check_count={needs_check_pr_count}
```

## Related Skills

- **`/autoskillit:merge-pr`** — Merges individual PRs from this skill's ordered list
- **`/autoskillit:make-plan`** — Called for complex PRs that need conflict resolution plans
- **`/autoskillit:audit-impl`** — Receives `temp/pr-merge-pipeline/` as plans_input
