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

- **If `retry_reason: resume` AND `subtype: stale`** → re-execute the same step (decrement the
  retries counter). A stale session was killed by the hung-process watchdog — this is NOT a
  context limit. Do NOT follow `on_context_limit`. If retries are exhausted, follow `on_exhausted`.
- **If `retry_reason: resume` AND `subtype≠stale` AND the step defines `on_context_limit`** → follow `on_context_limit`.
  The worktree or partial state is on disk; route to the designated recovery step
  (typically `test` or `retry_worktree`) to check whether partial work was sufficient.
- **If `retry_reason: resume` AND `subtype≠stale` AND the step has no `on_context_limit`** → fall through to `on_failure`.
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
- **If `retry_reason: stale`** → decrement the `retries` counter for this step.
  Re-execute the same step if retries remain. If retries are exhausted, fall through
  to `on_failure`. Do NOT route to `on_context_limit` — stale is a transient failure,
  not a context limit. No partial progress is assumed.

**For `implement-worktree-no-merge` specifically:**
- `on_context_limit` routes to `retry_worktree` in standard recipes.
- Use `/autoskillit:retry-worktree` — pass the existing `worktree_path` from the
  partial session's output. The worktree is on disk with all commits made so far.
- **Do NOT call `implement-worktree-no-merge` again.** A new call creates a fresh
  timestamped worktree, discarding all partial progress.

When a completed worktree implementation needs to be redone (e.g., after a plan revision):
- Call `implement-worktree-no-merge` on the revised plan (creates a fresh worktree).
- Clean up the old worktree explicitly if needed.

Summary: `needs_retry=true` + `retry_reason=resume` + `subtype=stale` → re-execute step (decrement retries; on_exhausted when budget gone).
         `needs_retry=true` + `retry_reason=resume` + `subtype≠stale` + step has `on_context_limit` → follow `on_context_limit`.
         `needs_retry=true` + `retry_reason=resume` + `subtype≠stale` + no `on_context_limit` → `on_failure`.
         `needs_retry=true` + `retry_reason=drain_race` + step has `on_context_limit` → follow `on_context_limit`.
         `needs_retry=true` + `retry_reason=drain_race` + no `on_context_limit` → `on_failure`.
         `needs_retry=true` + `retry_reason=stale` → decrement retries counter → `on_failure` when exhausted (no partial progress, not a context limit).
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

## PARALLEL STEP SCHEDULING — MANDATORY

This rule applies whenever you are running **multiple pipelines in parallel** (run_mode=parallel
or user says "parallel"). Within each batched round, pipeline steps have two speeds:

**Fast steps** — MCP tool calls that complete in seconds:
`run_cmd`, `clone_repo`, `create_unique_branch`, `fetch_github_issue`,
`claim_issue`, `merge_worktree`, `test_check`, `reset_test_dir`, `classify_fix`

**Slow steps** — headless sessions that take minutes:
Any `run_skill` invocation (investigate, implement, audit, review, etc.)

### Wavefront Scheduling Rule

1. **Complete all fast steps for ALL pipelines first.** Before launching any slow step,
   advance every pipeline through its pending fast steps. Continue re-inspecting after
   each fast-step batch until no pipeline has a fast step pending.

2. **Launch all slow steps together in one parallel batch.** Once all pipelines are aligned
   at a slow step boundary (every pipeline's next pending step is a `run_skill`), launch
   all of them simultaneously so they overlap in wall-clock time.

3. **Never launch a slow step for one pipeline while another pipeline still has fast steps
   pending.** This is the most critical rule: a batched round waits for the slowest step in
   the batch. A fast step launched alongside a slow step completes instantly but sits idle
   until the slow step finishes — wasting wall-clock time and blocking re-inspection.

### Rationale

Batched rounds wait for the **slowest step** in the batch. If a slow `run_skill` is launched
alongside a fast `run_cmd`, the fast step completes instantly but cannot trigger the next
fast step for its pipeline until the entire batch (including the slow session) finishes.
Draining all fast steps first ensures every pipeline arrives at the slow-step boundary
simultaneously, after which all slow steps run in parallel and their wall-clock time overlaps.

---

## STEP NAME IMMUTABILITY — MANDATORY

The `step_name` passed to `run_skill` (and all other recipe-step tools that accept
`step_name`) must be the **exact value from the recipe YAML `with:` block**.

**NEVER** append clone numbers, instance indices, retry counts, or any other
disambiguation strings. The telemetry layer aggregates all invocations of the same
logical step automatically — suffixing produces garbage rows in token and timing tables.

Correct:
```yaml
with:
  step_name: implement
```

Wrong (produces garbage):
```yaml
with:
  step_name: implement-30   # ← NEVER DO THIS
```

This rule applies whether running sequential or parallel pipelines. Each clone or
parallel run of the same step reports under the same canonical step name.

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

Run this **once per orchestration run**, not per-PR.

After detecting queue availability, also detect auto-merge availability:

```bash
gh api graphql -f query="query {
  repository(owner:\"$OWNER\", name:\"$REPO_NAME\") {
    autoMergeAllowed
  }
}" | jq -r '.data.repository.autoMergeAllowed // false' || echo false
```

Capture the result as `auto_merge_available`. If detection fails, default to `"false"`.

**Note:** All three recipes (`implementation`, `implementation-groups`, `remediation`)
perform both detections automatically via `check_merge_queue` + `check_auto_merge` —
**do not repeat them manually when following a recipe**.

### 2. Route based on queue availability and auto-merge availability

The recipes route on the four-cell matrix `queue_available × auto_merge_available`:

| `queue_available` | `auto_merge_available` | Recipe path                                    |
|-------------------|------------------------|------------------------------------------------|
| `true`            | `true`                 | `enable_auto_merge` → `wait_for_queue`         |
| `true`            | `false`                | `queue_enqueue_no_auto` → `wait_for_queue`     |
| `false`           | `true`                 | `direct_merge` → `wait_for_direct_merge`       |
| `false`           | `false`                | `immediate_merge` → `wait_for_immediate_merge` |

**When `queue_available == true`:** GitHub's merge queue intercepts every merge
request on the branch regardless of the `--auto` flag. Both queue cells route
through `wait_for_queue` (the merge-queue-aware waiter). The
`enable_auto_merge` cell uses `--auto` so the queue serializes via GitHub
auto-merge; the `queue_enqueue_no_auto` cell (condition:
`queue_available == true and auto_merge_available == false`) uses plain `--squash`
because the repository's `autoMergeAllowed=false` setting causes `--auto` to be
rejected by the API auto-merge gate **before** the queue interception.

**When `queue_available == false`:** there is no queue, so behaviour matches
the historical paths — `direct_merge` waits via auto-merge, `immediate_merge`
executes synchronously.

- If following a recipe: `route_queue_mode` selects the correct cell
  automatically from `context.queue_available` and `context.auto_merge_available`.
- **NEVER use** `gh pr merge --squash --auto` when `auto_merge_available == false`,
  regardless of `queue_available`. The `--auto` flag is rejected by GitHub's API
  auto-merge gate before the queue intercepts. Use plain `gh pr merge --squash`;
  if a queue exists on the branch the queue still enqueues the call.
- **NEVER** route a queue+no-auto enqueue call through `wait_for_immediate_merge`
  — its 5-minute poll is too short for a busy queue and on timeout the recipe
  reports `merge unconfirmed` even though the PR will eventually merge.

For ad-hoc (off-recipe) merges:
- If merging multiple PRs collected from parallel pipelines: route through the
  `merge-prs` recipe for batch sequential merging.

### 3. NEVER bypass recipe merge steps

**NEVER use `run_cmd` with `gh pr merge` to merge a PR outside of a named recipe
step.** All PR merges must flow through the recipe's `merge_pr`, `direct_merge`,
`immediate_merge`, `enable_auto_merge` or `queue_enqueue_no_auto` steps. Bypassing these steps skips CI
enforcement, conflict detection, and conflict routing.

### 4. Merge conflict failure handling

When `wait_for_direct_merge` or `wait_for_immediate_merge` returns `closed` (PR was
closed due to a stale base):

- **Route to the appropriate conflict fix** — `direct_merge_conflict_fix` or
  `immediate_merge_conflict_fix` handles rebase-and-retry automatically.
- **NEVER use `run_cmd` for git investigation** (git rebase, git log, git reset,
  git merge). The `resolve-merge-conflicts` skill run by `direct_merge_conflict_fix`
  and `immediate_merge_conflict_fix` has full diagnostic access.
- **NEVER abandon a pipeline** because merge failed — route through the conflict
  recovery cycle until the PR merges or escalation is required.

---

## QUOTA WAIT PROTOCOL — MANDATORY

When quota-related events occur during pipeline execution:

### PreToolUse Denial (run_skill blocked)

When a `run_skill` call is DENIED by the quota guard hook:

1. This is a **temporary** block — NOT a permanent error, NOT a pipeline failure.
2. The deny message contains a `run_cmd` sleep command with the required duration.
3. **MANDATORY**: Execute the sleep command via `run_cmd` immediately.
4. After the sleep completes, retry the **exact same** `run_skill` call with
   identical arguments (`skill_command`, `cwd`, `model`, `step_name`).
5. Do NOT follow `on_failure`. Do NOT report to the user. Do NOT skip the step.
6. Before executing, state aloud: "Quota denied. Sleeping N seconds, then retrying."

### Post-Execution Warning (quota high after run_skill)

When `run_skill` output contains `--- QUOTA WARNING ---`:

1. The warning contains a `run_cmd` sleep command.
2. **MANDATORY**: Execute the sleep command via `run_cmd` BEFORE calling the next
   `run_skill` (whether it is the next pipeline step or a retry).
3. After sleeping, proceed normally with the next step.
4. Before executing, state aloud: "Quota warning. Sleeping N seconds before next step."

### Key Rules

- Quota denials are **always temporary**. The API enforces multiple rate-limit windows (e.g. one-minute, one-hour, five-hour, one-day). The guard waits for the most constrained window — the one that resets latest among all windows above the threshold — to reset before retrying.
- A denied `run_skill` has **zero side effects** — no partial state, no worktree changes.
  Retrying with the same arguments is always safe.
- Multiple consecutive denials may occur if the sleep duration was underestimated.
  Keep sleeping and retrying until the call succeeds.
- NEVER use `AskUserQuestion` for quota events — they are fully automated.
