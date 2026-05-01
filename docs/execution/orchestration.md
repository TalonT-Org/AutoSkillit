# Orchestration

> **See also:** [`docs/orchestration-levels.md`](../orchestration-levels.md) for
> the formal definition of the L0–L3 orchestration hierarchy.

How AutoSkillit routes work between the **L2** orchestrator and the **L1**
worker sessions, what the orchestrator does on every retry verdict, and how
the merge pipeline decides whether a worktree is ready to land.

## Multi-level orchestration model

AutoSkillit defines four orchestration levels (L0–L3). The recipe execution
pipeline primarily connects the L2 orchestrator and L1 workers:

- **L2 — orchestrator.** A Claude Code session running the
  `autoskillit order` CLI command, with the kitchen pre-opened. Sees all 42
  MCP tools, spawns headless workers, and routes verdicts. Never reads or
  writes code itself.
- **L1 — worker.** A headless Claude session launched by `run_skill`.
  Sees the 2 free range tools plus `test_check` (the only `headless`-tagged
  tool). Cannot call `run_skill`, `run_cmd`, or `run_python`.

The boundary is enforced three ways: FastMCP visibility, the
`leaf_orchestration_guard.py` PreToolUse hook, and the
`_require_orchestrator_or_higher()` runtime guard inside `tools_execution.py`. All
three must independently agree before any orchestration tool can fire.

## Recipe as a program

A recipe (`recipe/schema.py:Recipe`) is a sequenced list of `RecipeStep`
entries. Each step names either an MCP tool (`run_skill`, `merge_worktree`,
…) or a sub-recipe. The orchestrator's job is to walk the step graph,
collect each step's inputs from the recipe ingredients and from prior
captures, invoke the tool, and route the verdict.

## Verdict routing

The orchestrator's gate (`pipeline/gate.py:DefaultGateState`) inspects each
tool result. The routing rules per tool:

- `run_skill` — read the structured tokens emitted by the worker; route to
  the matching `verdict_routes` entry. Unrouted verdicts are a recipe
  validation error caught by `recipe/rules_verdict.py`.
- `merge_worktree` — read `merge_state` from the JSON response; reroute on
  the `MergeFailedStep` value (path validation, protected branch, dirty
  tree, test gate, …).
- `clone_repo` / `remove_clone` — read `clone_id` and stash for later
  cleanup via `register_clone_status` and `batch_cleanup_clones`.

## The 11 `retry_reason` values

`RetryReason` is a `StrEnum` in `src/autoskillit/core/_type_enums.py` with
11 distinct values. Each value triggers a different recovery route:

| Value | When the orchestrator sets it | Recovery |
|-------|-------------------------------|----------|
| `resume` | Worker hit context limit but left a worktree intact | Route to `/autoskillit:retry-worktree` against the same worktree path |
| `stale` | No output for `run_skill.stale_threshold` seconds | Kill and re-spawn from scratch (not a context exhaustion) |
| `none` | No retry — tool succeeded | Continue to the next step |
| `budget_exhausted` | Token-budget cap reached for the step | Re-plan or escalate; do not auto-retry |
| `early_stop` | Worker emitted a structured `early_stop` token | Skip remaining sub-steps and route to a fallback |
| `zero_writes` | Worker exited cleanly but produced no file writes | Re-spawn once, then escalate |
| `empty_output` | Natural exit with rc=0 and no stdout, no partial progress | Treat as a transient failure; one retry then escalate |
| `drain_race` | Channel-confirmed completion but stdout was not flushed before kill | Replay the captured channel record; do not re-spawn |
| `path_contamination` | Worker wrote outside its CWD boundary | Hard-fail; do not retry — this is an isolation breach |
| `contract_recovery` | Marker present and write evidence on disk, but the structured contract token was missing | Treat as success and synthesise the contract from disk |
| `clone_contamination` | Worker mutated the source clone instead of the worktree | Hard-fail; abort the entire `order` |

`recipe/rules_isolation.py` enforces matching `clone_contamination` and
`path_contamination` defenses at recipe-validation time.

## Wavefront scheduling

The `implementation-groups` recipe runs many sibling implementations in
parallel waves rather than serially. The three-part rule:

1. Group the input issues into **independent groups** by analyzing file
   overlap.
2. Within each group, run every issue in parallel via background `run_skill`
   calls supervised by `pipeline/background.py:DefaultBackgroundSupervisor`.
3. Wait for the entire wave to settle before starting the next wave.

## Multi-part sequencing

When an implementation plan is split into `_part_a`, `_part_b`, … files, the
orchestrator MUST merge each part's worktree into the base branch BEFORE
spawning the next part's `implement-worktree-no-merge`. Part N+1 starts from
the post-merge state of the base branch, not from Part N's base commit. This
rule applies even when running off-recipe.

## Merge-phase decision tree

`server/git.py:perform_merge` runs a 13-step pipeline before allowing a
worktree to land:

```
path validation
  → protected branch check
    → branch detection
      → dirty-tree check
        → pre-merge test gate
          → fetch
            → pre-rebase guard (no in-flight merge commits)
              → merge-commits-detected stop
                → rebase
                  → generated-file cleanup
                    → post-rebase test gate
                      → merge
                        → editable-install guard
```

Failure at any step yields a `MergeFailedStep` value that the orchestrator
maps to a recovery skill (`resolve-failures`, `resolve-merge-conflicts`,
`diagnose-ci`, …).

## Sous-chef injection

`open_kitchen` (in `server/tools_kitchen.py`) materialises the internal
`sous-chef` skill into the live session at runtime. Sous-chef is not
registered as a slash command — it lives in `src/autoskillit/skills/` only so
the plugin scanner picks up its directory metadata. The injection guarantees
the orchestrator's prompt always includes the canonical operating instructions
even on session resume.

## CI watcher

`execution/ci.py` polls GitHub Actions for the active PR via httpx and never
raises. The three-phase algorithm:

1. **Discovery** — find the workflow runs on the head branch.
2. **In-progress poll** — back off exponentially while runs are queued or
   running.
3. **Verdict** — once every required check has settled, report
   `success`/`failure`/`neutral` and exit.

## Merge-queue stall recovery

`execution/merge_queue.py` watches GitHub's merge queue. When a queued PR
stalls (no state transition within the configured window), the watcher
re-toggles `auto_merge` via `toggle_auto_merge` to nudge the queue. If the
queue does not advance after that, the orchestrator escalates to
`/autoskillit:diagnose-ci`.
