---
name: resolve-research-review
categories: [research]
description: Apply changes_requested review comments to a research worktree. Resolves inline GitHub review comments and commits fixes.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: resolve-research-review] Resolving research review comments...'"
          once: true
---

# Resolve Research Review Skill

Apply `changes_requested` review comments from a research PR to the research
worktree. Reads open review threads, resolves each finding, and commits the
changes so a follow-up push closes the review cycle.

## Arguments

`/autoskillit:resolve-research-review {worktree_path} {base_branch}`

- **worktree_path** — Absolute path to the research worktree
- **base_branch** — Target branch for the PR

## When to Use

Called by the research recipe when `review_research_pr` routes `changes_requested`.
Bounded by `retries: 2` — on exhaustion routes to `research_complete`.

## Critical Constraints

**NEVER:**
- Merge or push the branch — the recipe's `re_push_research` step handles push
- Dismiss review threads without addressing the underlying comment

**ALWAYS:**
- Commit all fixes before returning control to the orchestrator
- Read open review threads before making any changes

## Output

Emit on the final line of output:

```
%%ORDER_UP%%
```
