# Sprint guide

How to drive a multi-issue overnight pipeline with AutoSkillit, tune the
quota guard so it neither blocks nor over-runs, and recover from common
stall modes.

## Four-skill chain

The sprint workflow chains four skills end to end:

1. **`triage-issues`** — reads open GitHub issues, classifies them by
   complexity and domain, and writes a triage report.
2. **`sprint-planner`** — consumes the triage report, groups issues into
   sprints by domain coupling, and emits a sprint plan.
3. **`process-issues`** — runs the `implementation-groups` recipe over each
   sprint, in wavefront-scheduled parallel batches.
4. **`pipeline-summary`** — reads the audit log, the token log, and the
   timing log; opens a GitHub issue and PR summarising what landed and what
   bugs surfaced during the run.

Each skill consumes the previous skill's structured output via the
orchestrator's capture-and-route mechanism (see
[../execution/orchestration.md](../execution/orchestration.md)).

## `triage-issues`

Skill: `/autoskillit:triage-issues`. Reads open issues via `gh issue list`,
groups by label and file path, and writes
`.autoskillit/temp/triage/triage_<date>.md`. The report classifies every
issue as `simple`, `needs_check`, or `needs_plan`.

## `sprint-planner`

Skill: `/autoskillit:sprint-planner`. Consumes the triage report and groups
issues into independent batches by file overlap. The output is a sprint plan
with explicit "wave 1", "wave 2", … markers that map onto the wavefront
scheduling rule in `recipes/implementation-groups.yaml`.

## `process-issues`

Skill: `/autoskillit:process-issues`. Drives `autoskillit order
implementation-groups` against the sprint plan. Each wave runs in parallel
under `pipeline/background.DefaultBackgroundSupervisor`.

## `pipeline-summary`

Skill: `/autoskillit:pipeline-summary`. Reads the accumulated audit log,
token log, timing log, and quota events, then opens a GitHub issue listing
every PR that landed, every test that flaked, every quota event, and every
bug surfaced during the run.

## Quota tuning + buffer

The quota guard blocks new headless sessions when the binding rate-limit
window crosses its own per-window threshold. Short windows (e.g. `five_hour`)
use `quota_guard.short_window_threshold` (default 85.0%); long windows
matched by `quota_guard.long_window_patterns` (default `weekly`, `sonnet`,
`opus`) use `quota_guard.long_window_threshold` (default 98.0%) — short-window
exhaustion means imminent throttling, while 15% headroom on a multi-day
weekly window is comfortable.

```yaml
quota_guard:
  enabled: true
  short_window_threshold: 85.0   # block at 85% for short windows
  long_window_threshold: 98.0    # block at 98% for long windows (weekly, sonnet, opus)
  long_window_patterns:
    - weekly
    - sonnet
    - opus
  buffer_seconds: 60             # extra delay above the strict reset time
  cache_path: "~/.claude/autoskillit_quota_cache.json"
```

The `buffer_seconds` value is the slack the guard adds to the cache hit
when computing how long to sleep, so multiple parallel pipelines do not
hammer the API at the exact reset second.

## Interpreting `get_quota_events`

`get_quota_events` returns the list of throttle events the quota guard has
fired during the current orchestrator session. Each event has a timestamp,
the observed utilization percentage, and the sleep duration the orchestrator
was instructed to wait. Use the event stream to confirm that overnight
pipelines are pacing correctly.

## Stale session detection

If a worker emits no output for `run_skill.stale_threshold` seconds (default
1200, 20 minutes), the orchestrator records a `stale` `retry_reason` and
re-spawns from scratch. Stale events are visible in the audit log under the
step that produced them.

## Overnight scheduling pattern

The recommended pattern for an overnight run on a 50-issue sprint:

| Phase | Time | Activity |
|-------|------|----------|
| 19:00 | 5 min | Run `triage-issues` and review the report |
| 19:10 | 5 min | Run `sprint-planner` and adjust sprint groupings |
| 19:15 | 8 hours | Launch `process-issues` and walk away |
| 03:30 | 5 min | Read `pipeline-summary` output the next morning |

## Per-pipeline env-var overrides

Individual pipeline runs can override config values via `AUTOSKILLIT_*`
environment variables (see `cli/_prompts.py` for the full list). The most
common override is the API key per project:

```bash
AUTOSKILLIT_GITHUB__TOKEN=ghp_xxx autoskillit order implementation
```

## CI integration with `autoskillit migrate --check`

Add `autoskillit migrate --check` to the project's CI matrix to fail the
build if any project-local recipes are stale relative to the running
package. Pair with the gitleaks pre-commit hook to enforce the secrets-in-
secrets-file invariant on every commit.

## `report_bug` auto-dedup

When `pipeline-summary` discovers a bug fingerprint that already has an
open GitHub issue, `report_bug` deduplicates by appending a comment to the
existing issue rather than opening a new one. Dedup is keyed by the bug
fingerprint emitted by the bug-investigation skill.

## Quota-guard multi-window selection

`execution/quota.py` reads up to three rolling windows from the API quota
endpoint (1-hour, 5-hour, daily). The guard selects the window with the
highest current utilization and blocks against that one. This avoids the
common pitfall of being well under the daily budget but pegged on the
hourly rate.

## See also

- [../examples/research-pipeline.md](../examples/research-pipeline.md) —
  end-to-end example with merged research and archive PRs.
