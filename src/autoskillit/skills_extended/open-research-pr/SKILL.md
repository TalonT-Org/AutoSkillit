---
name: open-research-pr
categories: [research]
description: Open a research PR with experiment design diagrams and structured body composition. Implements the open-pr pattern for research pipelines. See issue #593.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: open-research-pr] Opening research pull request...'"
          once: true
---

# Open Research PR Skill

Open a GitHub PR for a completed research worktree. Handles experiment design
diagram embedding and structured PR body composition following the open-pr pattern.

## Arguments

`/autoskillit:open-research-pr {worktree_path} {base_branch} {task} {report_path}`

- **worktree_path** — Absolute path to the research worktree
- **base_branch** — Target branch for the PR
- **task** — Research question or topic (used for PR title)
- **report_path** — Path to the research report artifact (embedded in PR body)

## When to Use

Called by the research recipe after `push_branch` to open the research PR. Embeds
experiment design artifacts and structures the PR body for research quality review.

## Critical Constraints

**NEVER:**
- Auto-merge or approve the PR — research PRs are for human review only
- Push without a prior `git push -u origin HEAD`

**ALWAYS:**
- Embed experiment design diagrams and report path in the PR body
- Emit `pr_url` as an absolute GitHub URL on the final output line

## Output

Emit on the final line of output:

```
pr_url = {url}
%%ORDER_UP%%
```
