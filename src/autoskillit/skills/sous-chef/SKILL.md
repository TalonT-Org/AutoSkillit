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

## CONTEXT LIMIT ROUTING — MANDATORY

When `run_skill` returns `needs_retry=true` for **any step**:

- **If `retry_reason: resume` AND the step defines `on_context_limit`** → follow `on_context_limit`.
  The worktree or partial state is on disk; route to the designated recovery step
  (typically `test` or `retry_worktree`) to check whether partial work was sufficient.
- **If `retry_reason: resume` AND the step has no `on_context_limit`** → fall through to `on_failure`.
- **If `retry_reason: drain_race` AND the step defines `on_context_limit`** → follow `on_context_limit`.
  The channel signal confirmed session completion; stdout was not fully flushed before kill.
  Partial progress is confirmed — treat identically to `resume` for routing purposes.
- **If `retry_reason: drain_race` AND the step has no `on_context_limit`** → fall through to `on_failure`.
- **If `retry_reason: empty_output`** → fall through to `on_failure`. The session produced no
  output; there is no partial state on disk. Do NOT route to `on_context_limit` even if defined.
- **If `retry_reason: path_contamination`** → fall through to `on_failure`. The session wrote
  files outside its working directory. This is a CWD boundary violation, not a context limit.
  Do NOT route to `on_context_limit` even if defined.
- **If `retry_reason: early_stop` or `zero_writes`** → fall through to `on_failure`.

**For `implement-worktree-no-merge` specifically:**
- `on_context_limit` routes to `retry_worktree` in standard recipes.
- Use `/autoskillit:retry-worktree` — pass the existing `worktree_path` from the
  partial session's output. The worktree is on disk with all commits made so far.
- **Do NOT call `implement-worktree-no-merge` again.** A new call creates a fresh
  timestamped worktree, discarding all partial progress.

When a completed worktree implementation needs to be redone (e.g., after a plan revision):
- Call `implement-worktree-no-merge` on the revised plan (creates a fresh worktree).
- Clean up the old worktree explicitly if needed.

Summary: `needs_retry=true` + `retry_reason=resume` or `drain_race` + step has `on_context_limit` → follow `on_context_limit`.
         `needs_retry=true` + `retry_reason=resume` or `drain_race` + no `on_context_limit` → `on_failure`.
         `needs_retry=true` + any other `retry_reason` → `on_failure` (no partial progress).

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
plan_parts = /abs/path/to/plan_part_a_....md
/abs/path/to/plan_part_b_....md
```

Act on this list as follows:

1. Implement parts in the **order listed** (top to bottom).
2. **Merge each part** (`merge_worktree`) before moving to the next.
3. Each subsequent part's worktree must be created from the post-merge state of
   the base branch — not from the original base commit.
4. **Never batch-implement** multiple parts from the same base commit.

---

## MULTIPLE ISSUES — MANDATORY

When the user provides **more than one issue or task** in a single request:

1. **If the user says "parallel"** (or "run in parallel", "simultaneously", "at the
   same time", "concurrently") → launch N independent pipeline sessions **immediately**.
   No questions, no pushback, no alternative suggestions.

2. **If the user says "sequential"** (or "one at a time", "in order", "one by one") →
   run them one at a time without asking.

3. **If the user does not specify** → ask **exactly one question** using AskUserQuestion:
   > "Do you want to run these sequentially (one at a time) or in parallel (all at once)?"
   Present exactly **two options**. Nothing else.

**NEVER:**
- Claim "the recipe handles one issue at a time" — each pipeline instance is fully
  independent (separate clones, branches, PRs). Parallel execution is fully supported.
- Suggest switching to `implementation-groups` — that recipe is for coordinated
  multi-issue planning with a shared plan, not independent parallel execution.
- Suggest picking a subset of the given issues — the user chose the scope.
- Offer any option other than sequential or parallel when asking.
- Ask the user to clarify scope, prioritization, or issue ordering.

---

## MERGE PHASE — MANDATORY

This rule applies whenever the orchestrator must merge **one or more open PRs**, whether
produced by a single pipeline or by N parallel pipelines.

### 1. Detect merge queue availability — once per orchestration session

Before initiating any merge, run the following detection step via `run_cmd` (not a
headless session):

```bash
REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner) &&
OWNER=${REPO%%/*} && REPO_NAME=${REPO##*/} &&
BRANCH="<base_branch>" &&    # substitute the PR's target branch (e.g. "main", "integration")
gh api graphql -f query="query {
  repository(owner:\"$OWNER\", name:\"$REPO_NAME\") {
    mergeQueue(branch:\"$BRANCH\") { id }
  }
}" | jq -r 'if .data.repository.mergeQueue != null then "true" else "false" end' || echo false
```

Capture the result as `queue_available`. If `gh api graphql` fails (auth error, network
error), the `|| echo false` fallback ensures `queue_available` defaults to `"false"`,
routing to the safe sequential (non-queue) path rather than leaving the variable unset.

Run this **once per orchestration run**, not per-PR. The `implementation` recipe performs
this detection automatically via the `check_merge_queue` step — **do not repeat it
manually when following a recipe**.

### 2. Route based on queue availability

**When `queue_available == true`:**
GitHub's merge queue serializes concurrent merges. Each pipeline's
`enable_auto_merge → wait_for_queue` sequence is safe to proceed in parallel — the
queue handles ordering. No additional sequencing is required from the orchestrator.

**When `queue_available == false`:**
PRs MUST be merged **one at a time**. The orchestrator must wait for each merge
(the `wait_for_direct_merge` step) to complete before advancing to the next PR.

- If following a recipe: the recipe's sequential-merge loop is authoritative. Do not
  skip or compress loop iterations.
- If merging multiple PRs collected from parallel pipelines: route through the
  `merge-prs` recipe for batch sequential merging.
- **NEVER** execute two `direct_merge` steps concurrently on a non-queue branch.

### 3. NEVER bypass recipe merge steps

**NEVER use `run_cmd` with `gh pr merge` to merge a PR outside of a named recipe
step.** All PR merges must flow through the recipe's `merge_pr`, `direct_merge`, or
`enable_auto_merge` steps. Bypassing these steps skips CI enforcement, conflict
detection, and conflict routing.

### 4. Merge conflict failure handling

When `wait_for_direct_merge` returns `closed` (PR was closed due to a stale base):

- **Route to `on_failure`** — the recipe's `direct_merge_conflict_fix` step handles
  rebase-and-retry automatically.
- **NEVER use `run_cmd` for git investigation** (git rebase, git log, git reset,
  git merge). The `resolve-merge-conflicts` skill run by `direct_merge_conflict_fix`
  has full diagnostic access.
- **NEVER abandon a pipeline** because merge failed — route through the conflict
  recovery cycle until the PR merges or escalation is required.
