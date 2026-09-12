# Session Diagnostics

## Overview

AutoSkillit captures two kinds of diagnostic output:

- **Structured logs** (structlog) go to stderr for real-time monitoring
- **Session diagnostics** go to files for post-hoc analysis of headless session behavior

Session diagnostics capture process-level data (memory, OOM scores, file descriptors, signals, CPU state) at regular intervals during headless sessions, then write structured JSON files after the session completes.

## Directory Structure

Logs are stored in a **global** directory (not per-project), so they persist across worktrees and clones.

### Platform Defaults

| Platform | Default Path |
|----------|-------------|
| Linux | `$XDG_DATA_HOME/autoskillit/logs` (defaults to `~/.local/share/autoskillit/logs`) |
| macOS | `~/Library/Application Support/autoskillit/logs` |

### Layout

```
~/.local/share/autoskillit/logs/
├── sessions.jsonl                    # Retained derived index (one row per committed session)
├── sessions-archive.jsonl            # Append-only rows evicted from the retained index
├── otlp.jsonl                        # Current scrubbed vendor-native OTLP capture
├── otlp.jsonl.1                      # Single rotated generation
├── child-outcomes/
│   └── {backend}/
│       └── {parent_session_id}.json  # Durable per-parent child-terminal-reason snapshot
└── sessions/
    └── {session_id}/                 # or pid_{pid}_{timestamp} if session_id unavailable
        ├── proc_trace.jsonl          # Full ProcSnapshot series
        ├── token_usage.json          # Bounded aggregate and turn-series descriptor
        ├── turn_usage.jsonl          # One row per observed parent model request
        ├── summary.json              # Session metadata and outcome
        └── anomalies.jsonl           # Present only if anomalies detected
```

## What Gets Captured

### ProcSnapshot Fields

| Field | Source | Description |
|-------|--------|-------------|
| `state` | psutil | Process state (running, sleeping, zombie, etc.) |
| `vm_rss_kb` | psutil | Resident set size in KB |
| `threads` | psutil | Thread count |
| `fd_count` | psutil | Open file descriptor count |
| `fd_soft_limit` | psutil | Soft limit for open file descriptors |
| `ctx_switches_voluntary` | psutil | Voluntary context switches |
| `ctx_switches_involuntary` | psutil | Involuntary context switches |
| `sig_pnd` | /proc | Pending signals bitmask (hex) |
| `sig_blk` | /proc | Blocked signals bitmask (hex) |
| `sig_cgt` | /proc | Caught signals bitmask (hex) |
| `oom_score` | /proc | OOM killer score (0-1000) |
| `wchan` | /proc | Kernel wait channel |

### Session Summary Fields

`summary.json` contains: `session_id`, `dir_name`, `pid`, `cwd`, `skill_command`, `success`, `subtype`, `exit_code`, `start_ts`, `snapshot_count`, `anomaly_count`, `peak_rss_kb`, `peak_oom_score`, `peak_fd_ratio`, `session_type`, `child_outcomes` (see [Child Terminal Reasons](#child-terminal-reasons)).

### Anomaly Types

| Kind | Condition | Severity |
|------|-----------|----------|
| `oom_spike` | OOM score delta > 200 between consecutive snapshots | warning |
| `oom_critical` | OOM score >= 800 | critical |
| `zombie_detected` | Process in zombie state | warning |
| `zombie_persistent` | Zombie state for >= 3 consecutive snapshots | critical |
| `signals_pending` | Pending signals transition from zero to non-zero | warning |
| `rss_growth` | RSS grows > 2x initial over 5+ snapshots | warning |
| `fd_high` | fd_count / fd_soft_limit > 0.80 | warning |

## How It Works

1. **Accumulate**: During a headless session, `LinuxTracingHandle` collects `ProcSnapshot` objects in memory at the configured interval (default 5s)
2. **Flush**: After the session completes, `flush_session_log()` writes all per-session artifacts
3. **Commit**: `summary.json` is published last and commits an eligible diagnostic session
4. **Index**: One exclusive transaction upserts the committed session into the retained `sessions.jsonl` projection
5. **Retain and archive**: Automatic cleanup targets at most 2,000 committed directories; active-campaign protection may keep more, while evicted index rows are appended to `sessions-archive.jsonl`

`sessions.jsonl` is a bounded derived index, not an append-only ledger. Its
`timestamp` field remains the session `start_ts`, not completion or index-write
time. Starting from an exact summary/index projection, a successful transaction
preserves that projection, while deterministic crash-recovery replay heals its
current key. Historical inconsistencies for other keys remain doctor-visible.
Publication is atomic for concurrent writers and process crashes, but does not
promise strict power-loss durability or snapshot isolation for unlocked readers.

`sessions-archive.jsonl` is an append-only historical record of complete rows
evicted from that live projection. Its rows remain after their session artifact
directories are pruned. Archive appends use at-least-once semantics after an
uncertain failure, so historical readers must deduplicate by `dir_name`.
Archive-only queries exclude the current live survivor window, and existing
live-index tools do not automatically union the two files.

Schema-v8 index rows store the validated managed-launch classification in
`sessions.jsonl.session_type`, using the canonical headless values `skill`,
`orchestrator`, or `fleet`. Pre-v8 rows can omit the additive field; v8 rows
without a validated managed-launch classification store `null`. Backend-native
L0 leaves do not create AutoSkillit session rows, and interactive CLI sessions
remain outside this headless-only field. See the authoritative
[orchestration-level mapping](../orchestration-levels.md).

Schema-v9 rows add `subagent_model_outcomes`; v8 rows are retained without
that field and require neither a rewrite nor a version-specific reader.
Absent values read as empty lists.

Schema-v11 rows add `child_outcomes` (see [Child Terminal
Reasons](#child-terminal-reasons)); older rows are retained without that
field, same as v9's `subagent_model_outcomes`.

| Field | Meaning | Source |
| --- | --- | --- |
| `model_identifier` | Effective model (OTLP-proven native top-level when available, otherwise launch/token fallback). | `sessions.jsonl`, `token_usage.json`, `summary.json.versions.model_identifier` (when a versions bundle exists) |
| `configured_model` | Requested launch value. | `sessions.jsonl`, `token_usage.json`. Not written to `summary.json`. |

## Per-request token usage

Completed sessions with per-request evidence publish `turn_usage.jsonl` before
`token_usage.json`; `summary.json` remains the final completion artifact. The
version-3 token descriptor adds `turn_usage_file`, `turn_usage_count`, and
`turn_usage_schema_version`. The file reference is `null` with count zero when
no series was observed. The JSONL row schema is version 1.

Each row retains nullable source `message_id`, `request_id`, `timestamp`, and
observed `model`, plus `input_tokens`, `output_tokens`, `cache_read_tokens`,
`cache_creation_tokens`, `context_window_tokens`, and `context_fraction`.
Ledger input is inclusive of cached input: Claude raw counters are combined
only when all input components are known, while Codex input is already
inclusive. Unknown counters and metadata remain `null`.

`context_fraction` is the normalized cache-read proxy
`cache_read_tokens / context_window_tokens` for that row's observed model. It
does not include uncached or newly cached input and is not total context
occupancy. Claude snapshots deduplicate only by a non-empty native
`message.id`, preserving first-seen order and the first timestamp. Codex rows
come from advancing `last_token_usage` snapshots in the native rollout, bounded
to the subprocess interval; the terminal stdout aggregate is not a request row.

Use the `turn_usage` handle of `inspect_session_logs` to read the series. Reads
use the tool's existing byte cap and signed continuation token, and incomplete
final JSONL records are withheld. Inspection reads the retained sidecar and
does not reparse a transcript. Older diagnostics acquire a series only when
their native evidence is explicitly processed; no automatic archive backfill
is performed.

## Native OTLP capture and correlation

Headless execution enables vendor-native logs and metrics against one
invocation-scoped loopback HTTP/JSON sink. Claude Code is activated with
`CLAUDE_CODE_ENABLE_TELEMETRY`, `OTEL_LOGS_EXPORTER`, and
`OTEL_METRICS_EXPORTER`. Codex derives native per-launch `[otel]` CLI overrides
from the same sink endpoints; Claude-only activation and exporter-selection
variables are removed from the Codex child environment. Interactive Codex and
persistent `config.toml` files are not changed.

The persisted `otlp.jsonl` wrapper adds only `signal` and `payload`; the payload
keeps the vendor's OTLP nesting and event vocabulary. Claude log records use the
`com.anthropic.claude_code.events` instrumentation scope and carry `session.id`.
Codex log records retain `event.name=codex.*` and carry `conversation.id`.
Those native values equal the corresponding `sessions.jsonl.session_id` and are
the direct join keys:

| Backend | Native log attribute | Authoritative session field |
|---|---|---|
| Claude Code | `session.id` | `sessions.jsonl.session_id` |
| Codex | `conversation.id` | `sessions.jsonl.session_id` |

`thread_id` remains the Codex rollout/notify and resume identifier; it is not
the OTLP attribute name. Codex metrics may omit `conversation.id`, so the direct
join guarantee applies to emitted log records, not every signal. Field
availability otherwise remains whatever the vendor emitted. For a specialized
Codex child, native `session_meta.payload.agent_role` is the stable registered
agent-definition name; the child `payload.id` and its explicit parent link are
the structural run identity.

PII scrubbing happens recursively before persistence, including nested OTLP
attribute lists. Native join and event-name attributes are retained while user,
account, organization, and email identifiers are removed. Prompt, assistant,
tool-content, and raw-API-body capture are not enabled by this integration.

Consumers must preserve raw accounting and stop metadata. Do not add
cache-read tokens to input tokens, add reasoning tokens to output tokens, or
treat `finish_reasons=["length"]` as proof of context exhaustion.
`sessions.jsonl` is only the retained session projection.

## Child Terminal Reasons

Issue #4623. Every observed L0 child run (native Claude subagent, native Codex
`spawn_agent` thread, or managed leaf) gets one durable row keyed by structural
identity (backend, parent session, child/attempt ID), independent of whether
its parent ever produces a committed `sessions.jsonl` row. This is this
project's own observational taxonomy, not an adopted external standard.

### Canonical reasons

| Reason | Meaning |
| --- | --- |
| `completed` | Explicit normal child terminal result, no adverse terminal evidence |
| `context_exhausted` | Explicit context-window terminal evidence (`InfraExitCategory.CONTEXT_EXHAUSTED`) |
| `turn_limited` | Explicit `max_turns`/`error_max_turns` terminal evidence |
| `error` | Explicit provider/execution terminal error, including `api_error` even when subtype reports success |
| `interrupted` | Confirmed child cancellation/interrupt |
| `abandoned` | Owner explicitly abandons or kills a still-running child, with no recovered result |
| `unknown` | Only a generic stop/completion notification, absent result, or an unrecognized cause — countable, never omitted |

Explicit adverse terminal evidence (context exhaustion, turn limit, error,
interrupt, abandonment) always outranks a success subtype or a generic
lifecycle stop/completion notification. A historical run with no retained
terminal marker stays `unknown` forever — recovery and later evidence can
*refine* `unknown` into a known reason, never invent one from silence,
timing, role, or transcript-marker absence. `unknown` splits into two causes,
both preserved in the row's raw fields rather than collapsed: **unknown from
absence** (no terminal evidence was ever retained) and **unknown from
conflict** (two equally authoritative terminal records disagree — the raw
conflicting evidence stays on the row for diagnosis).

### Row shape

Each row carries `child_id`, `launch_alias` (a later-bound backend-native
session/thread ID merged onto the same row, not a second row), `backend`,
`parent_session_id`, `role`, `attribution_skill`, `effective_model`,
`effective_effort`, `effective_provider`, `terminal_reason`, and the raw
evidence that produced it: `raw_reason`, `raw_subtype`, `raw_code`,
`evidence_source`. For Codex, `role` is copied verbatim from the linked child's
native `agent_role`, while model and effort come from that child's
`turn_context.payload.model` and `.effort`. Blank historical or undeclared
roles remain unresolved; task names, paths, instructions, and timestamps never
reconstruct them.

### Native vs. managed paths

- **Native** — a `SubagentStart`/`SubagentStop`/parent `PostToolUse` hook
  (Claude) or structural `sub_agent_activity` rollout evidence (Codex)
  confirms the child. A start event inserts a durable `unknown` row
  immediately, even if a terminal event never arrives. Codex discovery requires
  the parent's exact `event_msg` / `sub_agent_activity` / `kind: started` /
  `agent_thread_id` envelope. Metadata attaches only when the child's sole
  `session_meta.payload.id` and explicit parent link match those IDs.
- **Managed** — the executor records each physical provider-attempt
  directly (`ManagedAttemptRecorder` in `execution/child_outcomes.py`) using
  internal `SkillResult` evidence (`cli_subtype`, `api_failure.terminal_reason`,
  `infra.exit_category`) no hook can observe. A reservation is promoted to a
  row only on confirmed process/session start — a cancelled or rejected
  reservation never fabricates a child.

### Orphan discovery and retention

The snapshot is the canonical record; `summary.json`'s and `sessions.jsonl`'s
`child_outcomes` fields are read-only projections of it, refreshed from one
frozen snapshot read per publication. The canonical snapshot is never pruned
by session retention or archival — a session whose own `sessions/` directory
was evicted, or one with no `sessions.jsonl` row at all (an orphan, or an
interactive parent), remains directly queryable under
`child-outcomes/{backend}/{parent_session_id}.json`.

Headless sessions publish through their normal telemetry flush. Managed
interactive Codex attempts publish after rollout promotion and after releasing
their lifecycle and thread leases. Normal finalization and crash recovery use
the same collector. A completed attempt view stays available until publication
succeeds, including recovery of a view that was already marked complete.

The following query combines canonical snapshots (including interactive
parents with no retained session row) with retained summary/index projections,
deduplicates repeated projections by structural identity, and groups named
roles by backend and definition name:

```bash
diagnostic_log_root="${XDG_DATA_HOME:-$HOME/.local/share}/autoskillit/logs"
query_dir=".autoskillit/temp/codex-child-agent-identity-4634"
mkdir -p "$query_dir"
{
  find "$diagnostic_log_root/child-outcomes" -type f -name '*.json' -print0 \
    | xargs -0 -r jq -c '.children[].outcome'
  find "$diagnostic_log_root/sessions" -type f -name summary.json -print0 \
    | xargs -0 -r jq -c '.child_outcomes[]?'
  jq -c '.child_outcomes[]?' "$diagnostic_log_root/sessions.jsonl"
} >"$query_dir/all-child-rows.jsonl" 2>"$query_dir/query-errors.txt"
jq -sc '
  unique_by([.backend, .parent_session_id, .child_id])
  | map(select(.role != ""))
  | group_by([.backend, .role])
  | map({backend: .[0].backend, role: .[0].role,
         runs: map({parent_session_id, child_id, effective_model, effective_effort})})
' "$query_dir/all-child-rows.jsonl" >"$query_dir/role-view.json" 2>&1
head -c 65536 "$query_dir/role-view.json"
```

## Configuration

In `.autoskillit/config.yaml`:

```yaml
linux_tracing:
  enabled: true          # default: true
  proc_interval: 5.0     # seconds between snapshots
  log_dir: ""            # empty = platform default, set absolute path to override
```

## Per-Run Enablement

By default, `pipeline_health` resolves from `diagnostics.pipeline_health` (shipped
default: `false`). To enable diagnostics for a single run, the orchestrator can pass
`overrides={"pipeline_health": "true"}` to `open_kitchen`. The override takes effect
for that kitchen session only and does not modify the persistent config.

The orchestrator can also lock the value for the session via
`lock_ingredients(locked={"pipeline_health": "true"})` to ensure it is not overridden
by downstream steps.

For post-hoc diagnostics on a completed run, use:
```bash
run_skill /autoskillit:analyze-pipeline-health <kitchen_id>
```

## Finding Problematic Sessions

```bash
# Sessions with anomalies
jq 'select(.anomaly_count > 0)' ~/.local/share/autoskillit/logs/sessions.jsonl

# Failed sessions
jq 'select(.success == false)' ~/.local/share/autoskillit/logs/sessions.jsonl

# Managed L2 orchestrator sessions
jq 'select(.session_type == "orchestrator")' ~/.local/share/autoskillit/logs/sessions.jsonl

# View anomalies for a specific session
cat ~/.local/share/autoskillit/logs/sessions/{session_id}/anomalies.jsonl | jq .

# High memory sessions
jq 'select(.peak_rss_kb > 1000000)' ~/.local/share/autoskillit/logs/sessions.jsonl
```

## Disabling

Set `linux_tracing.enabled: false` in your config to disable all session diagnostics file output. Non-Linux platforms produce no output regardless of this setting.

## Path components use hyphens, not underscores

Log directory names and session folder names are hyphen-separated. Never assume
underscores when constructing or searching for log paths — a hyphen mismatch
causes ENOENT (the root cause of the session `f9170655` debugging session).
The invariant is documented in `AGENTS.md` §6 and enforced when
`execution/session_log.py` lays out per-session directories.
