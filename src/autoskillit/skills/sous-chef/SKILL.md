<!-- Internal bootstrap document — not a user-invocable skill.
     Injected by open_kitchen() into every orchestrator session. -->

# Sous Chef: Global Orchestration Rules

These rules apply to ALL orchestration sessions, whether following a recipe or
operating ad-hoc. They are permanent — they cannot be overridden by individual
recipe kitchen_rules or plan-file instructions.

---

## MULTI-PART PLAN SEQUENCING — MANDATORY

When `plan_parts` contains more than one file (Part A, Part B, …):

1. Process parts **strictly in order**: A before B, B before C, etc.
2. After implementing each part: **test it** (`test_check`) and **merge it**
   (`merge_worktree`) into the base branch before implementing the next part.
3. The next part's worktree **MUST** be created from the post-merge state of the
   base branch — never from the same commit as the previous part.
4. **Never invoke `implement-worktree-no-merge` for Part N+1 while Part N's
   worktree is unmerged.**

This rule applies whether or not you are following a recipe, and whether or not
Part B's plan file says "Part A is a prerequisite." The orchestrator is
responsible for enforcing this regardless of what the plan says.

---

## RETRY-WORKTREE VS RE-INVOKE — MANDATORY

When `run_skill_retry` returns `needs_retry=true` for an
`implement-worktree-no-merge` call:

- **Use `/autoskillit:retry-worktree`** — pass the existing `worktree_path`
  from the partial session's output. The worktree is on disk with all commits
  made so far; retry-worktree continues from where context was exhausted.
- **Do NOT call `implement-worktree-no-merge` again.** A new call creates a
  brand-new timestamped worktree, discarding all partial progress.

When a completed worktree implementation needs to be redone (e.g., after a plan
revision):
- Call `implement-worktree-no-merge` on the revised plan (creates a fresh worktree).
- Clean up the old worktree explicitly if needed.

Summary: `needs_retry=true` → `retry-worktree`. Plan revision → new
`implement-worktree-no-merge` call.

---

## AUDIT-IMPL ACROSS MULTI-GROUP PIPELINES

`audit-impl` uses a SHA-based diff: it compares the worktree HEAD against the
merge-base with the base branch, scoping the diff to exactly that group's changes.

Rules:
- Pass the **specific plan file** for each group (not a combined plan).
- Run `audit-impl` **before merging** — it inspects the unmerged worktree diff.
- After merging a group, the next group's `audit-impl` will correctly see only
  that group's diff against the now-updated base branch.
- Never run one `audit-impl` call against multiple merged groups — the diff scope
  will be too broad and the audit will be inaccurate.

---

## READING AND ACTING ON `plan_parts=` OUTPUT

`make-plan` emits `plan_parts=` as a flat newline-delimited ordered list of
absolute paths:

```
plan_parts=/abs/path/to/plan_part_a_....md
/abs/path/to/plan_part_b_....md
```

Act on this list as follows:

1. Implement parts in the **order listed** (top to bottom).
2. **Merge each part** (`merge_worktree`) before moving to the next.
3. Each subsequent part's worktree must be created from the post-merge state of
   the base branch — not from the original base commit.
4. **Never batch-implement** multiple parts from the same base commit.
