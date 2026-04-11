# Getting Started

Walk through the implementation workflow end-to-end. You'll give AutoSkillit a GitHub issue, and it will plan, implement, test, and open a PR — all automatically.

## Prerequisites

- AutoSkillit installed (`autoskillit doctor` should pass)
- A GitHub repository with an open issue to implement
- `gh auth login` completed

## Start the Recipe

```bash
cd your-project
autoskillit order implementation
```

Select `implementation` from the menu, then confirm the launch.

## Provide the Ingredients

AutoSkillit asks for the task details. You can:

- **Paste a GitHub issue URL** — AutoSkillit fetches the title, body, and comments
- **Describe the task** in plain text
- **Both** — paste the URL and add extra context

Example:
```
Task: https://github.com/your-org/your-repo/issues/42
```

AutoSkillit fills in the rest automatically: it detects your repository, base branch, and creates a feature branch named from the issue (e.g., `fix-auth-regression/42`).

## What Happens Next

The pipeline runs through these stages without intervention:

### 1. Clone and Setup (~1 min)
Your repo is cloned into an isolated directory. Your working tree is never touched. A feature branch is created and published.

### 2. Planning (~5-7 min)
A headless Claude session analyzes your codebase with parallel subagents, designs the best technical approach, and writes a detailed implementation plan. If the plan is large, it's split into sequential parts.

### 3. Dry Walkthrough (~4-5 min)
The plan is validated against the actual codebase. Missing files, wrong function signatures, and broken assumptions are caught and fixed in the plan before any code is written.

### 4. Implementation (~6-10 min)
Code changes are made in an isolated git worktree, committed phase by phase. If the session runs out of context, it automatically resumes where it left off.

### 5. Testing (~1 min)
Your project's test suite runs. If tests fail, a fix skill automatically diagnoses and resolves the failures (up to 3 attempts).

### 6. Quality Audit (~2-5 min)
The full implementation is diffed against the plan and audited for correctness, scope, and test coverage. If the audit fails, the pipeline re-plans and re-implements the gaps.

### 7. PR Creation (~7-8 min)
A PR is opened with architecture diagrams, a structured summary, and `Closes #42`. Token usage is included in the PR body.

### 8. Automated Review (~6-10 min)
Parallel audit subagents review the PR across multiple dimensions (architecture, tests, bugs, cohesion, and more). Inline comments are posted to the PR. If changes are requested, they're applied automatically.

### 9. CI and Merge Queue (~5-15 min)
CI is monitored. If it fails, the pipeline diagnoses and fixes the failure. Once CI passes, the PR is enrolled in the merge queue (if enabled).

## When You're Done

After the pipeline completes, you're asked whether to delete the clone directory. Keep it if you want to inspect the work; delete it to clean up.

The PR is open and ready for human review or automatic merge.

## Monitoring Progress

During the run, you can see each step executing in your terminal. The orchestrator shows tool calls and their results as they complete.

## Common Variations

### No GitHub issue — just a description
Skip the issue URL and describe the task directly:
```
Task: Add rate limiting to the /api/search endpoint with a 100 req/min limit per API key
```

### Skip the PR
Set `open_pr` to `false` when asked for ingredients. Changes merge directly to your base branch.

### Skip the audit
Set `audit` to `false` for faster runs when you trust the implementation.

## Typical Timing

Based on real pipeline runs:

| Stage | Typical Duration |
|-------|-----------------|
| Clone + setup | ~1 min |
| Planning | 5-7 min |
| Dry walkthrough | 4-5 min |
| Implementation | 6-10 min |
| Testing | 1-2 min |
| Quality audit | 2-5 min |
| PR + review | 13-18 min |
| CI + merge queue | 5-15 min |
| **Total** | **~45-60 min** |

## Other Recipes

### Fixing bugs: `remediation`
When you have a bug or regression, use the remediation recipe instead. It starts with deep investigation and root-cause analysis before planning:

```bash
autoskillit order remediation
```

See **[Recipes](recipes/overview.md)** for details.

### Large documents: `implementation-groups`
For architecture proposals, migration plans, or large feature specs, use `implementation-groups`. It decomposes the document into ordered groups and implements each one:

```bash
autoskillit order implementation-groups
```

### Consolidating PRs: `merge-prs`
When you have multiple open PRs to merge together:

```bash
autoskillit order merge-prs
```

## Interactive Skills: Cook

For one-off tasks without a full pipeline, launch an interactive session with all skills available:

```bash
autoskillit cook
```

Then use any skill as a slash command: `/autoskillit:investigate`, `/autoskillit:review-pr`, `/autoskillit:audit-arch`, etc.

## Next Steps

- **[Recipes](recipes/overview.md)** — All recipes with ingredients and flow diagrams
- **[CLI Reference](cli.md)** — All commands and options
- **[Configuration](configuration.md)** — Customize test commands, safety settings, and more
