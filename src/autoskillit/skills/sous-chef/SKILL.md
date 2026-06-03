---
name: sous-chef
backend_requirements: [claude-code]
uses_capabilities: [cross_skill_ref, open_kitchen, run_skill, test_check]
description: Internal bootstrap document injected by open_kitchen into every orchestrator session.
---
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

## SKILL_COMMAND FORMATTING — MANDATORY

When calling `run_skill`, the `skill_command` argument MUST be a space-separated token
string — never a structured document or markdown section list.

- Substitute `${{ context.* }}` and `${{ inputs.* }}` placeholders with their resolved
  values and pass the result **VERBATIM** to `run_skill`.
- **Do NOT** add markdown headers (`##`), labels, notes, or explanatory prose to
  `skill_command`. It is not a document — it is a command string.
- Path arguments are single tokens: `/path/to/file.md` — not a labeled section.
- Extra arguments from a step `note:` are appended as space-separated tokens.

**Wrong:** `/autoskillit:implement-worktree-no-merge\n\n## Plan Path\n/path/plan.md\n\n## Branch\nimpl-926`
**Right:** `/autoskillit:implement-worktree-no-merge /path/plan.md impl-926`

This applies to ALL skills, including bare-placeholder steps where you supply values
at runtime (`/autoskillit:arch-lens-{slug} {context_path}` → substitute, then pass verbatim).

---

## CONTEXT LIMIT ROUTING — MANDATORY

When `run_skill` returns `needs_retry=true` for **any step**:

- **If `retry_reason: resume` AND `subtype: stale`** → re-execute the same step (decrement the
  retries counter). A stale session was killed by the hung-process watchdog — this is NOT a
  context limit. Do NOT follow `on_context_limit`. If retries are exhausted, follow `on_exhausted`.
- **If `retry_reason: resume` AND `subtype≠stale` AND the step defines `on_context_limit`** → follow `on_context_limit`.
  The worktree or partial state is on disk; route to the designated recovery step
  (typically `test` or `retry_worktree`) to check whether partial work was sufficient.
  API infrastructure errors (overload, 529, ECONNRESET) also produce `retry_reason=resume`
  with `infra_exit_category="api_error"` — route them identically to context exhaustion.
  The `infra_exit_category` field is informational for telemetry and is NOT used for
  routing decisions — routing is driven exclusively by `retry_reason`.
- **If `retry_reason: resume` AND `subtype≠stale` AND the step has no `on_context_limit`** → fall through to `on_failure`.
- **If `retry_reason: drain_race` AND the step defines `on_context_limit`** → follow `on_context_limit`.
  The channel signal confirmed session completion; stdout was not fully flushed before kill.
  Partial progress is confirmed — treat identically to `resume` for routing purposes.
- **If `retry_reason: drain_race` AND the step has no `on_context_limit`** → fall through to `on_failure`.
- **If `retry_reason: completed_no_flush` AND the step defines `on_context_limit`** → follow `on_context_limit`.
  The session exited with empty stdout but write evidence confirms files were written to the worktree.
  Partial progress is confirmed — treat identically to `resume` for routing purposes.
- **If `retry_reason: completed_no_flush` AND the step has no `on_context_limit`** → fall through to `on_failure`.
- **If `retry_reason: empty_output`** → fall through to `on_failure`. The session produced no
  output AND no write evidence (no Write/Edit calls, no filesystem writes). Do NOT route to `on_context_limit` even if defined.
- **If `retry_reason: path_contamination`** → fall through to `on_failure`. The session wrote
  files outside its working directory. This is a CWD boundary violation, not a context limit.
  Do NOT route to `on_context_limit` even if defined.
- **If `retry_reason: contract_recovery` AND `has_progress_evidence` is true AND the step
  defines `on_context_limit`** → follow `on_context_limit`. The session wrote files but omitted
  the structured output token. Partial progress is confirmed on disk.
- **If `retry_reason: contract_recovery` AND `has_progress_evidence` is false** → fall through to `on_failure`.
- **If `retry_reason: thinking_stall` AND `lifespan_started` is true AND the step defines
  `on_context_limit`** → follow `on_context_limit`. The model consumed tokens (thinking
  blocks) but produced no final output. Prior tool calls suggest partial progress on disk.
- **If `retry_reason: thinking_stall` AND `lifespan_started` is false** → fall through to `on_failure`.
  No progress was made.
- **If `retry_reason: idle_stall` AND `lifespan_started` is true AND the step defines
  `on_context_limit`** → follow `on_context_limit`. The idle watchdog killed the session,
  but prior tool calls suggest partial progress on disk.
- **If `retry_reason: idle_stall` AND `lifespan_started` is false** → fall through to `on_failure`.
  No progress was made.
- **If `retry_reason: rate_limited` AND the step defines `on_rate_limit`** → follow `on_rate_limit`.
  HTTP 429 or text-based rate-limit signal detected. Route to the designated recovery step
  (typically `test`) to continue after the rate limit window resets. Partial progress may
  exist on disk.
- **If `retry_reason: rate_limited` AND the step has no `on_rate_limit`** → fall back to `on_context_limit`.
  Backward compatible with recipes that do not yet declare `on_rate_limit`. If the step also
  has no `on_context_limit`, fall through to `on_failure`.
- **If `retry_reason: early_stop` AND `has_progress_evidence` is true in the result AND the step
  defines `on_context_limit`** → follow `on_context_limit`. The model made progress (wrote files
  or created a worktree) but stopped before emitting the completion marker. Partial progress
  exists on disk.
- **If `retry_reason: early_stop` AND `has_progress_evidence` is false** → fall through to `on_failure`.
- **If `retry_reason: zero_writes` AND `has_progress_evidence` is true in the result AND the step
  defines `on_context_limit`** → follow `on_context_limit`. The model made filesystem contact
  but made no Write/Edit tool calls. Partial progress may exist on disk.
- **If `retry_reason: zero_writes` AND `has_progress_evidence` is false** → fall through to `on_failure`.
- **If `retry_reason: stale`** → decrement the `retries` counter for this step.
  Re-execute the same step if retries remain. If retries are exhausted, fall through
  to `on_failure`. Do NOT route to `on_context_limit` — stale is a transient failure,
  not a context limit. No partial progress is assumed.

**Worktree-stale carve-out:** When a step that invokes a worktree-creating skill
(`implement-worktree-no-merge`, `implement-worktree`, `implement-experiment`) returns
`retry_reason: stale` (or `retry_reason: resume` with `subtype: stale`), re-execute the
step **without consuming the retries budget**. Stale means the session produced nothing
useful — the worktree orphan concern that motivates `retries: 0` does not apply.
This is a one-shot retry: if the retry also goes stale, fall through to `on_failure`.
Before re-executing, if the stale result captured `worktree_path`, remove the empty
worktree (`git worktree remove --force <path>`) to prevent orphaned worktrees.

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
         `needs_retry=true` + `retry_reason=completed_no_flush` + step has `on_context_limit` → follow `on_context_limit`.
         `needs_retry=true` + `retry_reason=completed_no_flush` + no `on_context_limit` → `on_failure`.
         `needs_retry=true` + `retry_reason=empty_output` → `on_failure`.
         `needs_retry=true` + `retry_reason=path_contamination` → `on_failure`.
         `needs_retry=true` + `retry_reason=clone_contamination` → `on_failure`.
         `needs_retry=true` + `retry_reason=contract_recovery` + `has_progress_evidence=true` + step has `on_context_limit` → follow `on_context_limit`.
         `needs_retry=true` + `retry_reason=contract_recovery` + `has_progress_evidence=false` → `on_failure`.
         `needs_retry=true` + `retry_reason=thinking_stall` + `lifespan_started=true` + step has `on_context_limit` → follow `on_context_limit`.
         `needs_retry=true` + `retry_reason=thinking_stall` + `lifespan_started=false` → `on_failure`.
         `needs_retry=true` + `retry_reason=idle_stall` + `lifespan_started=true` + step has `on_context_limit` → follow `on_context_limit`.
         `needs_retry=true` + `retry_reason=idle_stall` + `lifespan_started=false` → `on_failure`.
         `needs_retry=true` + `retry_reason=rate_limited` + step has `on_rate_limit` → follow `on_rate_limit`.
         `needs_retry=true` + `retry_reason=rate_limited` + no `on_rate_limit` → follow `on_context_limit` (fallback) or `on_failure`.
         `needs_retry=true` + `retry_reason=early_stop` + `has_progress_evidence=true` + step has `on_context_limit` → follow `on_context_limit`.
         `needs_retry=true` + `retry_reason=early_stop` + `has_progress_evidence=false` → `on_failure`.
         `needs_retry=true` + `retry_reason=zero_writes` + `has_progress_evidence=true` + step has `on_context_limit` → follow `on_context_limit`.
         `needs_retry=true` + `retry_reason=zero_writes` + `has_progress_evidence=false` → `on_failure`.
         `needs_retry=true` + `retry_reason=stale` → decrement retries counter → `on_failure` when exhausted (no partial progress, not a context limit).
         `needs_retry=true` + `retry_reason=stale` + worktree-creating step → one-shot re-execute (bypasses retries budget; on_failure if repeated stale).

**Fallback — `on_failure` is undefined (None):**
If the routing decision is to follow `on_failure` but the step has no `on_failure`
declared, this is a recipe authoring error. Emit the L3 result sentinel with
`success=false` and `reason=missing_on_failure`, then halt. Do NOT improvise a
routing target — the recipe is structurally incomplete.

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
   same time", "concurrently"):

   a. **Build execution map first.** Call `run_skill` with `/autoskillit:build-execution-map --assess-review-approach`
      passing all issue numbers. This produces an `execution_map` JSON artifact at the
      emitted path.

   b. **Read the execution map.** Parse the JSON to extract `groups`, `merge_order`,
      and per-issue `review_approach_recommended` and `investigation_complete` fields.
      Build a set `review_approach_issues` containing the issue numbers where
      `review_approach_recommended` is true. Build a set `investigation_complete_issues`
      containing the issue numbers where `investigation_complete` is true.

   c. **Dispatch groups in order.** For each group in ascending `group` number:
      - If `parallel: true` → launch all issues in the group as independent pipeline
        sessions simultaneously, using the wavefront scheduling rule (defined in the section below).
        When dispatching, include `review_approach: "true"` in ingredients for issues
        whose issue number appears in `review_approach_issues` and `investigate: "false"`
        in ingredients for issues whose issue number appears in `investigation_complete_issues`
        and the user has not explicitly overridden the `investigate` ingredient.
      - If `parallel: false` → run the group's issues one at a time in sequence.
        When dispatching, include `review_approach: "true"` in ingredients for issues
        whose issue number appears in `review_approach_issues` and `investigate: "false"`
        in ingredients for issues whose issue number appears in `investigation_complete_issues`
        and the user has not explicitly overridden the `investigate` ingredient.

   d. **Merge-wait between groups.** Group N+1 must NOT begin cloning until ALL of
      Group N's PRs have merged to the base branch. This ensures every group's clones
      capture a base SHA that includes all prior groups' changes. Use the MERGE PHASE
      rules to merge each group's PRs, following the `merge_order` from the map for
      intra-group merge sequencing.

   e. **Fallback.** If `build-execution-map` fails or returns an error, fall back to
      launching all N pipelines immediately (current behavior). Do not block dispatch
      on map failure.

2. **If the user says "sequential"** (or "one at a time", "in order", "one by one") →
   run them one at a time without asking.

3. **If the user does not specify** → ask **exactly one question** using AskUserQuestion:
   > "Do you want to run these sequentially (one at a time) or in parallel (all at once)?"
   Present exactly **two options**. Nothing else.

**NEVER:**
- Fabricate, invent, or embellish information not supported by the available evidence or code.

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
`claim_issue`, `merge_worktree`, `test_check`, `reset_test_dir`, `classify_fix`,
`push_to_remote`

**Slow steps** — headless sessions that take minutes:
Any `run_skill` invocation (investigate, implement, audit, review_approach, etc.)

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

4. **Advance every active pipeline in every round.** A pipeline is "active" if it has not
   reached `done` or `escalate_stop`. In every batched round, every active pipeline MUST
   receive at least one step — either a fast step is drained or a slow step is launched.
   Never leave an active pipeline idle for an entire round while sibling pipelines are
   progressing. If a pipeline has completed all its `plan_parts` and only has finalization
   steps remaining (push, merge, close), it is still active and must be advanced.

5. **A retrying pipeline does not block sibling pipelines.** When a batched round returns
   with a mix of successful and `needs_retry` results, issue the retry for the failing
   pipeline(s) AND the next steps for the successful pipelines in the same response.
   Route the retry per CONTEXT LIMIT ROUTING (same rules apply — check `retry_reason`,
   `subtype`, `on_context_limit`, etc.). The retrying pipeline rejoins the wavefront at
   whatever step boundary the other pipelines have reached when its retry completes.
   Never hold successful pipelines idle while waiting for a retry to finish.

### Rationale

Batched rounds wait for the **slowest step** in the batch. If a slow `run_skill` is launched
alongside a fast `run_cmd`, the fast step completes instantly but cannot trigger the next
fast step for its pipeline until the entire batch (including the slow session) finishes.
Draining all fast steps first ensures every pipeline arrives at the slow-step boundary
simultaneously, after which all slow steps run in parallel and their wall-clock time overlaps.

---

## EXECUTION MAP — GROUP DISPATCH — MANDATORY

When dispatching from an execution map:

1. **Group iteration is outer loop.** The group number (1, 2, 3, ...) is the primary
   ordering. Within each group, the wavefront scheduling rule governs step interleaving.

2. **Merge-wait is mandatory between groups.** After all pipelines in Group N complete
   (including their merge phase), verify all Group N PRs have merged to the base branch
   before starting Group N+1. This prevents Group N+1 from cloning a stale base.

3. **merge_order governs intra-group PR merge sequencing.** Within a parallel group,
   merge PRs in the order specified by `merge_order` (not by completion time). This
   minimizes merge conflicts by merging simpler changes first.

4. **Single-issue groups skip wavefront.** If a group has `parallel: false` or contains
   only one issue, run it as a single pipeline — no wavefront scheduling needed.

5. **Do not pause for confirmation between groups.** Once merge-wait verifies all
   Group N PRs have merged, dispatch Group N+1 immediately. NEVER use
   AskUserQuestion to ask whether to proceed to the next group.

6. Handle deferred issues before dispatching any group.

   After reading the execution map, check for `has_deferred: true`.

   **If `has_deferred` is false** (no deferrals): proceed directly to Group 1 dispatch.

   **If `has_deferred` is true:**

   **6a. Pre-dispatch freshness check.** Before presenting any escalation question,
   re-query the current label state of ALL unique blocker issue numbers across all
   `deferred_groups` entries' `gated_by` arrays in a single batched GraphQL request using aliases:

   ```graphql
   query {
     i887: repository(owner:"<OWNER>", name:"<REPO>") {
       issue(number:887) { labels(first:20) { nodes { name } } }
     }
     i912: repository(owner:"<OWNER>", name:"<REPO>") {
       issue(number:912) { labels(first:20) { nodes { name } } }
     }
   }
   ```

   Build the alias query from the `gated_by` issue numbers and invoke:
   ```bash
   LABEL_QUERY="query { $(for NUM in $BLOCKER_NUMS; do echo "i${NUM}: repository(owner:\"$OWNER\", name:\"$REPO\") { issue(number:${NUM}) { labels(first:20) { nodes { name } } } }"; done) }"
   gh api graphql -f query="$LABEL_QUERY"
   ```

   For each blocker, check whether the `in-progress` label is still present. If a
   blocker's label has been removed since the map was built, remove it from that deferred
   group's `gated_by` array. If all blockers for a deferred group have cleared,
   all issues in that group are auto-cleared — collect it in an "auto-cleared" list for the
   supplementary map (step 6e). **Never issue individual `gh issue view` calls per blocker
   — always batch into a single GraphQL request.**

   **6b. Present AskUserQuestion for each still-deferred group.** For each deferred group
   where at least one `gated_by` blocker's `in-progress` label is still present, call
   `AskUserQuestion`:

   > "Deferred group {N} ({count} issues: #X title, #Y title, ...) is gated by in-progress
   > issue(s): #M1 (title1), #M2 (title2).
   > Choose:
   > 1. **Wait** — Hold the group; retry after the blocking issues complete
   > 2. **Proceed anyway** — Dispatch all issues in the group now, accepting conflict risk
   > 3. **Drop** — Release all issues in the group from this session"

   **Headless-mode rule (MANDATORY):** When `AskUserQuestion` is denied by the hook
   (the deny message says "proceed without user confirmation" — this refers to general
   tool behavior, NOT this decision), treat the response as **Wait**. Do NOT interpret
   the deny message as permission to proceed. This is the explicit safe default for
   unattended sessions.

   **6c. Route based on user answer (or Wait default):**

   - **Wait**: Hold the entire deferred group. At each group-completion barrier (after all
     `run_skill` calls in a group return — the natural inter-group barrier in both queue-mode
     and classic-mode), re-check ALL outstanding Wait-path blockers via a single batched
     GraphQL aliases query. When all blockers for a deferred group have cleared, move all
     issues in that group to the auto-cleared list for step 6e. After the final group
     completes, if any Wait groups remain with uncleared blockers, report them as skipped
     in the session result.

   - **Proceed**: Add all issues from the deferred group as a **new sequential group**
     inserted at the end of the dispatch sequence (after all other groups). This is transparent
     in both merge modes: in queue-mode (#1268), the pipeline self-merges; in classic mode,
     `merge-prs` handles it. Batch all Proceed groups together into the single final group.

   - **Drop**: Release all issues in the deferred group. If an issue has already been
     claimed (its `in-progress` label is set by this session), call `release_issue` to
     remove it. Then exclude all issues in the group from all dispatch, merge, and reporting.
     Do not mark them as failed — they are not failures.

   **6d. Zero-non-deferred-groups edge case.** When `groups[]` is empty (all assembled
   groups were moved to `deferred_groups[]` by Step 3b partitioning), skip group dispatch
   entirely. Enter an explicit poll loop:

   1. Wait `github.deferred_poll_interval_seconds` (default: 60 seconds between checks).
   2. Re-query ALL outstanding blocker labels via a single batched GraphQL aliases query.
   3. If at least one deferred group becomes unblocked (all its `gated_by` blockers cleared),
      proceed to step 6e for all issues in that group.
   4. Repeat until the first unblocked group is found OR the elapsed time exceeds
      `github.deferred_poll_timeout_seconds` (default: 1800 seconds / 30 minutes).
   5. On timeout: report all deferred groups as skipped. Set session result
      `success: false` with `failure_reason: "All target issues deferred due to
      in-progress conflicts — human decision required"`. Exit.

   **If in headless mode and all issues default to Wait with zero dispatch groups:**
   skip the poll loop and immediately set `success: false` with the above
   `failure_reason`. Do not poll indefinitely in unattended sessions.

   **6e. Supplementary map for auto-cleared and freshness-cleared groups.** When one or
   more Wait/freshness-cleared groups become eligible:

   1. Re-run `build-execution-map` for the newly eligible issues **only** — pass their
      issue numbers as arguments. This is a full analysis (pairwise + cross-assessment),
      not a passthrough. The pre-computed `deferred_groups` ordering is advisory — the
      supplementary BEM performs its own pairwise analysis but can use the prior grouping
      as a starting point when the issue set is unchanged.
   2. If the supplementary map returns `has_deferred=true`, apply steps 6b and 6c to
      the newly deferred groups before dispatching the supplementary groups. In headless
      mode, treat all new deferrals as Wait (same rule as 6b). Groups that remain deferred
      after this re-entry are skipped and reported in the session result.
   3. Dispatch the resulting dispatch groups as additional sequential group(s) appended
      after the last completed group.
   4. Apply the same group-boundary merge-wait rule before dispatching the supplementary
      groups.

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

## PARAMETER FORWARDING — step_name, output_dir, stale_threshold, idle_output_timeout, step_provider

When a recipe step's `with:` block contains `step_name`, you MUST pass it as the
`step_name` parameter of `run_skill`. This enables ingredient lock enforcement,
pipeline dependency checks, and all per-step parameter auto-fills. Omitting it
degrades enforcement and may cause the call to be denied when locks are active.

When a recipe step's `with:` block contains `output_dir`, you MUST pass it as the
`output_dir` parameter of `run_skill`. This controls the write guard — omitting it
causes the session to write to the wrong directory.

When a recipe step has a top-level `stale_threshold` or `idle_output_timeout` field,
pass it as the corresponding `run_skill` parameter. These control session kill thresholds.

When a recipe step has a top-level `provider` field, pass the value as the
`step_provider` parameter of `run_skill`. This controls which LLM provider
(e.g., Minimax, Bedrock) the session uses. Omitting it causes the session to
fall back to the default Anthropic provider, silently ignoring the recipe's
declared provider.

**Example:**
```yaml
implement:
  tool: run_skill
  stale_threshold: 2400
  provider: minimax
  with:
    skill_command: "/implement ..."
    cwd: "${{ context.work_dir }}"
    step_name: implement
    output_dir: "${{ context.work_dir }}/${{ context.autoskillit_temp }}"
```

Call: `run_skill(skill_command=..., cwd=..., step_name="implement", output_dir="...", stale_threshold=2400, step_provider="minimax")`

This provides defense-in-depth: the server resolves parameters server-side, AND the LLM is instructed to forward them.

---

## RUN_PYTHON PARAMETER PARTITIONING — MANDATORY

The `run_python` tool has two distinct parameter scopes. Confusing them causes path
anchoring failures.

**Tool-level parameters** (top-level arguments to `run_python` itself):
- `callable` — dotted path to the Python function
- `args` — dict of keyword arguments forwarded to the callable
- `timeout` — max seconds before abort
- `work_dir` — anchor directory for resolving relative path-like args (`output_dir`, `workspace`, `diagnostics_log_dir`)

**Callable parameters** (keys inside the `args` dict, forwarded to the function):
- Everything the target function's signature declares

`work_dir` appears in BOTH scopes across the recipe — some callables accept `work_dir` as
a function parameter (inside `args`), while others use it only for tool-level path anchoring
(outside `args`). Use the recipe step's YAML structure to determine which scope applies:

**Top-level `work_dir` (path anchoring — NOT forwarded to callable):**
```yaml
annotate_pr_diff:
  tool: run_python
  with:
    callable: autoskillit.smoke_utils.annotate_pr_diff
    work_dir: ${{ context.work_dir }}          # ← tool-level, anchors output_dir
    output_dir: review-pr/iter_0
    cwd: ${{ context.work_dir }}
    pr_number: ${{ context.pr_number }}
```
Call: `run_python(callable=..., work_dir="/abs/path", args={"output_dir": "...", "cwd": "...", "pr_number": "..."})`

**Nested `work_dir` (callable kwarg — forwarded to function):**
```yaml
pre_review_rebase:
  tool: run_python
  with:
    callable: autoskillit.recipe._cmd_rpc.review_path_rebase
    args:
      work_dir: ${{ context.work_dir }}        # ← callable kwarg, NOT tool-level
      base_branch: ${{ inputs.base_branch }}
```
Call: `run_python(callable=..., args={"work_dir": "/abs/path", "base_branch": "main"})`

**NEVER place `work_dir` inside `args` when the callable does not accept it.** Doing so
leaves the tool-level `work_dir` empty and causes `validate_path_arg_anchoring` to reject
the call with: `"arg 'output_dir' is a relative path but work_dir was not provided"`.

---

## MODEL PROPAGATION — MANDATORY

**MODEL PROPAGATION** — When the user specifies a model (e.g. "use opus"), apply it to the `model` parameter of ALL `run_skill` calls for steps that declare a `model:` field — including follow-on steps (retry_worktree, fix, resolve_review, resolve_ci, conflict resolution). All `run_skill` steps in orchestrated recipes must declare a `model:` field; steps that omit it are ineligible for propagation and silently bypass user model selection.

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
BRANCH="<base_branch>" &&    # substitute the PR's target branch (e.g. "main", "develop")
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
- When `queue_available=true` (and `sequential_queue` is not `"true"`): each pipeline's
  implementation recipe handles its own enqueue via `route_queue_mode` →
  `enqueue_to_queue` → `wait_for_queue`. Do NOT invoke `merge-prs`. The orchestrator's
  natural parallel-batch join (waiting for all Group N `run_skill` invocations to
  return) serves as the inter-group barrier — each pipeline only returns after
  `wait_for_queue` reports `merged`, confirming its PR is in the base branch. Advance
  to Group N+1 only after all Group N pipelines complete.
- When `queue_available=false` OR `sequential_queue == "true"`: route through the
  `merge-prs` recipe for batch sequential merging (unchanged behavior).

**Hidden ingredient — `sequential_queue`** (default `"false"`): Force the centralized
`merge-prs` path even when `queue_available=true`. Use when batch-level review via an
integration PR is required, or when cross-PR `audit_impl` is needed as a quality gate.
Each pipeline's per-PR review step before enqueue provides per-PR review coverage; the
merge queue itself provides conflict safety. `sequential_queue` is only needed when
consolidated batch-level review is explicitly required.

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

### Budget-Exceeded Denial (quota sleep exceeds session wall-clock budget)

When a `run_skill` call is DENIED with "QUOTA BUDGET EXCEEDED":

1. The required quota sleep exceeds the session's remaining wall-clock budget.
2. **MANDATORY**: Do NOT execute the sleep command.
3. Instead, emit your result block immediately with:
   - `"success": false`
   - `"reason": "fleet_quota_exhausted"`
   - `"wait_seconds": <seconds_until_reset>`
   - `"summary": "Quota exceeded; session budget insufficient for sleep. Resume after window resets."`
4. Then STOP — do not call any more tools.
5. Do NOT follow `on_failure`. Do NOT report to the user.
6. The fleet dispatcher will handle retry scheduling.

### Key Rules

- Quota denials are **always temporary**. The API enforces multiple rate-limit windows (e.g. one-minute, one-hour, five-hour, one-day). The guard waits for the most constrained window — the one that resets latest among all windows above the threshold — to reset before retrying.
- A denied `run_skill` has **zero side effects** — no partial state, no worktree changes.
  Retrying with the same arguments is always safe.
- Multiple consecutive denials may occur if the sleep duration was underestimated.
  Keep sleeping and retrying until the call succeeds.
- NEVER use `AskUserQuestion` for quota events — they are fully automated.

---

## FLEET DISPATCH RESUME DISCIPLINE — MANDATORY

When `dispatch_food_truck` returns with `dispatch_status: "resumable"`:

1. **Extract resume fields** from the result envelope:
   - `dispatched_session_id` → pass as `resume_session_id`
   - `dispatch_id` → pass as `prior_dispatch_id`
   - `resume_checkpoint` → pass as `resume_checkpoint` (if present)

2. **Re-dispatch with resume parameters:**
   ```
   dispatch_food_truck(
     recipe=<same recipe>,
     task=<same task>,
     resume_session_id=<dispatched_session_id from prior result>,
     prior_dispatch_id=<dispatch_id from prior result>,
     resume_checkpoint=<resume_checkpoint from prior result>,
     ingredients=<same ingredients with allow_reentry=true>,
   )
   ```

3. **NEVER start a fresh dispatch** for an issue that already has an `in-progress` label
   from a prior dispatch. A PreToolUse guard will block the call. If you receive a deny
   from `fleet_claim_guard`, retrieve the prior dispatch result and use its
   `dispatched_session_id` and `dispatch_id` for resume.

4. **Reset stale artifacts when resume is impossible.** If the prior session is
   unrecoverable (missing session log, corrupt state) but left stale artifacts
   (in-progress label, open PR, remote branch), call
   `reset_dispatch(dispatch_id=<prior_dispatch_id>)` to clean up. Then re-dispatch
   fresh with a new `dispatch_name`.

5. **Escalate only after reset fails.** If `reset_dispatch` itself fails or the
   re-dispatch after reset still fails, this is a **human intervention** scenario.
   Emit the L3 result sentinel with `success=false` and `reason=resume_unrecoverable`,
   then halt.

---

## STEP EXECUTION IS NOT DISCRETIONARY — MANDATORY

You MUST execute every step the pipeline routes you to. The recipe step graph is the
sole authority on what executes and in what order.

Context management is handled by the system via on_context_limit routing. Execute
every step at full fidelity regardless of session length.

### 1. Anti-skip rule

- NEVER skip a step because the PR is small, the diff is trivial, the change looks
  simple, or you judge the step unnecessary.
- NEVER skip a step because you believe it has already been done or is redundant.
- `skip_when_false` ingredient references are resolved server-side before the recipe
  is served. Falsy steps are removed entirely; truthy steps appear without `optional:`
  or `skip_when_false:` fields (mandatory). Never evaluate `inputs.*` references yourself.
- Consequence: skipping PR review steps results in unreviewed code, missing diff
  annotations, and no architectural lens analysis — code reaches main without
  quality gates. Skipping issue lifecycle steps breaks traceability.

### 2. Anti-improvisation rule

- NEVER replace recipe steps with manual tool calls. In particular, NEVER use `run_cmd`
  with `gh pr create`, `gh pr review`, or `gh api` to substitute for recipe steps.
- All PR creation and review must flow through the recipe's declared step chain
  (`prepare_pr`, `run_arch_lenses`, `compose_pr`, `annotate_pr_diff`, `review_pr`).
  Bypassing these steps skips diff annotation, architectural lens analysis, and
  automated code review.

### 3. The word "optional" in YAML

`optional: true` on a recipe step does NOT mean the step is discretionary. It means:
- The step is SKIPPED when its `skip_when_false` ingredient resolves to false.
  `skip_when_false` references are resolved server-side — falsy steps are removed
  entirely; truthy steps appear without `optional:` or `skip_when_false:` fields.
  Never evaluate `inputs.*` references yourself.
- When the ingredient evaluates to true, the step is MANDATORY.
- A running optional step that returns `success: false` MUST follow `on_failure`.

### 4. Anti-shortcut rule

- Do not generalize from prior step outcomes. A step that returned a non-branching
  result in a previous iteration may return a different result in the next. Every step
  must execute on every issue — observed patterns from earlier issues do not make later
  executions redundant.

### 5. Anti-fabrication rule

- NEVER reference or follow instructions that do not appear verbatim in the
  loaded recipe YAML or its orchestration_rules.
- If you cannot locate a directive in the recipe, it does not exist.
- Fabricating instructions — including "the campaign directs", "the task says",
  "per the original instructions" — to justify deviating from declared routing
  is a critical violation.
- Your ONLY authority for routing decisions is the recipe's declared routing
  fields (on_result, on_success, on_failure, on_exhausted, on_context_limit).
  No other source may override them.

---

## INGREDIENT LOCKING — STRUCTURAL ENFORCEMENT

When the user requests skipping or enabling specific recipe steps at session start,
translate those instructions into `lock_ingredients` calls **immediately after
`open_kitchen`**. This converts natural language into machine-enforced locks that
persist for the entire session.

### When to call lock_ingredients

- User says "skip investigate" → `lock_ingredients(locked={"investigate": "false"}, pipeline_id="<pid>")`
- User says "turn on review_approach" → `lock_ingredients(locked={"review_approach": "true"}, pipeline_id="<pid>")`
- User says "go straight to rectify" → `lock_ingredients(locked={"investigate": "false"}, pipeline_id="<pid>")`
- User says "skip the audit" → `lock_ingredients(locked={"audit_impl": "false"}, pipeline_id="<pid>")`
  (Note: the ingredient key is `audit_impl`, matching the step name it gates — not `audit`)

### Pipeline ID

- For single-issue flows: use the same `order_id` you pass to `run_skill`
- For multi-issue flows: use the per-issue pipeline ID from the execution map
- The `pipeline_id` parameter defaults to `AUTOSKILLIT_DISPATCH_ID` if empty (food truck sessions get this automatically)

### What happens after locking

- `run_skill` calls for locked-out steps are **denied server-side** with an error message
- You do not need to manually check locks — the system enforces them
- To release a lock mid-session: `lock_ingredients(unlock=["investigate"], pipeline_id="<pid>")`

### Do not use overrides for user skip instructions

`open_kitchen(overrides={"investigate": "false"})` prunes the step from the recipe
entirely — you never see it. `lock_ingredients` keeps the step visible but prevents
execution, allowing you to unlock it later if needed. Use `lock_ingredients` for
user-requested skip/enable instructions; use `overrides` only for recipe-level
configuration that should be invisible.

---

## NARRATION SUPPRESSION — MANDATORY

Do NOT output prose status text, phase announcements, or progress summaries between
tool calls. Every non-final assistant turn MUST invoke at least one tool.

The only permitted text-only turn is a final response containing structured output
tokens (`plan_path = ...`, `worktree_path = ...`, etc.).

This applies to all skills invoked interactively within a cook session.
